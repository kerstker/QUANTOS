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
from tools import paper_realtime
from tools.cache_io import atomic_write_json


OUTPUT_PATH = "data/cache/paper_shadow_realtime_status.json"
PROFILE_ARGS: tuple[str, ...] = (
    "--cycles",
    "1",
    "--no-bootstrap",
    "--aum",
    "1000000",
    "--paper-store",
    "data/cache/paper_shadow_ledger.db",
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
    paper_realtime.main(list(PROFILE_ARGS))
    try:
        report = json.loads(Path(OUTPUT_PATH).read_text(encoding="utf-8"))
        verdict = report.get("verdict")
        safety = report.get("safety")
        alphas = report.get("alphas")
        completed = bool(
            isinstance(verdict, dict)
            and verdict.get("paper_mark_ready") is True
            and isinstance(safety, dict)
            and safety.get("paper_only") is True
            and safety.get("broker_connectivity") is False
            and safety.get("broker_order_submission") is False
            and isinstance(alphas, dict)
            and len(alphas) == len(paper_realtime.ALPHA_IDS)
        )
        report.update(
            {
                "flow": "AUTOMATED_SHADOW_PAPER_REALTIME_CYCLE",
                "execution_class": "AUTOMATED_NONPROMOTABLE_SHADOW_PAPER",
                "official_paper_execution": "PAPER_BLOCKED",
                "promotion_eligible": False,
                "forward_evidence_eligible": False,
                "deployment_eligible": False,
            }
        )
        verdict.update(
            {
                "cycle_completed": completed,
                "shadow_paper_running": completed,
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
