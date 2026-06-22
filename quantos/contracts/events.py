"""Canonical event envelope (blueprint Phase 1 — event schemas).

A single typed envelope all domain events share. Concrete Avro/protobuf schemas
under schemas/ and proto/ are generated from / kept consistent with this
envelope (tracked as a follow-up); this is the source-of-truth shape.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .hashing import short_hash


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)        # e.g. "trading.risk.pretrade.rejected"
    event_version: str = "1.0.0"
    timestamp: int = Field(ge=0)                  # epoch micros
    source: str = Field(min_length=1)
    domain: str = ""                              # data | research | risk | trading | governance
    criticality: str = "C2"                       # C0 (highest) .. C3
    payload: dict = Field(default_factory=dict)

    @property
    def reproducibility_hash(self) -> str:
        return short_hash(self.model_dump(mode="json"))


# A few well-known critical event types (not exhaustive; the envelope accepts any
# dotted name). Kept as constants so producers/consumers share spelling.
EVENT_PRETRADE_APPROVED = "trading.risk.pretrade.approved"
EVENT_PRETRADE_REJECTED = "trading.risk.pretrade.rejected"
EVENT_KILL_SWITCH_ACTIVATED = "trading.risk.killswitch.activated"
EVENT_ALPHA_PROMOTED = "research.alpha.promoted"
EVENT_DEPLOYMENT_RECORDED = "governance.deployment.recorded"
