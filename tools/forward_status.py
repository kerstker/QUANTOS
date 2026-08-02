"""Normalize the append-only forward-paper status contract.

The release worker added attestation fields to the forward ledger status while
older read-only reports still consumed ``pending_decisions`` and
``quality_evidence_current``.  Keep the compatibility logic in one pure module
so a successful scheduled run cannot be rendered as missing evidence.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ALPHA_IDS = ("carry_oi_majors", "crowd_contra", "top_contra")


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def alpha_forward_status(
    status: Mapping[str, Any], alpha_id: str
) -> dict[str, Any]:
    """Return one Alpha's forward fields across legacy and attested schemas."""
    alphas = status.get("alphas")
    row = alphas.get(alpha_id) if isinstance(alphas, Mapping) else None
    row = row if isinstance(row, Mapping) else {}

    attested_pending = _int_or_none(row.get("attested_pending_decisions"))
    legacy_pending = _int_or_none(row.get("pending_decisions"))
    pending = attested_pending if attested_pending is not None else legacy_pending
    return {
        "n_forward_periods": _int_or_none(row.get("n_forward_periods")),
        # ``pending_decisions`` remains the stable report field.  When the
        # attested worker is present it is explicitly the attested count.
        "pending_decisions": pending,
        "attested_pending_decisions": attested_pending,
        "legacy_unattested_decisions": _int_or_none(
            row.get("legacy_unattested_decisions")
        ),
        "evidence_state": row.get("evidence_state"),
        "paper_sharpe": row.get("paper_sharpe"),
        "last_decision_ts": row.get("last_decision_ts"),
    }


def refresh_evidence_available(status: Mapping[str, Any]) -> bool:
    """Accept the current attested name and the pre-attestation name."""
    return bool(
        status.get("quality_evidence_current") is True
        or status.get("refresh_evidence_available") is True
    )


def forward_evidence_is_current(
    status: Mapping[str, Any], gonogo: Mapping[str, Any]
) -> bool:
    """Return whether a Go/No-Go snapshot describes the current forward run."""
    evidence = gonogo.get("forward_evidence")
    if not isinstance(evidence, Mapping) or evidence.get("present") is not True:
        return False
    status_ran_at = _int_or_none(status.get("ran_at"))
    evidence_ran_at = _int_or_none(evidence.get("ran_at"))
    if status_ran_at is None:
        return True
    # A present snapshot with no bound forward run is not strong enough to
    # prove current evidence once the attested worker has emitted a run.
    return evidence_ran_at is not None and evidence_ran_at >= status_ran_at


__all__ = [
    "ALPHA_IDS",
    "alpha_forward_status",
    "forward_evidence_is_current",
    "refresh_evidence_available",
]
