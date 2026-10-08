"""Prospective reference to an original frozen backtest, without authority."""

import copy
import re

from quantos.streaming_identity import exact_stream_identity_hash as digest

SCHEMA = "QUANTOS_GOLD_FROZEN_VALIDATION_POLICY_V1"
REVIEW_SCHEMA = "QUANTOS_GOLD_FROZEN_VALIDATION_REVIEW_V1"
HASH_FIELDS = (
    "review_id",
    "content_hash",
    "project_cut_sha256",
    "invocation_id",
    "native_trial_id",
    "config_hash",
    "statistical_policy_sha256",
    "statistical_report_sha256",
)
REFERENCE_FIELDS = (*HASH_FIELDS, "reviewed_at_ms", "registered_at_ms", "attempt_count", "unique_definition_count")
FIELDS = (
    "schema",
    "reference",
    "candidate_id",
    "strategy_definition_sha256",
    "declared_at_ms",
    "series_start_ms",
    "economic_authority",
    "submission_enabled",
)


def require(ok, reason):
    if not ok:
        raise ValueError("GOLD_FROZEN_VALIDATION_" + reason)


def hashed(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None, "HASH")
    return value


def timestamp(value):
    require(type(value) is int and 0 <= value < 2**63, "TIME")
    return value


def validate(value):
    require(type(value) is dict and set(value) == set(FIELDS) and value["schema"] == SCHEMA, "FIELDS")
    ref = value["reference"]
    require(type(ref) is dict and set(ref) == set(REFERENCE_FIELDS), "REFERENCE")
    for field in HASH_FIELDS:
        hashed(ref[field])
    hashed(value["strategy_definition_sha256"])
    require(type(value["candidate_id"]) is str and 0 < len(value["candidate_id"]) <= 256, "CANDIDATE")
    for field in ("attempt_count", "unique_definition_count"):
        require(type(ref[field]) is int and 1 <= ref[field] <= 100_000, "POPULATION")
    require(ref["unique_definition_count"] <= ref["attempt_count"], "POPULATION")
    require(
        timestamp(ref["reviewed_at_ms"])
        <= timestamp(ref["registered_at_ms"])
        < timestamp(value["declared_at_ms"])
        < timestamp(value["series_start_ms"]),
        "PROSPECTIVE_TIME",
    )
    require(value["economic_authority"] is False and value["submission_enabled"] is False, "AUTHORITY")
    return value


def declare(*, reference, candidate_id, strategy_definition_sha256, declared_at_ms, series_start_ms):
    return validate(
        dict(
            schema=SCHEMA,
            reference=copy.deepcopy(reference),
            candidate_id=candidate_id,
            strategy_definition_sha256=strategy_definition_sha256,
            declared_at_ms=declared_at_ms,
            series_start_ms=series_start_ms,
            economic_authority=False,
            submission_enabled=False,
        )
    )


def policy_sha256(value):
    return digest(validate(value))


def validate_review(value, *, policy, subject, candidate_id, native_result_hash, project_cut_sha256, snapshot_as_of_ms):
    validate(policy)
    require(
        type(value) is dict
        and value.get("schema") == REVIEW_SCHEMA
        and value["review_sha256"] == digest({k: v for k, v in value.items() if k != "review_sha256"}),
        "REVIEW",
    )
    require(
        value["policy_sha256"] == policy_sha256(policy)
        and value["reference"] == policy["reference"]
        and value["candidate_id"] == candidate_id == policy["candidate_id"]
        and all(value[k] == subject[k] for k in ("invocation_id", "native_trial_id", "config_hash"))
        and value["native_result_hash"] == native_result_hash
        and value["project_cut_sha256"] == project_cut_sha256
        and value["snapshot_as_of_ms"] == snapshot_as_of_ms,
        "REVIEW_BINDING",
    )
    require(
        value["status"] == "AVAILABLE"
        and value["selection_history_status"] == "COMPLETE_RETAINED_PREFIX"
        and value["frozen_attempt_count"] == policy["reference"]["attempt_count"]
        and value["frozen_unique_definition_count"] == policy["reference"]["unique_definition_count"]
        and type(value["validation_as_of_ms"]) is int
        and 0 <= value["validation_as_of_ms"] <= policy["reference"]["reviewed_at_ms"] <= snapshot_as_of_ms,
        "REVIEW_HISTORY",
    )
    require(
        all(
            value[k] is False
            for k in ("economic_authority", "submission_enabled", "independent_oos_verified", "promotion_approved")
        ),
        "REVIEW_AUTHORITY",
    )
    return value
