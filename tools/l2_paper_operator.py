"""Run the complete, one-shot L2 paper operator flow.

The operator deliberately composes the two existing safe seams instead of
creating a new execution path: the official three-Alpha paper flow first
refreshes public evidence and rebuilds readiness artifacts, then the paper
order preview sends each attested decision through the real pre-trade gate and
the dry-run adapter.  Any failed stage is reported fail-closed.  No credential,
private venue, broker SDK, or live order path is imported or called.
"""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from tools.cache_io import atomic_write_json
from tools.paper_order_preview import run_preview
from tools.three_alpha_paper_flow import run_flow


DEFAULT_OUTPUT = "data/cache/l2_paper_operator.json"


def _error_stage(name: str, exc: BaseException) -> dict[str, Any]:
    return {
        "status": "ERROR",
        "stage": name,
        "error_type": type(exc).__name__,
        "error": str(exc)[:240],
    }


def run_operator(
    *,
    advance_forward: bool = True,
    output_path: str = DEFAULT_OUTPUT,
    flow_runner: Callable[[], Mapping[str, Any]] | None = None,
    preview_runner: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the official paper flow followed by the idempotent order preview."""
    started_at = int(time.time() * 1000)
    try:
        flow = dict(
            flow_runner()
            if flow_runner is not None
            else run_flow(advance_forward=advance_forward)
        )
        flow_verdict = flow.get("verdict")
        flow_completed = isinstance(flow_verdict, Mapping) and flow_verdict.get(
            "flow_completed"
        ) is True
    except Exception as exc:
        flow = {}
        flow_verdict = {}
        flow_completed = False
        flow_stage: dict[str, Any] = _error_stage(
            "three_alpha_paper_flow", exc
        )
    else:
        flow_stage = {
            "status": "PASS" if flow_completed else "ERROR",
            "flow_completed": flow_completed,
            "flow": flow,
        }

    if not flow_completed:
        preview_stage: dict[str, Any] = {
            "status": "SKIPPED",
            "reason": "THREE_ALPHA_FLOW_NOT_COMPLETED",
            "paper_preview_ready": False,
        }
        preview = {}
    else:
        try:
            preview = dict(
                preview_runner()
                if preview_runner is not None
                else run_preview()
            )
            preview_verdict = preview.get("verdict")
            preview_ready = isinstance(preview_verdict, Mapping) and preview_verdict.get(
                "paper_preview_ready"
            ) is True
            preview_stage = {
                "status": "PASS" if preview_ready else "ERROR",
                "paper_preview_ready": preview_ready,
                "preview": preview,
            }
        except Exception as exc:
            preview = {}
            preview_stage = _error_stage("paper_order_preview", exc)
            preview_stage["paper_preview_ready"] = False

    preview_ready = preview_stage.get("paper_preview_ready") is True
    report = {
        "schema_version": 1,
        "flow": "L2_PAPER_OPERATOR",
        "readiness": "L2_DRY_RUN_ONLY",
        "started_at": started_at,
        "finished_at": int(time.time() * 1000),
        "stages": {
            "three_alpha_paper_flow": flow_stage,
            "paper_order_preview": preview_stage,
        },
        "verdict": {
            "operator_completed": flow_completed and preview_ready,
            "paper_preview_ready": preview_ready,
            "research_packet_ready_for_all_three": bool(
                isinstance(flow_verdict, Mapping)
                and flow_verdict.get("paper_packet_ready_for_all_three") is True
            ),
            "live_allowed": False,
            "order_submission_allowed": False,
            "human_review_required": True,
        },
        "safety": {
            "paper_only": True,
            "private_binance_access": False,
            "credential_access": False,
            "broker_order_submission": False,
        },
    }
    atomic_write_json(output_path, report, indent=2)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-forward",
        action="store_true",
        help="skip the public forward refresh and use current evidence",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = run_operator(
        advance_forward=not args.no_forward,
        output_path=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"-> {args.output}")
    return 0 if report["verdict"]["operator_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_OUTPUT", "run_operator"]
