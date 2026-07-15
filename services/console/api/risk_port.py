"""Risk snapshot port consumed by the console HTTP presentation layer."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from quantos.contracts.killswitch import KillSwitchState


class RiskConsoleUnavailable(RuntimeError):
    """The read-only console was started without its risk composition root."""


class RiskConsolePort(Protocol):
    def risk_decision(
        self,
        alpha_id: str,
        returns: Sequence[float],
        equity_points: Sequence[Mapping[str, float]],
        capacity: float,
    ) -> dict[str, Any]: ...

    def monitoring_snapshot(
        self, *, audit_db: str, kill_switch_state: KillSwitchState
    ) -> dict[str, Any]: ...

    def safety_primitives(
        self, *, kill_switch_state: KillSwitchState
    ) -> dict[str, Any]: ...


class RiskConsoleRuntime:
    """One-time composition registry; missing or replacement wiring fails closed."""

    def __init__(self) -> None:
        self._port: RiskConsolePort | None = None

    def configure(self, port: RiskConsolePort) -> None:
        required = ("risk_decision", "monitoring_snapshot", "safety_primitives")
        if not all(callable(getattr(port, name, None)) for name in required):
            raise TypeError("risk console port is incomplete")
        if self._port is None:
            self._port = port
            return
        if self._port is not port:
            raise RuntimeError("risk console runtime is already configured")

    def require(self) -> RiskConsolePort:
        if self._port is None:
            raise RiskConsoleUnavailable(
                "risk console runtime is unavailable; start services.console.main:app"
            )
        return self._port


risk_console_runtime = RiskConsoleRuntime()


__all__ = [
    "RiskConsolePort",
    "RiskConsoleRuntime",
    "RiskConsoleUnavailable",
    "risk_console_runtime",
]
