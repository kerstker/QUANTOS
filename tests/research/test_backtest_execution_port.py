import pytest

from quantos.contracts.cost import CostModel
from quantos.contracts.data import Bar
from services.research.backtest_engine.adapter import DeterministicBacktestAdapter
from services.research.backtest_engine.engine import run_backtest
from services.research.ports.backtest import BacktestExecutionPort, BacktestOutcome


def _bars() -> list[Bar]:
    return [
        Bar(
            instrument_id="X",
            event_time=index,
            knowledge_time=index,
            open=price,
            high=price + 1.0,
            low=price - 1.0,
            close=price,
            volume=10_000.0,
        )
        for index, price in enumerate((100.0, 101.0, 103.0, 102.0), start=1)
    ]


def test_deterministic_adapter_implements_port_and_preserves_validation_outcome():
    bars = _bars()
    targets = [10, 10, 0, 0]
    cost = CostModel(fee_bps=1.0, slippage_bps=2.0)
    adapter = DeterministicBacktestAdapter()

    outcome = adapter.run(bars, targets, cost=cost)
    direct = run_backtest(bars, targets, cost=cost)

    assert isinstance(adapter, BacktestExecutionPort)
    assert outcome == BacktestOutcome(
        initial_capital=direct.initial_capital,
        equity_curve=tuple(direct.equity_curve),
    )
    assert outcome.total_pnl == pytest.approx(direct.total_pnl)


@pytest.mark.parametrize(
    ("initial_capital", "equity_curve"),
    [
        (0.0, (1.0,)),
        (float("inf"), (1.0,)),
        (1.0, (float("nan"),)),
        (1.0, ()),
    ],
)
def test_backtest_outcome_rejects_incomplete_or_non_finite_evidence(initial_capital, equity_curve):
    with pytest.raises(ValueError):
        BacktestOutcome(initial_capital=initial_capital, equity_curve=equity_curve)
