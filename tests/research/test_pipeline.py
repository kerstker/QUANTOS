"""End-to-end validation retains catalog DSR and batch PBO evidence."""

import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.data import Bar
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.research.backtest_engine.adapter import (
    DeterministicBacktestAdapter,
)
from services.research.backtest_engine.costs import CostModel
from services.research.ports.backtest import (
    BacktestExecutionPort,
    BacktestOutcome,
)
from services.research.validation.pipeline import run_research_validation


_BACKTEST = DeterministicBacktestAdapter()


def _rising_bars(n, rate=0.02, start=100.0):
    bars, price = [], start
    for index in range(n):
        close = round(price, 4)
        epsilon = close * 0.001 + 0.01
        bars.append(
            Bar(
                instrument_id="X",
                event_time=index + 1,
                knowledge_time=index + 1,
                open=close,
                high=close + epsilon,
                low=max(close - epsilon, 0.01),
                close=close,
                volume=1000,
            )
        )
        price *= 1.0 + rate
    return bars


def _catalog(candidate_ids):
    catalog = AlphaCatalog()
    for candidate_id in candidate_ids:
        catalog.register(
            AlphaDefinition(
                alpha_id=candidate_id,
                name=candidate_id,
                factor_ids=(f"factor-{candidate_id}",),
                universe="test",
            )
        )
    return catalog


def test_pipeline_passes_genuine_strategy():
    bars = _rising_bars(30)
    candidates = ([100] * 30, [50] * 30)
    candidate_ids = ("good", "good-alt")
    result = run_research_validation(
        "good",
        bars,
        candidates,
        candidate_ids=candidate_ids,
        catalog=_catalog(candidate_ids),
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=True,
    )
    assert result.promotable, result.reasons
    assert result.catalog_evidence is not None
    assert result.pbo_evidence is not None


def test_pipeline_flags_overfit_candidate_set():
    size, candidate_count = 32, 4
    bars = _rising_bars(size)
    segment = size // candidate_count
    candidates = []
    for candidate in range(candidate_count):
        targets = [0] * size
        for index in range(candidate * segment, (candidate + 1) * segment):
            targets[index] = 100
        candidates.append(targets)
    candidate_ids = tuple(
        f"overfit-{index}" for index in range(candidate_count)
    )
    # The first segment is partly consumed by training; segment 1 is the first
    # full OOS winner under deterministic tie-breaking.
    result = run_research_validation(
        "overfit-1",
        bars,
        candidates,
        candidate_ids=candidate_ids,
        catalog=_catalog(candidate_ids),
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=True,
    )
    assert not result.promotable, result.reasons


def test_catalog_ledger_replaces_raw_trial_overrides():
    bars = _rising_bars(30)
    candidates = ([100] * 30, [50] * 30)
    candidate_ids = ("g", "g-alt")
    base_catalog = _catalog(candidate_ids)
    base = run_research_validation(
        "g",
        bars,
        candidates,
        candidate_ids=candidate_ids,
        catalog=base_catalog,
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=True,
    )

    large_catalog = _catalog(candidate_ids)
    for index in range(498):
        large_catalog.register_trial(stable_hash({"historical": index}))
    large = run_research_validation(
        "g",
        bars,
        candidates,
        candidate_ids=candidate_ids,
        catalog=large_catalog,
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=True,
    )
    assert base.trial_count == 2
    assert large.trial_count == 500
    assert large.deflated_sharpe <= base.deflated_sharpe


def test_pipeline_fails_on_detected_leakage():
    bars = _rising_bars(30)
    candidates = ([100] * 30, [50] * 30)
    candidate_ids = ("leak", "leak-alt")
    result = run_research_validation(
        "leak",
        bars,
        candidates,
        candidate_ids=candidate_ids,
        catalog=_catalog(candidate_ids),
        backtest=_BACKTEST,
        train_size=6,
        test_size=3,
        cost=CostModel(0, 0),
        leakage_ok=False,
    )
    assert result.verdict == ValidationVerdict.FAIL
    assert any("LEAKAGE" in reason for reason in result.reasons)


def test_pipeline_executes_every_oos_fold_through_the_injected_port():
    class RecordingPort:
        def __init__(self):
            self.calls = []

        def run(
            self,
            bars,
            target_shares,
            *,
            initial_capital=1_000_000.0,
            cost=CostModel(),
        ):
            self.calls.append(
                (tuple(bars), tuple(target_shares), initial_capital, cost)
            )
            return _BACKTEST.run(
                bars,
                target_shares,
                initial_capital=initial_capital,
                cost=cost,
            )

    port = RecordingPort()
    bars = _rising_bars(18)
    candidates = ([100] * len(bars), [50] * len(bars))
    candidate_ids = ("port-wiring", "port-wiring-alt")

    run_research_validation(
        "port-wiring",
        bars,
        candidates,
        candidate_ids=candidate_ids,
        catalog=_catalog(candidate_ids),
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
        def run(
            self,
            bars,
            target_shares,
            *,
            initial_capital=1_000_000.0,
            cost=CostModel(),
        ):
            return object()

    bars = _rising_bars(12)
    candidate_ids = ("invalid-output", "invalid-output-alt")
    with pytest.raises(TypeError, match="BacktestOutcome"):
        run_research_validation(
            "invalid-output",
            bars,
            ([100] * len(bars), [50] * len(bars)),
            candidate_ids=candidate_ids,
            catalog=_catalog(candidate_ids),
            backtest=InvalidOutputPort(),
            train_size=6,
            test_size=3,
            cost=CostModel(0, 0),
            leakage_ok=True,
        )


def test_pipeline_rejects_backtest_output_not_aligned_to_oos_bars():
    class MisalignedOutputPort:
        def run(
            self,
            bars,
            target_shares,
            *,
            initial_capital=1_000_000.0,
            cost=CostModel(),
        ):
            return BacktestOutcome(
                initial_capital=initial_capital,
                equity_curve=(initial_capital,),
            )

    bars = _rising_bars(12)
    candidate_ids = ("misaligned-output", "misaligned-output-alt")
    with pytest.raises(ValueError, match="equity_curve must align"):
        run_research_validation(
            "misaligned-output",
            bars,
            ([100] * len(bars), [50] * len(bars)),
            candidate_ids=candidate_ids,
            catalog=_catalog(candidate_ids),
            backtest=MisalignedOutputPort(),
            train_size=6,
            test_size=3,
            cost=CostModel(0, 0),
            leakage_ok=True,
        )


def test_pipeline_rejects_single_candidate_and_subject_relabeling():
    bars = _rising_bars(18)
    single_catalog = _catalog(("single",))
    with pytest.raises(ValueError, match="at least two"):
        run_research_validation(
            "single",
            bars,
            ([100] * len(bars),),
            candidate_ids=("single",),
            catalog=single_catalog,
            backtest=_BACKTEST,
            train_size=6,
            test_size=3,
            leakage_ok=True,
        )

    candidate_ids = ("best", "weaker")
    with pytest.raises(ValueError, match="selected best"):
        run_research_validation(
            "weaker",
            bars,
            ([100] * len(bars), [50] * len(bars)),
            candidate_ids=candidate_ids,
            catalog=_catalog(candidate_ids),
            backtest=_BACKTEST,
            train_size=6,
            test_size=3,
            leakage_ok=True,
        )
