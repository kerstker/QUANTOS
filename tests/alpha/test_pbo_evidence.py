import math

import pytest

from quantos.contracts.alpha import PboAlignment
from quantos.contracts.hashing import stable_hash
from services.validation.pbo_evidence import (
    create_batch_pbo_evidence,
    verify_batch_pbo_evidence,
)


HASHES = tuple(stable_hash({"candidate": index}) for index in range(3))
PERFORMANCE = (
    (0.04, 0.01, -0.02),
    (-0.03, 0.02, 0.01),
    (0.05, -0.01, 0.02),
    (-0.01, 0.03, -0.02),
)
TIMES = tuple(range(100, 900, 100))


def _evidence():
    return create_batch_pbo_evidence(
        candidate_definition_hashes=HASHES,
        performance_matrix=PERFORMANCE,
        alignment=PboAlignment.EVENT_TIME_INTERSECTION,
        aligned_period_count=len(TIMES),
        aligned_event_times=TIMES,
    )


def test_pbo_evidence_is_deterministic_and_replayable():
    first = _evidence()
    second = _evidence()

    assert first == second
    assert first.evidence_hash == second.evidence_hash
    assert first.candidate_definition_hashes == HASHES
    assert first.aligned_period_count == len(TIMES)
    assert first.aligned_event_times_sha256 == stable_hash(list(TIMES))
    assert verify_batch_pbo_evidence(first) is True


def test_pbo_evidence_detects_scalar_and_matrix_tamper():
    evidence = _evidence()
    changed_pbo = evidence.model_copy(update={"pbo": 1.0 - evidence.pbo})
    changed_matrix = evidence.model_copy(
        update={
            "performance_matrix": (
                (99.0, *evidence.performance_matrix[0][1:]),
                *evidence.performance_matrix[1:],
            )
        }
    )

    assert verify_batch_pbo_evidence(changed_pbo) is False
    assert verify_batch_pbo_evidence(changed_matrix) is False
    assert changed_matrix.evidence_hash != evidence.evidence_hash


def test_pbo_evidence_rejects_duplicate_candidates():
    with pytest.raises(ValueError, match="distinct candidate"):
        create_batch_pbo_evidence(
            candidate_definition_hashes=(HASHES[0], HASHES[0]),
            performance_matrix=tuple(row[:2] for row in PERFORMANCE),
            alignment=PboAlignment.POSITIONAL_LEGACY,
            aligned_period_count=8,
        )


@pytest.mark.parametrize(
    "matrix",
    (
        ((0.1, 0.2), (0.3,)),
        ((0.1, 0.2), (math.nan, 0.3)),
        tuple((0.1, 0.2) for _ in range(9)),
    ),
)
def test_pbo_evidence_rejects_invalid_matrix(matrix):
    with pytest.raises(ValueError):
        create_batch_pbo_evidence(
            candidate_definition_hashes=HASHES[:2],
            performance_matrix=matrix,
            alignment=PboAlignment.POSITIONAL_LEGACY,
            aligned_period_count=8,
        )


def test_event_time_alignment_requires_exact_sorted_calendar():
    with pytest.raises(ValueError, match="aligned event times"):
        create_batch_pbo_evidence(
            candidate_definition_hashes=HASHES,
            performance_matrix=PERFORMANCE,
            alignment=PboAlignment.EVENT_TIME_INTERSECTION,
            aligned_period_count=8,
            aligned_event_times=(100, 200, 200, 400),
        )
