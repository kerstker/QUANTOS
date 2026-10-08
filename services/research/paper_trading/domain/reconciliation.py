"""Compatibility exports for execution-owned paper PnL reconciliation.

Replaces the placeholder `notional * 0.001` attribution. Processes fills with
signed average-cost accounting (handles longs, shorts, partial closes and flips),
marks open positions, and decomposes PnL into realized + unrealized − costs per
instrument. The aggregate reconciles EXACTLY (like the backtest engine):

    equity_end − initial_capital  ==  gross_trading_pnl − total_costs

`reconciliation_residual()` is asserted ~0. Deterministic; pure. The canonical
implementation lives in services.execution so execution never depends on research.
"""

from services.execution.paper_reconciliation import (
    InstrumentPnL,
    ReconciliationResult,
    reconcile,
)


__all__ = ["InstrumentPnL", "ReconciliationResult", "reconcile"]
