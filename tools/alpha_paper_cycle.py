"""Build a read-only inventory for the three crypto Alpha paper candidates.

The inventory deliberately does not create paper periods or alter any ledger.  It
joins the existing research, dry-run, capacity, forward-ledger, data-admission,
and event-health artifacts so the operator can see which candidate is actually
ready for a forward clock and which one is still only a research lead.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Mapping

from services.execution.backtest_authority import (
    is_authoritative_backtest_record,
    quarantine_legacy_backtest,
)
from services.risk.research_risk.dry_run_guard import full_research_risk_approved
from tools.cache_io import atomic_write_json
from tools.forward_status import alpha_forward_status, forward_evidence_is_current


DEFAULT_OUTPUT = os.path.join("evidence", "alpha_paper_cycle.json")
REQUIRED_FORWARD_PERIODS = 8

ALPHAS: dict[str, dict[str, str]] = {
    "carry_oi_majors": {
        "display_name": "carry",
        "expression": "composite negative funding carry",
        "research_artifact": "data/cache/crypto_carry_strategy.json",
        "dry_run_id": "carry",
        "capacity_id": "carry",
        "forward_id": "carry_oi_majors",
    },
    "crowd_contra": {
        "display_name": "crowd_contra",
        "expression": "cs_rank(neg(global_ls))",
        "research_artifact": "data/cache/crypto_oi_verify.json",
        "dry_run_id": "crowd_contra",
        "capacity_id": "crowd_contra",
        "forward_id": "crowd_contra",
    },
    "top_contra": {
        "display_name": "top_contra",
        "expression": "cs_zscore(neg(top_ls))",
        "research_artifact": "data/cache/crypto_oi_research.json",
        "dry_run_id": "top_contra",
        "capacity_id": "top_contra",
        "forward_id": "top_contra",
    },
}


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _mapping_num(mapping: Any, key: int | str) -> float | None:
    if not isinstance(mapping, Mapping):
        return None
    value = mapping.get(key) if key in mapping else mapping.get(str(key))
    return _num(value)


def _record_research_risk(record: object) -> Mapping[str, Any] | None:
    if not isinstance(record, Mapping):
        return None
    detail = record.get("detail")
    if isinstance(detail, Mapping) and isinstance(detail.get("research_risk"), Mapping):
        return detail["research_risk"]
    risk = record.get("research_risk")
    return risk if isinstance(risk, Mapping) else None


def _factor_row(research: Mapping[str, Any], alpha_id: str) -> dict[str, Any]:
    factors = research.get("factors")
    if not isinstance(factors, list):
        return {}
    for row in factors:
        if isinstance(row, dict) and row.get("name") == alpha_id:
            return dict(row)
    return {}


def _research_snapshot(alpha_id: str, artifacts: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    if alpha_id == "carry_oi_majors":
        strategy = artifacts.get("carry_strategy", {})
        walk = artifacts.get("carry_walkforward", {})
        net = _num(strategy.get("sharpe_net_5bps"))
        robust = walk.get("verdict") == "DESIGN_ROBUST"
        return {
            "verdict": "PASS_WITH_WARNINGS" if net is not None and robust else "BLOCKED",
            "evidence": {
                "net_sharpe_5bps": net,
                "walkforward_verdict": walk.get("verdict"),
                "walkforward_blocks": walk.get("n_blocks"),
            },
            "warnings": [
                "carry is an economic funding-yield edge; price-only alpha is weaker",
                "forward evidence is still required",
            ],
        }
    if alpha_id == "crowd_contra":
        ref = artifacts.get("oi_verify", {})
        checks = ref.get("refutation_checks")
        checks = checks if isinstance(checks, dict) else {}
        passed = sum(bool(value) for value in checks.values())
        net = _mapping_num(ref.get("net_sharpe_by_fee_bps"), 10)
        lag = _mapping_num(ref.get("price_sharpe_by_lag"), 1)
        oos = ref.get("embargoed_oos") or {}
        oos_mean = _num(oos.get("mean_oos")) if isinstance(oos, dict) else None
        ok = str(ref.get("verdict", "")).startswith("SURVIVES") and passed >= 4
        return {
            "verdict": "PASS_WITH_WARNINGS" if ok else "BLOCKED",
            "evidence": {
                "refutation_pass_count": passed,
                "net_sharpe_10bps": net,
                "one_day_lag_sharpe": lag,
                "embargoed_oos_mean_sharpe": oos_mean,
                "source_verdict": ref.get("verdict"),
            },
            "warnings": ["survivorship and recent-decay risk remain open"],
        }

    research = artifacts.get("oi_research", {})
    row = _factor_row(research, "top_contra")
    top_verify = artifacts.get("top_verify", {})
    refutation_pass_count = top_verify.get("n_pass")
    if not isinstance(refutation_pass_count, int):
        refutation_pass_count = None
    refutation_verdict = top_verify.get("verdict")
    top_research_verdict = (
        "PASS_WITH_WARNINGS"
        if refutation_pass_count is not None and refutation_pass_count >= 4
        else "UNCONFIRMED"
        if row
        else "BLOCKED"
    )
    dry_books = artifacts.get("dry_run", {}).get("books")
    official_dry_run = (
        dry_books.get("top_contra")
        if isinstance(dry_books, dict)
        else None
    )
    warnings = []
    if refutation_pass_count is None:
        warnings.append("top_contra has no dedicated adversarial refutation battery")
    elif refutation_pass_count < 4:
        warnings.append(
            f"top_contra dedicated refutation is incomplete: {refutation_verdict}"
        )
    if isinstance(official_dry_run, dict):
        warnings.append("capacity remains an uncalibrated diagnostic, not a promotion gate")
    else:
        warnings.append("top_contra has not yet passed an execution dry-run and capacity diagnostic")
    return {
        "verdict": top_research_verdict,
        "evidence": {
            "price_sharpe": _num(row.get("price_sharpe")),
            "economic_sharpe": _num(row.get("econ_sharpe")),
            "ic": _num(row.get("ic")),
            "incremental_ir_vs_carry": _num(row.get("incremental_ir_vs_carry")),
            "source_verdict": research.get("verdict"),
            "dedicated_refutation_pass_count": refutation_pass_count,
            "dedicated_refutation_verdict": refutation_verdict,
        },
        "warnings": warnings,
    }


def build_report(*, artifact_paths: Mapping[str, str] | None = None) -> dict[str, Any]:
    paths = {
        "carry_strategy": "data/cache/crypto_carry_strategy.json",
        "carry_walkforward": "data/cache/crypto_carry_walkforward.json",
        "oi_research": "data/cache/crypto_oi_research.json",
        "oi_verify": "data/cache/crypto_oi_verify.json",
        "dry_run": "evidence/dry_run_books.json",
        "carry_dry_run_authoritative": "data/cache/carry_oi_majors_dry_run.json",
        "crowd_dry_run_authoritative": "data/cache/crowd_contra_dry_run.json",
        "top_dry_run_diagnostic": "data/cache/top_contra_dry_run_diagnostic.json",
        "top_dry_run_authoritative": "data/cache/top_contra_dry_run.json",
        "top_verify": "data/cache/crypto_oi_top_verify.json",
        "capacity": "evidence/capacity.json",
        "forward": "data/cache/forward_track_status.json",
        "admission": "evidence/data_quality_admission.json",
        "event_health": "data/cache/domain_event_health.json",
        "event_consistency": "data/cache/domain_event_consistency.json",
        "projection_recovery": "data/cache/domain_event_projection_consumer_recovery.json",
        "gonogo": "data/cache/gonogo_report.json",
    }
    if artifact_paths:
        paths.update(artifact_paths)
    artifacts = {name: _load(path) for name, path in paths.items()}
    dry_books = artifacts["dry_run"].get("books")
    dry_books = dry_books if isinstance(dry_books, dict) else {}
    cap_numbers = artifacts["capacity"].get("numbers")
    cap_numbers = cap_numbers if isinstance(cap_numbers, dict) else {}
    forward_alphas = artifacts["forward"].get("alphas")
    forward_alphas = forward_alphas if isinstance(forward_alphas, dict) else {}

    admission = artifacts["admission"]
    event_health = artifacts["event_health"]
    event_consistency = artifacts["event_consistency"]
    projection_recovery = artifacts["projection_recovery"]
    gonogo = artifacts["gonogo"]
    comparison = event_consistency.get("comparison")
    comparison = comparison if isinstance(comparison, dict) else {}
    projection_exact = (
        comparison.get("prefix_mismatch_count") == 0
        and comparison.get("authority_event_records")
        == comparison.get("projection_event_records")
        and comparison.get("consumer_offset")
        == comparison.get("authority_max_seq")
    )
    projection_recovered = (
        projection_recovery.get("verdict") == "PASS"
        and (projection_recovery.get("preflight") or {}).get("verdict") == "PASS"
        and projection_exact
    )
    common_blockers: list[str] = []
    if admission.get("verdict") != "PASS":
        common_blockers.append("DATA_ADMISSION_BLOCK")
    if event_health.get("verdict") != "PASS":
        common_blockers.append("EVENT_BUS_NOT_HEALTHY")
    if event_consistency and event_consistency.get("verdict") != "PASS":
        common_blockers.append(
            "EVENT_AUTHORITY_HISTORICAL_DELIVERY_FAILURE"
            if projection_recovered
            else "EVENT_AUTHORITY_PROJECTION_INCONSISTENT"
        )
    gonogo_forward = gonogo.get("forward_evidence")
    if not isinstance(gonogo_forward, Mapping) or gonogo_forward.get("present") is not True:
        common_blockers.append("GONOGO_FORWARD_EVIDENCE_ABSENT")
    elif not forward_evidence_is_current(artifacts["forward"], gonogo):
        common_blockers.append("GONOGO_FORWARD_EVIDENCE_STALE")

    rows: dict[str, Any] = {}
    for alpha_id, definition in ALPHAS.items():
        research = _research_snapshot(alpha_id, artifacts)
        blockers = list(common_blockers)
        dry_id = definition["dry_run_id"]
        dry = dry_books.get(dry_id) if isinstance(dry_books.get(dry_id), dict) else None
        diagnostic = artifacts["top_dry_run_diagnostic"] if alpha_id == "top_contra" else {}
        canonical_paths = {
            "carry_oi_majors": "carry_dry_run_authoritative",
            "crowd_contra": "crowd_dry_run_authoritative",
            "top_contra": "top_dry_run_authoritative",
        }
        canonical_candidate = artifacts.get(canonical_paths[alpha_id], {})
        canonical_candidate = (
            canonical_candidate if isinstance(canonical_candidate, Mapping) else {}
        )
        authority_valid = bool(
            canonical_candidate
            and is_authoritative_backtest_record(canonical_candidate)
        )
        candidate_risk = _record_research_risk(canonical_candidate)
        risk_valid = bool(
            isinstance(candidate_risk, Mapping)
            and full_research_risk_approved(candidate_risk)
        )
        if not canonical_candidate or not authority_valid:
            blockers.append("DRY_RUN_AUTHORITY_INVALID")
        elif not risk_valid:
            blockers.append("DRY_RUN_RESEARCH_RISK_NOT_FULL")
        canonical = canonical_candidate if authority_valid and risk_valid else {}
        canonical_pnl = canonical.get("pnl")
        canonical_pnl = canonical_pnl if isinstance(canonical_pnl, dict) else {}
        if canonical:
            # The canonical top-contra driver is the same L2 dry-run seam as the
            # other candidates.  Surface it separately from the legacy
            # diagnostic artifact so the report cannot mislabel a persisted run.
            dry = {
                "sharpe": _num(canonical_pnl.get("sharpe")),
                "max_drawdown": _num(canonical_pnl.get("max_drawdown")),
                "total_return": _num(canonical_pnl.get("total_return")),
                "authoritative": True,
                "evidence_class": canonical.get("evidence_class"),
                "run_id": canonical.get("run_id"),
            }
        if dry is None:
            blockers.append("DRY_RUN_EVIDENCE_MISSING")
        cap_key = f"{definition['capacity_id']}.participation_cap_aum"
        cap = cap_numbers.get(cap_key)
        if not isinstance(cap, dict) or _num(cap.get("value")) is None:
            blockers.append("CAPACITY_DIAGNOSTIC_MISSING")
        forward_id = definition["forward_id"]
        forward = forward_alphas.get(forward_id)
        forward = forward if isinstance(forward, dict) else {}
        forward_normalized = alpha_forward_status(artifacts["forward"], forward_id)
        n_forward = forward_normalized["n_forward_periods"]
        if not isinstance(n_forward, int) or n_forward < REQUIRED_FORWARD_PERIODS:
            blockers.append("FORWARD_EVIDENCE_INSUFFICIENT")
        rows[alpha_id] = {
            "definition": definition,
            "research": research,
            "dry_run": dry,
            "canonical_dry_run": {
                "authoritative": canonical.get("authoritative") if canonical else None,
                "evidence_class": canonical.get("evidence_class") if canonical else None,
                "n_periods": canonical.get("n_periods") if canonical else None,
                "raw_sharpe": _num(canonical_pnl.get("sharpe")) if canonical else None,
                "vol_target_sharpe": _num(canonical_pnl.get("sharpe_vt")) if canonical else None,
                "reconciliation_residual": _num(canonical_pnl.get("residual")) if canonical else None,
                "run_id": canonical.get("run_id") if canonical else None,
                "strategy_id": canonical.get("strategy_id") if canonical else None,
                "timestamp": canonical.get("timestamp") if canonical else None,
                "audit_root": canonical.get("audit_root") if canonical else None,
                "detail": dict(canonical.get("detail"))
                if canonical and isinstance(canonical.get("detail"), Mapping)
                else {},
                "research_risk": dict(candidate_risk)
                if canonical and isinstance(candidate_risk, Mapping)
                else {},
                "research_risk_decision": (
                    candidate_risk.get("decision")
                    if canonical
                    else None
                ),
                "crowding_gate_blocks": (
                    (canonical.get("gate") or {}).get("n_block")
                    if canonical
                    else None
                ),
            },
            "diagnostic_dry_run": {
                "authoritative": diagnostic.get("authoritative") if diagnostic else None,
                "evidence_class": diagnostic.get("evidence_class"),
                "n_periods": diagnostic.get("n_periods"),
                "raw_sharpe": (diagnostic.get("pnl") or {}).get("sharpe")
                if isinstance(diagnostic.get("pnl"), dict) else None,
                "vol_target_sharpe": (diagnostic.get("pnl") or {}).get("sharpe_vt")
                if isinstance(diagnostic.get("pnl"), dict) else None,
                "reconciliation_residual": (diagnostic.get("pnl") or {}).get("residual")
                if isinstance(diagnostic.get("pnl"), dict) else None,
                "persistence": diagnostic.get("persistence"),
            },
            "capacity": {
                "participation_cap_aum": _num(cap.get("value")) if isinstance(cap, dict) else None,
                "provenance": artifacts["capacity"].get("provenance"),
            },
            "forward": {
                "n_forward_periods": n_forward,
                "paper_sharpe": forward_normalized["paper_sharpe"],
                "pending_decisions": forward_normalized["pending_decisions"],
                "attested_pending_decisions": forward_normalized[
                    "attested_pending_decisions"
                ],
                "legacy_unattested_decisions": forward_normalized[
                    "legacy_unattested_decisions"
                ],
                "evidence_state": forward_normalized["evidence_state"],
                "last_decision_ts": forward_normalized["last_decision_ts"],
            },
            "paper_verdict": "BLOCKED" if blockers or research["verdict"] in {"BLOCKED", "UNCONFIRMED"} else "PENDING_REVIEW",
            "blockers": list(dict.fromkeys(blockers)),
        }

    next_actions: list[str] = []
    if admission.get("verdict") != "PASS":
        next_actions.append(
            "wait for a clean recent-data admission and matching raw-lineage backfill"
        )
    if event_health.get("verdict") != "PASS" or event_consistency.get("verdict") != "PASS":
        next_actions.append(
            (
                "resolve retained event delivery dead letters without deleting the append-only history"
                if projection_recovered
                else "review/promote the event projection candidate outside this read-only report"
            )
        )
    if not all(
        isinstance((row.get("forward") or {}).get("n_forward_periods"), int)
        and (row.get("forward") or {}).get("n_forward_periods") >= REQUIRED_FORWARD_PERIODS
        for row in rows.values()
    ):
        next_actions.append(
            "run the official forward driver on each daily poll until every candidate has at least 8 realized periods"
        )
    if not isinstance(dry_books.get("top_contra"), dict):
        next_actions.append(
            "keep top_contra research-only until its official dry-run, adversarial refutation, and forward evidence exist"
        )
    elif rows.get("top_contra", {}).get("research", {}).get("verdict") != "PASS_WITH_WARNINGS":
        next_actions.append(
            "keep top_contra research-only until its adversarial refutation and forward evidence exist"
        )
    else:
        next_actions.append(
            "keep top_contra research-only until its forward evidence reaches the required period count"
        )
    return {
        "schema_version": 1,
        "readiness": "L2_DRY_RUN_ONLY",
        "cycle": {
            "mode": "FORWARD_PAPER_ONLY",
            "state": "BLOCKED_BY_DATA_AND_EVENT_GATES" if common_blockers else "PENDING_FORWARD_EVIDENCE",
            "required_forward_periods": REQUIRED_FORWARD_PERIODS,
            "official_driver": "tools/forward_track_advance.py",
            "forward_return_semantics": {
                "source": "FORWARD_LIVE_PERP_MARK_GROSS_PLUS_FUNDING",
                "includes_trading_costs": False,
                "net_cost_evidence": "data/cache/dry_run.db",
            },
            "registered_alphas": ["crowd_contra", "carry_oi_majors", "top_contra"],
            "live_allowed": False,
            "order_submission_allowed": False,
            "event_authority": {
                "projection_recovery": (
                    "PROMOTED_AND_RECOVERED" if projection_recovered
                    else "REQUIRES_REVIEW"
                ),
                "projection_exact": projection_exact,
            },
        },
        "alphas": rows,
        "common_blockers": list(dict.fromkeys(common_blockers)),
        "next_actions": next_actions,
    }


def main(argv: list[str] | None = None) -> int:
    quarantine_legacy_backtest("tools.alpha_paper_cycle")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build_report()
    atomic_write_json(args.output, report, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
