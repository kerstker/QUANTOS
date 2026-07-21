"""Read-only Alpha verification and L2 small-capital readiness report.

This module deliberately stops at research / paper evidence.  It never imports a
broker client, reads credentials, submits an order, or returns a live-authorized
decision.  The report combines the existing refutation, dry-run, capacity,
forward-evidence, data-admission, event-health, and safety artifacts so a stale
or partial artifact cannot be mistaken for a small-capital go decision.
"""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from typing import Any

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS
from tools.cache_io import atomic_write_json


REQUIRED_FORWARD_PERIODS = 8
DEFAULT_OUTPUT = os.path.join("evidence", "alpha_readiness_crowd_contra.json")

ALPHA_CATALOG: dict[str, dict[str, str]] = {
    "crowd_contra": {
        "expression": "cs_rank(neg(global_ls))",
        "universe": "crypto_oi_majors",
        "cadence": "weekly",
        "dry_run_id": "crowd_contra",
        "capacity_id": "crowd_contra",
    },
    "carry_oi_majors": {
        "expression": "composite negative funding carry",
        "universe": "crypto_oi_majors",
        "cadence": "weekly",
        "dry_run_id": "carry",
        "capacity_id": "carry",
    },
    "top_contra": {
        "expression": "cs_zscore(neg(top_ls))",
        "universe": "crypto_oi_majors",
        "cadence": "weekly",
        "dry_run_id": "top_contra",
        "capacity_id": "top_contra",
    },
}

