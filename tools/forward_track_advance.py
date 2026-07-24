"""真前向 paper-track 周度驱动器(Stage 0,实盘路线)。设计:
docs/engineering/specs/2026-07-04-forward-track-live-design.md §2.3。

每次运行(幂等,错过可 catch-up):
1. 增量刷新四个数据源(tools/live_data_refresh):现货参考、funding、永续 mark price、OI;
2. build_panel(OI 大币宇宙)→ 与干跑驱动器**同一条信号路径**算 CROWD_CONTRA(主边)与
   CARRY(对照,同宇宙变体,诚实命名 carry_oi_majors)→ _legs 权重;
3. 距上次决策 ≥7 天则 record_decision(决策先落库,墙钟 recorded_at 证明先后);
4. realize_due 兑现所有到期决策(gross+资金费口径,与干跑一致,不含交易成本——source 诚实标注);
5. 写 data/cache/forward_track_status.json + 告警(alert_sink)。

墙钟/网络只在此驱动器边界;核心(forward.py)纯确定性。这是证据积累,非晋级门禁——
真钱 Go/No-Go 是 Phase 3A 的人工评审(≥8 周门,用户已拍板)。
调度(Windows,每周一 09:00,错过下次自动补;必须 -m 方式跑——repo 根要在 sys.path):
  schtasks /Create /SC DAILY /TN QUANTOS_ForwardTrack /ST 21:00 ^
    /TR "cmd /c cd /d H:\\code\\QUANTOS && python -m tools.forward_track_advance"
"""
from __future__ import annotations

import json
import os
import sys
import time

from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.paper_trading.forward import realize_due
from services.research.paper_trading.attested_store import AttestedForwardLedger
from services.research.paper_trading.forward_evidence import load_forward_admission_ref
from services.research.paper_trading.store import PaperTrackStore
from services.data.admission_store import DataQualityAdmissionStore
from tools.alert_sink import emit
from tools.crypto_oi_research import CARRY, QUANTILE, _legs, build_panel
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.data_quality_admission import _AUTHORITY, run_data_quality_admission
from tools.live_data_refresh import STALE_LIMITS, run_refresh, staleness_days

# 前向跟踪的 crypto alpha(crowd_contra/carry/top_contra)证据只依赖 Binance 系四源;
# MEXC 现货源(mexc_btc)是 btc_spot_trend 的数据面,不属于本驱动器的证据判据——
# live_data_refresh 第 5 源加入 STALE_LIMITS 后必须显式收窄,否则 MEXC 数据停更会
# 误挡 crowd/carry 的证据钟(2026-07 MEXC 对接审查发现)。
_FORWARD_STALE_LIMITS = {k: v for k, v in STALE_LIMITS.items() if k != "mexc_btc"}
from tools import binance_oi_metrics_fetch as oi_fetch
from tools.binance_perp_mark_fetch import load_mark_prices
from tools.signal_quality import rows_with_acceptable_coverage
from services.data.panel_quality import gate_decision_inputs   # P1-8 决策门(纯跨标的门)

_DB = os.path.join("data", "cache", "paper_track.db")
_FUND = os.path.join("data", "cache", "crypto_funding_rates.json")
_STATUS = os.path.join("data", "cache", "forward_track_status.json")
_LOCK_FILE = os.path.join("data", "cache", "forward_track_advance.lock")
_LOCK_STALE_S = 600  # assume dead if lock held > 10 minutes
_DAY_MS = 86_400_000
_WEEK_MS = 7 * _DAY_MS
_DECISION_DUE_MS = _WEEK_MS - 12 * 3600 * 1000      # 7 天 − 12h 容差(数据网格抖动)
_MAX_DECISION_AGE_MS = 8 * _DAY_MS                  # P1-8:决策数据时间合法上限(OI 逐 symbol 滞后回看
#                                                     ~7 天为合法,超 8 天=信号源实质冻结)。工程占位,待用户确认。
_SYSTEMIC_STALE_FRAC = 0.2                          # >20% ticker 落后 = 系统性问题(≠个别退市)
# Keep the three paper candidates on one signal path.  ``top_contra`` is a
# deliberately weaker research candidate than the first two, but it is still
# evaluated with the same append-only decision ledger and fail-closed quality
# gates.  Registering it here does not promote it or authorize trading; it only
# lets a clean future data-admission run start a separately attributable paper
# clock instead of silently dropping the third candidate.
TOP_CONTRA = ("cs_zscore", ("neg", ("field", "top_ls")))
_ALPHAS = {
    "crowd_contra": CROWD_CONTRA,
    "carry_oi_majors": CARRY,
    "top_contra": TOP_CONTRA,
}
_REQUIRED_RAW_LINEAGE = frozenset({"funding", "oi_metrics", "perp_mark_prices"})


