import math

import pytest

from quantos.contracts.hashing import stable_hash
from services.validation.pbo_evidence import (
    create_batch_pbo_evidence,
    verify_batch_pbo_evidence,
)


HASHES = tuple(stable_hash({"candidate": index}) for index in range(3))
TIMES = tuple(range(100, 900, 100))
RETURNS = {
    HASHES[0]: tuple((t, v) for t, v in zip(TIMES, (0.01, 0.02, -0.01, 0.03, 0.02, -0.02, 0.01, 0.04))),
    HASHES[1]: tuple((t, v) for t, v in zip(TIMES, (0.00, 0.01, 0.02, -0.01, 0.03, 0.01, -0.02, 0.02))),
    HASHES[2]: tuple((t, v) for t, v in zip(TIMES, (-0.01, 0.03, 0.01, 0.00, -0.01, 0.02, 0.04, -0.02))),
}


def _evidence():
    return create_batch_pbo_evidence(return_series_by_definition=RETURNS)


def test_pbo_v3_is_deterministic_definition_bound_and_replayable():
    first = _evidence()
    second = create_batch_pbo_evidence(return_series_by_definition=dict(reversed(tuple(RETURNS.items()))))
    assert first == second
    assert first.schema_version == 3
    assert first.candidate_definition_hashes == tuple(sorted(HASHES))
    assert first.aligned_event_times == TIMES
    assert first.aligned_event_times_sha256 == stable_hash(list(TIMES))
    assert first.performance_matrix == tuple(
        tuple((dict(RETURNS[h])[t]) for h in sorted(HASHES))
        for t in TIMES
    )
    assert verify_batch_pbo_evidence(first) is True


def test_pbo_v3_uses_event_time_intersection_and_even_block_rule():
    source = {key: values[index:] if index else values for index, (key, values) in enumerate(RETURNS.items())}
    evidence = create_batch_pbo_evidence(return_series_by_definition=source)
    assert evidence.aligned_event_times == TIMES[2:]
    assert len(evidence.performance_matrix) == 6


def test_pbo_v3_detects_source_matrix_calendar_and_scalar_tamper():
    evidence = _evidence()
    first_series = evidence.return_series[0]
    changed_source = evidence.model_copy(update={"return_series": (
        first_series.model_copy(update={"observations": (
            first_series.observations[0].model_copy(update={"value": 99.0}),
            *first_series.observations[1:],
        )}),
        *evidence.return_series[1:],
    )})
    changed_matrix = evidence.model_copy(update={"performance_matrix": ((99.0, *evidence.performance_matrix[0][1:]), *evidence.performance_matrix[1:])})
    changed_calendar = evidence.model_copy(update={"aligned_event_times": tuple(range(8))})
    changed_pbo = evidence.model_copy(update={"pbo": 1.0 - evidence.pbo})
    assert verify_batch_pbo_evidence(changed_source) is False
    assert verify_batch_pbo_evidence(changed_matrix) is False
    assert verify_batch_pbo_evidence(changed_calendar) is False
    assert verify_batch_pbo_evidence(changed_pbo) is False


def test_pbo_v3_rejects_duplicate_times_non_float_returns_and_short_intersection():
    duplicate = dict(RETURNS)
    duplicate[HASHES[0]] = (*duplicate[HASHES[0]], duplicate[HASHES[0]][-1])
    with pytest.raises(ValueError, match="sorted and unique"):
        create_batch_pbo_evidence(return_series_by_definition=duplicate)
    non_float = dict(RETURNS)
    non_float[HASHES[0]] = ((100, True), *non_float[HASHES[0]][1:])
    with pytest.raises(TypeError, match="exact float"):
        create_batch_pbo_evidence(return_series_by_definition=non_float)
    short = {key: values[: 4 - index] for index, (key, values) in enumerate(RETURNS.items())}
    with pytest.raises(ValueError, match="at least four aligned periods"):
        create_batch_pbo_evidence(return_series_by_definition=short)


def test_pbo_v3_builder_does_not_accept_legacy_matrix_inputs():
    with pytest.raises(TypeError):
        create_batch_pbo_evidence(candidate_definition_hashes=HASHES, performance_matrix=((0.1, 0.2, 0.3),) * 4)
