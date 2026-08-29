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
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import time
from collections.abc import Callable, Mapping
from typing import Any

from services.execution.backtest_authority import quarantine_legacy_backtest
from tools.cache_io import atomic_write_json
from tools.paper_candidate_universe import paper_candidate_ids
from tools.paper_order_preview import run_preview
from tools.three_alpha_paper_flow import run_flow


DEFAULT_OUTPUT = "data/cache/l2_paper_operator.json"
DEFAULT_FACTORY_OUTPUT = "data/cache/crypto_factory_loop.json"
FACTORY_MIN_INTERVAL_MS = 24 * 60 * 60 * 1000


def _error_stage(name: str, exc: BaseException) -> dict[str, Any]:
    return {
        "status": "ERROR",
        "stage": name,
        "error_type": type(exc).__name__,
        "error": str(exc)[:240],
    }


def _run_candidate_evaluation() -> dict[str, Any]:
    """Run the independent evaluator for every current forward challenger."""
    from tools.paper_alpha_evaluation import run_forward

    document = run_forward()
    return {
        "status": "PASS",
        "schema_version": document.get("schema_version"),
        "candidate_count": len(document.get("alphas") or {}),
        "verified_count": sum(
            row.get("status") == "VERIFIED"
            for row in (document.get("alphas") or {}).values()
            if isinstance(row, Mapping)
        ),
        "artifact": "data/cache/forward_alpha_evaluations.json",
        "paper_only": True,
    }


