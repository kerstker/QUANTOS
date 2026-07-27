"""Canonical event envelope (blueprint Phase 1 — event schemas).

A single typed envelope all domain events share. Concrete Avro/protobuf schemas
under schemas/ and proto/ are generated from / kept consistent with this
envelope (tracked as a follow-up); this is the source-of-truth shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .hashing import short_hash


EVENT_TIMESTAMP_UNIT_MICROSECONDS = "unix_epoch_microseconds"
EVENT_TIMESTAMP_UNIT_MILLISECONDS = "unix_epoch_milliseconds"
EventTimestampUnit = Literal[
    "unix_epoch_microseconds",
    "unix_epoch_milliseconds",
]


def event_timestamp_from_milliseconds(timestamp_ms: int) -> int:
    """Convert a caller-supplied Unix millisecond clock to canonical microseconds."""
    if (
        isinstance(timestamp_ms, bool)
        or not isinstance(timestamp_ms, int)
        or timestamp_ms < 0
    ):
        raise ValueError("timestamp_ms must be a non-negative integer")
    return timestamp_ms * 1_000


def event_timestamp_to_milliseconds(
    timestamp: int,
    unit: EventTimestampUnit,
) -> int:
    """Normalize a version-declared event timestamp without magnitude heuristics."""
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise ValueError("timestamp must be a non-negative integer")
    if unit == EVENT_TIMESTAMP_UNIT_MICROSECONDS:
        return timestamp // 1_000
    if unit == EVENT_TIMESTAMP_UNIT_MILLISECONDS:
        return timestamp
    raise ValueError(f"unsupported event timestamp unit: {unit!r}")


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)        # e.g. "trading.risk.pretrade.rejected"
    event_version: str = "1.0.0"
    # Unit is declared by the registered (event_type, event_version) schema.
    # Current producers use Unix epoch microseconds; selected legacy v1.0 events
    # retain their historical millisecond interpretation for replay.
    timestamp: int = Field(ge=0)
    source: str = Field(min_length=1)
    domain: str = ""                              # data | research | risk | trading | governance
    criticality: str = "C2"                       # C0 (highest) .. C3
    payload: dict = Field(default_factory=dict)

    @property
    def reproducibility_hash(self) -> str:
        return short_hash(self.model_dump(mode="json"))


@dataclass(frozen=True)
class EventOutboxMessage:
    """Canonical event plus causal metadata retained by a transactional outbox."""

    event: Event
    correlation_id: str = ""
    causation_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.event, Event):
            raise TypeError("outbox event must be a canonical Event")
        if not isinstance(self.correlation_id, str) or not isinstance(
            self.causation_id, str
        ):
            raise TypeError("outbox causal identifiers must be strings")

    @property
    def outbox_id(self) -> str:
        return self.event.event_id


# A few well-known critical event types (not exhaustive; the envelope accepts any
# dotted name). Kept as constants so producers/consumers share spelling.
EVENT_PRETRADE_APPROVED = "trading.risk.pretrade.approved"
EVENT_PRETRADE_REJECTED = "trading.risk.pretrade.rejected"
EVENT_KILL_SWITCH_ACTIVATED = "trading.risk.killswitch.activated"
EVENT_KILL_SWITCH_RESET = "trading.risk.killswitch.reset"
EVENT_ALPHA_PROMOTED = "research.alpha.promoted"
EVENT_ALPHA_MAINTENANCE_TRANSITION = "research.alpha.maintenance.transition"
EVENT_DRY_RUN_STARTED = "execution.dry_run.started"
EVENT_DRY_RUN_COMPLETED = "execution.dry_run.completed"
EVENT_DEPLOYMENT_RECORDED = "governance.deployment.recorded"
EVENT_DATA_REFRESH_RECORDED = "data.market.refresh.recorded"
EVENT_DATA_QUALITY_ADMISSION_DECIDED = "data.quality.admission.decided"
EVENT_RESEARCH_RISK_EVALUATED = "research.risk.dry_run.evaluated"
EVENT_SHADOW_OBSERVED = "execution.shadow.observed"
