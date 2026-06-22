"""Safety configuration contract (blueprint §1.2).

Pydantic v2 model. Every flag defaults to the SAFE value; live trading is
impossible unless every live precondition is explicitly enabled AND it is not an
AI-driven context. Even when "armed", an actual live order still requires a
separate human-approval token at runtime.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SafetyViolation(RuntimeError):
    """Raised when a safety invariant would be breached."""


class SafetyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Blueprint §1.2 required defaults (all SAFE by default):
    live_trading_enabled: bool = False
    real_broker_enabled: bool = False
    allow_order_submission: bool = False
    allow_ai_direct_trading: bool = False
    require_human_approval: bool = True

    @property
    def live_trading_armed(self) -> bool:
        """True only if EVERY live precondition is explicitly enabled and the
        context is not AI-driven. Arming is necessary but not sufficient: a live
        order also needs a runtime human-approval token."""
        return (
            self.live_trading_enabled
            and self.real_broker_enabled
            and self.allow_order_submission
            and not self.allow_ai_direct_trading
        )

    def assert_research_safe(self) -> None:
        """Guard for research / paper / dry-run contexts (fail-closed)."""
        if self.live_trading_enabled or self.real_broker_enabled or self.allow_order_submission:
            raise SafetyViolation("live-trading flags must be disabled in research/paper context")
        if self.allow_ai_direct_trading:
            raise SafetyViolation("AI direct trading is forbidden (blueprint §1.1)")
        if not self.require_human_approval:
            raise SafetyViolation("human approval is required and cannot be disabled here")

    def to_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict) -> "SafetyConfig":
        return cls.model_validate(data or {})


# The canonical safe default used everywhere research/paper code runs.
RESEARCH_SAFE_DEFAULTS = SafetyConfig()
