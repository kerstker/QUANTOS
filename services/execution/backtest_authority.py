"""Fail-closed authority boundary for backtest evidence.

The historical ``run_multi_period_dry_run`` records predate a complete input
manifest and use non-causal/spot-approximation semantics.  They remain useful
as immutable diagnostics, but they are not eligible for selection, promotion,
paper readiness, or live-readiness decisions.

``RunManifestV2`` is deliberately separate from the legacy store schema.  Its
identity binds every selection-bearing input and the causal decision/execution
timeline.  A new engine may only claim authority when the manifest and all
load-bearing semantic attestations are present and self-consistent.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import Decimal
from enum import Enum
from typing import Any

from services.validation.research_risk_authority import (
    full_research_risk_approved,
)

RUN_MANIFEST_SCHEMA_VERSION = 2
CANONICAL_ENGINE_SEMANTICS = "LINEAR_PERPETUAL_EVENT_V2"
AUTHORITATIVE_EVIDENCE_STATUS = "AUTHORITATIVE"
LEGACY_INVALIDATION_CODE = "INVALIDATED_PERP_LEDGER_V1"

# A self-described JSON document is not an authority boundary.  The deployed
# receipt-authority registry intentionally has no active keys, so no record may
# be admitted until a separately controlled signer and pinned verification path
# bind both the manifest and immutable result hash.  This flag must not be
# enabled as a configuration toggle; replacing it requires a reviewed verifier
# integration and adversarial tests.
_EXTERNAL_SIGNED_AUTHORITY_VERIFIER_INTEGRATED = False

_REQUIRED_ATTESTATIONS = frozenset(
    {
        "causal_available_time",
        "strict_next_event_execution",
        "decimal_contracts",
        "settlement_event_funding",
        "historical_venue_rules",
        "margin_liquidation_state_machine",
        "complete_cashflow_identity",
        "deployment_equivalent_risk",
        "capacity_is_load_bearing",
        "full_research_risk_9_of_9",
        "risk_history_ledger_replayed",
    }
)
_REQUIRED_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "reconciliation",
        "ending_state",
        "ledger_events",
        "liquidations",
        "decision_contexts",
        "research_risk_results",
        "execution_results",
    }
)


class BacktestIntegrityBlocked(RuntimeError):
    """Raised when a quarantined historical engine is invoked as authority."""


def _canonical_identity(value: Any, *, name: str) -> Any:
    """Encode authority inputs without lossy float rounding or ``default=str``.

    The platform-wide ``stable_hash`` deliberately rounds floats for portable
    business objects.  A run manifest has a stricter contract: every exact
    load-bearing input must alter the run identity.  Float values are therefore
    represented by their lossless IEEE-754 hexadecimal form and unsupported
    object types fail closed instead of inheriting a potentially unstable
    string representation.
    """

    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} contains a non-finite float")
        return ["float64", value.hex()]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{name} contains a non-finite Decimal")
        return ["decimal", str(value)]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, Enum):
        return [
            "enum",
            f"{type(value).__module__}.{type(value).__qualname__}",
            _canonical_identity(value.value, name=name),
        ]
    if isinstance(value, Mapping):
        entries = [
            [
                _canonical_identity(key, name=f"{name}.key"),
                _canonical_identity(item, name=f"{name}[{key!r}]"),
            ]
            for key, item in value.items()
        ]
        entries.sort(
            key=lambda item: json.dumps(
                item[0], ensure_ascii=False, separators=(",", ":")
            )
        )
        return ["mapping", entries]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_identity(item, name=name) for item in value]
        items.sort(
            key=lambda item: json.dumps(
                item, ensure_ascii=False, separators=(",", ":")
            )
        )
        return ["set", items]
    if isinstance(value, Sequence):
        return [
            "sequence",
            [_canonical_identity(item, name=name) for item in value],
        ]
    raise TypeError(
        f"{name} contains unsupported authority identity type "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )


def _authority_hash(value: Any, *, name: str) -> str:
    canonical = _canonical_identity(value, name=name)
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def exact_identity_hash(value: Any, *, name: str = "authority_payload") -> str:
    """Public exact hash for V2 input and immutable-result evidence."""

    if not isinstance(name, str) or not name.strip():
        raise ValueError("identity hash name must be non-empty")
    return _authority_hash(value, name=name)


def _digest(value: Any, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} must be provided")
    if isinstance(value, (str, bytes, Sequence, Mapping)) and len(value) == 0:
        raise ValueError(f"{name} must be non-empty")
    return _authority_hash(value, name=name)


def _event_time(event: Any, *, name: str) -> int:
    if isinstance(event, Mapping):
        time_keys = (
            "t",
            "timestamp",
            "event_time",
            "event_time_ms",
            "decision_time_ms",
            "execution_time_ms",
        )
        present_times = [(key, event[key]) for key in time_keys if key in event]
        if not present_times:
            raise ValueError(f"{name} has no event time")
        for key, candidate in present_times:
            if (
                isinstance(candidate, bool)
                or not isinstance(candidate, int)
                or candidate < 0
            ):
                raise ValueError(
                    f"{name}.{key} time must be a non-negative integer"
                )
        value = present_times[0][1]
        if any(candidate != value for _, candidate in present_times[1:]):
            raise ValueError(f"{name} has conflicting event times")
    else:
        value = event
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} time must be a non-negative integer")
    return value


@dataclass(frozen=True)
class RunManifestV2:
    """Canonical identity for one causal linear-perpetual backtest run."""

    release_hash: str
    data_hash: str
    universe_hash: str
    alpha_hash: str
    periods_hash: str
    decision_events_hash: str
    execution_events_hash: str
    causal_timeline_hash: str
    weights_hash: str
    prices_hash: str
    cost_model_hash: str
    funding_policy_hash: str
    venue_rules_hash: str
    risk_limits_hash: str
    filters_hash: str
    impact_model_hash: str
    adv_hash: str
    parameters_hash: str
    random_seed: int
    schema_version: int = RUN_MANIFEST_SCHEMA_VERSION
    engine_semantics: str = CANONICAL_ENGINE_SEMANTICS

    def __post_init__(self) -> None:
        if self.schema_version != RUN_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported run manifest schema_version")
        if self.engine_semantics != CANONICAL_ENGINE_SEMANTICS:
            raise ValueError("unsupported engine_semantics")
        if (
            isinstance(self.random_seed, bool)
            or not isinstance(self.random_seed, int)
            or not 0 <= self.random_seed < (1 << 63)
        ):
            raise ValueError("random_seed must be a signed-SQLite-safe non-negative integer")
        for item in fields(self):
            if not item.name.endswith("_hash"):
                continue
            value = getattr(self, item.name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{item.name} must be a lowercase sha256 digest")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "engine_semantics": self.engine_semantics,
            "release_hash": self.release_hash,
            "data_hash": self.data_hash,
            "universe_hash": self.universe_hash,
            "alpha_hash": self.alpha_hash,
            "periods_hash": self.periods_hash,
            "decision_events_hash": self.decision_events_hash,
            "execution_events_hash": self.execution_events_hash,
            "causal_timeline_hash": self.causal_timeline_hash,
            "weights_hash": self.weights_hash,
            "prices_hash": self.prices_hash,
            "cost_model_hash": self.cost_model_hash,
            "funding_policy_hash": self.funding_policy_hash,
            "venue_rules_hash": self.venue_rules_hash,
            "risk_limits_hash": self.risk_limits_hash,
            "filters_hash": self.filters_hash,
            "impact_model_hash": self.impact_model_hash,
            "adv_hash": self.adv_hash,
            "parameters_hash": self.parameters_hash,
            "random_seed": self.random_seed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunManifestV2":
        if not isinstance(value, Mapping):
            raise TypeError("run manifest must be a mapping")
        expected = {item.name for item in fields(cls)}
        if set(value) != expected:
            missing = sorted(expected - set(value))
            extra = sorted(set(value) - expected)
            raise ValueError(f"run manifest fields mismatch: missing={missing}, extra={extra}")
        return cls(**dict(value))

    @property
    def manifest_hash(self) -> str:
        return _authority_hash(self.as_dict(), name="run_manifest_v2")

    @property
    def run_id(self) -> str:
        # The input identity is the run identity; no mutable output is included.
        return self.manifest_hash


def build_run_manifest_v2(
    *,
    release_identity: Any,
    data_identity: Any,
    universe: Any,
    alpha_definition: Any,
    periods: Sequence[Any],
    decision_events: Sequence[Any],
    execution_events: Sequence[Any],
    weights_by_period: Any,
    prices_by_period: Any,
    cost_model: Any,
    funding_policy: Any,
    venue_rules: Any,
    risk_limits: Any,
    filters: Any,
    impact_model: Any,
    adv_by_period: Any,
    parameters: Any,
    random_seed: int,
) -> RunManifestV2:
    """Build a manifest after enforcing a strictly causal execution timeline."""

    if not periods:
        raise ValueError("periods must be non-empty")
    period_count = len(periods)
    if (
        not decision_events
        or period_count != len(decision_events)
        or period_count != len(execution_events)
    ):
        raise ValueError(
            "periods, decision events, and execution events must be non-empty "
            "and one-to-one aligned"
        )
    try:
        weights_count = len(weights_by_period)
        prices_count = len(prices_by_period)
    except TypeError as exc:
        raise ValueError(
            "weights_by_period and prices_by_period must be sized one-to-one records"
        ) from exc
    if weights_count != period_count or prices_count != period_count:
        raise ValueError(
            "periods, weights_by_period, and prices_by_period must be one-to-one aligned"
        )
    decisions = [
        _event_time(event, name=f"decision_events[{index}]")
        for index, event in enumerate(decision_events)
    ]
    executions = [
        _event_time(event, name=f"execution_events[{index}]")
        for index, event in enumerate(execution_events)
    ]
    for decision_time, execution_time in zip(decisions, executions, strict=True):
        if execution_time <= decision_time:
            raise ValueError("execution event must be strictly after decision event")
    if decisions != sorted(decisions) or len(set(decisions)) != len(decisions):
        raise ValueError("decision events must be strictly increasing")
    if executions != sorted(executions) or len(set(executions)) != len(executions):
        raise ValueError("execution events must be strictly increasing")

    return RunManifestV2(
        release_hash=_digest(release_identity, "release_identity"),
        data_hash=_digest(data_identity, "data_identity"),
        universe_hash=_digest(universe, "universe"),
        alpha_hash=_digest(alpha_definition, "alpha_definition"),
        periods_hash=_digest(periods, "periods"),
        decision_events_hash=_digest(decision_events, "decision_events"),
        execution_events_hash=_digest(execution_events, "execution_events"),
        causal_timeline_hash=_authority_hash(
            tuple(zip(decisions, executions, strict=True)),
            name="causal_timeline",
        ),
        weights_hash=_digest(weights_by_period, "weights_by_period"),
        prices_hash=_digest(prices_by_period, "prices_by_period"),
        cost_model_hash=_digest(cost_model, "cost_model"),
        funding_policy_hash=_digest(funding_policy, "funding_policy"),
        venue_rules_hash=_digest(venue_rules, "venue_rules"),
        risk_limits_hash=_digest(risk_limits, "risk_limits"),
        filters_hash=_digest(filters, "filters"),
        impact_model_hash=_digest(impact_model, "impact_model"),
        adv_hash=_digest(adv_by_period, "adv_by_period"),
        parameters_hash=_digest(parameters, "parameters"),
        random_seed=random_seed,
    )


def is_authoritative_backtest_record(record: Mapping[str, Any]) -> bool:
    """Fail closed until an external signed authority verifier is integrated.

    Internal hashes below are valuable tamper evidence, but they cannot prove
    who produced a record.  In particular, a caller that can rewrite the JSON
    can recompute every unkeyed digest and assert every boolean attestation.
    Therefore the public admission predicate remains unconditionally closed
    while the governed receipt-authority registry has no independent trust
    root.  The structural checks are retained as defense in depth for the
    future signed-verifier integration.
    """

    if not _EXTERNAL_SIGNED_AUTHORITY_VERIFIER_INTEGRATED:
        return False

    try:
        if not isinstance(record, Mapping):
            return False
        detail = record.get("detail")
        if not isinstance(detail, Mapping):
            return False
        manifest = RunManifestV2.from_dict(detail.get("run_manifest_v2"))
        if detail.get("manifest_hash") != manifest.manifest_hash:
            return False
        if record.get("run_id") != manifest.run_id:
            return False
        if detail.get("engine_semantics") != CANONICAL_ENGINE_SEMANTICS:
            return False
        if detail.get("evidence_status") != AUTHORITATIVE_EVIDENCE_STATUS:
            return False
        attestations = detail.get("engine_attestations")
        if not isinstance(attestations, Mapping):
            return False
        if not all(attestations.get(name) is True for name in _REQUIRED_ATTESTATIONS):
            return False

        result_payload = detail.get("result_hash_payload")
        if not isinstance(result_payload, Mapping):
            return False
        if set(result_payload) != _REQUIRED_RESULT_FIELDS:
            return False
        if result_payload.get("schema_version") != 1:
            return False
        if result_payload.get("run_id") != manifest.run_id:
            return False
        if detail.get("result_hash") != exact_identity_hash(
            result_payload, name="authoritative_backtest_result"
        ):
            return False

        reconciliation = result_payload.get("reconciliation")
        ending_state = result_payload.get("ending_state")
        if not isinstance(reconciliation, Mapping) or not isinstance(ending_state, Mapping):
            return False
        if reconciliation.get("residual") != "0":
            return False
        if ending_state.get("cashflow_identity") != reconciliation:
            return False

        ledger_events = result_payload.get("ledger_events")
        decision_contexts = result_payload.get("decision_contexts")
        risk_history = result_payload.get("research_risk_results")
        execution_results = result_payload.get("execution_results")
        if not all(
            isinstance(value, (list, tuple)) and bool(value)
            for value in (
                ledger_events,
                decision_contexts,
                risk_history,
                execution_results,
            )
        ):
            return False

        decision_ids = {
            item.get("decision_id")
            for item in decision_contexts
            if isinstance(item, Mapping)
        }
        risk_ids = {
            item.get("decision_id")
            for item in risk_history
            if isinstance(item, Mapping)
        }
        if (
            len(decision_ids) != len(decision_contexts)
            or len(risk_ids) != len(risk_history)
            or decision_ids != risk_ids
            or None in decision_ids
        ):
            return False
        for item in risk_history:
            if not isinstance(item, Mapping):
                return False
            risk = item.get("result")
            if not isinstance(risk, Mapping) or not full_research_risk_approved(risk):
                return False
        if detail.get("research_risk") != risk_history[-1].get("result"):
            return False
        if detail.get("research_risk_history") != list(risk_history):
            return False
        return True
    except (TypeError, ValueError):
        return False


def quarantine_legacy_backtest(component: str) -> None:
    """Unconditionally stop a legacy engine from producing authoritative evidence."""

    name = str(component).strip() or "unknown"
    raise BacktestIntegrityBlocked(
        f"{LEGACY_INVALIDATION_CODE}: {name} uses legacy timing/perpetual-ledger semantics; "
        "raw collection and read-only forward observation may continue, but historical "
        "selection, reuse, persistence, and promotion are blocked"
    )


__all__ = [
    "AUTHORITATIVE_EVIDENCE_STATUS",
    "BacktestIntegrityBlocked",
    "CANONICAL_ENGINE_SEMANTICS",
    "LEGACY_INVALIDATION_CODE",
    "RUN_MANIFEST_SCHEMA_VERSION",
    "RunManifestV2",
    "build_run_manifest_v2",
    "exact_identity_hash",
    "is_authoritative_backtest_record",
    "quarantine_legacy_backtest",
]
