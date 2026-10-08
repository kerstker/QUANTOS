"""QUANTOS Backtest Engine (blueprint §03 / Phase 4).

Deterministic, event-driven, point-in-time backtest. Decisions made at bar i
(from data up to close[i]) are executed at bar i+1 (next-bar fill) so the engine
cannot peek at the future. Mark-to-market equity reconciles EXACTLY to
(mark-to-market PnL - costs), which is asserted in tests.
"""
