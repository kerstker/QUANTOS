"""Phase 5 wiring — the end-to-end research validation pipeline feeds REAL
walk-forward OOS + DSR + PBO + leakage into the gate.

leakage_ok is REQUIRED (no passing default) — these tests pass it explicitly (audit fix: the gate
trusts leakage_ok, so a caller must COMPUTE it; a silent True is no longer allowed)."""
import pytest

from quantos.contracts.data import Bar
from quantos.contracts.research import ValidationVerdict
from services.research.backtest_engine.adapter import DeterministicBacktestAdapter
from services.research.backtest_engine.costs import CostModel
from services.research.ports.backtest import BacktestExecutionPort, BacktestOutcome
from services.research.validation.pipeline import run_research_validation


_BACKTEST = DeterministicBacktestAdapter()


def _rising_bars(n, rate=0.02, start=100.0):
    bars, p = [], start
    for i in range(n):
        c = round(p, 4)
        eps = c * 0.001 + 0.01
        bars.append(Bar(instrument_id="X", event_time=i + 1, knowledge_time=i + 1,
                        open=c, high=c + eps, low=max(c - eps, 0.01), close=c, volume=1000))
        p *= (1.0 + rate)
    return bars


def test_pipeline_passes_genuine_strategy():
    bars = _rising_bars(30)
    hold = [100] * 30  # buy-and-hold a steadily rising market
    res = run_research_validation(
        "good",
        bars,
        [hold],
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=True,
    )
    assert res.promotable, res.reasons


def test_pipeline_flags_overfit_candidate_set():
    n, K = 32, 4
    bars = _rising_bars(n)
    seg = n // K
    candidates = []
    for c in range(K):  # each candidate is long only in its own segment (block-diagonal wins)
        t = [0] * n
        for i in range(c * seg, (c + 1) * seg):
            t[i] = 100
        candidates.append(t)
    res = run_research_validation(
        "overfit",
        bars,
        candidates,
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=True,
    )
    assert not res.promotable, res.reasons


def test_catalog_wide_trial_override_deflates_and_sets_trial_count():
    bars = _rising_bars(30)
    hold = [100] * 30
    base = run_research_validation(
        "g",
        bars,
        [hold],
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=True,
    )
    catalog = run_research_validation(
        "g",
        bars,
        [hold],
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=True,
        n_trials_override=500,
        var_sr_override=0.05,
    )
    # catalog-wide multiple-testing reaches DSR and the reported trial_count
    assert catalog.trial_count == 500
    assert catalog.deflated_sharpe <= base.deflated_sharpe


def test_pipeline_fails_on_detected_leakage():
    bars = _rising_bars(30)
    hold = [100] * 30
    res = run_research_validation(
        "leak",
        bars,
        [hold],
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=False,
    )
    assert res.verdict == ValidationVerdict.FAIL
    assert any("LEAKAGE" in r for r in res.reasons)


def test_pipeline_executes_every_oos_fold_through_the_injected_port():
    class RecordingPort:
        def __init__(self):
            self.calls = []

        def run(self, bars, target_shares, *, initial_capital=1_000_000.0, cost=CostModel()):
            self.calls.append((tuple(bars), tuple(target_shares), initial_capital, cost))
            return _BACKTEST.run(
                bars,
                target_shares,
                initial_capital=initial_capital,
                cost=cost,
            )

    port = RecordingPort()
    bars = _rising_bars(18)
    candidates = [[100] * len(bars), [0, 100] * (len(bars) // 2)]

    run_research_validation(
        "port-wiring",
        bars,
        candidates,
        backtest=port,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=True,
    )

    assert isinstance(port, BacktestExecutionPort)
    assert len(port.calls) == 8
    assert all(len(call[0]) == len(call[1]) == 3 for call in port.calls)


def test_pipeline_rejects_non_contract_backtest_output():
    class InvalidOutputPort:
        def run(self, bars, target_shares, *, initial_capital=1_000_000.0, cost=CostModel()):
            return object()

    bars = _rising_bars(12)
    with pytest.raises(TypeError, match="BacktestOutcome"):
        run_research_validation(
            "invalid-output",
            bars,
            [[100] * len(bars)],
            backtest=InvalidOutputPort(),
            train_size=6,
            test_size=3,
            cost=CostModel(0, 0),
            leakage_ok=True,
        )


def test_pipeline_rejects_backtest_output_not_aligned_to_oos_bars():
    class MisalignedOutputPort:
        def run(self, bars, target_shares, *, initial_capital=1_000_000.0, cost=CostModel()):
            return BacktestOutcome(
                initial_capital=initial_capital,
                equity_curve=(initial_capital,),
            )

    bars = _rising_bars(12)
    with pytest.raises(ValueError, match="equity_curve must align"):
        run_research_validation(
            "misaligned-output",
            bars,
            [[100] * len(bars)],
            backtest=MisalignedOutputPort(),
            train_size=6,
            test_size=3,
            cost=CostModel(0, 0),
            leakage_ok=True,
        )