def decision_block_reason(*, weights, prices, decided_ts, now, stale, quality_failed,
                          quality_evidence_current=False):
    """P1-8 决策门(纯 helper,镜像 live 驱动器 batch_skip_reason 范式)。返回 None=可落决策;返回原因串=
    数据不够干净,调用方暂停本轮决策(证据钟暂停=留 pending,数据恢复下周期重试),绝不把脏数据当真决策记进
    不可改写台账。复用 gate_decision_inputs 四项检查;staleness 判据与上面 :102 的告警**同判据**,只把'只告警'
    升成'挡决策'、不更激进。"""
    rep = gate_decision_inputs(
        weights=weights, prices=prices, decided_ts=decided_ts, now=now,
        max_decision_age_ms=_MAX_DECISION_AGE_MS, staleness=stale, stale_limits=_FORWARD_STALE_LIMITS,
        ingestion_quality_failed=tuple(quality_failed),
        quality_evidence_current=quality_evidence_current)
    if rep.passed:
        return None
    failed = [c.name for c in rep.checks if not c.passed]
    return f"decision blocked (evidence clock paused,留 pending): {failed}"


def raw_lineage_block_reason(raw_lineage) -> str | None:
    if not isinstance(raw_lineage, dict):
        return "raw lineage summary missing"
    if raw_lineage.get("status") != "PASS":
        return f"raw lineage integrity is not PASS: {raw_lineage.get('status')}"
    datasets = set(raw_lineage.get("datasets", {}))
    missing = sorted(_REQUIRED_RAW_LINEAGE - datasets)
    if missing:
        return f"raw lineage datasets missing: {missing}"
    return None


def authoritative_admission_status(*, now_ms: int, runner=None) -> tuple:
    """Persist the admission decision and return a redacted load-bearing summary."""
    resolved_runner = runner or run_data_quality_admission
    try:
        evidence, decision, inserted = resolved_runner(now_ms=now_ms)
        if (
            not isinstance(evidence, dict)
            or decision.verdict != evidence.get("verdict")
            or decision.quality_verdict != evidence.get("quality_verdict")
            or decision.lineage_verdict != evidence.get("lineage_verdict")
        ):
            raise ValueError("authority decision does not match evidence")
        current = decision.verdict == "PASS"
        summary = {
            "status": "PASS",
            "admission_id": decision.admission_id,
            "verdict": decision.verdict,
            "quality_verdict": decision.quality_verdict,
            "lineage_verdict": decision.lineage_verdict,
            "evidence_sha256": decision.evidence_sha256,
            "decision_hash": decision.decision_hash,
            "inserted": bool(inserted),
        }
        alerts = [] if current else [
            "data-quality admission BLOCK "
            f"(reasons={len(decision.block_reasons)})"
        ]
        return current, summary, alerts
    except Exception as exc:
        return False, {
            "status": "BREACH",
            "error_type": type(exc).__name__,
        }, [
        "data-quality admission authority BREACH: "
            f"{type(exc).__name__}"
        ]


def _load_attested_admission_ref(summary: dict, *, instruments) -> object | None:
    """Resolve the exact PASS decision from the append-only authority.

    The summary written to the status artifact is intentionally redacted.  The
    worker must still verify the full authority chain before it can write a new
    forward decision; missing or conflicting authority is a hard stop.
    """
    if not isinstance(summary, dict) or summary.get("verdict") != "PASS":
        return None
    try:
        authority = DataQualityAdmissionStore(_AUTHORITY, create=False)
        try:
            return load_forward_admission_ref(
                authority,
                admission_id=str(summary["admission_id"]),
                evidence_sha256=str(summary["evidence_sha256"]),
                decision_hash=str(summary["decision_hash"]),
                instruments=tuple(str(item) for item in instruments),
            )
        finally:
            authority.close()
    except Exception:
        return None


