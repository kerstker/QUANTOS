"""Phase 6 step 5b — alpha evaluator: series battery + dollar-neutral net-of-cost
panel backtest + catalog-wide DSR -> AlphaEvaluation."""
import math

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.evaluator import AlphaEvaluator
from services.alpha.panel_backtest import PanelPeriod
from services.research.backtest_engine.costs import CostModel


def _panel(n_periods=15, n_inst=12):
    insts = tuple(f"i{k}" for k in range(n_inst))
    periods = []
    for t in range(n_periods):
        signals = tuple(float(k) for k in range(n_inst))
        fwd = tuple(0.004 * (k - (n_inst - 1) / 2) + 0.002 * (((k * 5 + t * 3) % 7) - 3)
                    for k in range(n_inst))
        periods.append(PanelPeriod(instruments=insts, signals=signals, forward_returns=fwd))
    return periods


def _cat():
    c = AlphaCatalog()
    c.register(AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1",)))
    return c


def test_evaluation_populates_series_battery_and_coverage_ok():
    r = AlphaEvaluator(_cat(), cost=CostModel(0, 0)).evaluate("a1", _panel())
    assert r.coverage_ok is True
    assert r.icir is not None and r.ic_tstat is not None and r.ic_pvalue is not None
    assert math.isfinite(r.net_sharpe)
    assert 0.0 <= r.deflated_sharpe <= 1.0
    assert r.n_periods == 15


def test_metrics_are_net_of_cost():
    free = AlphaEvaluator(_cat(), cost=CostModel(0, 0)).evaluate("a1", _panel())
    costed = AlphaEvaluator(_cat(), cost=CostModel(fee_bps=50, slippage_bps=50)).evaluate("a1", _panel())
    assert costed.net_sharpe < free.net_sharpe


def test_evaluation_is_deterministic():
    r1 = AlphaEvaluator(_cat(), cost=CostModel(1, 2)).evaluate("a1", _panel())
    r2 = AlphaEvaluator(_cat(), cost=CostModel(1, 2)).evaluate("a1", _panel())
    assert r1.reproducibility_hash == r2.reproducibility_hash


def test_thin_coverage_routes_to_human_review():
    r = AlphaEvaluator(_cat(), cost=CostModel(0, 0)).evaluate("a1", _panel(n_periods=4))
    assert r.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert "THIN_COVERAGE" in r.reasons


def test_catalog_wide_trial_count_deflates_dsr():
    r_small = AlphaEvaluator(_cat(), cost=CostModel(0, 0)).evaluate("a1", _panel())
    big = _cat()
    for k in range(400):
        big.register_trial(f"c{k}")
    r_big = AlphaEvaluator(big, cost=CostModel(0, 0)).evaluate("a1", _panel())
    assert r_big.catalog_trial_count >= 400
    assert r_big.deflated_sharpe <= r_small.deflated_sharpe
