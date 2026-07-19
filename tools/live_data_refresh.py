"""增量数据刷新(Stage 0,实盘路线)—— 把四个一次性研究抓取器包成幂等的生产刷新。设计:
docs/engineering/specs/2026-07-04-forward-track-live-design.md §2.2。

四个源全部复用现有幂等原语,只补水位之后的缺口:
1. 价格 `crypto_funding.db`(PIT 库,主键去重 + 未来数据守卫):fetch_daily_rows → ingest_provider_rows;
2. 资金费 `crypto_funding_rates.json`:fetch_daily_funding 按日桶合并(已有日不覆盖);
3. 永续 mark price `crypto_perp_mark_prices.json`:Data Vision 日归档,16 日定价覆盖 + 最近 8 日质量门;
4. OI 指标 `crypto_oi_metrics.json`:binance_oi_metrics_fetch.main 本身断点续传(缺日补齐)。

产出 data/cache/live_refresh_status.json:各源刷新前/后水位、新增数、失败列表、staleness 天数。
逐 ticker fail-soft(单个失败记录、不中断整轮)。网络/墙钟只在此(门外);读写只碰 data/cache。
Read-only 公开行情,无 key、无交易所 SDK、无下单/转账——与既有抓取器同一治理边界。
"""
from __future__ import annotations

import json
from math import isfinite
import multiprocessing
import os
from queue import Empty
import sqlite3
import time

from services.data.persistent_store import SqlitePITStore
from services.data.market_provider import ingest_provider_rows
from services.data.file_ingest import FatalIngestionError
from services.data.quality import build_ingestion_report
from services.data.raw_archive import (DEFAULT_RAW_ARCHIVE,
                                       summarize_archive)
from services.data.refresh_history import (RefreshHistoryStore,
                                           source_outcome)
from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    DOMAIN_EVENT_PROJECTION_DB,
    dispatch_refresh_event_outbox,
    open_domain_event_bus,
    project_pending_domain_events,
)
from tools.alert_sink import emit
from tools.binance_klines_fetch import fetch_daily_rows
from tools.binance_funding_fetch import (FundingDataError, fetch_daily_funding,
                                         validate_daily_funding)
from tools.binance_perp_mark_fetch import (load_mark_prices,
                                           refresh_mark_prices)
from tools import binance_oi_metrics_fetch as oi_fetch
from tools.cache_io import atomic_write_json
from tools.source_failures import source_failure_detail

_DAY_MS = 86_400_000
_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND = os.path.join("data", "cache", "crypto_funding_rates.json")
_OI = os.path.join("data", "cache", "crypto_oi_metrics.json")
_MARK = os.path.join("data", "cache", "crypto_perp_mark_prices.json")
_STATUS = os.path.join("data", "cache", "live_refresh_status.json")
_HISTORY = os.path.join("data", "cache", "live_refresh_history.db")
_SLO_STATUS = os.path.join("data", "cache", "live_refresh_slo.json")
_RAW_ARCHIVE = DEFAULT_RAW_ARCHIVE
_OI_REQUIRED_FIELDS = ("oi", "oi_value", "top_ls", "global_ls", "taker_ls")
_OI_QUALITY_LOOKBACK_DAYS = 8
_SOURCE_ORDER = ("prices", "funding", "perp_mark_prices", "oi_metrics")
SOURCE_TIMEOUT_SECONDS = {
    "prices": 900.0,
    "funding": 900.0,
    "perp_mark_prices": 1_200.0,
    "oi_metrics": 1_800.0,
}
GLOBAL_TIMEOUT_SECONDS = 1_800.0
SLO_WINDOW_RUNS = 30
SLO_MINIMUM_RUNS = 30
SLO_MINIMUM_SUCCESS_RATE = 0.99
SLO_MINIMUM_INSTRUMENT_COVERAGE = 0.99
SLO_MAX_CONSECUTIVE_FAILURES = 3


