"""Kill-switch contract (blueprint §1 / Phase 8).

The immutable state of the emergency-halt latch. The latch *semantics* (arm /
human-only reset) live in the risk engine; this is the frozen, auditable datum.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .alpha import Actor


class KillSwitchScope(str, Enum):
    GLOBAL = "GLOBAL"
    ACCOUNT = "ACCOUNT"
    INSTRUMENT = "INSTRUMENT"


class KillSwitchState(BaseModel):
    model_config = ConfigDict(frozen=True)
    tripped: bool = False
    scope: KillSwitchScope = KillSwitchScope.GLOBAL
    scope_value: str = ""                  # account/instrument id when narrowed ("" for GLOBAL)
    incident_id: str = ""                  # ties a reset approval to THIS trip (review P0-4)
    armed_by: Optional[Actor] = None
    reason: str = ""
    armed_at: int = Field(default=0, ge=0)
    reset_by: Optional[Actor] = None
    reset_at: int = Field(default=0, ge=0)
    trip_count: int = Field(default=0, ge=0)

    @property
    def is_active(self) -> bool:
        return self.tripped
