"""Run one fixed realtime valuation cycle for the automated shadow-paper ledger."""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import sys
import time

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from services.monitoring.paper_operational_evidence import assess_shadow_artifact
from tools import paper_realtime, paper_shadow_auto
from tools.cache_io import atomic_write_json
from tools.paper_candidate_universe import (
    paper_candidate_ids,
    realtime_mechanism_candidate_ids,
    shadow_paper_candidate_ids,
)


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


def _shadow_candidate_scope() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return (full population, routed shadow scope, realtime handoff)."""
    population = tuple(paper_candidate_ids())
    expected = shadow_paper_candidate_ids(candidate_ids=population)
    deferred = realtime_mechanism_candidate_ids(candidate_ids=population)
    if set(expected).intersection(deferred) or set(expected).union(deferred) != set(population):
        raise RuntimeError("paper candidate ownership scopes are not a partition")
    return population, expected, deferred


def _artifact_identity(path: str) -> tuple[int, int, int, int] | None:
    """Return enough filesystem identity to reject a stale prior-cycle artifact."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)


def _write_upstream_block(
    *,
    code: str,
    exit_code: object,
    started_at_ms: int,
    finished_at_ms: int,
) -> None:
    atomic_write_json(
        OUTPUT_PATH,
        {
            "schema_version": 1,
            "flow": "AUTOMATED_SHADOW_PAPER_REALTIME_CYCLE",
            "execution_class": "AUTOMATED_NONPROMOTABLE_SHADOW_PAPER",
            "status": "SAFE_BLOCKED",
            "errors": {
                "paper_realtime": {
                    "code": code,
                    "exit_code": exit_code,
                }
            },
            "upstream_cycle": {
                "started_at_ms": started_at_ms,
                "finished_at_ms": finished_at_ms,
                "exit_code": exit_code,
                "artifact_refreshed": False,
            },
            "verdict": {
                "cycle_completed": False,
                "paper_mark_ready": False,
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
        # Missing attested decisions are an expected fail-closed state while
        # realtime candidates are still in shadow/forward observation.  Keep
        # the worker visibly blocked, but do not report a host/process fault
        # to the scheduler watchdog.
        return SCHEDULE_EXIT_SAFE_BLOCKED
    started_at_ms = int(time.time() * 1000)
    prior_artifact = _artifact_identity(OUTPUT_PATH)
    upstream_exit_code = paper_realtime.main(list(PROFILE_ARGS))
    finished_at_ms = int(time.time() * 1000)
    current_artifact = _artifact_identity(OUTPUT_PATH)
    artifact_refreshed = (
        current_artifact is not None and current_artifact != prior_artifact
    )
    if not artifact_refreshed:
        _write_upstream_block(
            code=(
                "UPSTREAM_PAPER_REALTIME_FAILED"
                if upstream_exit_code != SCHEDULE_EXIT_SUCCESS
                else "PAPER_REALTIME_ARTIFACT_NOT_REFRESHED"
            ),
            exit_code=upstream_exit_code,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
        )
        return (
            SCHEDULE_EXIT_SAFE_BLOCKED
            if upstream_exit_code == SCHEDULE_EXIT_SAFE_BLOCKED
            else SCHEDULE_EXIT_FAILURE
        )
    try:
        report = json.loads(Path(OUTPUT_PATH).read_text(encoding="utf-8"))
        verdict = report.get("verdict")
        population, expected, deferred = _shadow_candidate_scope()
        upstream_universe = report.get("candidate_universe")
        raw_alphas = report.get("alphas")
        # ``paper_realtime`` keeps every discovered candidate visible, which
        # is correct for the official account but mixes 24 realtime pending
        # rows into this worker's operational scope.  The routed shadow
        # artifact is an isolated book: retain only its attested-decision
        # scope and record the realtime handoff explicitly instead of
        # silently claiming coverage for those candidates.
        alphas = (
            {
                alpha_id: raw_alphas[alpha_id]
                for alpha_id in expected
                if isinstance(raw_alphas, Mapping) and alpha_id in raw_alphas
            }
            if isinstance(raw_alphas, Mapping)
            else {}
        )
        report["upstream_candidate_universe"] = (
            dict(upstream_universe) if isinstance(upstream_universe, Mapping) else {}
        )
        report["upstream_alpha_count"] = (
            len(raw_alphas) if isinstance(raw_alphas, Mapping) else 0
        )
        report["alphas"] = alphas
        completed, operational_reasons = assess_shadow_artifact(
            report,
            expected_candidates=expected,
            upstream_exit_code=upstream_exit_code,
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
                    "scope": "ATTESTED_DECISION_SHADOW_PAPER",
                    "expected": list(expected),
                    "expected_count": len(expected),
                    "running_count": len(alphas),
                    "coverage_complete": completed,
                    "full_population": list(population),
                    "full_population_count": len(population),
                    "deferred_to_realtime_workers": list(deferred),
                    "deferred_count": len(deferred),
                    "deferred_reason": "REALTIME_MECHANISM_OWNED_BY_REALTIME_SHADOW_AND_EXECUTION_PAPER",
                },
                "upstream_cycle": {
                    "started_at_ms": started_at_ms,
                    "finished_at_ms": finished_at_ms,
                    "exit_code": upstream_exit_code,
                    "artifact_refreshed": artifact_refreshed,
                    "operational_artifact_accepted": completed,
                    "operational_reasons": list(operational_reasons),
                },
            }
        )
        verdict.update(
            {
                "cycle_completed": completed,
                "shadow_paper_running": completed,
                "candidate_coverage_complete": completed,
                "candidate_count": len(expected),
                "full_population_count": len(population),
                "deferred_realtime_candidate_count": len(deferred),
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
    if completed:
        return SCHEDULE_EXIT_SUCCESS
    # A refreshed but incomplete artifact is normally a current, auditable
    # safety block (for example, an immature candidate has no attested
    # decision).  Preserve a hard failure when the upstream worker itself
    # reports an operational error; the watchdog must not hide that signal.
    if (
        upstream_exit_code == SCHEDULE_EXIT_SAFE_BLOCKED
        or "UPSTREAM_OUTCOME_NOT_OPERATIONALLY_ACCEPTABLE" not in operational_reasons
    ):
        return SCHEDULE_EXIT_SAFE_BLOCKED
    return SCHEDULE_EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["OUTPUT_PATH", "PROFILE_ARGS", "main"]
