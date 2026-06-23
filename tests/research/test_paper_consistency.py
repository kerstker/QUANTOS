"""Phase 9 — backtest-vs-paper consistency feeds the decay/retirement loop."""
import pytest

from services.alpha.config import AlphaFactoryConfig
from services.alpha.decay import detect_decay
from services.research.paper_trading.domain.consistency import (
    is_consistent, paper_sharpe, performance_gap,
)
from services.research.paper_trading.domain.models import PaperRun


def test_performance_gap_and_consistency():
    assert performance_gap(1.5, 0.2) == pytest.approx((1.5 - 0.2) / 1.5)
    assert performance_gap(1.5, 1.4) == pytest.approx((1.5 - 1.4) / 1.5)
    assert performance_gap(0.0, 0.5) is None            # non-positive backtest -> undefined
    assert performance_gap(1.5, None) is None           # missing paper data
    assert is_consistent(1.5, 1.4, max_gap=0.6) is True
    assert is_consistent(1.5, 0.2, max_gap=0.6) is False
    assert is_consistent(1.5, None) is False            # fail-closed on missing evidence


def test_paper_sharpe_extracted_from_run_metrics():
    assert paper_sharpe(PaperRun(run_id="r", alpha_id="a", metrics={"sharpe": 0.7})) == pytest.approx(0.7)
    assert paper_sharpe(PaperRun(run_id="r", alpha_id="a")) is None


def test_paper_underperformance_triggers_decay_retirement():
    # IC series healthy, but paper Sharpe (0.2) << backtest (1.5) -> perf_gap 0.87 -> decay
    run = PaperRun(run_id="r", alpha_id="a", metrics={"sharpe": 0.2})
    gap = performance_gap(1.5, paper_sharpe(run))
    assert gap > 0.6
    rep = detect_decay("a", [0.10, 0.11, 0.09, 0.10, 0.10, 0.11, 0.10],
                       config=AlphaFactoryConfig(), baseline=0.10, perf_gap=gap)
    assert rep.decayed is True and "PERF_GAP_EXCEEDS_MAX" in rep.reasons