DEFAULT_ARTIFACTS = {
    "refutation": os.path.join("data", "cache", "crypto_oi_verify.json"),
    "dry_run": os.path.join("evidence", "dry_run_books.json"),
    "capacity": os.path.join("evidence", "capacity.json"),
    "forward": os.path.join("data", "cache", "forward_track_status.json"),
    "data_admission": os.path.join("evidence", "data_quality_admission.json"),
    "event_health": os.path.join("data", "cache", "domain_event_health.json"),
    "event_consistency": os.path.join(
        "data", "cache", "domain_event_consistency.json"
    ),
    "event_rebuild": os.path.join(
        "data", "cache", "domain_event_projection_rebuild.json"
    ),
    "gonogo": os.path.join("data", "cache", "gonogo_report.json"),
    "ic_panel": os.path.join("data", "cache", "ic_panel_snapshot.json"),
    "paper_cycle": os.path.join("evidence", "alpha_paper_cycle.json"),
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _ic_panel_row(payload: Mapping[str, Any], alpha_id: str) -> dict[str, Any]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and row.get("name") == alpha_id:
            return {
                key: row.get(key)
                for key in ("name", "universe", "mean_ic", "icir", "t_stat", "n_periods")
                if key in row
            }
    return {}


def evaluate_alpha_readiness(
    alpha_id: str,
    *,
    refutation: Mapping[str, Any],
    dry_run: Mapping[str, Any],
    capacity: Mapping[str, Any],
    forward: Mapping[str, Any],
    data_admission: Mapping[str, Any],
    event_health: Mapping[str, Any],
    gonogo: Mapping[str, Any],
    ic_panel: Mapping[str, Any] | None = None,
    safety: Mapping[str, Any] | None = None,
    event_consistency: Mapping[str, Any] | None = None,
    event_rebuild: Mapping[str, Any] | None = None,
    paper_cycle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate artifacts without performing IO or changing runtime state."""
    definition = ALPHA_CATALOG.get(alpha_id)
    warnings: list[str] = []
    blockers: list[str] = []

    cycle_row: Mapping[str, Any] = {}
    if isinstance(paper_cycle, Mapping):
        cycle_rows = paper_cycle.get("alphas")
        if isinstance(cycle_rows, Mapping) and isinstance(cycle_rows.get(alpha_id), Mapping):
            cycle_row = cycle_rows[alpha_id]
    cycle_research = cycle_row.get("research") if isinstance(cycle_row, Mapping) else None
    # carry/top have Alpha-specific research snapshots in the paper-cycle
    # artifact.  Never let a missing snapshot fall back to the crowd refutation
    # artifact: that would make a report look complete while proving the wrong
    # candidate.  crowd retains its dedicated legacy checks for compatibility.
    use_cycle_research = alpha_id in {"carry_oi_majors", "top_contra"}

    ref_checks = refutation.get("refutation_checks")
    ref_checks = ref_checks if isinstance(ref_checks, dict) else {}
    ref_pass_count = sum(bool(value) for value in ref_checks.values())
    refutation_pass = (
        isinstance(refutation.get("verdict"), str)
        and str(refutation["verdict"]).startswith("SURVIVES")
        and ref_pass_count >= 4
    )
    net_10bps = _number(
        (refutation.get("net_sharpe_by_fee_bps") or {}).get(10)
        or (refutation.get("net_sharpe_by_fee_bps") or {}).get("10")
    )
    lag_1d = _number(
        (refutation.get("price_sharpe_by_lag") or {}).get(1)
        or (refutation.get("price_sharpe_by_lag") or {}).get("1")
    )
    oos = refutation.get("embargoed_oos") or {}
    oos_folds = oos.get("n_folds") if isinstance(oos, dict) else None
    oos_sharpe = _number(oos.get("mean_oos") if isinstance(oos, dict) else None)

    dry_books = dry_run.get("books") if isinstance(dry_run.get("books"), dict) else {}
    dry_run_id = (definition or {}).get("dry_run_id", alpha_id)
    capacity_id = (definition or {}).get("capacity_id", alpha_id)
    dry_book = dry_books.get(dry_run_id) if isinstance(dry_books, dict) else None
    dry_book = dry_book if isinstance(dry_book, dict) else {}
    residual = _number(dry_run.get("residual_max"))
    residual_limit = _number((dry_run.get("tolerances") or {}).get("residual"))
    if residual_limit is None:
        residual_limit = 1e-6

    cap_numbers = capacity.get("numbers") if isinstance(capacity.get("numbers"), dict) else {}
    cap = cap_numbers.get(f"{capacity_id}.participation_cap_aum")
    cap_value = _number(cap.get("value") if isinstance(cap, dict) else None)

    research_checks = {
        "registered_alpha": definition is not None,
        "refutation_battery": refutation_pass,
        "net_sharpe_at_10bps": net_10bps is not None and net_10bps > 0.5,
        "survives_one_day_execution_lag": lag_1d is not None and lag_1d > 0.5,
        "embargoed_oos_positive": (
            isinstance(oos_folds, int) and oos_folds >= 5 and oos_sharpe is not None and oos_sharpe > 0
        ),
        "dry_run_reconciles": (
            bool(dry_book)
            and residual is not None
            and residual <= residual_limit
        ),
        "capacity_diagnostic_present": cap_value is not None and cap_value > 0,
    }
    if use_cycle_research:
        cycle_verdict = cycle_research.get("verdict") if isinstance(cycle_research, Mapping) else None
        research_checks = {
            "registered_alpha": definition is not None,
            "paper_cycle_research_evidence": cycle_verdict in {"PASS", "PASS_WITH_WARNINGS"},
            "dry_run_reconciles": (
                bool(dry_book)
                and residual is not None
                and residual <= residual_limit
            ),
            "capacity_diagnostic_present": cap_value is not None and cap_value > 0,
        }
        if not cycle_row:
            blockers.append("PAPER_CYCLE_EVIDENCE_MISSING")
        cycle_warnings = cycle_research.get("warnings") if isinstance(cycle_research, Mapping) else []
        if isinstance(cycle_warnings, list):
            warnings.extend(str(item) for item in cycle_warnings[:8])
        cycle_blockers = cycle_row.get("blockers")
        if isinstance(cycle_blockers, list):
            blockers.extend(str(item) for item in cycle_blockers[:8])
        if cycle_row.get("paper_verdict") == "BLOCKED":
            blockers.append("PAPER_CYCLE_BLOCKED")
        canonical_cycle = cycle_row.get("canonical_dry_run")
        if isinstance(canonical_cycle, Mapping):
            cycle_risk = canonical_cycle.get("research_risk_decision")
            if cycle_risk is not None and cycle_risk != "PASS":
                blockers.append("DRY_RUN_RESEARCH_RISK_NOT_PASS")
    if not research_checks["registered_alpha"]:
        blockers.append("ALPHA_NOT_REGISTERED")
    if not all(research_checks.values()):
        blockers.append("RESEARCH_EVIDENCE_INCOMPLETE")

    ic_row = _ic_panel_row(ic_panel or {}, alpha_id)
    ic_t = _number(ic_row.get("t_stat"))
    if ic_row and ic_t is not None and ic_t < 2.0:
        warnings.append("IC_PANEL_DIAGNOSTIC_WEAK_TSTAT")

    forward_alphas = forward.get("alphas") if isinstance(forward.get("alphas"), dict) else {}
    forward_row = forward_alphas.get(alpha_id) if isinstance(forward_alphas, dict) else None
    forward_row = forward_row if isinstance(forward_row, dict) else {}
    n_forward = forward_row.get("n_forward_periods")
    forward_sharpe = _number(forward_row.get("paper_sharpe"))
    admission_verdict = data_admission.get("verdict")
    # The clock-integrity forward driver renamed this field when it bound each
    # decision/realization to the data-admission authority. Accept the legacy
    # field for old artifacts, but prefer the current pair: a complete refresh
    # evidence set plus a PASS admission decision. This is report-only; the
    # driver and its fail-closed gates remain authoritative.
    quality_current = (
        forward.get("quality_evidence_current") is True
        or (
            forward.get("refresh_evidence_available") is True
            and admission_verdict == "PASS"
        )
    )
    if admission_verdict != "PASS":
        blockers.append("DATA_ADMISSION_BLOCK")
    if not isinstance(n_forward, int) or n_forward < REQUIRED_FORWARD_PERIODS:
        blockers.append("FORWARD_EVIDENCE_INSUFFICIENT")
    if not quality_current:
        blockers.append("FORWARD_QUALITY_EVIDENCE_UNAVAILABLE")
    if n_forward and n_forward >= REQUIRED_FORWARD_PERIODS and (forward_sharpe is None or forward_sharpe <= 0):
        blockers.append("FORWARD_PAPER_SHARPE_NON_POSITIVE")

    event_verdict = event_health.get("verdict")
    pending_events = event_health.get("pending_events")
    pending_limit = (event_health.get("thresholds") or {}).get("max_pending_events", 1000)
    event_healthy = event_verdict == "PASS" and isinstance(pending_events, int) and pending_events <= pending_limit
    if not event_healthy:
        blockers.append("EVENT_BUS_NOT_HEALTHY")

    if event_consistency and event_consistency.get("verdict") != "PASS":
        blockers.append("EVENT_AUTHORITY_PROJECTION_INCONSISTENT")

    forward_present = (gonogo.get("forward_evidence") or {}).get("present") is True
    if not forward_present:
        blockers.append("GONOGO_FORWARD_EVIDENCE_ABSENT")

    safe = dict(safety or {})
    live_flags = any(bool(safe.get(name)) for name in (
        "live_trading_enabled", "real_broker_enabled", "allow_order_submission", "allow_ai_direct_trading"
    ))
    human_required = safe.get("require_human_approval", True) is True
    safety_locked = not live_flags and human_required
    if not safety_locked:
        blockers.append("SAFETY_CONFIG_NOT_LOCKED")

    # L2 is intentionally non-overridable: even an otherwise clean research packet
    # can only be reviewed for a later human-controlled phase, never executed here.
    unique_blockers = list(dict.fromkeys(blockers))
    research_verdict = "PASS" if all(research_checks.values()) else "BLOCKED"
    if research_verdict == "PASS" and warnings:
        research_verdict = "PASS_WITH_WARNINGS"

    next_action = (
        "修复数据准入与事件投影积压，重新积累至少 8 个真实前向 paper 周期；"
        "通过后仍需人工多方审批，当前不会生成或提交真实订单。"
    )
    # Keep operator guidance aligned with the current evidence rather than
    # always claiming that admission or projection is broken. The report is
    # descriptive only and cannot relax any gate.
    if admission_verdict != "PASS":
        next_action = (
            "Wait for a clean data-admission PASS before recording another forward decision; "
            "the L2 system will not submit real orders."
        )
    elif not event_healthy or (
        event_consistency and event_consistency.get("verdict") != "PASS"
    ):
        next_action = (
            "Keep the event authority and projection healthy and consistent, then continue "
            "the forward-paper clock; live order submission remains disabled."
        )
    elif not isinstance(n_forward, int) or n_forward < REQUIRED_FORWARD_PERIODS:
        next_action = (
            "Continue the official forward driver until this Alpha has 8 realized periods; "
            "the L2 system will not generate or submit real orders."
        )
    else:
        next_action = (
            "Start the separately approved human multi-party review and post-L2 controls; "
            "this report still cannot authorize real orders."
        )

    return {
        "schema_version": 1,
        "alpha": {
            "id": alpha_id,
            "definition": definition,
            "ic_panel": ic_row,
        },
        "research": {
            "verdict": research_verdict,
            "checks": research_checks,
            "refutation_pass_count": ref_pass_count,
            "net_sharpe_10bps": net_10bps,
            "one_day_lag_sharpe": lag_1d,
            "embargoed_oos_mean_sharpe": oos_sharpe,
            "dry_run_residual_max": residual,
            "capacity_participation_cap_aum": cap_value,
            "warnings": warnings,
        },
        "forward_gate": {
            "data_admission_verdict": admission_verdict,
            "n_forward_periods": n_forward,
            "paper_sharpe": forward_sharpe,
            "quality_evidence_current": quality_current,
            "event_health_verdict": event_verdict,
            "pending_events": pending_events,
            "gonogo_forward_evidence_present": forward_present,
            "event_projection_rebuild_candidate": {
                "verdict": event_rebuild.get("verdict")
                if event_rebuild
                else None,
                "promotion": event_rebuild.get("promotion")
                if event_rebuild
                else None,
            },
        },
        "deployment": {
            "readiness": "L2_DRY_RUN_ONLY",
            "execution_mode": "DRY_RUN_ONLY",
            "live_allowed": False,
            "order_submission_allowed": False,
            "human_review_required": True,
            "small_capital_live_verdict": "BLOCKED_L2_DRY_RUN_ONLY",
            "research_packet_ready_for_paper": research_verdict in {"PASS", "PASS_WITH_WARNINGS"}
            and not unique_blockers,
            "blockers": unique_blockers,
            "next_action": next_action,
        },
        "safety": {
            "live_trading_armed": False,
            "safety_lock_verified": safety_locked,
        },
        "honesty_note": (
            "研究反证通过不等于可实盘；前向数据、事件投影和多方人工授权是独立门禁。"
            "本报告永远不会授权真实下单。"
        ),
    }


def build_report(alpha_id: str = "crowd_contra", *, artifact_paths: Mapping[str, str] | None = None) -> dict[str, Any]:
    paths = dict(DEFAULT_ARTIFACTS)
    if artifact_paths:
        paths.update(artifact_paths)
    artifacts = {name: _load_json(path) for name, path in paths.items()}
    safety = RESEARCH_SAFE_DEFAULTS.model_dump()
    return evaluate_alpha_readiness(
        alpha_id,
        refutation=artifacts["refutation"],
        dry_run=artifacts["dry_run"],
        capacity=artifacts["capacity"],
        forward=artifacts["forward"],
        data_admission=artifacts["data_admission"],
        event_health=artifacts["event_health"],
        event_consistency=artifacts["event_consistency"],
        event_rebuild=artifacts["event_rebuild"],
        gonogo=artifacts["gonogo"],
        ic_panel=artifacts["ic_panel"],
        safety=safety,
        paper_cycle=artifacts["paper_cycle"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a read-only Alpha/L2 readiness report")
    parser.add_argument("--alpha-id", default="crowd_contra")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build_report(args.alpha_id)
    atomic_write_json(args.output, report, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
