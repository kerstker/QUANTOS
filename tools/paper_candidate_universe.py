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
from collections.abc import Mapping
from pathlib import Path
from typing import Any


STATIC_ALPHA_IDS: tuple[str, ...] = (
    "carry_oi_majors",
    "crowd_contra",
    "top_contra",
)
DEFAULT_FORWARD_STATUS = Path("data/cache/forward_track_status.json")
DEFAULT_EVALUATIONS = Path("data/cache/forward_alpha_evaluations.json")


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


def paper_candidate_ids(
    *,
    forward_status: str | Path = DEFAULT_FORWARD_STATUS,
    evaluations: str | Path = DEFAULT_EVALUATIONS,
) -> tuple[str, ...]:
    """Return static candidates followed by all artifact-attested challengers.

    The union is intentional: a candidate can be evaluated immediately after
    discovery, before the next forward status refresh has persisted it.  The
    candidate still must pass the downstream pending-decision, maturity, and
    full-risk gates before it can create a paper fill.
    """
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
) -> tuple[str, ...]:
    return tuple(
        alpha_id
        for alpha_id in paper_candidate_ids(
            forward_status=forward_status,
            evaluations=evaluations,
        )
        if alpha_id not in STATIC_ALPHA_IDS
    )


__all__ = [
    "DEFAULT_EVALUATIONS",
    "DEFAULT_FORWARD_STATUS",
    "STATIC_ALPHA_IDS",
    "dynamic_paper_candidate_ids",
    "paper_candidate_ids",
]
