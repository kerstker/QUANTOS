"""Phase 6 step 5b — alpha evaluator: series battery + dollar-neutral net-of-cost
panel backtest + catalog-wide DSR -> AlphaEvaluation."""
import math

import pytest

from quantos.contracts.alpha import AlphaDefinition, PboAlignment
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.evaluator import AlphaEvaluator
from services.alpha.panel_backtest import PanelPeriod, run_panel_backtest
from services.research.backtest_engine.costs import CostModel
from services.validation.pbo_evidence import create_batch_pbo_evidence


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


def _cat_with_approved(approved_returns):
    """a1 (the candidate under test) + an already-APPROVED alpha with the given return series."""
    c = AlphaCatalog()
    c.register(AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1",)))
    c.register(AlphaDefinition(alpha_id="approved1", name="B", factor_ids=("f2",)))
    c.set_returns("approved1", approved_returns)
    c._set_state("approved1", AlphaState.APPROVED)
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


def test_periods_per_year_default_is_252_and_matches_sharpe_directly():
    """2026-07-09: a caller whose periods are a coarser cadence than daily (e.g. weekly, via
    panel_to_periods(non_overlapping=True)) must be able to annualize correctly. Default (252,
    unspecified) must reproduce the exact value services.research.backtest_engine.metrics.sharpe
    itself would give at its own default ppy — i.e. this is genuinely wired through, not ignored."""
    from services.alpha.panel_backtest import run_panel_backtest
    from services.research.backtest_engine.metrics import sharpe as _sharpe
    cost = CostModel(0, 0)
    periods = _panel()
    r_default = AlphaEvaluator(_cat(), cost=cost).evaluate("a1", periods)
    net = run_panel_backtest(periods, cost=cost).period_returns
    assert math.isclose(r_default.net_sharpe, _sharpe(net), rel_tol=1e-9)


def test_periods_per_year_override_changes_net_sharpe_by_the_expected_sqrt_ratio():
    """A weekly caller (ppy=52) must NOT get the daily (ppy=252) annualization — confirms the
    2026-07-09 fix (tools/register_alpha_catalog.py) actually changes the reported net_sharpe,
    not just accepts-and-ignores the parameter."""
    cost = CostModel(0, 0)
    daily = AlphaEvaluator(_cat(), cost=cost, periods_per_year=252).evaluate("a1", _panel())
    weekly = AlphaEvaluator(_cat(), cost=cost, periods_per_year=52).evaluate("a1", _panel())
    assert daily.net_sharpe != weekly.net_sharpe
    assert math.isclose(weekly.net_sharpe, daily.net_sharpe * math.sqrt(52 / 252), rel_tol=1e-9)


def test_periods_per_year_also_scales_marginal_ir_not_just_net_sharpe():
    """2026-07-09 sibling bug (found by adversarial re-verification of the above fix):
    marginal_ir (from services/alpha/orthogonalize.py) used to hardcode sqrt(252) regardless of
    self.periods_per_year, silently inflating it ~2.2x whenever a caller (like the real weekly
    tools/register_alpha_catalog.py registration) used periods_per_year=52. Confirms evaluate()
    now threads periods_per_year into the orthogonalize() call too, not just sharpe()."""
    cost = CostModel(0, 0)
    daily = AlphaEvaluator(_cat(), cost=cost, periods_per_year=252).evaluate("a1", _panel())
    weekly = AlphaEvaluator(_cat(), cost=cost, periods_per_year=52).evaluate("a1", _panel())
    assert daily.marginal_ir is not None and weekly.marginal_ir is not None
    assert daily.marginal_ir != weekly.marginal_ir
    assert math.isclose(weekly.marginal_ir, daily.marginal_ir * math.sqrt(52 / 252), rel_tol=1e-9)


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


def _bound_pbo(cat, alpha_id="a1"):
    subject_hash = cat.get(alpha_id).definition_hash
    trial_hashes = list(cat.snapshot().trials)
    if len(trial_hashes) < 2:
        extra_hash = "f" * 64 if subject_hash != "f" * 64 else "e" * 64
        cat.register_trial(extra_hash)
        trial_hashes = list(cat.snapshot().trials)
    ordered = (subject_hash, *sorted(value for value in trial_hashes if value != subject_hash))
    matrix = tuple(
        tuple(base + column * 0.003 for column in range(len(ordered)))
        for base in (0.04, -0.03, 0.05, -0.01)
    )
    return create_batch_pbo_evidence(
        candidate_definition_hashes=ordered,
        performance_matrix=matrix,
        alignment=PboAlignment.POSITIONAL_LEGACY,
        aligned_period_count=8,
    )


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
    cat = _seeded_cat(0)
    evidence = _bound_pbo(cat)
    r = AlphaEvaluator(cat, cost=CostModel(0, 0)).evaluate(
        "a1", _panel(), leakage_ok=True, pbo_evidence=evidence, regime_profitable_frac=1.0)
    assert all("NOT_ASSESSED" not in reason for reason in r.reasons)
    assert r.pbo == evidence.pbo
    assert r.pbo_evidence == evidence
    assert r.pbo_evidence_hash == evidence.evidence_hash
    assert r.catalog_trial_set_hash == cat.trial_set_hash()


def test_raw_pbo_scalar_is_no_longer_an_evaluator_api():
    with pytest.raises(TypeError):
        AlphaEvaluator(_seeded_cat(0), cost=CostModel(0, 0)).evaluate(
            "a1", _panel(), leakage_ok=True, pbo=0.1, regime_profitable_frac=1.0
        )


def test_foreign_candidate_pbo_evidence_fails_closed():
    cat = _seeded_cat(0)
    foreign = tuple(value for value in cat.snapshot().trials if value != cat.get("a1").definition_hash)
    evidence = create_batch_pbo_evidence(
        candidate_definition_hashes=foreign,
        performance_matrix=((0.1, 0.0), (-0.1, 0.1), (0.2, -0.1), (-0.2, 0.2)),
        alignment=PboAlignment.POSITIONAL_LEGACY,
        aligned_period_count=8,
    )
    result = AlphaEvaluator(cat, cost=CostModel(0, 0)).evaluate(
        "a1", _panel(), leakage_ok=True, pbo_evidence=evidence, regime_profitable_frac=1.0
    )
    assert result.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert "OVERFIT_EVIDENCE_SUBJECT_MISMATCH" in result.reasons


def test_tampered_pbo_evidence_fails_closed():
    cat = _seeded_cat(0)
    evidence = _bound_pbo(cat)
    tampered = evidence.model_copy(update={"pbo": 1.0 - evidence.pbo})
    result = AlphaEvaluator(cat, cost=CostModel(0, 0)).evaluate(
        "a1", _panel(), leakage_ok=True, pbo_evidence=tampered, regime_profitable_frac=1.0
    )
    assert result.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert "OVERFIT_EVIDENCE_INVALID" in result.reasons


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


# ---- marginal_ir redundancy gate (2026-07-09, gap audit rank #3) -----------------------------

def test_candidate_duplicating_an_approved_alpha_routes_to_human_review():
    """A candidate whose net returns are IDENTICAL to an already-approved alpha's has ~zero
    marginal_ir (fully explained by the approved set) — must not silently PASS just because its
    own DSR/PBO/leakage/regime all clear in isolation."""
    cost = CostModel(0, 0)
    net = run_panel_backtest(_panel(), cost=cost).period_returns
    cat = _cat_with_approved(approved_returns=net)   # approved1's returns == a1's own net returns
    cfg = AlphaFactoryConfig(min_overlap=10)          # panel has 15 periods; default min_overlap=60
    r = AlphaEvaluator(cat, cfg, cost=cost).evaluate(
        "a1", _panel(), leakage_ok=True, pbo_evidence=_bound_pbo(cat), regime_profitable_frac=1.0)
    assert r.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert "TOO_REDUNDANT_VS_APPROVED_SET" in r.reasons
    assert r.marginal_ir is not None and abs(r.marginal_ir) < cfg.min_marginal_ir_vs_approved


def test_candidate_genuinely_independent_of_approved_set_is_not_flagged_redundant():
    """A candidate whose net returns are essentially UNCORRELATED with the approved alpha's keeps
    a healthy marginal_ir and must NOT be routed to human review by the redundancy check (though
    it may still land there for other honest reasons — this test only asserts the redundancy
    reason specifically is absent)."""
    cost = CostModel(0, 0)
    net = run_panel_backtest(_panel(), cost=cost).period_returns
    # an approved-alpha series that alternates independently of a1's own pattern
    unrelated = tuple(0.01 if i % 2 == 0 else -0.01 for i in range(len(net)))
    cat = _cat_with_approved(approved_returns=unrelated)
    cfg = AlphaFactoryConfig(min_overlap=10)
    r = AlphaEvaluator(cat, cfg, cost=cost).evaluate(
        "a1", _panel(), leakage_ok=True, pbo_evidence=_bound_pbo(cat), regime_profitable_frac=1.0)
    assert "TOO_REDUNDANT_VS_APPROVED_SET" not in r.reasons
    assert r.marginal_ir is not None and abs(r.marginal_ir) >= cfg.min_marginal_ir_vs_approved


def test_insufficient_overlap_with_nonempty_approved_set_routes_to_human_review():
    """An approved set exists, but overlap is too thin to honestly compute a marginal_ir (default
    min_overlap=60 vs this panel's 15 periods) — must fail closed rather than silently PASS an
    unassessed-for-redundancy candidate, distinct from the TOO_REDUNDANT reason."""
    cost = CostModel(0, 0)
    net = run_panel_backtest(_panel(), cost=cost).period_returns
    cat = _cat_with_approved(approved_returns=net)   # default config: min_overlap=60 > 15 periods
    r = AlphaEvaluator(cat, cost=cost).evaluate(
        "a1", _panel(), leakage_ok=True, pbo_evidence=_bound_pbo(cat), regime_profitable_frac=1.0)
    assert r.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert "MARGINAL_IR_NOT_ASSESSED" in r.reasons
    assert r.marginal_ir is None


def test_empty_approved_set_never_triggers_the_redundancy_check():
    """No approved alpha exists yet (e.g. a catalog's first-ever registration) — there is nothing
    to be redundant AGAINST, so the new check must be a complete no-op, matching pre-fix
    behavior exactly (regression guard for the 2026-07-09 gap-audit rank #3 change)."""
    cat = _seeded_cat(0)
    r = AlphaEvaluator(cat, cost=CostModel(0, 0)).evaluate(
        "a1", _panel(), leakage_ok=True, pbo_evidence=_bound_pbo(cat), regime_profitable_frac=1.0)
    assert "TOO_REDUNDANT_VS_APPROVED_SET" not in r.reasons
    assert "MARGINAL_IR_NOT_ASSESSED" not in r.reasons


# ---- pairwise-correlation redundancy gate (2026-07-10, gap audit rank #3 follow-up) -----------
# marginal_ir's multivariate Gram-Schmidt residual can clear a candidate that is still ~99.9%
# pairwise-correlated with ONE approved alpha, as long as the candidate carries enough of its own
# idiosyncratic edge — independently verified via adversarial review. This narrower single-pair
# check catches exactly that case.

def _highly_correlated_series(n=200, own_edge=0.0005):
    approved = tuple(0.001 * math.sin(i * 0.31) + 0.0001 for i in range(n))
    candidate = tuple(0.90 * a + 0.10 * (own_edge + 0.0005 * math.sin(i * 0.53))
                      for i, a in enumerate(approved))
    return approved, candidate


def _evaluate_with_pinned_net(cat, cfg, net, *, evidence=None):
    """Pin AlphaEvaluator's internal `net` (period_returns) to an exact hand-crafted series —
    the cross-sectional panel-backtest weighting can't be reverse-engineered to hit an exact
    target series directly, so this monkeypatches run_panel_backtest for the call only."""
    import services.alpha.evaluator as ev_mod

    class _FakeRes:
        def __init__(self, period_returns):
            self.period_returns = period_returns
            self.turnovers = [0.1] * len(period_returns)

    real = ev_mod.run_panel_backtest
    ev_mod.run_panel_backtest = lambda periods, cost=None, quantile=None, target_aum=None: _FakeRes(net)
    try:
        evidence = evidence or {
            "leakage_ok": True,
            "pbo_evidence": _bound_pbo(cat),
            "regime_profitable_frac": 1.0,
        }
        return AlphaEvaluator(cat, cfg, cost=CostModel(0, 0)).evaluate("a1", _panel(n_periods=15), **evidence)
    finally:
        ev_mod.run_panel_backtest = real


def test_candidate_highly_pairwise_correlated_to_one_approved_alpha_routes_to_human_review():
    """The exact gap marginal_ir alone can miss: ~99.9% pairwise-correlated with one approved
    alpha, but the candidate's own idiosyncratic edge is strong enough to clear marginal_ir >=
    0.10 anyway. The narrower pairwise-correlation check must still catch it."""
    approved, candidate = _highly_correlated_series()
    cat = _cat_with_approved(approved_returns=approved)
    cfg = AlphaFactoryConfig(min_overlap=10)
    r = _evaluate_with_pinned_net(cat, cfg, candidate)
    assert r.max_corr_vs_approved is not None and r.max_corr_vs_approved > cfg.rho_cut
    assert r.marginal_ir is not None and r.marginal_ir >= cfg.min_marginal_ir_vs_approved  # clears the OTHER check
    assert r.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
    assert "TOO_CORRELATED_VS_APPROVED_SET" in r.reasons


def test_candidate_with_low_correlation_is_not_flagged_by_the_pairwise_check():
    approved, _ = _highly_correlated_series()
    independent = tuple(0.001 if i % 2 == 0 else -0.001 for i in range(len(approved)))
    cat = _cat_with_approved(approved_returns=approved)
    cfg = AlphaFactoryConfig(min_overlap=10)
    r = _evaluate_with_pinned_net(cat, cfg, independent)
    assert "TOO_CORRELATED_VS_APPROVED_SET" not in r.reasons


def test_pairwise_correlation_check_is_a_noop_on_empty_approved_set():
    """Regression guard: no approved alpha yet -> the new check must never fire."""
    cat = _seeded_cat(0)
    r = AlphaEvaluator(cat, cost=CostModel(0, 0)).evaluate(
        "a1", _panel(), leakage_ok=True, pbo_evidence=_bound_pbo(cat), regime_profitable_frac=1.0)
    assert "TOO_CORRELATED_VS_APPROVED_SET" not in r.reasons


# ---- capacity_ratio wiring + default impact_coef (2026-07-10, gap audit rank #5) --------------

def test_capacity_ratio_is_none_without_real_adv_data():
    """Every real production PanelPeriod-construction path today never populates adv_notional —
    capacity_ratio must stay None (not assessed), never defaulted to a passing/failing value."""
    cat = _seeded_cat(0)
    r = AlphaEvaluator(cat, cost=CostModel(0, 0)).evaluate(
        "a1", _panel(), leakage_ok=True, pbo_evidence=_bound_pbo(cat), regime_profitable_frac=1.0)
    assert not any("CAPACITY_BELOW_TARGET_AUM" in reason for reason in r.reasons)


def test_thin_capacity_reaches_the_gate_as_a_warn():
    """With real ADV data present and a target_aum the alpha genuinely cannot carry, the new
    capacity_ratio wiring must reach ValidationGate and produce the CAPACITY WARN reason."""
    cat = _seeded_cat(0)
    r = AlphaEvaluator(cat, cost=CostModel(0, 0), target_aum=1e12).evaluate(
        "a1", _panel_with_adv(adv=1e5), leakage_ok=True,
        pbo_evidence=_bound_pbo(cat), regime_profitable_frac=1.0)
    assert any("CAPACITY_BELOW_TARGET_AUM" in reason for reason in r.reasons)
    assert r.verdict in (ValidationVerdict.WARN, ValidationVerdict.FAIL)


def test_default_impact_coef_is_inert_without_adv_notional():
    """2026-07-10: AlphaEvaluator's default CostModel gained a nonzero impact_coef (10.0). This
    MUST NOT change any result for periods without adv_notional — confirmed by comparing the
    fully-default evaluator (impact_coef=10.0 implicitly) against one with impact EXPLICITLY
    disabled (impact_coef=0.0), both fed the same adv_notional-less panel: byte-identical."""
    cat_a, cat_b = _seeded_cat(0), _seeded_cat(0)
    default_evaluator = AlphaEvaluator(cat_a)   # picks up the new impact_coef=10.0 default
    explicit_no_impact = AlphaEvaluator(cat_b, cost=CostModel(fee_bps=1.0, slippage_bps=2.0, impact_coef=0.0))
    r_default = default_evaluator.evaluate("a1", _panel())
    r_explicit = explicit_no_impact.evaluate("a1", _panel())
    assert r_default.net_sharpe == r_explicit.net_sharpe
    assert r_default.deflated_sharpe == r_explicit.deflated_sharpe
    assert r_default.reproducibility_hash == r_explicit.reproducibility_hash


def test_default_impact_coef_activates_when_adv_notional_is_present():
    """The flip side: once a caller DOES supply real adv_notional, the new nonzero default
    impact_coef must actually bite (lower net_sharpe than impact explicitly disabled) — proving
    it's genuinely wired through run_panel_backtest, not silently inert everywhere."""
    cat_a, cat_b = _seeded_cat(0), _seeded_cat(0)
    default_evaluator = AlphaEvaluator(cat_a)
    explicit_no_impact = AlphaEvaluator(cat_b, cost=CostModel(fee_bps=1.0, slippage_bps=2.0, impact_coef=0.0))
    panel = _panel_with_adv(adv=1e4)   # small ADV -> high participation -> impact cost bites
    r_default = default_evaluator.evaluate("a1", panel)
    r_explicit = explicit_no_impact.evaluate("a1", panel)
    assert r_default.net_sharpe != r_explicit.net_sharpe