def _weekly_rows(event_times) -> list:
    rows, last = [], None
    for r in range(len(event_times)):
        if last is None or event_times[r] - last >= _WEEK_MS:
            rows.append(r)
            last = event_times[r]
    return rows


def _weights_and_signals(sig_row, insts):
    legs = _legs(sig_row, QUANTILE)
    weights = {insts[i]: w for i, w in legs.items()}
    signals = {insts[i]: sig_row[i] for i in range(len(insts)) if sig_row[i] is not None}
    return weights, signals


def _mark_price_map(mark_by_ticker, timestamps) -> dict:
    return {
        int(timestamp): {
            ticker: float(days[int(timestamp)])
            for ticker, days in mark_by_ticker.items()
            if int(timestamp) in days
        }
        for timestamp in timestamps
    }


def _candidate_advances_ledger(decided_ts: int, existing_timestamps) -> bool:
    """Return whether a candidate is strictly newer than every ledger row.

    Legacy rows are intentionally retained for audit.  A new attested paper
    clock may not reuse or backdate behind them, because either action would
    make the append-only timeline ambiguous.
    """
    latest = max((int(ts) for ts in existing_timestamps), default=-1)
    return int(decided_ts) > latest


def _reference_sharpe(sig, rows, ev, insts, prices, funding_sum, *, before_ts) -> tuple:
    """冻结的历史参照:对 before_ts 之前的周度网格,用与兑现完全相同的公式重算周度序列的 sharpe。
    与 paper_sharpe 同单位(周度、非年化)——apples-to-apples。返回 (sharpe, n_weeks)。"""
    rets = []
    for k in range(len(rows) - 1):
        r0, r1 = rows[k], rows[k + 1]
        t0, t1 = int(ev[r0]), int(ev[r1])
        if t1 >= before_ts:
            break
        legs = _legs(sig[r0], QUANTILE)
        if not legs:
            continue
        weights = {insts[i]: w for i, w in legs.items()}
        p0, p1 = prices.get(t0, {}), prices.get(t1, {})
        names = [n for n in weights if (p0.get(n) or 0) > 0 and (p1.get(n) or 0) > 0]
        if len(names) < 0.8 * len(weights):
            continue
        r = sum(weights[n] * (p1[n] / p0[n] - 1.0) for n in names)
        r += sum(-weights[n] * funding_sum(n, t0, t1) for n in names)
        rets.append(r)
    return (sharpe(rets) if len(rets) >= 10 else None), len(rets)


def _attested_snapshot(ledger, alpha_id: str) -> dict:
    """Build paper metrics from attested realizations only.

    The shared paper store can contain legacy periods.  They remain useful for
    historical audit but must not inflate the current forward clock.
    """
    realizations = ledger.attested_realizations(alpha_id)
    returns = [float(row["realized_return"]) for row in realizations]
    ic_values = [
        float(row["realized_ic"])
        for row in realizations
        if row.get("realized_ic") is not None
    ]
    equity = 1.0
    for value in returns:
        equity *= 1.0 + value
    return {
        "n_forward_periods": len(realizations),
        "paper_sharpe": sharpe(returns) if len(returns) >= 2 else None,
        "paper_ic": (sum(ic_values) / len(ic_values)) if ic_values else None,
        "cum_return": equity - 1.0 if returns else None,
        "attested_pending_decisions": None,
        "legacy_unattested_decisions": None,
    }


def _try_acquire_lock() -> bool:
    """Atomically acquire the single-run lock; return False if another run is active."""
    try:
        if time.time() - os.path.getmtime(_LOCK_FILE) > _LOCK_STALE_S:
            os.unlink(_LOCK_FILE)  # stale lock from a crashed previous run
    except (FileNotFoundError, OSError):
        pass
    try:
        fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except (FileExistsError, OSError):
        return False


def _release_lock() -> None:
    try:
        os.unlink(_LOCK_FILE)
    except (FileNotFoundError, OSError):
        pass


def main() -> None:
    if not _try_acquire_lock():
        sys.exit("forward_track_advance: another run is active, exiting to avoid consumer-group race")
    try:
        _run()
    finally:
        _release_lock()