def _run_candidate_mining(
    *,
    output_path: str = DEFAULT_FACTORY_OUTPUT,
    now_ms: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run or reuse the real multi-dimensional Alpha Factory evidence.

    Mining is heavier than the market-data heartbeat, so a recent immutable
    factory receipt is reused for 24 hours.  A stale/missing receipt invokes
    the existing factory loop in-process; stdout/stderr are captured so the
    IDE does not open or spam a separate terminal window.
    """
    path = Path(output_path)
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    current: dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            current = loaded
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        current = {}
    generated = current.get("generated_at")
    if isinstance(generated, (int, float)) and not isinstance(generated, bool):
        generated_ms = (
            int(generated * 1000)
            if generated < 10_000_000_000
            else int(generated)
        )
    else:
        generated_ms = None
    if (
        not force
        and current.get("schema_version") == 2
        and generated_ms is not None
        and generated_ms <= now
        and now - generated_ms < FACTORY_MIN_INTERVAL_MS
    ):
        chain = current.get("discovered_chain")
        eligible = current.get("eligible_discoveries")
        return {
            "status": "PASS",
            "mode": "REUSED_RECENT_ARTIFACT",
            "artifact": str(path),
            "generated_at": generated_ms,
            "survivor_count": len(chain) if isinstance(chain, list) else 0,
            "eligible_count": len(eligible) if isinstance(eligible, list) else 0,
            "n_evaluated_total": current.get("n_evaluated_total"),
            "verdict": current.get("verdict"),
            "paper_only": True,
        }

    from tools.crypto_factory_loop import main as run_factory_loop

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        run_factory_loop()
    try:
        refreshed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"factory loop did not publish a valid receipt: {exc}") from exc
    if not isinstance(refreshed, dict) or refreshed.get("schema_version") != 2:
        raise RuntimeError("factory loop receipt schema is invalid")
    chain = refreshed.get("discovered_chain")
    eligible = refreshed.get("eligible_discoveries")
    return {
        "status": "PASS",
        "mode": "RAN_NOW",
        "artifact": str(path),
        "generated_at": refreshed.get("generated_at"),
        "survivor_count": len(chain) if isinstance(chain, list) else 0,
        "eligible_count": len(eligible) if isinstance(eligible, list) else 0,
        "n_evaluated_total": refreshed.get("n_evaluated_total"),
        "verdict": refreshed.get("verdict"),
        "paper_only": True,
    }


def _run_candidate_risk_bundle() -> dict[str, Any]:
    """Block the legacy evaluation-to-risk bundle before any read or write."""
    quarantine_legacy_backtest("tools.l2_paper_operator.candidate_risk_bundle")

    # The implementation below is retained only as a reviewable migration
    # target.  In particular, do not move ``paper_candidate_ids`` above the
    # quarantine: it reads legacy workflow/evaluation artifacts.
    from pathlib import Path

    from tools.paper_research_risk_bundle import (
        DEFAULT_OUTPUT as risk_output,
        _write,
        build_automatic_research_risk_bundle,
    )

    candidate_ids = paper_candidate_ids()
    document = build_automatic_research_risk_bundle(alpha_ids=candidate_ids)
    _write(Path(risk_output), document)
    return {
        "status": "PASS",
        "bundle_status": document.get("status"),
        "candidate_count": len(document.get("alphas") or {}),
        "ready_count": sum(
            row.get("status") == "READY"
            for row in (document.get("alphas") or {}).values()
            if isinstance(row, Mapping)
        ),
        "artifact": str(risk_output),
        "paper_only": True,
    }


def run_operator(
    *,
    advance_forward: bool = True,
    refresh_candidate_research: bool = True,
    output_path: str = DEFAULT_OUTPUT,
    flow_runner: Callable[[], Mapping[str, Any]] | None = None,
    preview_runner: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the official paper flow followed by the idempotent order preview.

    ``refresh_candidate_research=False`` is the bounded mark-loop mode. It
    consumes persisted challenger evidence instead of launching Alpha mining,
    candidate backtests, evaluation, or risk-bundle work. Those heavy stages
    are owned by independent scheduled tasks.
    """
    started_at = int(time.time() * 1000)
    try:
        flow = dict(
            flow_runner()
            if flow_runner is not None
            else run_flow(
                advance_forward=advance_forward,
                run_candidates=refresh_candidate_research,
            )
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
        evaluation_stage = {
            "status": "SKIPPED",
            "reason": "THREE_ALPHA_FLOW_NOT_COMPLETED",
        }
        mining_stage = {
            "status": "SKIPPED",
            "reason": "THREE_ALPHA_FLOW_NOT_COMPLETED",
        }
        risk_stage = {
            "status": "SKIPPED",
            "reason": "THREE_ALPHA_FLOW_NOT_COMPLETED",
        }
    else:
        if flow_runner is not None:
            mining_stage = {
                "status": "SKIPPED",
                "reason": "INJECTED_FLOW_RUNNER",
            }
            evaluation_stage = {
                "status": "SKIPPED",
                "reason": "INJECTED_FLOW_RUNNER",
            }
            risk_stage = {
                "status": "SKIPPED",
                "reason": "INJECTED_FLOW_RUNNER",
            }
        elif not refresh_candidate_research:
            mining_stage = {
                "status": "SKIPPED",
                "reason": "INDEPENDENT_RESEARCH_SCHEDULE",
            }
            evaluation_stage = {
                "status": "SKIPPED",
                "reason": "INDEPENDENT_RESEARCH_SCHEDULE",
            }
            risk_stage = {
                "status": "SKIPPED",
                "reason": "INDEPENDENT_RESEARCH_SCHEDULE",
            }
        else:
            try:
                mining_stage = _run_candidate_mining()
            except Exception as exc:
                mining_stage = _error_stage("candidate_mining", exc)
            try:
                evaluation_stage = _run_candidate_evaluation()
            except Exception as exc:
                evaluation_stage = _error_stage("forward_candidate_evaluation", exc)
            try:
                risk_stage = _run_candidate_risk_bundle()
            except Exception as exc:
                risk_stage = _error_stage("paper_research_risk_bundle", exc)
        try:
            preview = dict(
                preview_runner()
                if preview_runner is not None
                else run_preview(alpha_ids=paper_candidate_ids())
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
            "candidate_mining": mining_stage,
            "forward_candidate_evaluation": evaluation_stage,
            "paper_research_risk_bundle": risk_stage,
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
