import pytest


def test_paper_fill_contract_preserves_research_compatibility():
    from quantos.contracts.paper import FillMethod, OrderSide, PaperFill
    from services.research.paper_trading.domain.models import (
        FillMethod as ResearchFillMethod,
        OrderSide as ResearchOrderSide,
        PaperFill as ResearchPaperFill,
    )

    assert ResearchFillMethod is FillMethod
    assert ResearchOrderSide is OrderSide
    assert ResearchPaperFill is PaperFill
    fill = PaperFill(
        fill_id="f-1",
        instrument_id="AAA",
        side=OrderSide.BUY,
        quantity=2,
        price=10.0,
        fill_method=FillMethod.NEXT_OPEN,
        timestamp=1,
        commission=0.5,
        slippage=0.25,
    )
    assert fill.quantity == 2


def test_execution_reconciliation_preserves_legacy_identity_and_math():
    from quantos.contracts.paper import OrderSide, PaperFill
    from services.execution.paper_reconciliation import (
        InstrumentPnL,
        ReconciliationResult,
        reconcile,
    )
    from services.research.paper_trading.domain.reconciliation import (
        InstrumentPnL as ResearchInstrumentPnL,
        ReconciliationResult as ResearchReconciliationResult,
        reconcile as research_reconcile,
    )

    assert ResearchInstrumentPnL is InstrumentPnL
    assert ResearchReconciliationResult is ReconciliationResult
    assert research_reconcile is reconcile
    fills = [
        PaperFill(
            fill_id="buy",
            instrument_id="AAA",
            side=OrderSide.BUY,
            quantity=10,
            price=100.0,
            timestamp=1,
            commission=1.0,
        ),
        PaperFill(
            fill_id="sell",
            instrument_id="AAA",
            side=OrderSide.SELL,
            quantity=10,
            price=110.0,
            timestamp=2,
            commission=1.0,
        ),
    ]
    result = reconcile(fills, initial_capital=1_000.0)
    assert result.total_net_pnl == pytest.approx(98.0)
    assert result.reconciliation_residual() < 1e-9
