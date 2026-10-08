"""Deterministic PBO v3 evidence derived from timestamped definition returns."""
from __future__ import annotations

import math
from typing import Mapping, Sequence

from quantos.contracts.canonical import canonical_sha256
from quantos.contracts.hashing import stable_hash
from quantos.contracts.validation import (
    BatchPboEvidence,
    PboAlignment,
    PboDefinitionReturnSeries,
    PboReturnObservation,
)
from services.validation.deflated_sharpe import pbo_cscv


def _body(*, hashes, series, matrix, times, pbo):
    return {
        "schema_version": 3,
        "algorithm": "CSCV_V1",
        "candidate_definition_hashes": list(hashes),
        "return_series": [item.model_dump(mode="python") for item in series],
        "performance_matrix": [list(row) for row in matrix],
        "alignment": PboAlignment.EVENT_TIME_INTERSECTION.value,
        "aligned_period_count": len(times),
        "aligned_event_times": list(times),
        "pbo": pbo,
    }


def create_batch_pbo_evidence(
    *,
    return_series_by_definition: Mapping[str, Sequence[tuple[int, float]]],
) -> BatchPboEvidence:
    """Build CSCV evidence from the only trusted input: timestamped returns."""
    if not isinstance(return_series_by_definition, Mapping):
        raise TypeError("PBO return population must be a mapping")
    hashes = tuple(sorted(return_series_by_definition))
    if len(hashes) < 2 or len(set(hashes)) != len(hashes):
        raise ValueError("PBO requires at least two distinct candidate definitions")
    series = []
    calendars = []
    by_hash = {}
    for definition_hash in hashes:
        raw = tuple(return_series_by_definition[definition_hash])
        observations = []
        for item in raw:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise TypeError("PBO observations must be event-time/value pairs")
            event_time, value = item
            if type(event_time) is not int:
                raise TypeError("PBO event times must be exact integers")
            if type(value) is not float:
                raise TypeError("PBO returns must be exact float64 values")
            observations.append(PboReturnObservation(event_time=event_time, value=value))
        times = tuple(item.event_time for item in observations)
        if not times or tuple(sorted(set(times))) != times:
            raise ValueError("PBO return event times must be sorted and unique")
        digest = canonical_sha256([item.model_dump(mode="python") for item in observations])
        retained = PboDefinitionReturnSeries(
            definition_hash=definition_hash,
            observations=tuple(observations),
            observations_sha256=digest,
        )
        series.append(retained)
        calendars.append(set(times))
        by_hash[definition_hash] = {item.event_time: item.value for item in observations}
    times = tuple(sorted(set.intersection(*calendars)))
    if len(times) < 4:
        raise ValueError("PBO requires at least four aligned periods")
    block_count = min(8, len(times))
    if block_count % 2:
        block_count -= 1
    matrix = tuple(
        tuple(
            math.fsum(
                by_hash[definition_hash][event_time]
                for event_time in times[len(times) * block // block_count:len(times) * (block + 1) // block_count]
            )
            for definition_hash in hashes
        )
        for block in range(block_count)
    )
    pbo = pbo_cscv(matrix)
    body = _body(hashes=hashes, series=series, matrix=matrix, times=times, pbo=pbo)
    evidence = BatchPboEvidence(
        **body,
        aligned_event_times_sha256=stable_hash(list(times)),
        identity_commitment=canonical_sha256(body),
    )
    if not verify_batch_pbo_evidence(evidence):
        raise ValueError("constructed PBO evidence did not replay")
    return evidence


def verify_batch_pbo_evidence(evidence: object) -> bool:
    """Rebuild calendar, matrix, commitments, and CSCV from retained returns."""
    try:
        if not isinstance(evidence, BatchPboEvidence):
            return False
        validated = BatchPboEvidence.model_validate(evidence.model_dump(mode="python"))
        if validated.schema_version != 3 or validated.alignment != PboAlignment.EVENT_TIME_INTERSECTION:
            return False
        source = {
            series.definition_hash: tuple((item.event_time, item.value) for item in series.observations)
            for series in validated.return_series
        }
        hashes = tuple(sorted(source))
        calendars = [set(event_time for event_time, _ in source[key]) for key in hashes]
        times = tuple(sorted(set.intersection(*calendars)))
        block_count = min(8, len(times))
        if block_count % 2:
            block_count -= 1
        by_hash = {key: dict(source[key]) for key in hashes}
        matrix = tuple(
            tuple(math.fsum(by_hash[key][t] for t in times[len(times)*b//block_count:len(times)*(b+1)//block_count]) for key in hashes)
            for b in range(block_count)
        )
        pbo = pbo_cscv(matrix)
        body = _body(hashes=hashes, series=validated.return_series, matrix=matrix, times=times, pbo=pbo)
        return (
            validated.candidate_definition_hashes == hashes
            and validated.aligned_event_times == times
            and validated.aligned_event_times_sha256 == stable_hash(list(times))
            and validated.performance_matrix == matrix
            and math.isclose(validated.pbo, pbo, rel_tol=0.0, abs_tol=1e-12)
            and validated.identity_commitment == canonical_sha256(body)
        )
    except (TypeError, ValueError, ArithmeticError):
        return False


__all__ = ["create_batch_pbo_evidence", "verify_batch_pbo_evidence"]
