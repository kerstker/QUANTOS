"""增量数据刷新(Stage 0,实盘路线)—— 把三个一次性研究抓取器包成幂等的生产刷新。设计:
docs/superpowers/specs/2026-07-04-forward-track-live-design.md §2.2。

三个源全部复用现有幂等原语,只补水位之后的缺口:
1. 价格 `crypto_funding.db`(PIT 库,主键去重 + 未来数据守卫):fetch_daily_rows → ingest_provider_rows;
2. 资金费 `crypto_funding_rates.json`:fetch_daily_funding 按日桶合并(已有日不覆盖);
3. OI 指标 `crypto_oi_metrics.json`:binance_oi_metrics_fetch.main 本身断点续传(缺日补齐)。

产出 data/cache/live_refresh_status.json:各源刷新前/后水位、新增数、失败列表、staleness 天数。
逐 ticker fail-soft(单个失败记录、不中断整轮)。网络/墙钟只在此(门外);读写只碰 data/cache。
Read-only 公开行情,无 key、无交易所 SDK、无下单/转账——与既有抓取器同一治理边界。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from services.data.persistent_store import SqlitePITStore
from services.data.market_provider import ingest_provider_rows
from tools.binance_klines_fetch import fetch_daily_rows
from tools.binance_funding_fetch import fetch_daily_funding
from tools import binance_oi_metrics_fetch as oi_fetch

_DAY_MS = 86_400_000
_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND = os.path.join("data", "cache", "crypto_funding_rates.json")
_OI = os.path.join("data", "cache", "crypto_oi_metrics.json")
_STATUS = os.path.join("data", "cache", "live_refresh_status.json")


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
                   pad_days: int = 3, sleep: float = 0.15) -> dict:
    """只对有缺口的 ticker 抓 klines 并 ingest(PIT 主键天然幂等)。宇宙 = 既有快照 instruments,
    刷新不扩宇宙(扩宇宙是研究决策,不是刷新的职责)。"""
    wm = price_watermarks(db_path)
    store = SqlitePITStore(db_path, create=False)
    report = {"source": "prices", "refreshed": 0, "skipped": 0, "ingested_rows": 0, "failed": [],
              "quality_failed": []}      # P1-8:本轮单标的接入质量报告不过的 ticker(供决策门聚合;不 raise)
    for tk in tickers:
        gap = _gap_days(wm.get(tk, 0), now_ms)
        if gap <= 1:                          # 只差今天(未收盘,PIT 本来就丢) → 无缺口
            report["skipped"] += 1
            continue
        try:
            rows = fetch(tk, days=gap + pad_days, now_ms=now_ms)
            rep = ingest_provider_rows(rows, store, dataset_id=f"bnf-{tk}", now=now_ms, time_unit="ms")
            report["refreshed"] += 1
            report["ingested_rows"] += getattr(rep, "bars_ingested", 0) or 0
            # P1-8:透出单标的质量 pass/fail 供跨标的决策门聚合(正规接入 build_report 的报告,不翻 strict 让刷新
            # raise——klines 已 dedup+sort 正常运行几乎不触发,近零假阳性;不 raise 保刷新 fail-soft 不变)
            q = getattr(rep, "quality", None)
            if q is not None and not q.passed:
                report["quality_failed"].append(tk)
        except Exception as exc:              # fail-soft:记录、继续
            report["failed"].append(f"{tk}: {exc}")
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
                    pad_days: int = 3, sleep: float = 0.15) -> dict:
    fund = json.load(open(fund_path, encoding="utf-8")) if os.path.exists(fund_path) else {}
    report = {"source": "funding", "refreshed": 0, "skipped": 0, "new_days": 0, "failed": []}
    for tk in tickers:
        days_known = [int(k) for k in fund.get(tk, {})]
        gap = _gap_days(max(days_known) if days_known else 0, now_ms)
        if gap <= 1:
            report["skipped"] += 1
            continue
        try:
            fresh = fetch(tk, days=gap + pad_days, now_ms=now_ms)
            report["new_days"] += merge_funding_days(fund.setdefault(tk, {}), fresh)
            report["refreshed"] += 1
        except Exception as exc:
            report["failed"].append(f"{tk}: {exc}")
        if sleep:
            time.sleep(sleep)
    json.dump(fund, open(fund_path, "w", encoding="utf-8"))
    wm = [max(int(k) for k in d) for d in fund.values() if d]
    report["watermark_after"] = min(wm) if wm else None
    return report


def refresh_oi(tickers=None) -> dict:
    """OI 指标归档刷新:直接复用 binance_oi_metrics_fetch.main(缺日补齐 + 逐 ticker 断点)。
    只刷 DEFAULT_TICKERS(在市流动大币);已退市 ticker 的历史段不动。"""
    before = _oi_watermark()
    oi_fetch.main(tickers or list(oi_fetch.DEFAULT_TICKERS))
    after = _oi_watermark()
    return {"source": "oi_metrics", "watermark_before": before, "watermark_after": after}


def _oi_watermark(oi_path: str = _OI):
    if not os.path.exists(oi_path):
        return None
    cache = json.load(open(oi_path, encoding="utf-8"))
    throughs = [m.get("through") for m in cache.get("_meta", {}).values() if m and m.get("through")]
    return max(throughs) if throughs else None


# 各源"落后几天算 stale"的阈值(OI 归档天然滞后 1-2 天,给 5)
STALE_LIMITS = {"prices": 3, "funding": 3, "oi_metrics": 5}


def _gap_summary(gaps, limit) -> dict:
    """诚实三维口径:freshest(整源是否还在更新)/worst/超阈值计数。survivorship-free 库里
    退市币的水位永久停更是**正确的**——单个 worst 不该报警,报警条件在调用方(整源停更或系统性落后)。"""
    if not gaps:
        return {"freshest_days": None, "worst_days": None, "n_stale": None, "n_total": 0}
    return {"freshest_days": min(gaps), "worst_days": max(gaps),
            "n_stale": sum(1 for g in gaps if g > limit), "n_total": len(gaps)}


def staleness_days(now_ms: int, *, db_path: str = _DB, fund_path: str = _FUND,
                   oi_path: str = _OI) -> dict:
    out = {}
    wm = price_watermarks(db_path)
    out["prices"] = _gap_summary([_gap_days(v, now_ms) for v in wm.values()], STALE_LIMITS["prices"])
    if os.path.exists(fund_path):
        fund = json.load(open(fund_path, encoding="utf-8"))
        wms = [max(int(k) for k in d) for d in fund.values() if d]
        out["funding"] = _gap_summary([_gap_days(v, now_ms) for v in wms], STALE_LIMITS["funding"])
    else:
        out["funding"] = _gap_summary([], STALE_LIMITS["funding"])
    oi_wm = _oi_watermark(oi_path)
    out["oi_metrics"] = _gap_summary([_gap_days(oi_wm, now_ms)] if oi_wm else [],
                                     STALE_LIMITS["oi_metrics"])
    return out


def run_refresh(*, now_ms=None) -> dict:
    """整轮刷新 + 状态落盘。宇宙 = 既有 funding 快照 instruments(价格/资金费)+ OI DEFAULT_TICKERS。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    tickers = json.load(open(_SNAP, encoding="utf-8"))["instruments"]
    status = {"ran_at": now_ms, "reports": [], "staleness_before": staleness_days(now_ms)}
    status["reports"].append(refresh_prices(tickers, now_ms=now_ms))
    status["reports"].append(refresh_funding(tickers, now_ms=now_ms))
    status["reports"].append(refresh_oi())
    status["staleness_after"] = staleness_days(now_ms)
    status["failed_total"] = sum(len(r.get("failed", [])) for r in status["reports"])
    json.dump(status, open(_STATUS, "w", encoding="utf-8"), indent=2)
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
