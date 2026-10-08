"""Resolve the current L2 paper-candidate universe from attested artifacts.

The forward tracker is the source of truth for candidates that are actually
being observed.  This small tools-layer adapter keeps that authority out of
the service packages while giving the operator, risk producer, and console a
single deterministic ordering.  Missing or malformed optional artifacts are
an empty dynamic set, never an invented candidate.

This module is paper-only.  It has no lifecycle-promotion, credential, broker,
or order-submission capability.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quantos.contracts.hashing import stable_hash


STATIC_ALPHA_IDS: tuple[str, ...] = (
    "carry_oi_majors",
    "crowd_contra",
    "top_contra",
)
DEFAULT_FORWARD_STATUS = Path("data/cache/forward_track_status.json")
DEFAULT_EVALUATIONS = Path("data/cache/forward_alpha_evaluations.json")
DEFAULT_WORKFLOW = Path("data/cache/alpha_production_workflow.json")


def _read_object(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _ids_from_mapping(value: object) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    return {
        str(alpha_id)
        for alpha_id in value
        if isinstance(alpha_id, str) and alpha_id.strip()
    }


def _workflow_ids(path: str | Path) -> tuple[str, ...] | None:
    resolved = Path(path)
    if not resolved.is_file():
        return None
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("alpha production workflow is unreadable") from exc
    if not isinstance(value, dict):
        raise RuntimeError("alpha production workflow is not an object")
    if (
        value.get("schema_version") != 1
        or value.get("authority") != "ALPHA_PRODUCTION_WORKFLOW"
        or value.get("status") != "VERIFIED"
    ):
        raise RuntimeError("alpha production workflow authority is not verified")
    safety = value.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(name) is not False
        for name in ("live_allowed", "order_submission_allowed")
    ):
        raise RuntimeError("alpha production workflow safety boundary is invalid")
    active = value.get("active_candidate_ids")
    rows = value.get("candidates")
    if (
        not isinstance(active, list)
        or not active
        or active != list(dict.fromkeys(active))
        or any(not isinstance(alpha_id, str) or not alpha_id for alpha_id in active)
        or not isinstance(rows, list)
    ):
        raise RuntimeError("alpha production workflow population is invalid")
    by_id = {
        str(row.get("candidate_id")): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("candidate_id"), str)
    }
    for alpha_id in active:
        row = by_id.get(alpha_id)
        identity = row.get("identity") if isinstance(row, Mapping) else None
        if not isinstance(identity, Mapping):
            raise RuntimeError(f"workflow identity is missing: {alpha_id}")
        if identity.get("candidate_id") != alpha_id:
            raise RuntimeError(f"workflow candidate identity mismatch: {alpha_id}")
        if row.get("identity_hash") != stable_hash(dict(identity)):
            raise RuntimeError(f"workflow candidate identity hash is invalid: {alpha_id}")
    return tuple(active)


def paper_candidate_ids(
    *,
    forward_status: str | Path = DEFAULT_FORWARD_STATUS,
    evaluations: str | Path = DEFAULT_EVALUATIONS,
    workflow: str | Path = DEFAULT_WORKFLOW,
) -> tuple[str, ...]:
    """Return static candidates followed by all artifact-attested challengers.

    The union is intentional: a candidate can be evaluated immediately after
    discovery, before the next forward status refresh has persisted it.  The
    candidate still must pass the downstream pending-decision, maturity, and
    full-risk gates before it can create a paper fill.
    """
    authoritative = _workflow_ids(workflow)
    if authoritative is not None:
        discovered = set(authoritative)
        missing_static = set(STATIC_ALPHA_IDS) - discovered
        if missing_static:
            raise RuntimeError(
                "alpha production workflow omitted static candidates: "
                + ",".join(sorted(missing_static))
            )
        return STATIC_ALPHA_IDS + tuple(
            sorted(alpha_id for alpha_id in discovered if alpha_id not in STATIC_ALPHA_IDS)
        )

    status = _read_object(forward_status)
    evaluation = _read_object(evaluations)
    discovered = set(STATIC_ALPHA_IDS)
    discovered.update(_ids_from_mapping(status.get("alphas")))
    discovered.update(_ids_from_mapping(evaluation.get("alphas")))
    return STATIC_ALPHA_IDS + tuple(
        sorted(alpha_id for alpha_id in discovered if alpha_id not in STATIC_ALPHA_IDS)
    )


def dynamic_paper_candidate_ids(
    *,
    forward_status: str | Path = DEFAULT_FORWARD_STATUS,
    evaluations: str | Path = DEFAULT_EVALUATIONS,
    workflow: str | Path = DEFAULT_WORKFLOW,
) -> tuple[str, ...]:
    return tuple(
        alpha_id
        for alpha_id in paper_candidate_ids(
            forward_status=forward_status,
            evaluations=evaluations,
            workflow=workflow,
        )
        if alpha_id not in STATIC_ALPHA_IDS
    )


def _workflow_realtime_ids(
    workflow: str | Path,
    candidate_ids: Sequence[str],
) -> set[str]:
    """Return candidates owned by the realtime-mechanism workers.

    ``paper_candidate_ids`` remains the complete production-workflow
    population.  This helper only derives an execution scope from the same
    verified identity document; it never infers ownership from an ``rt_``
    naming convention.  A caller-supplied subset is accepted for deterministic
    tests and adapters, while the production callers pass the verified full
    population.
    """
    authoritative = _workflow_ids(workflow)
    if authoritative is None:
        return set()
    document = _read_object(workflow)
    rows = document.get("candidates")
    by_id = {
        str(row.get("candidate_id")): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("candidate_id"), str)
    } if isinstance(rows, list) else {}
    realtime_ids: set[str] = set()
    for alpha_id in candidate_ids:
        row = by_id.get(str(alpha_id))
        # A caller-supplied synthetic subset may not exist in the live
        # workflow fixture.  It is treated as a non-realtime test candidate;
        # the production population has already been fully validated by
        # paper_candidate_ids/_workflow_ids above.
        if not isinstance(row, Mapping):
            continue
        identity = row.get("identity")
        if isinstance(identity, Mapping) and identity.get("definition_kind") == "REALTIME_MECHANISM":
            realtime_ids.add(str(alpha_id))
    return realtime_ids


def shadow_paper_candidate_ids(
    *,
    candidate_ids: Sequence[str] | None = None,
    forward_status: str | Path = DEFAULT_FORWARD_STATUS,
    evaluations: str | Path = DEFAULT_EVALUATIONS,
    workflow: str | Path = DEFAULT_WORKFLOW,
) -> tuple[str, ...]:
    """Return the candidates owned by the routed shadow-paper worker.

    The verified workflow marks realtime microstructure candidates with
    ``definition_kind=REALTIME_MECHANISM``.  Those candidates are observed by
    the independent realtime forward-shadow and execution-paper workers and
    must not make the daily attested-decision shadow worker fail closed.  All
    other candidates, including static and daily Challenger identities,
    remain in this worker's scope.

    When the workflow artifact is absent, the legacy/static universe is
    returned unchanged; an existing but invalid workflow still raises through
    ``paper_candidate_ids``/``_workflow_ids`` rather than silently widening
    the scope.
    """
    all_ids = tuple(
        str(alpha_id)
        for alpha_id in (
            candidate_ids
            if candidate_ids is not None
            else paper_candidate_ids(
                forward_status=forward_status,
                evaluations=evaluations,
                workflow=workflow,
            )
        )
        if isinstance(alpha_id, str) and alpha_id
    )
    realtime_ids = _workflow_realtime_ids(workflow, all_ids)
    return tuple(alpha_id for alpha_id in all_ids if alpha_id not in realtime_ids)


def realtime_mechanism_candidate_ids(
    *,
    candidate_ids: Sequence[str] | None = None,
    forward_status: str | Path = DEFAULT_FORWARD_STATUS,
    evaluations: str | Path = DEFAULT_EVALUATIONS,
    workflow: str | Path = DEFAULT_WORKFLOW,
) -> tuple[str, ...]:
    """Return the verified realtime-mechanism handoff scope."""
    all_ids = tuple(
        str(alpha_id)
        for alpha_id in (
            candidate_ids
            if candidate_ids is not None
            else paper_candidate_ids(
                forward_status=forward_status,
                evaluations=evaluations,
                workflow=workflow,
            )
        )
        if isinstance(alpha_id, str) and alpha_id
    )
    realtime_ids = _workflow_realtime_ids(workflow, all_ids)
    return tuple(alpha_id for alpha_id in all_ids if alpha_id in realtime_ids)


__all__ = [
    "DEFAULT_EVALUATIONS",
    "DEFAULT_FORWARD_STATUS",
    "DEFAULT_WORKFLOW",
    "STATIC_ALPHA_IDS",
    "dynamic_paper_candidate_ids",
    "paper_candidate_ids",
    "realtime_mechanism_candidate_ids",
    "shadow_paper_candidate_ids",
]
