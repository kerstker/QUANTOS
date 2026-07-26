from __future__ import annotations

import json

import pytest

from tools.all_alpha_realistic_paper import (
    DRAWDOWN_TRIPWIRE,
    RECONCILIATION_TOLERANCE,
    build_summary,
)


def _sim(*, sharpe, total_return, mdd, residual=1e-9, net_pnl=1000.0, rebalances=5):
    return {
        "stats": {
            "total_return": total_return,
            "sharpe": sharpe,
            "max_drawdown": mdd,
            "calmar": None,
        },
        "net_pnl": net_pnl,
        "gross_pnl": net_pnl + 100.0,
        "funding_pnl": 10.0,
        "total_costs": 100.0,
        "fee_cost": 60.0,
        "spread_cost": 20.0,
        "impact_cost": 20.0,
        "turnover": 12345.0,
        "n_trades": 42,
        "n_rebalances": rebalances,
        "omitted_orders": [{"day": 1, "instrument": "XRP", "reason": "MIN_QTY"}],
        "rebalances": [{"day": 1, "status": "EXECUTED"}, {"day": 8, "status": "NO_FILL"}],
        "participation_over_cap_count": 0,
        "reconciliation_residual": residual,
    }


def _report(simulations) -> dict:
    return {
        "schema_version": 2,
        "generated_at": 1_784_903_052_400,
        "quote_asset": "USDT",
        "venue": "BINANCE_USDM_PUBLIC_MARKET_DATA",
        "paper_verdict": "PAPER_ONLY_PENDING_FULL_DEPLOYMENT_GATE",
        "classification": "REJECTED",
        "data_window": {
            "requested_days": 730,
            "complete_days": 730,
            "start": "2024-07-24",
            "end": "2026-07-23",
        },
        "execution_assumptions": {
            "taker_fee_bps": 4.0,
            "participation_cap": 0.10,
            "impact_model": "Almgren-Chriss temporary sqrt + permanent linear",
            "impact_parameters": {"temp_coef": 0.1, "perm_coef": 0.1, "temp_exponent": 0.5},
            "spread": "current public bookTicker half-spread applied to historical mark executions",
            "quantity_rule": "current exchangeInfo MARKET_LOT_SIZE/LOT_SIZE floor + minQty + MIN_NOTIONAL",
        },
        "strategy_registry": {
            "provenance": "FORWARD_TRACK_REGISTRY_SHARED_ALPHA_SPEC",
            "source": "tools/forward_track_advance.py::_ALPHAS",
            "strategies": {
                "crowd_contra": {"expr": "(cs_rank (neg (field global_ls)))", "quantile": 0.3, "cadence_days": 7},
                "crowd_contra_3d": {"expr": "(cs_rank (neg (field global_ls)))", "quantile": 0.3, "cadence_days": 3},
                "carry_oi_majors": {"expr": "carry", "quantile": 0.3, "cadence_days": 7},
            },
        },
        "acceptance": {"complete_data_pass": True},
        "simulations": simulations,
    }


