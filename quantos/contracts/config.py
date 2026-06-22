"""Safety configuration contract (blueprint §1.2).

Encodes the non-negotiable safety defaults as a typed, frozen object. Every
flag defaults to the SAFE value; live trading is impossible unless every live
precondition is explicitly enabled AND human approval is granted at runtime
(modelled here as an explicit, separate step). AI-origin contexts can never be
"armed" for live trading.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


class SafetyViolation(RuntimeError):
    """Raised when a safety invariant would be breached."""


@dataclass(frozen=True)
class SafetyConfig:
    # Blueprint §1.2 required defaults (all SAFE by default):
    live_trading_enabled: bool = False
    real_broker_enabled: bool = False
    allow_order_submission: bool = False
    allow_ai_direct_trading: bool = False
    require_human_approval: bool = True

    @property
    def live_trading_armed(self) -> bool:
        """True only if EVERY live precondition is explicitly enabled. Even when
        armed, an actual live order still requires a separate human-approval
        token at runtime (see require_human_approval)."""
        return (
            self.live_trading_enabled
            and self.real_broker_enabled
            and self.allow_order_submission
            and not self.allow_ai_direct_trading  # AI can never be the live actor
        )

    def assert_research_safe(self) -> None:
        """Guard for research / paper / dry-run contexts: no live capability may
        be enabled. Raises SafetyViolation otherwise (fail-closed)."""
        if self.live_trading_enabled or self.real_broker_enabled or self.allow_order_submission:
            raise SafetyViolation("live-trading flags must be disabled in research/paper context")
        if self.allow_ai_direct_trading:
            raise SafetyViolation("AI direct trading is forbidden (blueprint §1.1)")
        if not self.require_human_approval:
            raise SafetyViolation("human approval is required and cannot be disabled here")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SafetyConfig":
        fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in fields})


# The canonical safe default used everywhere research/paper code runs.
RESEARCH_SAFE_DEFAULTS = SafetyConfig()
