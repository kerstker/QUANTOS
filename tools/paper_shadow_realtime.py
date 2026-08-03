"""Run one fixed realtime valuation cycle for the automated shadow-paper ledger."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SUCCESS,
)
from tools import paper_realtime, paper_shadow_auto
from tools.cache_io import atomic_write_json
from tools.paper_candidate_universe import paper_candidate_ids


OUTPUT_PATH = "data/cache/paper_shadow_realtime_status.json"
PROFILE_ARGS: tuple[str, ...] = (
    "--cycles",
    "1",
    "--no-bootstrap",
    "--aum",
    "1000000",
    "--paper-store",
    "data/cache/paper_shadow_routed_ledger_v2.db",
    "--store",
    "data/cache/paper_shadow_realtime.db",
    "--output",
    OUTPUT_PATH,
    "--operator-receipt",
    "data/cache/paper_shadow_auto.json",
)


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        if any(str(arg) in {"-h", "--help"} for arg in argv):
            argparse.ArgumentParser(description=__doc__).parse_args(list(argv))
        raise ValueError("paper_shadow_realtime does not accept worker arguments")
    shadow = paper_shadow_auto.run_shadow()
    shadow_verdict = shadow.get("verdict") if isinstance(shadow, dict) else None
    if not isinstance(shadow_verdict, dict) or shadow_verdict.get(
        "shadow_paper_running"
    ) is not True:
        atomic_write_json(
            OUTPUT_PATH,
            {
                "schema_version": 1,
                "flow": "AUTOMATED_SHADOW_PAPER_REALTIME_CYCLE",
                "execution_class": "AUTOMATED_NONPROMOTABLE_SHADOW_PAPER",
                "status": "SAFE_BLOCKED",
                "errors": dict(shadow.get("errors") or {})
                if isinstance(shadow, dict)
                else {"shadow": "invalid shadow-paper receipt"},
                "verdict": {
                    "cycle_completed": False,
                    "shadow_paper_running": False,
                    "candidate_coverage_complete": False,
                    "official_paper_ready": False,
                    "promotion_eligible": False,
                    "forward_evidence_eligible": False,
                    "live_allowed": False,
                    "order_submission_allowed": False,
                },
                "safety": {
                    "paper_only": True,
                    "broker_connectivity": False,
                    "broker_order_submission": False,
                },
            },
            indent=2,
        )
        return SCHEDULE_EXIT_FAILURE
    paper_realtime.main(list(PROFILE_ARGS))
    try:
        report = json.loads(Path(OUTPUT_PATH).read_text(encoding="utf-8"))
        verdict = report.get("verdict")
        safety = report.get("safety")
        alphas = report.get("alphas")
        expected = paper_candidate_ids()
        completed = bool(
            isinstance(verdict, dict)
            and verdict.get("paper_mark_ready") is True
            and isinstance(safety, dict)
            and safety.get("paper_only") is True
            and safety.get("broker_connectivity") is False
            and safety.get("broker_order_submission") is False
            and isinstance(alphas, dict)
            and set(alphas) == set(expected)
            and all(
                isinstance(row, dict) and row.get("status") == "PAPER_MARKED"
                for row in alphas.values()
            )
        )
        report.update(
            {
                "flow": "AUTOMATED_SHADOW_PAPER_REALTIME_CYCLE",
                "execution_class": "AUTOMATED_NONPROMOTABLE_SHADOW_PAPER",
                "official_paper_execution": "PAPER_BLOCKED",
                "promotion_eligible": False,
                "forward_evidence_eligible": False,
                "deployment_eligible": False,
                "candidate_universe": {
                    "authority": "ALPHA_PRODUCTION_WORKFLOW",
                    "expected": list(expected),
                    "expected_count": len(expected),
                    "running_count": len(alphas) if isinstance(alphas, dict) else 0,
                    "coverage_complete": completed,
                },
            }
        )
        verdict.update(
            {
                "cycle_completed": completed,
                "shadow_paper_running": completed,
                "candidate_coverage_complete": completed,
                "candidate_count": len(expected),
                "backtest_reference_required": False,
                "official_paper_ready": False,
                "promotion_eligible": False,
                "forward_evidence_eligible": False,
                "live_allowed": False,
                "order_submission_allowed": False,
            }
        )
        atomic_write_json(OUTPUT_PATH, report, indent=2)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return SCHEDULE_EXIT_FAILURE
    return SCHEDULE_EXIT_SUCCESS if completed else SCHEDULE_EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["OUTPUT_PATH", "PROFILE_ARGS", "main"]