def test_build_summary_applies_the_originals_bar(tmp_path) -> None:
    report_path = tmp_path / "paper.json"
    report_path.write_text(
        json.dumps(_report({
            # crowd_contra: clears every bar (like the historical original run)
            "crowd_contra": _sim(sharpe=0.95, total_return=0.35, mdd=0.23),
            # top_contra-like: trips the exact 0.30 drawdown tripwire that PAUSED it
            "carry_oi_majors": _sim(sharpe=0.18, total_return=0.03, mdd=DRAWDOWN_TRIPWIRE + 0.008),
            # broken net edge fails the descriptive bar
            "crowd_contra_3d": _sim(sharpe=-0.4, total_return=-0.1, mdd=0.2, net_pnl=-100.0),
        })),
        encoding="utf-8",
    )
    summary = build_summary(
        report_path=str(report_path),
        dry_run_db=str(tmp_path / "missing-dry-run.db"),
        paper_db=str(tmp_path / "missing-paper.db"),
        variants_evidence_path=str(tmp_path / "missing-variants.json"),
        now_ms=1_785_000_000_000,
    )
    assert summary["report_type"] == "ALL_ALPHA_REALISTIC_PAPER_SUMMARY"
    assert summary["paper_only"] and not summary["live_allowed"]
    strategies = summary["strategies"]
    assert strategies["crowd_contra"]["judgement"]["verdict"] == "PASS"
    assert strategies["carry_oi_majors"]["judgement"]["failed_bars"] == ["drawdown_tripwire"]
    assert strategies["crowd_contra_3d"]["judgement"]["failed_bars"] == ["positive_net_edge"]
    # cadence/quantile surface from the registry block
    assert strategies["crowd_contra_3d"]["cadence_days"] == 3
    # comparison table sorted by sharpe with dense ranks
    table = summary["comparison_table"]
    assert [row["strategy_id"] for row in table] == ["crowd_contra", "carry_oi_majors", "crowd_contra_3d"]
    assert [row["rank_by_sharpe"] for row in table] == [1, 2, 3]
    # fill/drop counts surfaced
    row = strategies["crowd_contra"]
    assert row["n_trades"] == 42
    assert row["n_omitted_orders"] == 1
    assert row["n_no_fill_rebalances"] == 1
    # excluded families recorded, not run
    families = {row["family"]: row["status"] for row in summary["excluded_families"]}
    assert families == {"mean_reversion_cluster": "EXCLUDED_BY_DISCIPLINE", "volatility_class": "NO_CANDIDATES"}
    # btc_spot_trend referenced honestly as a separate path, fail-soft without DBs
    ref = summary["btc_spot_trend_separate_path"]
    assert ref["strategy_id"] == "btc_spot_trend"
    assert ref["dry_run_store"]["status"] == "ABSENT_NO_DB"
    assert ref["paper_track"]["status"] == "ABSENT_NO_DB"
    assert "MEXC" in ref["why_not_in_this_engine"]
    # honesty block names the variant DSR caveat
    assert "DSR" in summary["honesty"]["variant_dsr_caveat"]


def test_build_summary_flags_reconciliation_breach(tmp_path) -> None:
    report_path = tmp_path / "paper.json"
    report_path.write_text(
        json.dumps(_report({
            "crowd_contra": _sim(
                sharpe=1.0, total_return=0.4, mdd=0.1,
                residual=RECONCILIATION_TOLERANCE * 10,
            ),
        })),
        encoding="utf-8",
    )
    summary = build_summary(
        report_path=str(report_path),
        dry_run_db=str(tmp_path / "a.db"),
        paper_db=str(tmp_path / "b.db"),
        variants_evidence_path=str(tmp_path / "c.json"),
    )
    assert summary["strategies"]["crowd_contra"]["judgement"]["failed_bars"] == ["reconciliation"]


def test_build_summary_reads_variant_dsr_from_evidence(tmp_path) -> None:
    report_path = tmp_path / "paper.json"
    report_path.write_text(
        json.dumps(_report({"crowd_contra_3d": _sim(sharpe=1.2, total_return=0.5, mdd=0.1)})),
        encoding="utf-8",
    )
    variants_path = tmp_path / "variants.json"
    variants_path.write_text(
        json.dumps({"trial_registration": {"candidates": {"crowd_contra_3d": {"deflated_sharpe": 0.0009}}}}),
        encoding="utf-8",
    )
    summary = build_summary(
        report_path=str(report_path),
        dry_run_db=str(tmp_path / "a.db"),
        paper_db=str(tmp_path / "b.db"),
        variants_evidence_path=str(variants_path),
    )
    assert summary["strategies"]["crowd_contra_3d"]["in_sample_dsr"] == pytest.approx(0.0009)


def test_build_summary_fails_closed_on_wrong_registry(tmp_path) -> None:
    report = _report({"crowd_contra": _sim(sharpe=1.0, total_return=0.4, mdd=0.1)})
    report["strategy_registry"]["provenance"] = "LEGACY_THREE_ALPHA_ONLY"
    report_path = tmp_path / "paper.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="provenance"):
        build_summary(
            report_path=str(report_path),
            dry_run_db=str(tmp_path / "a.db"),
            paper_db=str(tmp_path / "b.db"),
            variants_evidence_path=str(tmp_path / "c.json"),
        )


def test_build_summary_fails_closed_on_non_usdt_report(tmp_path) -> None:
    report = _report({"crowd_contra": _sim(sharpe=1.0, total_return=0.4, mdd=0.1)})
    report["quote_asset"] = "USDC"
    report_path = tmp_path / "paper.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="USDT"):
        build_summary(
            report_path=str(report_path),
            dry_run_db=str(tmp_path / "a.db"),
            paper_db=str(tmp_path / "b.db"),
            variants_evidence_path=str(tmp_path / "c.json"),
        )
