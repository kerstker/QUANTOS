"""Bitemporal record base (CT-1: point-in-time / no lookahead).

Every observation carries two timestamps:
  * event_time     — when the fact occurred,
  * knowledge_time — when the fact first became knowable (as-of).

The invariant `knowledge_time >= event_time` is enforced at construction so a
record can never claim to have been "known" before it happened — making
lookahead bias impossible to express at the contract boundary. Timestamps are
epoch microseconds (int) to stay deterministic and serialization-stable.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BitemporalRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_time: int = Field(ge=0)
    knowledge_time: int = Field(ge=0)

    @model_validator(mode="after")
    def _no_lookahead(self) -> "BitemporalRecord":
        if self.knowledge_time < self.event_time:
            raise ValueError(
                "knowledge_time precedes event_time (lookahead): "
                f"{self.knowledge_time} < {self.event_time}"
            )
        return self
