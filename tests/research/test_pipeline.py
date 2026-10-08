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
    # 2026-09-20 审计 P0-4:选择键已从 OOS 折改为**训练折**——这正是本次修复的要点,
    # 而且本用例恰好是最好的例证:两个键选出的赢家**不是同一个候选**。
    #   train 折收益和: overfit-0=0.002197  overfit-1=0.002829
    #                   overfit-2=0.003004  overfit-3=0.000328  -> 训练折选 overfit-2
    #   OOS   折收益和: overfit-0=0.000230  overfit-1=0.000777
    #                   overfit-2=0.000601  overfit-3=0.000676  -> 旧 OOS 键选 overfit-1
    # 此前 subject_id="overfit-1" 之所以成立,是因为选择就在被报告的 OOS 折上做的。
    # 现在必须命名训练折选出的 overfit-2,否则契约会抛错而不是悄悄放宽。
    result = run_research_validation(
        "overfit-2",
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
    # 2026-09-20 审计 P0-4:候选现在必须先在 **训练折** 上打分才能被选中,所以每个
    # 候选会在 OOS 折之外额外走一遍 train 窗口(长度 train_size=6)。本用例原本要
    # 保证的"每个 OOS 折都经由注入端口执行"依然成立,只是现在多了 train 那一遍。
    oos_lengths = [len(call[0]) for call in port.calls if len(call[0]) == 3]
    train_lengths = [len(call[0]) for call in port.calls if len(call[0]) == 6]
    assert len(port.calls) == 16
    assert len(oos_lengths) == 8        # 2 个候选 × 4 个 OOS 折
    assert len(train_lengths) == 8      # 2 个候选 × 4 个训练折
    assert all(len(call[0]) == len(call[1]) for call in port.calls)


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