def _run() -> None:
    now_ms = int(time.time() * 1000)
    alerts: list = []
    do_refresh = "--no-refresh" not in sys.argv

    blocked_instruments: set = set()
    quality_evidence_current = False
    admission_ref = None
    admission_status = {"status": "SKIPPED"}
    if do_refresh:
        try:
            refresh = run_refresh(now_ms=now_ms)
            reports = refresh.get("reports", [])
            expected_sources = {"prices", "funding", "oi_metrics", "perp_mark_prices"}
            observed_sources = {rep.get("source") for rep in reports}
            quality_evidence_current = expected_sources.issubset(observed_sources)
            if not quality_evidence_current:
                missing = sorted(expected_sources - observed_sources)
                alerts.append(f"refresh quality evidence incomplete; missing sources: {missing}")
            for rep in reports:
                failed_tickers = set(rep.get("failed_tickers", ()))
                failed_tickers.update(rep.get("quality_failed", ()))
                blocked_instruments.update(failed_tickers)
                for f in rep.get("failed", []):
                    alerts.append(f"refresh {rep['source']}: {f}")
                if rep.get("failed") and not failed_tickers:
                    quality_evidence_current = False
                    alerts.append(
                        f"refresh {rep.get('source')}: unstructured failure; all evidence blocked"
                    )
            lineage_block = raw_lineage_block_reason(refresh.get("raw_lineage"))
            if lineage_block is not None:
                quality_evidence_current = False
                alerts.append(lineage_block)
            admission_current, admission_status, admission_alerts = (
                authoritative_admission_status(now_ms=now_ms)
            )
            quality_evidence_current = (
                quality_evidence_current and admission_current
            )
            if quality_evidence_current:
                admission_ref = _load_attested_admission_ref(
                    admission_status,
                    instruments=oi_fetch.FORWARD_MAJOR_TICKERS,
                )
                if admission_ref is None:
                    quality_evidence_current = False
                    alerts.append("data-quality admission reference unavailable; evidence clock paused")
            alerts.extend(admission_alerts)
        except Exception as exc:
            emit([f"refresh CRASHED: {exc}"], context="forward_track", now_ms=now_ms)
            raise
    else:
        alerts.append(
            "refresh skipped: current ingestion quality evidence unavailable; "
            "decision and realization blocked"
        )
    # The archives retain delisted symbols for PIT research; forward decisions
    # must grade only the current Binance OI universe to avoid stale-history
    # warnings being mistaken for a live data halt.
    stale = staleness_days(now_ms, active_tickers=oi_fetch.FORWARD_MAJOR_TICKERS)
    for src, limit in _FORWARD_STALE_LIMITS.items():
        s = stale.get(src) or {}
        fresh, n_stale, n_total = s.get("freshest_days"), s.get("n_stale"), s.get("n_total", 0)
        if fresh is None or fresh > limit:
            alerts.append(f"staleness {src}: source stopped updating (freshest {fresh}d, limit {limit})")
        elif n_stale and n_total and n_stale / n_total > _SYSTEMIC_STALE_FRAC:
            alerts.append(f"staleness {src}: systemic lag {n_stale}/{n_total} tickers > {limit}d")

    panel, _ = build_panel(only=set(oi_fetch.FORWARD_MAJOR_TICKERS))
    insts = panel.instruments
    ev = panel.event_times
    close = panel.matrices["close"]
    rows = _weekly_rows(ev)
    # Keep the realization/reference grid weekly, but let a newly admitted
    # paper clock bootstrap from the latest available daily event.  This is
    # useful after an immutable legacy row: the latest event is still in the
    # admitted data window and is not a future timestamp, while waiting for
    # the next fixed weekly anchor would leave a clean data set unusable for
    # several days.
    latest_event_row = len(ev) - 1
    spot_prices = {int(ev[r]): {insts[i]: close[r][i] for i in range(len(insts))
                                if close[r][i] and close[r][i] > 0} for r in rows}
    prices = _mark_price_map(
        load_mark_prices(), [int(timestamp) for timestamp in ev]
    )
    fund = {tk: {int(k): v for k, v in d.items()}
            for tk, d in json.load(open(_FUND, encoding="utf-8")).items()}

    def funding_sum(name, t0, t1):
        return sum(v for day, v in fund.get(name, {}).items() if t0 < day <= t1)

    ledger = AttestedForwardLedger(_DB, create=True)
    store = PaperTrackStore(_DB, create=True)
    status = {"ran_at": now_ms, "staleness_days": stale, "alphas": {}}

    for alpha_id, expr in _ALPHAS.items():
        sig = ax.evaluate(expr, panel)
        r_last = latest_event_row
        t_last = int(ev[r_last])
        # A clean admission starts a new attested evidence clock.  Historical
        # legacy rows remain visible for audit but must not delay the first
        # decision bound to the current major-universe admission.
        if admission_ref is not None:
            decs = ledger.attested_decisions(alpha_id)
        else:
            decs = ledger.decisions(alpha_id)
        due = (not decs) or (t_last - decs[-1]["decided_ts"] >= _DECISION_DUE_MS)
        recorded_new = False
        recorded_ts = None
        blocked = False              # P1-8:本轮是否被决策门挡住(区分"无腿跳过"vs"数据脏暂停")
        if due and admission_ref is None:
            # Never fall back to the legacy unbound ledger when the current
            # admission authority is unavailable. Historical rows remain
            # visible as legacy evidence; no new decision is written.
            alerts.append(f"{alpha_id}: current admission reference unavailable; decision paused")
            blocked = True
            due = False
        if due:
            # 信号行回看(2026-07-05,与 live 驱动器同一修复):周一调度时本周首行往往就是
            # 面板最新行,而最新行的 OI 常常还没更新(K线刷新快于 OI 指标)——盲取会整周跳过
            # 决策、证据钟白丢一周。在本周窗口内回看最近一行有信号的行;decided_ts 如实用该行
            # 数据时间(决策带数据时间,口径不变)。回看行的价格补进 prices,兑现定价不受影响。
            # 2026-07-06 根因调查修复①:OI 归档逐 symbol 滞后发布,发布窗口内最新行可能是
            # "毛边行"(部分覆盖)——只查 `if weights:` 会把毛边行记成残缺决策,永久污染
            # append-only 证据台账。覆盖率门与 live 驱动器同一口径(tools/signal_quality.py)。
            # Search only inside the current weekly window.  When the latest
            # event is beyond the fixed weekly anchor, that anchor is the
            # lower exclusive bound; otherwise use the preceding anchor.
            if rows and rows[-1] < r_last:
                lo = rows[-1]
            else:
                lo = rows[-2] if len(rows) > 1 else max(-1, r_last - 7)
            candidates = list(range(r_last, lo, -1))
            acceptable = set(rows_with_acceptable_coverage(sig, candidates))
            occupied_timestamps = {
                int(row["decided_ts"]) for row in ledger.decisions(alpha_id)
            }
            # Never backdate a newly admitted decision behind an existing
            # (possibly legacy/unattested) ledger row.  The append-only
            # identity guard protects exact collisions, but a backdated row
            # would still make the paper clock chronologically ambiguous.
            latest_existing_ts = max(occupied_timestamps, default=-1)
            occupied_candidate = False
            for r_try in candidates:
                if r_try not in acceptable:
                    continue
                weights, signals = _weights_and_signals(sig[r_try], insts)
                if weights:
                    t_dec = int(ev[r_try])
                    if not _candidate_advances_ledger(t_dec, occupied_timestamps):
                        # Existing rows are immutable and cannot be silently
                        # rebound to the new admission.  Do not backdate a
                        # clean decision behind them; wait for the next
                        # forward weekly row instead.
                        occupied_candidate = True
                        continue
                    dec_prices = dict(prices.get(t_dec, {}))
                    # P1-8 决策门:数据不够干净(源停更/决策过旧/加权名字缺价/接入质量不过)则**暂停证据钟**
                    # (本轮不落决策=留 pending,数据恢复下周期重试),绝不把脏数据当真决策记进不可改写台账。
                    block = decision_block_reason(weights=weights, prices=dec_prices, decided_ts=t_dec,
                                                  now=now_ms, stale=stale,
                                                  quality_failed=blocked_instruments,
                                                  quality_evidence_current=quality_evidence_current)
                    if block is not None:
                        alerts.append(f"{alpha_id}: {block}")
                        blocked = True
                        break
                    recorded_new = ledger.record_decision(
                        alpha_id,
                        t_dec,
                        recorded_at=now_ms,
                        weights=weights,
                        signals=signals,
                        admission_ref=admission_ref,
                    )
                    prices.setdefault(t_dec, dec_prices)
                    recorded_ts = t_dec
                    if r_try != r_last:
                        alerts.append(f"{alpha_id}: freshest available row had no signal, "
                                      f"decided at ts={t_dec} instead of {t_last}")
                    break
            if recorded_ts is None and not blocked:
                if occupied_candidate:
                    alerts.append(
                        f"{alpha_id}: candidate timestamps do not advance the existing ledger "
                        f"(latest={latest_existing_ts}); decision deferred to the next clean weekly row"
                    )
                else:
                    alerts.append(f"{alpha_id}: no legs within this week's lookback of {t_last} "
                              f"— decision skipped")

        first_dec_ts = (decs[0]["decided_ts"] if decs
                        else recorded_ts if recorded_new else int(ev[rows[-1]]) + 1)
        ref_sharpe, ref_weeks = _reference_sharpe(sig, rows, ev, insts, spot_prices, funding_sum,
                                                  before_ts=first_dec_ts)
        out = realize_due(ledger, store, alpha_id, timestamps=[int(ev[r]) for r in rows],
                          prices=prices, funding_sum=funding_sum, backtest_sharpe=ref_sharpe,
                          blocked_instruments=blocked_instruments,
                          quality_evidence_current=quality_evidence_current,
                          admission_ref=admission_ref,
                          committed_at_ms=now_ms if admission_ref is not None else None)
        alerts.extend(out["alerts"])

        attested = _attested_snapshot(ledger, alpha_id)
        attested_pending = ledger.attested_pending(alpha_id)
        legacy_count = sum(
            1 for row in ledger.legacy_decisions() if row["alpha_id"] == alpha_id
        )
        attested["attested_pending_decisions"] = len(attested_pending)
        attested["legacy_unattested_decisions"] = legacy_count
        if admission_ref is None:
            evidence_state = "DATA_BLOCKED"
        elif attested["n_forward_periods"]:
            evidence_state = "ATTESTED"
        elif attested_pending:
            evidence_state = "ATTESTED_PENDING_REALIZATION"
        else:
            evidence_state = "ADMISSION_READY_PENDING_DECISION"
        status["alphas"][alpha_id] = {
            "n_forward_periods": attested["n_forward_periods"],
            "paper_sharpe": attested["paper_sharpe"],
            "paper_ic": attested["paper_ic"],
            "cum_return": attested["cum_return"],
            "backtest_reference_sharpe": ref_sharpe, "reference_weeks": ref_weeks,
            "pending_decisions": len(attested_pending),
            "attested_pending_decisions": len(attested_pending),
            "legacy_unattested_decisions": legacy_count,
            "evidence_state": evidence_state,
            "last_decision_ts": (ledger.decisions(alpha_id)[-1]["decided_ts"]
                                 if ledger.decisions(alpha_id) else None),
            "recorded_new_decision": recorded_new, "n_realized_this_run": out["n_realized"],
            "n_quality_blocked_this_run": out["n_quality_blocked"],
        }

    status["refresh_evidence_available"] = quality_evidence_current
    # Keep the pre-attestation name as a compatibility mirror. The attested
    # row fields above remain authoritative for the forward gate.
    status["quality_evidence_current"] = quality_evidence_current
    status["data_quality_admission"] = admission_status
    status["forward_price_basis"] = "BINANCE_USDM_PERP_MARK_PRICE"
    status["blocked_instruments"] = sorted(blocked_instruments)
    status["alerts"] = alerts
    json.dump(status, open(_STATUS, "w", encoding="utf-8"), indent=2)
    emit(alerts, context="forward_track", now_ms=now_ms)

    print(f"=== 前向 paper-track · {time.strftime('%Y-%m-%d %H:%M', time.gmtime(now_ms/1000))}Z "
          f"· staleness {stale} ===", flush=True)
    for aid, s in status["alphas"].items():
        print(f"  {aid}: 决策{'+1' if s['recorded_new_decision'] else '·'} "
              f"兑现+{s['n_realized_this_run']} · 前向周数 {s['n_forward_periods']} "
              f"· paper_sharpe {s['paper_sharpe']} (参照 {s['backtest_reference_sharpe']}"
              f"/{s['reference_weeks']}周) · pending {s['pending_decisions']}", flush=True)
    if alerts:
        print(f"  告警 {len(alerts)}: " + " | ".join(alerts[:5]), flush=True)
    print(f"-> {_STATUS}", flush=True)


if __name__ == "__main__":
    main()  # pragma: no cover
