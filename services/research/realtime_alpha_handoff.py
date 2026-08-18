"""Fail-closed handoff from realtime research into the canonical workflow.

This module freezes an autonomous realtime hypothesis as a canonical
``CandidateIdentity``.  It has no filesystem, database, clock, broker,
credential, lifecycle-promotion or order-submission seam; the tooling boundary
supplies already-read append-only registrations and a discovery document.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import math
import re
from typing import Any, Mapping, Sequence

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from services.research.alpha_production_workflow import (
    CandidateDefinitionKind,
    CandidateIdentity,
    MaterialChangeClass,
)
from services.research.realtime_alpha_discovery import (
    LABEL_POLICY,
    RealtimeAlphaPolicy,
)


REALTIME_RESEARCH_POLICY_VERSION = "realtime-alpha-research-policy.v1"
REALTIME_EVALUATOR_VERSION = "realtime-hypothesis-evaluator.v2"
REALTIME_SIGNAL_SPEC_VERSION = "realtime-candidate-signal-spec.v1"
REALTIME_UNIVERSE = "binance_usdm_14_realtime_public"

_SHA256 = re.compile(r"[0-9a-f]{64}")


class RealtimeAlphaHandoffError(ValueError):
    """Realtime registration/discovery evidence cannot be frozen safely."""


def canonical_realtime_research_policy(
    policy: RealtimeAlphaPolicy,
    *,
    feature_version: str,
) -> dict[str, Any]:
    """Return the complete content-addressed policy used by discovery."""
    if not isinstance(policy, RealtimeAlphaPolicy):
        raise TypeError("policy must be RealtimeAlphaPolicy")
    if not isinstance(feature_version, str) or not feature_version.strip():
        raise RealtimeAlphaHandoffError("feature_version is required")
    return {
        "policy_version": REALTIME_RESEARCH_POLICY_VERSION,
        "evaluator_version": REALTIME_EVALUATOR_VERSION,
        "feature_version": feature_version,
        "label_policy": LABEL_POLICY,
        **asdict(policy),
    }


def realtime_research_policy_hash(policy: Mapping[str, Any]) -> str:
    validated = _validated_policy(policy)
    return stable_hash(validated)


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RealtimeAlphaHandoffError(f"{name} is required")
    return value


def _required_int(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise RealtimeAlphaHandoffError(f"{name} is invalid")
    return value


def _required_float(value: object, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RealtimeAlphaHandoffError(f"{name} is invalid")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise RealtimeAlphaHandoffError(f"{name} is invalid")
    return result


def _validated_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, Mapping):
        raise RealtimeAlphaHandoffError("realtime research policy is missing")
    payload = dict(policy)
    if payload.get("policy_version") != REALTIME_RESEARCH_POLICY_VERSION:
        raise RealtimeAlphaHandoffError("realtime research policy version is invalid")
    if payload.get("evaluator_version") != REALTIME_EVALUATOR_VERSION:
        raise RealtimeAlphaHandoffError("realtime evaluator version is invalid")
    _required_text(payload.get("feature_version"), "feature_version")
    if payload.get("label_policy") != LABEL_POLICY:
        raise RealtimeAlphaHandoffError("realtime label policy is invalid")
    integer_fields = (
        "exploration_min_span_ms",
        "validation_min_span_ms",
        "shadow_min_span_ms",
        "max_feature_age_ms",
        "min_instruments",
        "min_rows_per_feature",
        "decision_interval_ms",
        "execution_latency_ms",
        "max_label_lateness_ms",
        "min_cross_section",
        "min_evaluation_periods",
    )
    for name in integer_fields:
        _required_int(
            payload.get(name),
            name,
            positive=name
            in {
                "exploration_min_span_ms",
                "validation_min_span_ms",
                "shadow_min_span_ms",
                "min_instruments",
                "min_rows_per_feature",
                "decision_interval_ms",
                "min_cross_section",
                "min_evaluation_periods",
            },
        )
    quantile = _required_float(payload.get("quantile"), "quantile")
    if not 0.0 < quantile <= 0.5:
        raise RealtimeAlphaHandoffError("quantile is invalid")
    _required_float(payload.get("fee_bps"), "fee_bps")
    _required_float(payload.get("slippage_bps"), "slippage_bps")
    if not (
        payload["exploration_min_span_ms"]
        <= payload["validation_min_span_ms"]
        <= payload["shadow_min_span_ms"]
    ):
        raise RealtimeAlphaHandoffError("realtime maturity spans are not monotone")
    expected = set(canonical_realtime_research_policy(
        RealtimeAlphaPolicy(), feature_version=str(payload["feature_version"])
    ))
    if set(payload) != expected:
        raise RealtimeAlphaHandoffError("realtime research policy fields are invalid")
    return payload


def _rows_by_id(rows: object, *, name: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise RealtimeAlphaHandoffError(f"{name} must be a list")
    result: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            raise RealtimeAlphaHandoffError(f"{name} row is invalid")
        candidate_id = _required_text(item.get("candidate_id"), "candidate_id")
        if candidate_id in result:
            raise RealtimeAlphaHandoffError(f"{name} candidate ids are duplicated")
        result[candidate_id] = dict(item)
    return result


def _expected_registration_config_hash(row: Mapping[str, Any]) -> str:
    required = row.get("required_features")
    if not isinstance(required, list) or not required or not all(
        isinstance(item, str) and item for item in required
    ):
        raise RealtimeAlphaHandoffError("required_features are invalid")
    params = {
        "family_id": _required_text(row.get("family_id"), "family_id"),
        "mechanism": _required_text(row.get("mechanism"), "mechanism"),
        "horizon_ms": str(_required_int(row.get("horizon_ms"), "horizon_ms", positive=True)),
        "signal_version": _required_text(row.get("signal_version"), "signal_version"),
        "label_policy": "post_latency_first_known_mid",
    }
    if "lookback_ms" in row:
        params["lookback_ms"] = str(
            _required_int(row.get("lookback_ms"), "lookback_ms", positive=True)
        )
    if row.get("adaptive_feedback") is True:
        params.update(
            {
                "adaptive_feedback": "true",
                "parent_candidate_id": _required_text(
                    row.get("parent_candidate_id"), "parent_candidate_id"
                ),
                "feedback_cutoff_ms": str(
                    _required_int(row.get("feedback_cutoff_ms"), "feedback_cutoff_ms")
                ),
                "mutation": _required_text(row.get("mutation"), "mutation"),
                "fresh_oos_required": "true",
            }
        )
    return AlphaDefinition(
        alpha_id=_required_text(row.get("candidate_id"), "candidate_id"),
        name="canonical handoff verification",
        factor_ids=tuple(sorted(required)),
        universe=REALTIME_UNIVERSE,
        params=params,
    ).definition_hash


def _validate_registration_matches_manifest(
    registration: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    immutable_fields = (
        "candidate_id",
        "family_id",
        "mechanism",
        "horizon_ms",
        "required_features",
        "economic_hypothesis",
        "falsification_standard",
        "signal_version",
        "lookback_ms",
        "adaptive_feedback",
        "parent_candidate_id",
        "feedback_cutoff_ms",
        "mutation",
        "fresh_oos_required",
        "adaptive_generation",
    )
    for name in immutable_fields:
        if registration.get(name) != manifest.get(name):
            raise RealtimeAlphaHandoffError(
                f"realtime registration/manifest mismatch: {manifest.get('candidate_id')}:{name}"
            )
    if registration.get("paper_eligible") is not False:
        raise RealtimeAlphaHandoffError("realtime registration widened paper authority")
    digest = registration.get("record_hash")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise RealtimeAlphaHandoffError("realtime registration record hash is invalid")
    if registration.get("config_hash") != _expected_registration_config_hash(manifest):
        raise RealtimeAlphaHandoffError("realtime registration config hash is invalid")
    _required_int(registration.get("recorded_at"), "recorded_at")


def _validate_safety(document: Mapping[str, Any]) -> None:
    safety = document.get("safety")
    if not isinstance(safety, Mapping) or safety.get("paper_only") is not True:
        raise RealtimeAlphaHandoffError("realtime discovery paper-only boundary is invalid")
    for name in (
        "live_trading_enabled",
        "real_broker_enabled",
        "allow_order_submission",
        "allow_ai_direct_trading",
    ):
        if safety.get(name) is not False:
            raise RealtimeAlphaHandoffError(
                f"realtime discovery widened authority: {name}"
            )
    feedback = document.get("feedback")
    if isinstance(feedback, Mapping):
        for name in (
            "formal_gate_impact",
            "promotion_eligible",
            "live_allowed",
            "order_submission_allowed",
        ):
            if feedback.get(name) is not False:
                raise RealtimeAlphaHandoffError(
                    f"realtime feedback widened authority: {name}"
                )


def _signal_spec(
    row: Mapping[str, Any],
    *,
    research_policy: Mapping[str, Any],
    research_policy_hash: str,
    registration_hash: str,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "schema_version": REALTIME_SIGNAL_SPEC_VERSION,
        "candidate_id": str(row["candidate_id"]),
        "family_id": str(row["family_id"]),
        "mechanism": str(row["mechanism"]),
        "signal_version": str(row["signal_version"]),
        "required_features": list(row["required_features"]),
        "universe": REALTIME_UNIVERSE,
        "decision_interval_ms": int(research_policy["decision_interval_ms"]),
        "label_horizon_ms": int(row["horizon_ms"]),
        "execution_latency_ms": int(research_policy["execution_latency_ms"]),
        "max_label_lateness_ms": int(research_policy["max_label_lateness_ms"]),
        "quantile": float(research_policy["quantile"]),
        "fee_bps": float(research_policy["fee_bps"]),
        "slippage_bps": float(research_policy["slippage_bps"]),
        "label_policy": research_policy["label_policy"],
        "feature_version": research_policy["feature_version"],
        "research_policy_hash": research_policy_hash,
        "registration_record_hash": registration_hash,
    }
    if row.get("adaptive_feedback") is True:
        spec["adaptive_lineage"] = {
            "parent_candidate_id": row["parent_candidate_id"],
            "feedback_cutoff_ms": row["feedback_cutoff_ms"],
            "mutation": row["mutation"],
            "fresh_oos_required": row["fresh_oos_required"],
            "adaptive_generation": row["adaptive_generation"],
        }
    return spec


def build_realtime_candidate_identities(
    registrations: Sequence[Mapping[str, Any]],
    discovery_document: Mapping[str, Any],
) -> tuple[CandidateIdentity, ...]:
    """Freeze the current realtime manifest into canonical workflow identities."""
    if not isinstance(discovery_document, Mapping):
        raise RealtimeAlphaHandoffError("realtime discovery document is invalid")
    document = dict(discovery_document)
    _validate_safety(document)
    manifests = _rows_by_id(document.get("hypotheses"), name="realtime manifest")
    if document.get("manifest_hash") != stable_hash(list(document.get("hypotheses") or [])):
        raise RealtimeAlphaHandoffError("realtime manifest hash is invalid")
    registered = _rows_by_id(registrations, name="realtime registrations")
    missing = sorted(set(manifests) - set(registered))
    if missing:
        raise RealtimeAlphaHandoffError(
            "realtime manifest registrations are missing: " + ",".join(missing)
        )
    policy = _validated_policy(document.get("research_policy"))
    policy_hash = realtime_research_policy_hash(policy)
    if document.get("research_policy_hash") != policy_hash:
        raise RealtimeAlphaHandoffError("realtime research policy hash is invalid")

    identities: list[CandidateIdentity] = []
    ordered_rows = sorted(
        manifests.values(),
        key=lambda row: (row.get("adaptive_feedback") is True, str(row["candidate_id"])),
    )
    roots: dict[str, Mapping[str, Any]] = {}
    for row in ordered_rows:
        registration = registered[str(row["candidate_id"])]
        _validate_registration_matches_manifest(registration, row)
        required = row.get("required_features")
        if not isinstance(required, list) or not required or not all(
            isinstance(item, str) and item for item in required
        ):
            raise RealtimeAlphaHandoffError("required_features are invalid")
        if len(required) != len(set(required)):
            raise RealtimeAlphaHandoffError("required_features are duplicated")
        adaptive = row.get("adaptive_feedback") is True
        predecessor_id: str | None = None
        change_class = MaterialChangeClass.NEW_CANDIDATE
        if adaptive:
            predecessor_id = _required_text(
                row.get("parent_candidate_id"), "parent_candidate_id"
            )
            parent = roots.get(predecessor_id)
            if parent is None:
                raise RealtimeAlphaHandoffError("adaptive parent is not a current root")
            if any(
                row.get(name) != parent.get(name)
                for name in ("family_id", "mechanism", "required_features")
            ):
                raise RealtimeAlphaHandoffError("adaptive lineage changes mechanism/data")
            _required_int(row.get("feedback_cutoff_ms"), "feedback_cutoff_ms")
            _required_text(row.get("mutation"), "mutation")
            if row.get("fresh_oos_required") is not True:
                raise RealtimeAlphaHandoffError("adaptive fresh OOS is not required")
            _required_int(
                row.get("adaptive_generation"),
                "adaptive_generation",
                positive=True,
            )
            # Feedback challengers freeze both a new executable signal shape
            # (horizon/version/lineage) and a new falsification statement, so
            # the canonical authority must classify the successor as a
            # signal + governance change rather than under-declare it.
            change_class = MaterialChangeClass.MATERIAL_MULTIPLE
        else:
            roots[str(row["candidate_id"])] = row

        registration_hash = str(registration["record_hash"])
        spec = _signal_spec(
            row,
            research_policy=policy,
            research_policy_hash=policy_hash,
            registration_hash=registration_hash,
        )
        signal_spec_json = _canonical_json(spec)
        source_definition_hash = stable_hash(
            {
                "registration_record_hash": registration_hash,
                "signal_spec_json": signal_spec_json,
            }
        )
        identities.append(
            CandidateIdentity(
                candidate_id=str(row["candidate_id"]),
                expression="",
                quantile=float(policy["quantile"]),
                cadence_days=0,
                universe=REALTIME_UNIVERSE,
                family_id=str(row["family_id"]),
                source_kind=(
                    "AUTONOMOUS_REALTIME_ADAPTIVE"
                    if adaptive
                    else "AUTONOMOUS_REALTIME_PREREGISTERED"
                ),
                source_ref=f"realtime_alpha_registry:{registration_hash}",
                source_definition_hash=source_definition_hash,
                required_datasets=tuple(sorted(required)),
                economic_hypothesis=_required_text(
                    row.get("economic_hypothesis"), "economic_hypothesis"
                ),
                falsification_criteria=(
                    _required_text(
                        row.get("falsification_standard"),
                        "falsification_standard",
                    ),
                ),
                created_at_ms=int(registration["recorded_at"]),
                predecessor_id=predecessor_id,
                change_class=change_class,
                definition_kind=CandidateDefinitionKind.REALTIME_MECHANISM,
                signal_spec_json=signal_spec_json,
                decision_interval_ms=int(policy["decision_interval_ms"]),
                label_horizon_ms=int(row["horizon_ms"]),
            )
        )
    return tuple(identities)


__all__ = [
    "REALTIME_EVALUATOR_VERSION",
    "REALTIME_RESEARCH_POLICY_VERSION",
    "REALTIME_SIGNAL_SPEC_VERSION",
    "REALTIME_UNIVERSE",
    "RealtimeAlphaHandoffError",
    "build_realtime_candidate_identities",
    "canonical_realtime_research_policy",
    "realtime_research_policy_hash",
]
