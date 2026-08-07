"""Versioned, replayable evidence for Probability of Backtest Overfitting.

This module wraps the existing validated CSCV implementation; it does not carry a
second PBO formula.  It is pure and deterministic: no I/O, RNG, wall clock, or
network access.
"""

from __future__ import annotations

import math
from typing import Sequence

from quantos.contracts.hashing import stable_hash
from quantos.contracts.validation import BatchPboEvidence, PboAlignment
from services.validation.deflated_sharpe import pbo_cscv


def create_batch_pbo_evidence(
    *,
    candidate_definition_hashes: Sequence[str],
    performance_matrix: Sequence[Sequence[float]],
    alignment: PboAlignment,
    aligned_period_count: int,
    aligned_event_times: Sequence[int] = (),
) -> BatchPboEvidence:
    """Build evidence from the exact matrix consumed by ``pbo_cscv``."""

    hashes = tuple(candidate_definition_hashes)
    matrix = tuple(tuple(float(value) for value in row) for row in performance_matrix)
    times = tuple(int(value) for value in aligned_event_times)
    if alignment == PboAlignment.EVENT_TIME_INTERSECTION:
        calendar_hash = stable_hash(list(times))
    else:
        calendar_hash = stable_hash(
            {
                "alignment": alignment.value,
                "aligned_period_count": aligned_period_count,
            }
        )
    pbo_value=pbo_cscv(matrix)
    commitment=stable_hash({
        "candidate_definition_hashes": list(hashes),
        "performance_matrix": [list(row) for row in matrix],
        "alignment": alignment.value,
        "aligned_period_count": aligned_period_count,
        "aligned_event_times": list(times),
        "pbo": pbo_value,
    })
    evidence = BatchPboEvidence(
        candidate_definition_hashes=hashes,
        performance_matrix=matrix,
        alignment=alignment,
        aligned_period_count=aligned_period_count,
        aligned_event_times=times,
        aligned_event_times_sha256=calendar_hash,
        pbo=pbo_value,
        identity_commitment=commitment,
    )
    if not verify_batch_pbo_evidence(evidence):
        raise ValueError("constructed PBO evidence did not replay")
    return evidence


def verify_batch_pbo_evidence(evidence: object) -> bool:
    """Revalidate structure/calendar binding and replay the existing CSCV math."""

    try:
        if not isinstance(evidence, BatchPboEvidence):
            return False
        validated = BatchPboEvidence.model_validate(evidence.model_dump(mode="python"))
        if not validated.identity_commitment:
            return False
        expected=stable_hash({
            "candidate_definition_hashes": list(validated.candidate_definition_hashes),
            "performance_matrix": [list(row) for row in validated.performance_matrix],
            "alignment": validated.alignment.value,
            "aligned_period_count": validated.aligned_period_count,
            "aligned_event_times": list(validated.aligned_event_times),
            "pbo": validated.pbo,
        })
        if validated.identity_commitment != expected:
            return False
        recomputed = pbo_cscv(validated.performance_matrix)
        return math.isclose(recomputed, validated.pbo, rel_tol=0.0, abs_tol=1e-12)
    except (TypeError, ValueError, ArithmeticError):
        return False


__all__ = ["create_batch_pbo_evidence", "verify_batch_pbo_evidence"]
