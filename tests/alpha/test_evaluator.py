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


def _seeded_cat(extra_trials=0):
    """Catalog with a1 + 2 populated seed alphas, so catalog_var_sr is a real
    estimate (not the fail-closed None for a <2-population catalog)."""
    c = _cat()
    for i, rets in enumerate([(0.01, 0.02, -0.01, 0.03), (0.02, -0.01, 0.0, 0.01)]):
        c.register(AlphaDefinition(alpha_id=f"seed{i}", name="S", factor_ids=(f"sf{i}",)))
        c.set_returns(f"seed{i}", rets)
    for k in range(extra_trials):
        c.register_trial(f"x{k}")
    return c


def test_catalog_wide_trial_count_deflates_dsr():
    r_small = AlphaEvaluator(_seeded_cat(0), cost=CostModel(0, 0)).evaluate("a1", _panel())
    r_big = AlphaEvaluator(_seeded_cat(400), cost=CostModel(0, 0)).evaluate("a1", _panel())
    assert r_big.catalog_trial_count >= 400
    assert r_big.deflated_sharpe <= r_small.deflated_sharpe


def test_single_alpha_catalog_routes_to_human_review():
    # a <2-population catalog has no honest var_sr -> fail-closed (review P0)
    r = AlphaEvaluator(_cat(), cost=CostModel(0, 0)).evaluate("a1", _panel())
    assert r.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert "INSUFFICIENT_CATALOG_FOR_DSR" in r.reasons


def _panel_with_adv(n_periods=15, n_inst=12, adv=1e5):
    insts = tuple(f"i{k}" for k in range(n_inst))
    out = []
    for t in range(n_periods):
        signals = tuple(float(k) for k in range(n_inst))
        fwd = tuple(0.004 * (k - (n_inst - 1) / 2) + 0.002 * (((k * 5 + t * 3) % 7) - 3)
                    for k in range(n_inst))
        out.append(PanelPeriod(instruments=insts, signals=signals, forward_returns=fwd,
                               adv_notional=tuple(adv for _ in range(n_inst))))
    return out


def test_evaluation_populates_marginal_ir_and_capacity_adj_sharpe():
    r = AlphaEvaluator(_seeded_cat(0), cost=CostModel(0, 0)).evaluate("a1", _panel_with_adv())
    assert r.marginal_ir is not None                       # incremental edge computed
    assert abs(r.capacity_adj_sharpe) <= abs(r.net_sharpe)  # capacity haircut in [0,1]


def test_unassessed_evidence_fails_closed_to_human_review():
    # coverage ok + DSR computable (seeded catalog), but leakage/pbo/regime were
    # NOT supplied -> must NOT auto-PASS; parks for a human (audit P0/P1).
    r = AlphaEvaluator(_seeded_cat(0), cost=CostModel(0, 0)).evaluate("a1", _panel())
    assert r.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert set(r.reasons) >= {"LEAKAGE_NOT_ASSESSED", "OVERFIT_NOT_ASSESSED", "REGIME_NOT_ASSESSED"}


def test_partial_evidence_still_fails_closed_on_the_missing_piece():
    # supply leakage + regime but NOT pbo -> still parked, only on the missing one
    r = AlphaEvaluator(_seeded_cat(0), cost=CostModel(0, 0)).evaluate(
        "a1", _panel(), leakage_ok=True, regime_profitable_frac=1.0)
    assert r.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert r.reasons == ("OVERFIT_NOT_ASSESSED",)


def test_fully_assessed_evidence_reaches_the_real_gate():
    # all three supplied -> gated on real thresholds, not parked for missing evidence
    r = AlphaEvaluator(_seeded_cat(0), cost=CostModel(0, 0)).evaluate(
        "a1", _panel(), leakage_ok=True, pbo=0.1, regime_profitable_frac=1.0)
    assert all("NOT_ASSESSED" not in reason for reason in r.reasons)


def test_compute_regime_evidence_flag_assesses_regime_from_panel():
    # opt-in flag: the evaluator derives regime evidence from the panel itself, so the regime
    # gate participates with REAL evidence; leakage/pbo still unassessed -> still human review
    # (the flag never weakens the gate, it only removes the REGIME 'not assessed' fail-closed).
    from services.alpha.config import AlphaFactoryConfig
    cfg = AlphaFactoryConfig(compute_regime_evidence=True, regime_vol_window=5)
    r = AlphaEvaluator(_seeded_cat(0), cfg, cost=CostModel(0, 0)).evaluate("a1", _panel())
    assert r.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert "REGIME_NOT_ASSESSED" not in r.reasons
    assert set(r.reasons) >= {"LEAKAGE_NOT_ASSESSED", "OVERFIT_NOT_ASSESSED"}


def test_compute_regime_evidence_default_off_unchanged():
    # default config -> regime still NOT assessed (byte-identical fail-closed behavior)
    r = AlphaEvaluator(_seeded_cat(0), cost=CostModel(0, 0)).evaluate("a1", _panel())
    assert "REGIME_NOT_ASSESSED" in r.reasons
