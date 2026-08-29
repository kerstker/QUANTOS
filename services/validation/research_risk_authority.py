"""Pure admission contract for complete research-risk evidence.

This module intentionally owns no engines and performs no I/O.  Keeping the
contract below the alpha and risk service domains lets both layers agree on
the exact nine-engine result without introducing a cross-domain cycle.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


FULL_RESEARCH_RISK_ENGINES = frozenset(
    {
        "market",
        "liquidity",
        "concentration",
        "correlation",
        "drawdown",
        "regime",
        "stress",
        "capacity",
        "model_alpha",
    }
)


def full_research_risk_approved(result: Mapping[str, Any]) -> bool:
    """Return whether ``result`` satisfies the exact full 9/9 contract."""

    engines_run = result.get("engines_run")
    return bool(
        result.get("decision") == "RISK_APPROVED"
        and result.get("status") == "APPROVED"
        and result.get("provenance") == "REAL_RESEARCH_RISK_FULL"
        and result.get("gates_run") == len(FULL_RESEARCH_RISK_ENGINES)
        and result.get("gates_passed") == len(FULL_RESEARCH_RISK_ENGINES)
        and isinstance(engines_run, Mapping)
        and set(engines_run) == FULL_RESEARCH_RISK_ENGINES
        and all(value is True for value in engines_run.values())
        and result.get("engines_skipped") == {}
        and result.get("engines_pending") == {}
    )


__all__ = ["FULL_RESEARCH_RISK_ENGINES", "full_research_risk_approved"]