def _record_failure(report: dict, ticker: str, exc: BaseException, *, quality=False) -> None:
    if ticker not in report["failed_tickers"]:
        report["failed_tickers"].append(ticker)
    if quality and ticker not in report["quality_failed"]:
        report["quality_failed"].append(ticker)
    report["failed"].append(f"{ticker}: {exc}")
    report["failure_details"].append(
        source_failure_detail(ticker, exc, kind="QUALITY" if quality else None)
    )


def price_watermarks(db_path: str = _DB) -> dict:
    """{ticker: 最新 event_time}。库不存在 → 空 dict(诚实,由调用方当 staleness 告警)。"""
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    try:
        return {r[0]: int(r[1]) for r in
                conn.execute("SELECT key, MAX(event_time) FROM bars GROUP BY key")}
    finally:
        conn.close()


def _gap_days(watermark_ms: int, now_ms: int) -> int:
    today = (now_ms // _DAY_MS) * _DAY_MS
    return max(0, (today - watermark_ms) // _DAY_MS)


def refresh_prices(tickers, *, now_ms: int, fetch=fetch_daily_rows, db_path: str = _DB,
                   pad_days: int = 3, sleep: float = 0.15,
                   archive_root=None) -> dict:
    """只对有缺口的 ticker 抓 klines 并 ingest(PIT 主键天然幂等)。宇宙 = 既有快照 instruments,
    刷新不扩宇宙(扩宇宙是研究决策,不是刷新的职责)。"""
    wm = price_watermarks(db_path)
    store = SqlitePITStore(db_path, create=False)
    report = {"source": "prices", "refreshed": 0, "skipped": 0, "ingested_rows": 0, "failed": [],
              "failure_details": [], "failed_tickers": [], "quality_failed": []}
    for tk in tickers:
        gap = _gap_days(wm.get(tk, 0), now_ms)
        if gap <= 1:                          # 只差今天(未收盘,PIT 本来就丢) → 无缺口
            report["skipped"] += 1
            continue
        try:
            if archive_root is None:
                rows = fetch(tk, days=gap + pad_days, now_ms=now_ms)
            else:
                rows = fetch(
                    tk, days=gap + pad_days, now_ms=now_ms,
                    archive_root=archive_root, observed_at_ms=now_ms,
                )
            today = (now_ms // _DAY_MS) * _DAY_MS
            expected_start = wm[tk] + _DAY_MS if tk in wm else None
            rep = ingest_provider_rows(
                rows,
                store,
                dataset_id=f"bnf-{tk}",
                now=now_ms,
                time_unit="ms",
                strict=True,
                expected_interval=_DAY_MS,
                expected_start=expected_start,
                expected_end=today - _DAY_MS,
            )
            report["refreshed"] += 1
            report["ingested_rows"] += getattr(rep, "bars_ingested", 0) or 0
        except FatalIngestionError as exc:
            _record_failure(report, tk, exc, quality=True)
        except Exception as exc:              # fail-soft:记录、继续
            _record_failure(report, tk, exc)
        if sleep:
            time.sleep(sleep)
    wm2 = price_watermarks(db_path)
    report["watermark_after"] = min(wm2.values()) if wm2 else None
    return report


def merge_funding_days(existing: dict, fresh: dict) -> int:
    """把 {day_ms:int → rate} 合并进 {day_ms_str → rate}(已有日不覆盖——历史资金费是事实,不改写)。
    返回新增日数。纯函数,便于离线测试。"""
    n_new = 0
    for day, rate in fresh.items():
        k = str(int(day))
        if k not in existing:
            existing[k] = rate
            n_new += 1
    return n_new


def refresh_funding(tickers, *, now_ms: int, fetch=fetch_daily_funding, fund_path: str = _FUND,
                    pad_days: int = 3, sleep: float = 0.15,
                    max_workers: int = 8, archive_root=None) -> dict:
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    fund = json.load(open(fund_path, encoding="utf-8")) if os.path.exists(fund_path) else {}
    report = {"source": "funding", "refreshed": 0, "skipped": 0, "new_days": 0,
              "failed": [], "failure_details": [], "failed_tickers": [], "quality_failed": []}
    pending = []
    for tk in tickers:
        days_known = [int(k) for k in fund.get(tk, {})]
        gap = _gap_days(max(days_known) if days_known else 0, now_ms)
        if gap <= 1:
            report["skipped"] += 1
            continue
        pending.append((tk, gap + pad_days))

    def fetch_one(item):
        tk, days = item
        try:
            if archive_root is None:
                fresh = fetch(tk, days=days, now_ms=now_ms)
            else:
                fresh = fetch(
                    tk, days=days, now_ms=now_ms,
                    archive_root=archive_root, observed_at_ms=now_ms,
                )
            return tk, fresh, None
        except Exception as exc:
            return tk, None, exc
        finally:
            if sleep:
                time.sleep(sleep)

    if max_workers == 1:
        results = list(map(fetch_one, pending))
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(fetch_one, pending))

    for tk, fresh, fetch_error in results:
        if fetch_error is not None:
            if isinstance(fetch_error, (FatalIngestionError, FundingDataError)):
                _record_failure(report, tk, fetch_error, quality=True)
            else:
                _record_failure(report, tk, fetch_error)
            continue
        try:
            candidate = dict(fund.get(tk, {}))
            n_new = merge_funding_days(candidate, fresh)
            today = (now_ms // _DAY_MS) * _DAY_MS
            validate_daily_funding(tk, candidate, expected_end=today - _DAY_MS)
            fund[tk] = candidate
            report["new_days"] += n_new
            report["refreshed"] += 1
        except (FatalIngestionError, FundingDataError) as exc:
            _record_failure(report, tk, exc, quality=True)
        except Exception as exc:
            _record_failure(report, tk, exc)
    atomic_write_json(fund_path, fund)
    wm = [max(int(k) for k in d) for d in fund.values() if d]
    report["watermark_after"] = min(wm) if wm else None
    return report


def refresh_oi(tickers=None, *, now_ms=None, fetch=oi_fetch.main,
               oi_path: str = _OI, archive_root=None) -> dict:
    """OI 指标归档刷新:直接复用 binance_oi_metrics_fetch.main(缺日补齐 + 逐 ticker 断点)。
    只刷 DEFAULT_TICKERS(在市流动大币);已退市 ticker 的历史段不动。"""
    selected = list(tickers or oi_fetch.DEFAULT_TICKERS)
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    before = _oi_watermark(oi_path)
    if archive_root is None:
        fetch_report = fetch(selected) or {}
    else:
        fetch_report = fetch(
            selected, archive_root=archive_root, observed_at_ms=now_ms
        ) or {}
    after = _oi_watermark(oi_path)
    quality_failed = oi_quality_failures(selected, now_ms=now_ms, oi_path=oi_path)
    source_details = list(fetch_report.get("failure_details", []))
    source_failed = {
        detail.get("ticker") for detail in source_details
        if detail.get("kind") != "NOT_FOUND" and detail.get("ticker")
    }
    failed_tickers = sorted(set(quality_failed) | source_failed)
    details = source_details + [
        {"ticker": ticker, "kind": "QUALITY", "error_type": "OIQualityError",
         "message": "cached OI series failed quality/freshness admission"}
        for ticker in quality_failed
    ]
    return {"source": "oi_metrics", "watermark_before": before, "watermark_after": after,
            "failed": [f"{item['ticker']}: {item['kind']}: {item['message']}" for item in details],
            "failure_details": details, "failed_tickers": failed_tickers,
            "quality_failed": quality_failed}


def _oi_watermarks(oi_path: str = _OI) -> dict:
    if not os.path.exists(oi_path):
        return {}
    cache = json.load(open(oi_path, encoding="utf-8"))
    data = cache.get("data")
    if not isinstance(data, dict):
        raise ValueError("OI cache data must be an object")
    out = {}
    for ticker, records in data.items():
        if not isinstance(records, dict) or not records:
            continue
        try:
            out[str(ticker)] = max(int(day) for day in records)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"OI cache has invalid day key for {ticker}") from exc
    return out


def _oi_watermark(oi_path: str = _OI):
    watermarks = _oi_watermarks(oi_path)
    return max(watermarks.values()) if watermarks else None


def oi_quality_failures(tickers, *, now_ms: int, oi_path: str = _OI,
                        lookback_days: int = _OI_QUALITY_LOOKBACK_DAYS) -> list:
    selected = sorted(set(str(ticker) for ticker in tickers))
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    try:
        cache = json.load(open(oi_path, encoding="utf-8"))
        data = cache.get("data")
        if not isinstance(data, dict):
            raise ValueError("OI cache data must be an object")
    except (OSError, ValueError, json.JSONDecodeError):
        return selected

    failed = []
    last_closed = (now_ms // _DAY_MS) * _DAY_MS - _DAY_MS
    for ticker in selected:
        raw_records = data.get(ticker)
        if not isinstance(raw_records, dict) or not raw_records:
            failed.append(ticker)
            continue
        try:
            pairs = sorted((int(day), record) for day, record in raw_records.items())
        except (TypeError, ValueError):
            failed.append(ticker)
            continue
        recent = pairs[-lookback_days:]
        days = [day for day, _record in recent]
        report = build_ingestion_report(
            f"oi-{ticker}",
            {ticker: days},
            as_of=last_closed,
            expected_interval=_DAY_MS,
        )
        invalid_record = len(recent) < lookback_days
        for _day, record in recent:
            if not isinstance(record, dict):
                invalid_record = True
                break
            for field in _OI_REQUIRED_FIELDS:
                value = record.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not isfinite(float(value)) or float(value) <= 0.0:
                    invalid_record = True
                    break
            if invalid_record:
                break
        through = days[-1] if days else 0
        stale = not through or _gap_days(through, now_ms) > STALE_LIMITS["oi_metrics"]
        if not report.passed or invalid_record or stale:
            failed.append(ticker)
    return sorted(failed)


# 各源"落后几天算 stale"的阈值(OI 归档天然滞后 1-2 天,给 5)
STALE_LIMITS = {"prices": 3, "funding": 3, "oi_metrics": 5,
                "perp_mark_prices": 3}


def _gap_summary(gaps, limit) -> dict:
    """诚实三维口径:freshest(整源是否还在更新)/worst/超阈值计数。survivorship-free 库里
    退市币的水位永久停更是**正确的**——单个 worst 不该报警,报警条件在调用方(整源停更或系统性落后)。"""
    if not gaps:
        return {"freshest_days": None, "worst_days": None, "n_stale": None, "n_total": 0}
    return {"freshest_days": min(gaps), "worst_days": max(gaps),
            "n_stale": sum(1 for g in gaps if g > limit), "n_total": len(gaps)}


def staleness_days(now_ms: int, *, db_path: str = _DB, fund_path: str = _FUND,
                   oi_path: str = _OI, mark_path: str = _MARK) -> dict:
    out = {}
    wm = price_watermarks(db_path)
    out["prices"] = _gap_summary([_gap_days(v, now_ms) for v in wm.values()], STALE_LIMITS["prices"])
    if os.path.exists(fund_path):
        fund = json.load(open(fund_path, encoding="utf-8"))
        wms = [max(int(k) for k in d) for d in fund.values() if d]
        out["funding"] = _gap_summary([_gap_days(v, now_ms) for v in wms], STALE_LIMITS["funding"])
    else:
        out["funding"] = _gap_summary([], STALE_LIMITS["funding"])
    oi_wm = _oi_watermarks(oi_path)
    out["oi_metrics"] = _gap_summary(
        [_gap_days(value, now_ms) for value in oi_wm.values()],
        STALE_LIMITS["oi_metrics"],
    )
    try:
        mark = load_mark_prices(mark_path)
        mark_wms = [max(days) for days in mark.values() if days]
    except (OSError, ValueError, json.JSONDecodeError):
        mark_wms = []
    out["perp_mark_prices"] = _gap_summary(
        [_gap_days(value, now_ms) for value in mark_wms],
        STALE_LIMITS["perp_mark_prices"],
    )
    return out


def _execute_source(source: str, tickers: list[str], *, now_ms: int) -> dict:
    if source == "prices":
        return refresh_prices(tickers, now_ms=now_ms, archive_root=_RAW_ARCHIVE)
    if source == "funding":
        return refresh_funding(tickers, now_ms=now_ms, archive_root=_RAW_ARCHIVE)
    if source == "perp_mark_prices":
        return refresh_mark_prices(
            tickers,
            now_ms=now_ms,
            archive_root=_RAW_ARCHIVE,
            observed_at_ms=now_ms,
        )
    if source == "oi_metrics":
        return refresh_oi(
            tickers,
            now_ms=now_ms,
            archive_root=_RAW_ARCHIVE,
        )
    raise ValueError(f"unknown refresh source: {source}")


def _source_worker(source: str, tickers: list[str], now_ms: int, result_queue) -> None:
    started = time.monotonic()
    try:
        report = _execute_source(source, tickers, now_ms=now_ms)
        result_queue.put({
            "source": source,
            "ok": True,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "report": report,
        })
    except BaseException as exc:
        result_queue.put({
            "source": source,
            "ok": False,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "error_type": type(exc).__name__,
            "message": str(exc),
        })


def _runtime_failure_report(source: str, tickers, *, outcome: str,
                            duration_ms: int, message: str,
                            budget_scope: str | None = None,
                            error_type: str | None = None) -> dict:
    blocked = sorted({str(ticker) for ticker in tickers})
    kind = "BUDGET_EXCEEDED" if outcome == "BUDGET_EXCEEDED" else "SOURCE_CRASHED"
    detail = {
        "ticker": "*",
        "kind": kind,
        "error_type": error_type or (
            "RefreshBudgetExceeded" if outcome == "BUDGET_EXCEEDED" else "RefreshSourceCrash"
        ),
        "message": message,
        "blocked_tickers": blocked,
    }
    report = {
        "source": source,
        "outcome": outcome,
        "duration_ms": max(0, int(duration_ms)),
        "failed": [message],
        "failure_details": [detail],
        "failed_tickers": blocked,
        "quality_failed": [],
        "watermark_after": None,
    }
    if budget_scope is not None:
        detail["budget_scope"] = budget_scope
        report["budget_scope"] = budget_scope
    return report


def _budget_report(source: str, tickers, *, scope: str,
                   duration_ms: int, timeout_s: float) -> dict:
    message = (
        f"{source} refresh exceeded {scope.lower()} wall-clock budget "
        f"({timeout_s:.3f}s)"
    )
    report = _runtime_failure_report(
        source,
        tickers,
        outcome="BUDGET_EXCEEDED",
        duration_ms=duration_ms,
        message=message,
        budget_scope=scope,
    )
    report["timeout_seconds"] = float(timeout_s)
    return report


def _crash_report(source: str, tickers, *, duration_ms: int,
                  error_type: str, message: str) -> dict:
    return _runtime_failure_report(
        source,
        tickers,
        outcome="CRASHED",
        duration_ms=duration_ms,
        message=f"{source} refresh child crashed: {error_type}: {message}",
        error_type=error_type,
    )


def _normalize_child_result(source: str, tickers, message: dict,
                            *, duration_ms: int) -> dict:
    if not isinstance(message, dict) or message.get("source") != source:
        return _crash_report(
            source,
            tickers,
            duration_ms=duration_ms,
            error_type="InvalidChildResult",
            message="child returned a malformed or mismatched source result",
        )
    if not message.get("ok"):
        return _crash_report(
            source,
            tickers,
            duration_ms=duration_ms,
            error_type=str(message.get("error_type") or "RefreshSourceCrash"),
            message=str(message.get("message") or "child exited without a report"),
        )
    raw_report = message.get("report")
    if not isinstance(raw_report, dict) or raw_report.get("source") != source:
        return _crash_report(
            source,
            tickers,
            duration_ms=duration_ms,
            error_type="InvalidChildReport",
            message="child report is not a source-matched object",
        )
    report = dict(raw_report)
    details = list(report.get("failure_details", ()))
    if details:
        warning_details = [
            detail for detail in details
            if isinstance(detail, dict) and detail.get("kind") == "NOT_FOUND"
        ]
        failure_details = [detail for detail in details if detail not in warning_details]
        report["warning_details"] = warning_details
        report["warnings"] = [
            f"{detail.get('ticker')}: {detail.get('kind')}: {detail.get('message')}"
            for detail in warning_details
        ]
        report["failure_details"] = failure_details
        report["failed"] = [
            f"{detail.get('ticker')}: {detail.get('kind')}: {detail.get('message')}"
            for detail in failure_details
        ]
    else:
        report["failed"] = list(report.get("failed", ()))
        report["failure_details"] = []
        report["warning_details"] = []
        report["warnings"] = []
    report["failed_tickers"] = sorted({str(ticker) for ticker in report.get("failed_tickers", ())})
    report["quality_failed"] = sorted({str(ticker) for ticker in report.get("quality_failed", ())})
    report["duration_ms"] = max(0, int(message.get("duration_ms", duration_ms)))
    report.pop("outcome", None)
    report["outcome"] = source_outcome(report)
    return report


def _stop_process(process) -> None:
    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=1.0)
    if not process.is_alive():
        process.join(timeout=0)


def _run_sources_with_deadlines(
    source_tickers: dict[str, list[str]],
    *,
    now_ms: int,
    source_timeouts: dict[str, float],
    global_timeout_s: float,
    worker_target=None,
    process_context=None,
    monotonic=time.monotonic,
) -> list[dict]:
    """Run every source in its own spawn process under absolute source/global deadlines."""
    if global_timeout_s <= 0:
        raise ValueError("global_timeout_s must be positive")
    for source in source_tickers:
        if source not in source_timeouts or source_timeouts[source] <= 0:
            raise ValueError(f"missing positive timeout for refresh source {source}")

    context = process_context or multiprocessing.get_context("spawn")
    target = worker_target or _source_worker
    result_queue = context.Queue()
    processes = {}
    started_at = {}
    messages: dict[str, dict] = {}
    exited_at: dict[str, float] = {}
    reports = {}
    global_started = monotonic()
    global_deadline = global_started + float(global_timeout_s)

    for source, tickers in source_tickers.items():
        process = context.Process(
            target=target,
            args=(source, list(tickers), int(now_ms), result_queue),
            name=f"quantos-refresh-{source}",
        )
        try:
            process.start()
        except BaseException as exc:
            reports[source] = _crash_report(
                source,
                tickers,
                duration_ms=int((monotonic() - global_started) * 1000),
                error_type=type(exc).__name__,
                message=str(exc),
            )
            continue
        processes[source] = process
        started_at[source] = monotonic()

    pending = set(processes)
    try:
        while pending:
            now = monotonic()
            for source in list(pending):
                source_deadline = started_at[source] + float(source_timeouts[source])
                scope = None
                deadline = source_deadline
                timeout_s = float(source_timeouts[source])
                if global_deadline <= source_deadline:
                    deadline = global_deadline
                    timeout_s = float(global_timeout_s)
                    scope = "GLOBAL"
                if now >= deadline:
                    _stop_process(processes[source])
                    reports[source] = _budget_report(
                        source,
                        source_tickers[source],
                        scope=scope or "SOURCE",
                        duration_ms=int((now - started_at[source]) * 1000),
                        timeout_s=timeout_s,
                    )
                    pending.remove(source)

            if not pending:
                break

            now = monotonic()
            for source in list(pending):
                process = processes[source]
                if source in messages and not process.is_alive():
                    process.join(timeout=0)
                    reports[source] = _normalize_child_result(
                        source,
                        source_tickers[source],
                        messages[source],
                        duration_ms=int((now - started_at[source]) * 1000),
                    )
                    pending.remove(source)
                    continue
                if process.exitcode is not None and source not in messages:
                    exited_at.setdefault(source, now)
                    if now - exited_at[source] >= 0.5:
                        reports[source] = _crash_report(
                            source,
                            source_tickers[source],
                            duration_ms=int((now - started_at[source]) * 1000),
                            error_type=f"ChildExit{process.exitcode}",
                            message="child exited without publishing a result",
                        )
                        process.join(timeout=0)
                        pending.remove(source)

            if not pending:
                break

            now = monotonic()
            nearest_deadline = min(
                [global_deadline]
                + [started_at[source] + float(source_timeouts[source]) for source in pending]
            )
            wait_seconds = max(0.01, min(0.1, nearest_deadline - now))
            try:
                message = result_queue.get(timeout=wait_seconds)
            except Empty:
                continue
            if isinstance(message, dict):
                message_source = message.get("source")
                if message_source in pending and message_source not in messages:
                    messages[message_source] = message
    finally:
        for source, process in processes.items():
            if process.is_alive():
                _stop_process(process)
        result_queue.close()
        result_queue.cancel_join_thread()

    return [reports[source] for source in source_tickers]


def build_refresh_alerts(status: dict, slo: dict) -> list[str]:
    alerts = []
    for report in status.get("reports", ()):
        source = report.get("source", "unknown")
        outcome = source_outcome(report)
        if outcome == "BUDGET_EXCEEDED":
            alerts.append(
                f"refresh {source} exceeded {report.get('budget_scope', 'SOURCE')} wall-clock budget"
            )
        elif outcome == "CRASHED":
            alerts.append(f"refresh {source} child process crashed")
    for violation in slo.get("violations", ()):
        code = violation.get("code")
        source = violation.get("source")
        if code == "CONSECUTIVE_FAILURES":
            alerts.append(
                f"refresh {source} failed {violation.get('observed')} consecutive runs "
                f"(threshold {violation.get('threshold')})"
            )
        elif code == "SUCCESS_RATE_BELOW_SLO":
            alerts.append(
                f"refresh {source} success rate {violation.get('observed'):.3f} below "
                f"SLO {violation.get('threshold'):.3f}"
            )
        elif code == "SOURCE_STALE":
            alerts.append(
                f"refresh {source} latest watermark age {violation.get('observed')}d exceeds "
                f"{violation.get('threshold')}d"
            )
        elif code == "HISTORY_CHAIN_INVALID":
            alerts.append("refresh history hash chain is invalid")
        elif code == "GLOBAL_DURATION_EXCEEDED":
            alerts.append(
                f"refresh global duration {violation.get('observed')}ms exceeds "
                f"{violation.get('threshold')}ms"
            )
    return alerts


def run_refresh(*, now_ms=None, source_timeouts=None,
                global_timeout_s: float = GLOBAL_TIMEOUT_SECONDS,
                history_path: str = _HISTORY, status_path: str = _STATUS,
                slo_status_path: str = _SLO_STATUS,
                event_bus_path: str = DOMAIN_EVENT_DB,
                event_projection_path: str = DOMAIN_EVENT_PROJECTION_DB,
                snapshot_path: str = _SNAP,
                source_runner=None, staleness_reader=None,
                archive_summary=None, alert_emitter=None) -> dict:
    """Run all four sources under hard budgets, persist history/SLO, and publish status."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    tickers = list(json.load(open(snapshot_path, encoding="utf-8"))["instruments"])
    resolved_source_runner = source_runner or _run_sources_with_deadlines
    resolved_staleness_reader = staleness_reader or staleness_days
    resolved_archive_summary = archive_summary or summarize_archive
    resolved_alert_emitter = alert_emitter or emit
    source_tickers = {
        "prices": tickers,
        "funding": tickers,
        "perp_mark_prices": list(oi_fetch.DEFAULT_TICKERS),
        "oi_metrics": list(oi_fetch.DEFAULT_TICKERS),
    }
    resolved_timeouts = dict(SOURCE_TIMEOUT_SECONDS)
    if source_timeouts:
        resolved_timeouts.update(source_timeouts)

    started = time.monotonic()
    status: dict = {
        "schema_version": 2,
        "run_id": f"refresh-{int(now_ms)}",
        "ran_at": int(now_ms),
        "reports": [],
        "staleness_before": resolved_staleness_reader(int(now_ms)),
        "budgets": {
            "global_timeout_seconds": float(global_timeout_s),
            "source_timeout_seconds": {
                source: float(resolved_timeouts[source]) for source in _SOURCE_ORDER
            },
        },
    }
    status["reports"] = resolved_source_runner(
        source_tickers,
        now_ms=int(now_ms),
        source_timeouts=resolved_timeouts,
        global_timeout_s=float(global_timeout_s),
    )
    status["raw_lineage"] = resolved_archive_summary(_RAW_ARCHIVE)
    status["staleness_after"] = resolved_staleness_reader(int(now_ms))
    status["duration_ms"] = int((time.monotonic() - started) * 1000)
    status["failed_total"] = sum(
        len(report.get("failed", ())) for report in status["reports"]
    )
    status["failed_ticker_total"] = sum(
        len(set(report.get("failed_tickers", ())) | set(report.get("quality_failed", ())))
        for report in status["reports"]
    )
    status["outcome"] = (
        "PASS" if all(source_outcome(report) == "PASS" for report in status["reports"])
        else "FAILED"
    )

    recorded_at_ms = int(time.time() * 1000)
    history = RefreshHistoryStore(history_path)
    try:
        entry = history.append(
            status,
            recorded_at_ms=recorded_at_ms,
            run_id=status["run_id"],
        )
        slo = history.evaluate_slo(
            expected_sources=_SOURCE_ORDER,
            stale_limits=STALE_LIMITS,
            evaluated_at_ms=recorded_at_ms,
            window_runs=SLO_WINDOW_RUNS,
            minimum_runs=SLO_MINIMUM_RUNS,
            minimum_success_rate=SLO_MINIMUM_SUCCESS_RATE,
            minimum_instrument_coverage=SLO_MINIMUM_INSTRUMENT_COVERAGE,
            max_consecutive_failures=SLO_MAX_CONSECUTIVE_FAILURES,
            max_global_duration_ms=int(float(global_timeout_s) * 1000),
        )
        history_count = history.count()
        event_bus = open_domain_event_bus(event_bus_path)
        try:
            event_dispatch = dispatch_refresh_event_outbox(
                history,
                event_bus,
                at_ms=recorded_at_ms,
            )
            event_projection = project_pending_domain_events(
                event_bus,
                now_ms=recorded_at_ms,
                projection_path=event_projection_path,
                worker_id="scheduled-data-refresh-projection",
            )
        finally:
            event_bus.close()
    finally:
        history.close()

    status["history"] = {
        "path": history_path,
        "entry_seq": entry.seq,
        "entry_hash": entry.entry_hash,
        "status_hash": entry.status_hash,
        "chain_valid": slo["chain_valid"],
        "run_count": history_count,
    }
    status["slo"] = slo
    status["event_delivery"] = {
        "bus_path": event_bus_path,
        "dispatch": event_dispatch,
        "projection": event_projection,
        "projection_path": event_projection_path,
    }
    alerts = build_refresh_alerts(status, slo)
    status["alert_delivery"] = resolved_alert_emitter(
        alerts,
        context="data_refresh",
        now_ms=recorded_at_ms,
    )
    atomic_write_json(status_path, status, indent=2)
    atomic_write_json(
        slo_status_path,
        {
            "schema_version": 1,
            "generated_at_ms": recorded_at_ms,
            "provenance": "REAL_REFRESH_HISTORY",
            "history_entry_hash": entry.entry_hash,
            "history_status_hash": entry.status_hash,
            "slo": slo,
        },
        indent=2,
    )
    return status


def main() -> None:
    s = run_refresh()
    for r in s["reports"]:
        extra = f" · 新增行 {r.get('ingested_rows')}" if "ingested_rows" in r else \
                f" · 新增日 {r.get('new_days')}" if "new_days" in r else ""
        print(f"[{r['source']}] 刷新 {r.get('refreshed', '-')} · 跳过 {r.get('skipped', '-')}"
              f"{extra} · 失败 {len(r.get('failed', []))}", flush=True)
    print(f"staleness(天): {s['staleness_before']} -> {s['staleness_after']} · 总失败 {s['failed_total']}"
          f" -> {_STATUS}", flush=True)


if __name__ == "__main__":
    main()
