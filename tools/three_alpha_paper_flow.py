"""Run the complete L2 paper flow for all three Alpha candidates.

This is the missing operator-level seam between the individual deterministic
dry-run drivers and the read-only readiness reports.  It deliberately stops at
paper evidence: it never imports a broker client, reads a credential, submits an
order, or enables a live flag.  The optional forward step uses only the public
forward-paper driver; it is never a private Binance/account call.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

from services.execution.dry_run_store import DryRunStore
from tools.alpha_paper_cycle import build_report as build_paper_cycle_report
from tools.alpha_readiness_check import build_report as build_alpha_readiness_report
from tools.cache_io import atomic_write_json


DEFAULT_OUTPUT = os.path.join("evidence", "three_alpha_paper_flow.json")
DEFAULT_CYCLE_OUTPUT = os.path.join("evidence", "alpha_paper_cycle.json")
DRY_RUN_DB = os.path.join("data", "cache", "dry_run.db")

ALPHA_DRIVERS: tuple[tuple[str, str, str | None], ...] = (
    (
        "carry_oi_majors",
        "tools.dry_run_carry_authoritative_multi",
        os.path.join("data", "cache", "carry_oi_majors_dry_run.json"),
    ),
    (
        "crowd_contra",
        "tools.dry_run_crowd_contra_multi",
        os.path.join("data", "cache", "crowd_contra_dry_run.json"),
    ),
    (
        "top_contra",
        "tools.dry_run_top_contra_multi",
        os.path.join("data", "cache", "top_contra_dry_run.json"),
    ),
)


def _load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _snapshot_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    detail = record.get("detail")
    detail = detail if isinstance(detail, Mapping) else {}
    pnl = record.get("pnl")
    pnl = pnl if isinstance(pnl, Mapping) else {}
    risk = detail.get("research_risk")
    risk = risk if isinstance(risk, Mapping) else {}
    return {
        "run_id": record.get("run_id"),
        "timestamp": record.get("timestamp"),
        "n_periods": detail.get("n_periods", pnl.get("n_periods")),
        "pnl": dict(pnl),
        "research_risk_decision": risk.get("decision"),
        "reconciliation_residual": pnl.get("residual"),
        "source": "dry_run_store",
    }


def _latest_dry_run_snapshot(
    alpha_id: str,
    report_path: str | None,
    *,
    db_path: str = DRY_RUN_DB,
) -> dict[str, Any]:
    """Read the latest paper artifact without creating or mutating a store."""
    if report_path:
        report = _load_json(report_path)
        if report.get("run_id"):
            pnl = report.get("pnl")
            pnl = pnl if isinstance(pnl, Mapping) else {}
            risk = report.get("research_risk")
            risk = risk if isinstance(risk, Mapping) else {}
            return {
                "run_id": report.get("run_id"),
                "timestamp": report.get("timestamp"),
                "n_periods": report.get("n_periods"),
                "pnl": dict(pnl),
                "research_risk_decision": risk.get("decision"),
                "reconciliation_residual": report.get("reconciliation_residual"),
                "source": report_path,
            }
    if not os.path.isfile(db_path):
        return {}
    store = None
    try:
        store = DryRunStore(db_path, create=False)
        records = [
            record
            for record in store.all_runs()
            if isinstance(record, Mapping) and record.get("strategy_id") == alpha_id
        ]
        if not records:
            return {}
        return _snapshot_from_record(records[-1])
    except (OSError, ValueError, RuntimeError):
        return {}
    finally:
        if store is not None:
            store.close()


def _run_driver(
    alpha_id: str,
    module_name: str,
    report_path: str | None,
    *,
    runner: Callable[[], Any] | None = None,
    snapshot_loader: Callable[[str, str | None], dict[str, Any]] = _latest_dry_run_snapshot,
) -> dict[str, Any]:
    """Execute one existing paper driver and verify its persisted result."""
    try:
        if runner is None:
            module = importlib.import_module(module_name)
            runner = getattr(module, "main")
        runner()
    except Exception as exc:  # fail closed, but preserve the other Alpha reports
        return {
            "alpha_id": alpha_id,
            "module": module_name,
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc)[:240],
            "paper_order_submission": False,
        }
    snapshot = snapshot_loader(alpha_id, report_path)
    if not snapshot:
        return {
            "alpha_id": alpha_id,
            "module": module_name,
            "status": "ERROR",
            "reason": "DRY_RUN_ARTIFACT_MISSING_AFTER_DRIVER",
            "paper_order_submission": False,
        }
    return {
        "alpha_id": alpha_id,
        "module": module_name,
        "status": "PASS",
        "paper_order_submission": False,
        "snapshot": snapshot,
    }


def _run_public_forward_driver() -> dict[str, Any]:
    """Advance the public forward-paper clock, never a private/live venue path."""
    try:
        module = importlib.import_module("tools.forward_track_advance")
        module.main()
    except Exception as exc:  # forward evidence is fail-closed, not a live fallback
        return {
            "status": "ERROR",
            "driver": "tools.forward_track_advance",
            "error_type": type(exc).__name__,
            "error": str(exc)[:240],
            "private_binance": False,
        }
    status_path = os.path.join("data", "cache", "forward_track_status.json")
    return {
        "status": "PASS",
        "driver": "tools.forward_track_advance",
        "private_binance": False,
        "snapshot_path": status_path,
        "snapshot": _load_json(status_path),
    }


def run_flow(
    *,
    run_drivers: bool = True,
    advance_forward: bool = False,
    output_path: str = DEFAULT_OUTPUT,
    cycle_output_path: str = DEFAULT_CYCLE_OUTPUT,
    driver_runners: Mapping[str, Callable[[], Any]] | None = None,
    snapshot_loader: Callable[[str, str | None], dict[str, Any]] = _latest_dry_run_snapshot,
    cycle_builder: Callable[[], dict[str, Any]] = build_paper_cycle_report,
    readiness_builder: Callable[[str], dict[str, Any]] = build_alpha_readiness_report,
) -> dict[str, Any]:
    """Execute and verify the full L2 paper flow for all three candidates."""
    started_at = int(time.time() * 1000)
    forward = (
        _run_public_forward_driver()
        if advance_forward
        else {"status": "SKIPPED", "reason": "FORWARD_DRIVER_NOT_REQUESTED", "private_binance": False}
    )

    drivers: list[dict[str, Any]] = []
    if run_drivers:
        for alpha_id, module_name, report_path in ALPHA_DRIVERS:
            drivers.append(
                _run_driver(
                    alpha_id,
                    module_name,
                    report_path,
                    runner=driver_runners.get(alpha_id) if driver_runners else None,
                    snapshot_loader=snapshot_loader,
                )
            )
    else:
        drivers = [
            {
                "alpha_id": alpha_id,
                "module": module_name,
                "status": "SKIPPED",
                "reason": "DRY_RUN_DRIVERS_NOT_REQUESTED",
                "paper_order_submission": False,
            }
            for alpha_id, module_name, _ in ALPHA_DRIVERS
        ]

    try:
        cycle = cycle_builder()
        atomic_write_json(cycle_output_path, cycle, indent=2)
        cycle_stage: dict[str, Any] = {"status": "PASS", "path": cycle_output_path, "snapshot": cycle}
    except Exception as exc:
        cycle = {}
        cycle_stage = {
            "status": "ERROR",
            "path": cycle_output_path,
            "error_type": type(exc).__name__,
            "error": str(exc)[:240],
        }

    readiness: dict[str, Any] = {}
    for alpha_id, _, _ in ALPHA_DRIVERS:
        try:
            report = readiness_builder(alpha_id)
            readiness[alpha_id] = {
                "status": "PASS",
                "research_packet_ready_for_paper": bool(
                    (report.get("deployment") or {}).get("research_packet_ready_for_paper")
                ),
                "small_capital_live_verdict": (report.get("deployment") or {}).get(
                    "small_capital_live_verdict"
                ),
                "blockers": list((report.get("deployment") or {}).get("blockers") or []),
                "report": report,
            }
        except Exception as exc:
            readiness[alpha_id] = {
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
                "research_packet_ready_for_paper": False,
                "small_capital_live_verdict": "BLOCKED_READINESS_REPORT_ERROR",
                "blockers": ["READINESS_REPORT_ERROR"],
            }

    driver_errors = [row for row in drivers if row.get("status") == "ERROR"]
    paper_ready = bool(
        cycle_stage.get("status") == "PASS"
        and not driver_errors
        and all(row.get("research_packet_ready_for_paper") is True for row in readiness.values())
    )
    report = {
        "schema_version": 1,
        "flow": "THREE_ALPHA_PAPER_FLOW",
        "started_at": started_at,
        "finished_at": int(time.time() * 1000),
        "readiness": "L2_DRY_RUN_ONLY",
        "stages": {
            "public_forward": forward,
            "authoritative_dry_runs": drivers,
            "paper_cycle": cycle_stage,
            "alpha_readiness": readiness,
        },
        "verdict": {
            "flow_completed": not driver_errors and cycle_stage.get("status") == "PASS",
            "paper_packet_ready_for_all_three": paper_ready,
            "live_allowed": False,
            "order_submission_allowed": False,
            "human_review_required": True,
        },
        "safety": {
            "private_binance_access": False,
            "credential_access": False,
            "broker_order_submission": False,
            "paper_only": True,
        },
    }
    atomic_write_json(output_path, report, indent=2)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-dry-runs", action="store_true", help="only refresh reports; do not execute paper drivers")
    parser.add_argument(
        "--advance-forward",
        action="store_true",
        help="run the public forward-paper driver; no private Binance/account path",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--cycle-output", default=DEFAULT_CYCLE_OUTPUT)
    args = parser.parse_args(argv)
    report = run_flow(
        run_drivers=not args.no_dry_runs,
        advance_forward=args.advance_forward,
        output_path=args.output,
        cycle_output_path=args.cycle_output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")
    return 0 if report["verdict"]["flow_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ALPHA_DRIVERS", "DEFAULT_OUTPUT", "run_flow"]
