"""Versioned schemas for canonical domain events.

Compatibility is explicit per producer version. A matching semantic-version major
is not treated as proof that an older consumer can safely parse a newer payload.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .events import (
    EVENT_TIMESTAMP_UNIT_MICROSECONDS,
    EVENT_TIMESTAMP_UNIT_MILLISECONDS,
    EVENT_ALPHA_MAINTENANCE_TRANSITION,
    EVENT_ALPHA_PROMOTED,
    EVENT_DATA_QUALITY_ADMISSION_DECIDED,
    EVENT_DATA_REFRESH_RECORDED,
    EVENT_DEPLOYMENT_RECORDED,
    EVENT_DRY_RUN_COMPLETED,
    EVENT_DRY_RUN_STARTED,
    EVENT_KILL_SWITCH_ACTIVATED,
    EVENT_KILL_SWITCH_RESET,
    EVENT_PRETRADE_APPROVED,
    EVENT_PRETRADE_REJECTED,
    EVENT_RESEARCH_RISK_EVALUATED,
    EVENT_SHADOW_OBSERVED,
    Event,
    EventTimestampUnit,
    event_timestamp_to_milliseconds,
)


_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class EventSchemaError(ValueError):
    """The event type, version, envelope metadata, or payload is unsupported."""


class EventSchemaCompatibilityError(EventSchemaError):
    """A registered producer version is unsafe for the declared consumer."""


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _PretradeDecisionPayload(_Payload):
    approved: bool
    instrument_id: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    reasons: list[str]
    run_id: str = Field(min_length=1)
    side: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)


class _KillSwitchActivatedPayload(_Payload):
    actor_id: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    reason_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: str = Field(min_length=1)
    scope_value: str
    trip_count: int = Field(ge=1)


class _KillSwitchResetPayload(_Payload):
    actor_id: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    reset_via: Literal["single", "multi_party"]
    trip_count: int = Field(ge=1)


class _AlphaPromotedPayload(_Payload):
    actor_id: str = Field(min_length=1)
    alpha_id: str = Field(min_length=1)
    dst: str = Field(min_length=1)
    eval_run_id: str
    evidence_notes: list[str]
    grant_head_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(min_length=1)
    src: str = Field(min_length=1)


class _AlphaMaintenancePayload(_Payload):
    action: Literal["DEGRADED", "PAUSED", "RETIRED"]
    actor_id: str = Field(min_length=1)
    alpha_id: str = Field(min_length=1)
    reasons: list[object]


class _DryRunStartedPayload(_Payload):
    mode: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)


class _DryRunCompletedV1Payload(_Payload):
    audit_root: str
    n_approved: int = Field(ge=0)
    n_fills: int = Field(ge=0)
    n_intents: int = Field(ge=0)
    n_rejected: int = Field(ge=0)
    run_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)


class _DryRunCompletedV11Payload(_DryRunCompletedV1Payload):
    run_repro_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class _DeploymentRecordedPayload(_Payload):
    actor_id: str = Field(min_length=1)
    allowed: bool
    environment: str = Field(min_length=1)
    reasons: list[str]
    subject: str
    version_ids: list[str]


class _DataRefreshRecordedPayload(_Payload):
    duration_ms: int = Field(ge=0)
    failed_instrument_count: int = Field(ge=0)
    failed_source_count: int = Field(ge=0)
    history_entry_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: Literal["PASS", "FAILED"]
    run_id: str = Field(min_length=1)
    source_count: int = Field(ge=0)
    status_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class _DataQualityAdmissionDecidedPayload(_Payload):
    admission_id: str = Field(min_length=1)
    block_reason_count: int = Field(ge=0)
    decision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_schema_version: int = Field(ge=1)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument_count: int = Field(ge=1)
    lineage_verdict: Literal["PASS", "BLOCK"]
    price_basis: str = Field(min_length=1)
    purpose: Literal["FORWARD_EVIDENCE_DATA_ADMISSION"]
    quality_verdict: Literal["PASS", "BLOCK"]
    readiness: Literal["L2_DRY_RUN_ONLY"]
    verdict: Literal["PASS", "BLOCK"]


class _ResearchRiskEvaluatedPayload(_Payload):
    config_provenance: str = Field(min_length=1)
    decision: Literal["RISK_APPROVED", "RISK_REJECTED"]
    engines_run: dict[str, bool]
    engines_skipped: list[str]
    gates_passed: int = Field(ge=0)
    gates_run: int = Field(ge=1)
    # SUBSET is the original honest dry-run seam.  FULL is emitted only when
    # all nine research-risk engines were fed validated, point-in-time inputs;
    # both remain research evidence and never grant live-trading capability.
    provenance: Literal["REAL_RESEARCH_RISK_SUBSET", "REAL_RESEARCH_RISK_FULL"]
    reasons: list[str]
    run_id: str = Field(min_length=1)
    run_repro_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    series: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)


class _ShadowObservedPayload(_Payload):
    """SHADOW 观测的脱敏摘要(727 §12.2,Phase 7 S2)——只含标量结论,
    不含盘口簿/walk 明细(完整观测在 shadow_store 行 payload_json)。新事件类型,
    timestamp 为 Unix epoch 微秒(现行约定,catalog 钉死 latest 一律 µs)。"""
    as_of_ms: int | None = Field(default=None, gt=0)
    book_age_ms: int | None = None
    decision_price: float = Field(gt=0)
    executable_vwap: float | None = Field(default=None, gt=0)
    instrument_id: str = Field(min_length=1)
    observation_id: str = Field(pattern=r"^shd_[0-9a-f]{16}$")
    provenance: Literal["SHADOW"]
    quantity: float = Field(gt=0)
    run_label: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    slippage_bps_vs_decision: float | None = None


@dataclass(frozen=True)
class EventSchemaDefinition:
    event_type: str
    version: str
    payload_model: type[BaseModel]
    domain: str
    sources: frozenset[str]
    criticalities: frozenset[str]
    compatible_consumer_versions: frozenset[str]
    timestamp_unit: EventTimestampUnit

    def __post_init__(self) -> None:
        if not self.event_type:
            raise ValueError("event_type must be non-empty")
        _parse_version(self.version)
        if not self.domain or not self.sources or not self.criticalities:
            raise ValueError("event schema metadata constraints must be non-empty")
        if not self.compatible_consumer_versions:
            raise ValueError("compatible_consumer_versions must be explicit")
        if self.timestamp_unit not in {
            EVENT_TIMESTAMP_UNIT_MICROSECONDS,
            EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        }:
            raise ValueError("timestamp_unit must be explicit and supported")
        for version in self.compatible_consumer_versions:
            _parse_version(version)


def _parse_version(version: str) -> tuple[int, int, int]:
    if not isinstance(version, str) or _SEMVER.fullmatch(version) is None:
        raise EventSchemaError(f"invalid event schema version: {version!r}")
    return tuple(int(part) for part in version.split("."))


class EventSchemaRegistry:
    """Immutable lookup and compatibility policy for event envelopes."""

    def __init__(self, definitions: tuple[EventSchemaDefinition, ...]):
        by_key: dict[tuple[str, str], EventSchemaDefinition] = {}
        by_type: dict[str, list[EventSchemaDefinition]] = {}
        for definition in definitions:
            key = (definition.event_type, definition.version)
            if key in by_key:
                raise ValueError(f"duplicate event schema: {key}")
            by_key[key] = definition
            by_type.setdefault(definition.event_type, []).append(definition)
        for event_type, schemas in by_type.items():
            registered = {schema.version for schema in schemas}
            for schema in schemas:
                unknown = schema.compatible_consumer_versions - registered
                if unknown:
                    raise ValueError(
                        f"{event_type} {schema.version} references unknown consumers: "
                        f"{sorted(unknown)}"
                    )
            schemas.sort(key=lambda item: _parse_version(item.version))
        self._by_key = by_key
        self._by_type = by_type

    def definition(self, event_type: str, version: str) -> EventSchemaDefinition:
        definition = self._by_key.get((event_type, version))
        if definition is None:
            if event_type not in self._by_type:
                raise EventSchemaError(f"unregistered event type: {event_type}")
            raise EventSchemaError(
                f"unregistered event version: {event_type} {version}"
            )
        return definition

    def validate(
        self,
        event: Event,
        *,
        consumer_version: str | None = None,
    ) -> Event:
        if not isinstance(event, Event):
            raise TypeError("event must be a canonical Event")
        definition = self.definition(event.event_type, event.event_version)
        if event.domain != definition.domain:
            raise EventSchemaError(
                f"event domain mismatch: expected {definition.domain}, got {event.domain}"
            )
        if event.source not in definition.sources:
            raise EventSchemaError(f"event source is not registered: {event.source}")
        if event.criticality not in definition.criticalities:
            raise EventSchemaError(
                f"event criticality is not registered: {event.criticality}"
            )
        try:
            definition.payload_model.model_validate(event.payload)
        except ValidationError as exc:
            raise EventSchemaError(
                f"event payload does not match {event.event_type} {event.event_version}: {exc}"
            ) from exc
        if consumer_version is not None:
            _parse_version(consumer_version)
            if consumer_version not in definition.compatible_consumer_versions:
                raise EventSchemaCompatibilityError(
                    f"producer {event.event_version} is not compatible with "
                    f"consumer {consumer_version} for {event.event_type}"
                )
        return event

    def timestamp_unit(self, event: Event) -> EventTimestampUnit:
        if not isinstance(event, Event):
            raise TypeError("event must be a canonical Event")
        return self.definition(event.event_type, event.event_version).timestamp_unit

    def timestamp_milliseconds(self, event: Event) -> int:
        if not isinstance(event, Event):
            raise TypeError("event must be a canonical Event")
        return event_timestamp_to_milliseconds(
            event.timestamp,
            self.timestamp_unit(event),
        )

    def timestamp_unix_seconds(self, event: Event) -> int:
        return self.timestamp_milliseconds(event) // 1_000

    def latest_version(self, event_type: str) -> str:
        schemas = self._by_type.get(event_type)
        if schemas is None:
            raise EventSchemaError(f"unregistered event type: {event_type}")
        return schemas[-1].version

    def latest_consumer_versions(self) -> dict[str, str]:
        return {
            event_type: schemas[-1].version
            for event_type, schemas in sorted(self._by_type.items())
        }

    @staticmethod
    def _manifest_definition(schema: EventSchemaDefinition) -> dict:
        payload_schema = schema.payload_model.model_json_schema()
        canonical = json.dumps(
            payload_schema,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "compatible_consumer_versions": sorted(
                schema.compatible_consumer_versions
            ),
            "criticalities": sorted(schema.criticalities),
            "payload_schema": payload_schema,
            "payload_schema_sha256": hashlib.sha256(canonical).hexdigest(),
            "sources": sorted(schema.sources),
            "timestamp_unit": schema.timestamp_unit,
            "version": schema.version,
        }

    def manifest(self) -> dict[str, dict]:
        return {
            event_type: {
                "domain": schemas[-1].domain,
                "latest_version": schemas[-1].version,
                "versions": [schema.version for schema in schemas],
                "definitions": [self._manifest_definition(schema) for schema in schemas],
            }
            for event_type, schemas in sorted(self._by_type.items())
        }


def _schema(
    event_type: str,
    payload_model: type[BaseModel],
    *,
    domain: str,
    source: str,
    criticalities: tuple[str, ...] = ("C2",),
    version: str = "1.0.0",
    consumers: tuple[str, ...] = ("1.0.0",),
    timestamp_unit: EventTimestampUnit = EVENT_TIMESTAMP_UNIT_MICROSECONDS,
) -> EventSchemaDefinition:
    return EventSchemaDefinition(
        event_type=event_type,
        version=version,
        payload_model=payload_model,
        domain=domain,
        sources=frozenset({source}),
        criticalities=frozenset(criticalities),
        compatible_consumer_versions=frozenset(consumers),
        timestamp_unit=timestamp_unit,
    )


CANONICAL_EVENT_SCHEMAS = EventSchemaRegistry(
    (
        _schema(
            EVENT_PRETRADE_APPROVED,
            _PretradeDecisionPayload,
            domain="risk",
            source="services.execution.dry_run_pipeline",
        ),
        _schema(
            EVENT_PRETRADE_REJECTED,
            _PretradeDecisionPayload,
            domain="risk",
            source="services.execution.dry_run_pipeline",
            criticalities=("C1",),
        ),
        _schema(
            EVENT_KILL_SWITCH_ACTIVATED,
            _KillSwitchActivatedPayload,
            domain="risk",
            source="services.risk.pre_trade.kill_switch",
            criticalities=("C0",),
            version="1.0.0",
            consumers=("1.0.0", "1.1.0"),
            timestamp_unit=EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        ),
        _schema(
            EVENT_KILL_SWITCH_ACTIVATED,
            _KillSwitchActivatedPayload,
            domain="risk",
            source="services.risk.pre_trade.kill_switch",
            criticalities=("C0",),
            version="1.1.0",
            consumers=("1.1.0",),
        ),
        _schema(
            EVENT_KILL_SWITCH_RESET,
            _KillSwitchResetPayload,
            domain="risk",
            source="services.risk.pre_trade.kill_switch",
            criticalities=("C0",),
            version="1.0.0",
            consumers=("1.0.0", "1.1.0"),
            timestamp_unit=EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        ),
        _schema(
            EVENT_KILL_SWITCH_RESET,
            _KillSwitchResetPayload,
            domain="risk",
            source="services.risk.pre_trade.kill_switch",
            criticalities=("C0",),
            version="1.1.0",
            consumers=("1.1.0",),
        ),
        _schema(
            EVENT_ALPHA_PROMOTED,
            _AlphaPromotedPayload,
            domain="research",
            source="services.alpha.promotion",
            criticalities=("C1",),
            version="1.0.0",
            consumers=("1.0.0", "1.1.0"),
            timestamp_unit=EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        ),
        _schema(
            EVENT_ALPHA_PROMOTED,
            _AlphaPromotedPayload,
            domain="research",
            source="services.alpha.promotion",
            criticalities=("C1",),
            version="1.1.0",
            consumers=("1.1.0",),
        ),
        _schema(
            EVENT_ALPHA_MAINTENANCE_TRANSITION,
            _AlphaMaintenancePayload,
            domain="research",
            source="services.alpha.maintenance",
            criticalities=("C1", "C2"),
        ),
        _schema(
            EVENT_DRY_RUN_STARTED,
            _DryRunStartedPayload,
            domain="execution",
            source="services.execution.dry_run_pipeline",
        ),
        _schema(
            EVENT_DRY_RUN_COMPLETED,
            _DryRunCompletedV1Payload,
            domain="execution",
            source="services.execution.dry_run_pipeline",
            version="1.0.0",
            consumers=("1.0.0", "1.1.0"),
        ),
        _schema(
            EVENT_DRY_RUN_COMPLETED,
            _DryRunCompletedV11Payload,
            domain="execution",
            source="services.execution.dry_run_pipeline",
            version="1.1.0",
            consumers=("1.1.0",),
        ),
        _schema(
            EVENT_DEPLOYMENT_RECORDED,
            _DeploymentRecordedPayload,
            domain="governance",
            source="services.deployment.gate",
            criticalities=("C0", "C1"),
            version="1.0.0",
            consumers=("1.0.0", "1.1.0"),
            timestamp_unit=EVENT_TIMESTAMP_UNIT_MILLISECONDS,
        ),
        _schema(
            EVENT_DEPLOYMENT_RECORDED,
            _DeploymentRecordedPayload,
            domain="governance",
            source="services.deployment.gate",
            criticalities=("C0", "C1"),
            version="1.1.0",
            consumers=("1.1.0",),
        ),
        _schema(
            EVENT_DATA_REFRESH_RECORDED,
            _DataRefreshRecordedPayload,
            domain="data",
            source="services.data.refresh_history",
            criticalities=("C1", "C2"),
        ),
        _schema(
            EVENT_DATA_QUALITY_ADMISSION_DECIDED,
            _DataQualityAdmissionDecidedPayload,
            domain="data",
            source="services.data.admission_store",
            criticalities=("C1", "C2"),
        ),
        _schema(
            EVENT_RESEARCH_RISK_EVALUATED,
            _ResearchRiskEvaluatedPayload,
            domain="risk",
            source="services.risk.research_risk.dry_run_guard",
            criticalities=("C1", "C2"),
        ),
        _schema(
            EVENT_SHADOW_OBSERVED,
            _ShadowObservedPayload,
            domain="execution",
            source="live.execution.shadow_store",
            criticalities=("C2",),
        ),
    )
)


__all__ = [
    "CANONICAL_EVENT_SCHEMAS",
    "EventSchemaCompatibilityError",
    "EventSchemaDefinition",
    "EventSchemaError",
    "EventSchemaRegistry",
]
