"""Pure preparation and semantic validation for legacy evaluation artifacts.

This module deliberately owns the exact v2 JSON and hash derivation used by
both the registry facade and the SQLite store.  It performs no IO and has no
transaction authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum

from quantos.contracts.hashing import short_hash, stable_hash
from services.research.ports.persistence import (
    EvaluationArtifactWrite,
    EvaluationAuditIntent,
    ExperimentStoreError,
)


def _artifact_error(detail: str) -> ExperimentStoreError:
    return ExperimentStoreError(f"evaluation artifact is inconsistent: {detail}")


def _payload_value(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str) or not value:
        raise _artifact_error(f"{field} must be a non-empty string")
    return value


def _derived_values(
    payload: Mapping[str, object],
    dataset_version: str,
) -> tuple[str, str, str, str]:
    alpha_id = _payload_value(payload, "alpha_id")
    reproducibility_hash = _payload_value(payload, "reproducibility_hash")
    verdict = _payload_value(payload, "verdict")
    content_hash = stable_hash(
        {"evaluation": dict(payload), "dataset_version": dataset_version}
    )
    run_id = short_hash(
        {
            "alpha_id": alpha_id,
            "repro": reproducibility_hash,
            "dataset_version": dataset_version,
        }
    )
    return run_id, alpha_id, verdict, content_hash


def _audit_intent(
    *,
    run_id: str,
    alpha_id: str,
    verdict: str,
    content_hash: str,
    recorded_ts: int,
) -> EvaluationAuditIntent:
    return EvaluationAuditIntent(
        event_id=f"exp-{run_id}",
        actor="experiment-registry",
        action="EXPERIMENT_RECORDED",
        timestamp=recorded_ts,
        target=alpha_id,
        metadata={
            "run_id": run_id,
            "content_hash": content_hash,
            "verdict": verdict,
        },
    )


def prepare_evaluation_artifact(
    evaluation,
    dataset_version: str,
    recorded_ts: int,
) -> EvaluationArtifactWrite:
    """Prepare byte-identical legacy v2 evaluation JSON, hashes, and audit intent."""
    if not isinstance(dataset_version, str) or not dataset_version:
        raise ValueError("dataset_version must be a non-empty string")
    if (
        isinstance(recorded_ts, bool)
        or not isinstance(recorded_ts, int)
        or recorded_ts < 0
    ):
        raise ValueError("recorded_ts must be a non-negative integer")
    try:
        payload = evaluation.model_dump()
    except AttributeError as exc:
        raise TypeError("evaluation must expose model_dump()") from exc
    if not isinstance(payload, Mapping):
        raise TypeError("evaluation.model_dump() must return a mapping")
    payload = dict(payload)
    try:
        run_id, alpha_id, verdict, content_hash = _derived_values(
            payload, dataset_version
        )
    except ExperimentStoreError as exc:
        raise ValueError(str(exc)) from exc
    evaluation_json = json.dumps(payload, sort_keys=True, default=str)
    return EvaluationArtifactWrite(
        run_id=run_id,
        alpha_id=alpha_id,
        dataset_version=dataset_version,
        recorded_ts=recorded_ts,
        verdict=verdict,
        content_hash=content_hash,
        evaluation_json=evaluation_json,
        audit_intent=_audit_intent(
            run_id=run_id,
            alpha_id=alpha_id,
            verdict=verdict,
            content_hash=content_hash,
            recorded_ts=recorded_ts,
        ),
    )


def validate_evaluation_artifact_write(
    write: EvaluationArtifactWrite,
) -> EvaluationArtifactWrite:
    """Re-derive every semantic field before a locked primitive may issue SQL.

    ``audit_intent=None`` remains supported for the historical low-level store
    API.  When an intent is supplied, it must be exactly the legacy derivation.
    """
    if not isinstance(write, EvaluationArtifactWrite):
        raise TypeError("write must be an EvaluationArtifactWrite")
    if not isinstance(write.dataset_version, str) or not write.dataset_version:
        raise _artifact_error("dataset_version must be a non-empty string")
    if (
        isinstance(write.recorded_ts, bool)
        or not isinstance(write.recorded_ts, int)
        or write.recorded_ts < 0
    ):
        raise _artifact_error("recorded_ts must be a non-negative integer")
    try:
        payload = json.loads(write.evaluation_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _artifact_error("evaluation_json is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise _artifact_error("evaluation_json must encode an object")
    expected_json = json.dumps(payload, sort_keys=True, default=str)
    if write.evaluation_json != expected_json:
        raise _artifact_error("evaluation_json is not the exact legacy serialization")
    run_id, alpha_id, verdict, content_hash = _derived_values(
        payload, write.dataset_version
    )
    mismatches = [
        field
        for field, actual, expected in (
            ("run_id", write.run_id, run_id),
            ("alpha_id", write.alpha_id, alpha_id),
            ("verdict", write.verdict, verdict),
            ("content_hash", write.content_hash, content_hash),
        )
        if actual != expected
    ]
    if mismatches:
        raise _artifact_error("derived field mismatch: " + ", ".join(mismatches))
    if write.audit_intent is not None:
        expected_intent = _audit_intent(
            run_id=run_id,
            alpha_id=alpha_id,
            verdict=verdict,
            content_hash=content_hash,
            recorded_ts=write.recorded_ts,
        )
        intent = write.audit_intent
        try:
            equal = (
                intent.event_id == expected_intent.event_id
                and intent.actor == expected_intent.actor
                and intent.action == expected_intent.action
                and intent.timestamp == expected_intent.timestamp
                and intent.target == expected_intent.target
                and dict(intent.metadata) == dict(expected_intent.metadata)
            )
        except Exception as exc:
            raise _artifact_error("audit_intent metadata is not a mapping") from exc
        if not equal:
            raise _artifact_error("audit_intent does not match the legacy derivation")
    return write


__all__ = [
    "prepare_evaluation_artifact",
    "validate_evaluation_artifact_write",
]
