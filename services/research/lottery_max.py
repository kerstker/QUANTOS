"""Frozen weekly crypto MAX primitives over the shared liquid-panel engine.

The MAX family deliberately reuses the IVOL experiment's already tested
dynamic-universe, missing-label, terminal-exit, and future-invariance boundary.
Only the pre-decision signal changes.  This module adds no IO, RNG, wall clock,
registry, lifecycle, credential, broker, or order capability.
"""
from __future__ import annotations

from typing import Literal

from services.research.alpha_expression import FieldPanel
from services.research.idiosyncratic_volatility import (
    ForcedExitEvent,
    IvolBreadthError,
    IvolDataIntegrityError,
    IvolDecisionPlan,
    IvolExperimentConfig,
    adverse_terminal_stress_periods,
    build_ivol_periods,
    find_contiguous_sample_stop_row,
    find_first_eligible_decision_row,
    ivol_signal_leakage_ok,
    sample_stop_diagnostics,
)


LotteryMaxSignalMode = Literal[
    "low_max", "high_max", "short_term_reversal", "lottery_hash"
]
LOTTERY_MAX_CONFIG = IvolExperimentConfig(
    return_lookback_rows=7,
    liquidity_lookback_rows=28,
    holding_rows=7,
    universe_size=60,
    liquid_size=30,
    max_terminal_events_per_period=2,
)

LotteryMaxBreadthError = IvolBreadthError
LotteryMaxDataIntegrityError = IvolDataIntegrityError
LotteryMaxDecisionPlan = IvolDecisionPlan


def build_lottery_max_periods(
    panel: FieldPanel,
    config: IvolExperimentConfig = LOTTERY_MAX_CONFIG,
    *,
    mode: LotteryMaxSignalMode = "low_max",
    universe_size: int | None = None,
    start_row: int | None = None,
    stop_row: int | None = None,
):
    """Build the frozen non-overlapping MAX holdings and terminal events."""

    return build_ivol_periods(
        panel,
        config,
        mode=mode,
        universe_size=universe_size,
        start_row=start_row,
        stop_row=stop_row,
    )


def lottery_max_leakage_ok(
    panel: FieldPanel,
    config: IvolExperimentConfig = LOTTERY_MAX_CONFIG,
    *,
    mode: LotteryMaxSignalMode = "low_max",
    universe_size: int | None = None,
    start_row: int | None = None,
    stop_row: int | None = None,
) -> bool | None:
    """Prove MAX-family signals are invariant to all strictly future cells."""

    return ivol_signal_leakage_ok(
        panel,
        config,
        mode=mode,
        universe_size=universe_size,
        start_row=start_row,
        stop_row=stop_row,
    )


__all__ = [
    "ForcedExitEvent",
    "LOTTERY_MAX_CONFIG",
    "LotteryMaxBreadthError",
    "LotteryMaxDataIntegrityError",
    "LotteryMaxDecisionPlan",
    "LotteryMaxSignalMode",
    "adverse_terminal_stress_periods",
    "build_lottery_max_periods",
    "find_contiguous_sample_stop_row",
    "find_first_eligible_decision_row",
    "lottery_max_leakage_ok",
    "sample_stop_diagnostics",
]
