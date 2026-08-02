"""全 alpha 真实执行口径 paper 汇总(2026-07-25)。

读取 canonical 连续 paper 报告(默认 ``data/cache/binance_usdt_continuous_paper.json``,由
``tools/binance_usdt_continuous_paper.py`` 刷新:730 天 USDT 窗口、PIT mark 价、4bps taker、
当前买卖价差校准、Almgren-Chriss 冲击、资金费现金流、整手/最小名义过滤、对账恒等式),
把**所有**在册策略(3 个 frozen originals + equal_weight_combo + 7 个预注册 crowd_contra 变体
CANDIDATE)放到同一张标准指标表,落 ``evidence/all_alpha_realistic_paper.json``。

判定 bar 与 originals 当年被评判的完全同一套(诚实写明):
1. 机械完整性:对账残差 ≤1e-6 ∧ 完整日 ≥35 ∧ ≥1 次调仓(引擎 acceptance 口径);
2. 衰退跳闸线 max_drawdown < 0.30(``services/alpha/degradation_monitor.py::DegradationThresholds``
   的 UNCALIBRATED 工程默认——top_contra 连续 paper 回撤 0.308 越此线被维护机器 PAUSED);
3. 净成本后边为正(net_pnl>0 ∧ sharpe>0,描述性,非正式门禁)。
报告级 deployment gate / 全九引擎 risk gate 对所有策略同为 DENIED/PENDING——本表不晋级任何策略,
只是又一条证据维度。诊断非门禁。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from typing import Any

from services.research.paper_trading.store import PaperTrackStore
from tools.cache_io import atomic_write_json

DEFAULT_REPORT = os.path.join("data", "cache", "binance_usdt_continuous_paper.json")
DEFAULT_DRY_RUN_DB = os.path.join("data", "cache", "dry_run.db")
DEFAULT_PAPER_DB = os.path.join("data", "cache", "paper_track.db")
DEFAULT_VARIANTS_EVIDENCE = os.path.join("evidence", "crowd_contra_variants.json")
DEFAULT_OUTPUT = os.path.join("evidence", "all_alpha_realistic_paper.json")

# The exact tripwire the platform applied to the originals: top_contra's
# 730-day continuous-paper max_drawdown 0.308 crossed this and was PAUSED by
# the maintenance machine.  UNCALIBRATED engineering default.
DRAWDOWN_TRIPWIRE = 0.30
RECONCILIATION_TOLERANCE = 1e-6
EXPECTED_REGISTRY_PROVENANCE = "FORWARD_TRACK_REGISTRY_SHARED_ALPHA_SPEC"


def _load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _variant_dsr(path: str) -> dict[str, float | None]:
    """In-sample Deflated-Sharpe per variant from the pre-registered family run (fail-soft)."""
    if not os.path.isfile(path):
        return {}
    try:
        evidence = _load_json(path)
        candidates = evidence.get("trial_registration", {}).get("candidates", {})
        return {
            str(name): (None if not isinstance(row, dict) else row.get("deflated_sharpe"))
            for name, row in candidates.items()
        }
    except (OSError, ValueError):
        return {}


def _judge(sim: dict[str, Any], *, complete_data_pass: bool) -> dict[str, Any]:
    """Apply the same bar the originals were judged by; every failed bar is named."""
    failed: list[str] = []
    if not complete_data_pass:
        failed.append("complete_data")
    residual = sim.get("reconciliation_residual")
    if residual is None or float(residual) > RECONCILIATION_TOLERANCE:
        failed.append("reconciliation")
    if int(sim.get("n_rebalances") or 0) < 1:
        failed.append("rebalances")
    stats = sim.get("stats") or {}
    max_dd = stats.get("max_drawdown")
    if max_dd is None or float(max_dd) >= DRAWDOWN_TRIPWIRE:
        failed.append("drawdown_tripwire")
    sharpe = stats.get("sharpe")
    if not (float(sim.get("net_pnl") or 0.0) > 0.0 and sharpe is not None and float(sharpe) > 0.0):
        failed.append("positive_net_edge")
    return {
        "verdict": "PASS" if not failed else "FAIL",
        "failed_bars": failed,
        "bar": {
            "reconciliation_residual_max": RECONCILIATION_TOLERANCE,
            "drawdown_tripwire_max": DRAWDOWN_TRIPWIRE,
            "positive_net_edge": "net_pnl > 0 AND sharpe > 0 (descriptive, not a formal gate)",
        },
    }


def _strategy_row(
    strategy_id: str,
    sim: dict[str, Any],
    registry: dict[str, Any],
    dsr: dict[str, float | None],
    *,
    complete_data_pass: bool,
) -> dict[str, Any]:
    stats = sim.get("stats") or {}
    spec = registry.get(strategy_id) or {}
    rebalances = sim.get("rebalances") or []
    return {
        "strategy_id": strategy_id,
        "cadence_days": spec.get("cadence_days", 7),
        "quantile": spec.get("quantile", 0.30),
        "expr": spec.get("expr"),
        "in_sample_dsr": dsr.get(strategy_id),
        "total_return": stats.get("total_return"),
        "sharpe": stats.get("sharpe"),
        "max_drawdown": stats.get("max_drawdown"),
        "calmar": stats.get("calmar"),
        "net_pnl": sim.get("net_pnl"),
        "gross_pnl": sim.get("gross_pnl"),
        "funding_pnl": sim.get("funding_pnl"),
        "total_costs": sim.get("total_costs"),
        "fee_cost": sim.get("fee_cost"),
        "spread_cost": sim.get("spread_cost"),
        "impact_cost": sim.get("impact_cost"),
        "turnover": sim.get("turnover"),
        "n_trades": sim.get("n_trades"),
        "n_rebalances": sim.get("n_rebalances"),
        "n_omitted_orders": len(sim.get("omitted_orders") or []),
        "n_no_fill_rebalances": sum(1 for row in rebalances if row.get("status") == "NO_FILL"),
        "participation_over_cap_count": sim.get("participation_over_cap_count"),
        "reconciliation_residual": sim.get("reconciliation_residual"),
        "judgement": _judge(sim, complete_data_pass=complete_data_pass),
    }


def _btc_spot_trend_reference(dry_run_db: str, paper_db: str) -> dict[str, Any]:
    """btc_spot_trend runs on a separate MEXC-spot paper path (different data
    surface) and is deliberately NOT forced into the Binance engine; reference
    its existing evidence instead of recomputing."""
    ref: dict[str, Any] = {
        "strategy_id": "btc_spot_trend",
        "why_not_in_this_engine": (
            "MEXC spot data surface (zero-fee BTC spot, MEXC has no spot testnet); "
            "different venue/data than the Binance USDT-M perp engine — mixing them would "
            "break the same-standard comparison"
        ),
        "dry_run_store": None,
        "paper_track": None,
    }
    if os.path.isfile(dry_run_db):
        try:
            conn = sqlite3.connect(f"file:{dry_run_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT strategy_id, timestamp, pnl FROM dry_run "
                    "WHERE strategy_id=? ORDER BY timestamp DESC LIMIT 1",
                    ("btc_spot_trend",),
                ).fetchone()
            finally:
                conn.close()
            if row is not None:
                pnl = json.loads(row["pnl"])
                ref["dry_run_store"] = {
                    "db": dry_run_db,
                    "strategy_id": row["strategy_id"],
                    "timestamp": row["timestamp"],
                    "total_return": pnl.get("total_return"),
                    "sharpe": pnl.get("sharpe"),
                    "sharpe_vt": pnl.get("sharpe_vt"),
                    "max_drawdown": pnl.get("max_drawdown"),
                    "max_drawdown_vt": pnl.get("max_drawdown_vt"),
                    "reconciliation_residual": pnl.get("residual"),
                    "provenance": "DRY_RUN_DB_CANONICAL_MEXC_PIT_RUN",
                }
        except (OSError, sqlite3.DatabaseError, ValueError):
            ref["dry_run_store"] = {"status": "ABSENT_UNREADABLE", "db": dry_run_db}
    else:
        ref["dry_run_store"] = {"status": "ABSENT_NO_DB", "db": dry_run_db}
    if os.path.isfile(paper_db):
        try:
            store = PaperTrackStore(paper_db, create=False)
            try:
                track = store.track("btc_spot_trend")
            finally:
                close = getattr(store, "close", None)
                if callable(close):
                    close()
            ref["paper_track"] = track if track is not None else {"status": "ABSENT_NO_TRACK"}
        except Exception as exc:  # read-only evidence reference; fail soft and honest
            ref["paper_track"] = {"status": "ABSENT_UNREADABLE", "error_type": type(exc).__name__}
    else:
        ref["paper_track"] = {"status": "ABSENT_NO_DB"}
    return ref


def build_summary(
    *,
    report_path: str = DEFAULT_REPORT,
    dry_run_db: str = DEFAULT_DRY_RUN_DB,
    paper_db: str = DEFAULT_PAPER_DB,
    variants_evidence_path: str = DEFAULT_VARIANTS_EVIDENCE,
    now_ms: int | None = None,
) -> dict[str, Any]:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    report = _load_json(report_path)
    registry_block = report.get("strategy_registry") or {}
    if registry_block.get("provenance") != EXPECTED_REGISTRY_PROVENANCE:
        raise ValueError(
            f"{report_path}: strategy_registry provenance is "
            f"{registry_block.get('provenance')!r}, expected {EXPECTED_REGISTRY_PROVENANCE!r} — "
            "refresh the continuous paper with the shared-registry engine first"
        )
    if str(report.get("quote_asset")) != "USDT":
        raise ValueError(f"{report_path}: quote_asset must be USDT for the canonical window")
    registry = registry_block.get("strategies") or {}
    dsr = _variant_dsr(variants_evidence_path)
    complete_data_pass = bool((report.get("acceptance") or {}).get("complete_data_pass"))
    rows = [
        _strategy_row(strategy_id, sim, registry, dsr, complete_data_pass=complete_data_pass)
        for strategy_id, sim in sorted((report.get("simulations") or {}).items())
    ]
    # Honest duplicate-portfolio detection: on the 14-name majors universe,
    # k = max(1, round(N * q)) can map two quantiles to the same leg count,
    # producing bit-identical weights (q=0.25 and q=0.30 both give k=4 on 14
    # names).  Flag the duplicate instead of letting two rows pose as
    # independent evidence.
    for row in rows:
        row["identical_portfolio_as"] = None
    for index, row in enumerate(rows):
        for other in rows[:index]:
            if (
                other["expr"] == row["expr"]
                and other["cadence_days"] == row["cadence_days"]
                and other["n_trades"] == row["n_trades"]
                and other["turnover"] == row["turnover"]
                and other["net_pnl"] == row["net_pnl"]
            ):
                row["identical_portfolio_as"] = other["strategy_id"]
                break
    table = sorted(
        rows,
        key=lambda row: (row["sharpe"] is not None, row["sharpe"] if row["sharpe"] is not None else 0.0),
        reverse=True,
    )
    for rank, row in enumerate(table, start=1):
        row["rank_by_sharpe"] = rank
    window = report.get("data_window") or {}
    execution = report.get("execution_assumptions") or {}
    return {
        "schema_version": 1,
        "report_type": "ALL_ALPHA_REALISTIC_PAPER_SUMMARY",
        "generated_at": int(now_ms),
        "readiness": "L2_DRY_RUN_ONLY",
        "paper_only": True,
        "live_allowed": False,
        "order_submission_allowed": False,
        "credentials_accessed": False,
        "source_report": {
            "path": report_path,
            "generated_at": report.get("generated_at"),
            "schema_version": report.get("schema_version"),
            "quote_asset": report.get("quote_asset"),
            "venue": report.get("venue"),
            "paper_verdict": report.get("paper_verdict"),
            "classification": report.get("classification"),
            "window": window,
            "config": {
                "requested_days": window.get("requested_days"),
                "complete_days": window.get("complete_days"),
                "aum_usdt": 1_000_000.0,
                "aum_source": "tools/binance_usdt_continuous_paper.py --aum default (source report does not persist aum)",
                "taker_fee_bps": execution.get("taker_fee_bps"),
                "participation_cap": execution.get("participation_cap"),
                "impact_model": execution.get("impact_model"),
                "impact_parameters": execution.get("impact_parameters"),
                "spread": execution.get("spread"),
                "quantity_rule": execution.get("quantity_rule"),
            },
            "registry_provenance": registry_block.get("provenance"),
            "registry_source": registry_block.get("source"),
        },
        "judgement_bar": {
            "description": (
                "The same bar the originals were judged by: (1) mechanical integrity "
                "(reconciliation residual <= 1e-6, >= 35 complete days, >= 1 rebalance); "
                "(2) degradation tripwire max_drawdown < 0.30 — the exact UNCALIBRATED "
                "DegradationThresholds default that PAUSED top_contra when its continuous-paper "
                "drawdown reached 0.308; (3) positive net-of-cost edge (net_pnl > 0 and "
                "sharpe > 0, descriptive).  Report-level deployment gate / full nine-engine "
                "risk gate remain DENIED/PENDING for every strategy — this table promotes nothing."
            ),
            "drawdown_tripwire": DRAWDOWN_TRIPWIRE,
            "reconciliation_tolerance": RECONCILIATION_TOLERANCE,
        },
        "strategies": {row["strategy_id"]: row for row in rows},
        "comparison_table": table,
        "btc_spot_trend_separate_path": _btc_spot_trend_reference(dry_run_db, paper_db),
        "excluded_families": [
            {
                "family": "mean_reversion_cluster",
                "status": "EXCLUDED_BY_DISCIPLINE",
                "reason": "8/8 pre-registered variants REJECTED on the same falsification+VT-Sharpe+drawdown bars (2026-07-25)",
                "evidence": "evidence/mean_reversion_cluster.json",
            },
            {
                "family": "volatility_class",
                "status": "NO_CANDIDATES",
                "reason": "B-line honest negative: 24 checks (6 factors x 2 underlyings x 2 horizons), none passed the multiple-comparison + non-overlap double bar",
                "evidence": "evidence/vol_research.json",
            },
        ],
        "honesty": {
            "evidence_class": "REALISTIC_EXECUTION_PAPER_ON_KNOWN_HISTORY",
            "not_forward_not_live": (
                "This is realistic-execution paper over known history (integer lots + gate + "
                "costs + funding + impact, reconciliation identity holds).  It is NOT forward "
                "evidence and NOT live trading; whether any strategy earns a promotion is a "
                "separate human-gated decision."
            ),
            "variant_dsr_caveat": (
                "The 7 crowd_contra variant CANDIDATEs carry a deeply skeptical DSR-adjusted "
                "in-sample edge (evidence/crowd_contra_variants.json, 17-trial family: 3d-cadence "
                "DSR ~0.001, 14d ~0.31-0.38, q20 0.63, taker_combo 0.05 vs baseline 0.84).  This "
                "realistic-paper run is one more evidence dimension, NOT a promotion; the forward "
                "paper clock remains the honest arbiter."
            ),
            "cadence_approximation": (
                "3d/14d variants decide on the deterministic daily-close grid with >= cadence-day "
                "spacing; the forward track uses a wall-clock cadence-12h jitter tolerance.  Same "
                "holding-period economics, different clock — labelled, not hidden."
            ),
            "combo_scope": (
                "equal_weight_combo blends ONLY the three frozen originals (byte-identical to the "
                "pre-variant wiring); variant sleeves are not blended in."
            ),
            "taker_combo_universe": (
                "taker_ls is an auxiliary metrics field with occasional blank days; "
                "crowd_taker_combo drops those names from that day's cross-section "
                "(source_counts.taker_ls_missing_days in the source report)."
            ),
            "quantile_granularity": (
                "On the 14-name Binance majors universe k = max(1, round(N * q)) maps "
                "q=0.25 and q=0.30 to the same k=4, so crowd_contra_3d and "
                "crowd_contra_q25_3d hold bit-identical portfolios (flagged "
                "identical_portfolio_as).  Universe-size granularity, not a bug; the two "
                "registrations remain distinct trial hypotheses on the forward clock."
            ),
            "top_contra_window_sensitivity": (
                "top_contra's refreshed-window max_drawdown (0.207) is BELOW the 0.30 "
                "tripwire that the 2026-07-24 window (0.308) crossed.  The maintenance "
                "authority PAUSED on the earlier evidence and that historical snapshot "
                "stands; the next maintenance sweep reads this refreshed file, so the "
                "drawdown leg of its quarantine evidence now reads below-threshold.  "
                "This summary changes no authority state."
            ),
            "risk_gate_annualization": (
                "The risk-gate series is sampled at each strategy's own rebalance grid; the "
                "ppy=52 label is exact for 7d cadences and approximate for 3d/14d.  The gate is "
                "a PENDING-full-inputs diagnostic in every case."
            ),
            "historical_microstructure_limit": (
                "Historical best bid/ask and historical filter snapshots are unavailable from "
                "public endpoints; current values are labelled calibration, not historical truth."
            ),
            "btc_spot_trend": (
                "Referenced from its separate MEXC-spot paper path, not recomputed in this engine."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--dry-run-db", default=DEFAULT_DRY_RUN_DB)
    parser.add_argument("--paper-db", default=DEFAULT_PAPER_DB)
    parser.add_argument("--variants-evidence", default=DEFAULT_VARIANTS_EVIDENCE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = build_summary(
        report_path=args.report,
        dry_run_db=args.dry_run_db,
        paper_db=args.paper_db,
        variants_evidence_path=args.variants_evidence,
    )
    atomic_write_json(args.output, summary, indent=2)
    table = summary["comparison_table"]
    print(f"=== all-alpha realistic paper · {len(table)} strategies · "
          f"window {summary['source_report']['window'].get('start')} -> "
          f"{summary['source_report']['window'].get('end')} ===", flush=True)
    for row in table:
        sharpe = row["sharpe"]
        mdd = row["max_drawdown"]
        ret = row["total_return"]
        print(
            f"  #{row['rank_by_sharpe']:2d} {row['strategy_id']:22s} "
            f"sharpe={sharpe if sharpe is None else round(sharpe, 3)} "
            f"ret={None if ret is None else round(ret * 100, 1)}% "
            f"mdd={None if mdd is None else round(mdd * 100, 1)}% "
            f"costs={None if row['total_costs'] is None else round(row['total_costs'])} "
            f"verdict={row['judgement']['verdict']}"
            f"{'' if not row['judgement']['failed_bars'] else ' failed=' + ','.join(row['judgement']['failed_bars'])}",
            flush=True,
        )
    print(f"-> {args.output}", flush=True)


if __name__ == "__main__":
    main()  # pragma: no cover
