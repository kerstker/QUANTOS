"""Shared diagnostic panel data; carries labels, never execution authority."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PanelPeriod:
    instruments: tuple[str, ...]
    signals: tuple[float, ...]            # cross-sectional alpha signal
    forward_returns: tuple[float, ...]    # next-period return per instrument
    adv_notional: tuple[float, ...] = ()  # per-name ADV (for impact); optional
    # 2026-07-09 (real-data bug, see services/alpha/factory.py::batch_pbo docstring for full
    # coverage list): the panel's own event_time for this period, when the caller has it.
    # Optional/backward-compatible (None only for hand-built PanelPeriods in tests/scratch code
    # with no real calendar to draw from) — every real PanelPeriod-construction site populates it.
    # Exists so batch_pbo can align candidates by CALENDAR time instead of by list position when
    # candidates have different valid-period histories (the normal case, not the exception, once
    # real factors have different data-availability windows).
    event_time: Optional[int] = None
    # ADV for names that were held in the previous period but have left the
    # current signal cross-section. This lets dynamic-universe liquidation pay
    # impact and prove participation instead of disappearing at base cost only.
    exit_adv_notional: tuple[tuple[str, float], ...] = ()
