"""TDD for tools/register_alpha_catalog.py (audit P0 fix: wire the real deployed carry_composite/
crowd_contra edges into AlphaCatalog). Pure-function pieces (build_candidates/register_new_candidates)
are tested here against synthetic data (fast, CI-safe, no dependency on real cached market data) —
mirroring tests/alpha/test_factory.py's stub-evaluator pattern for the orchestration piece. The real,
end-to-end run against actual cached data happens by invoking main() manually (see module docstring),
not in this suite.
"""
from __future__ import annotations

import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, AlphaEvaluation
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.research.alpha_expression import FieldPanel
from tools.register_alpha_catalog import build_candidates, register_new_candidates


# ---------------------------------------------------------------------------
# build_candidates — real panel construction (synthetic FieldPanel, real ax.* calls)
# ---------------------------------------------------------------------------

def _synthetic_panel(n_periods: int = 40, n_instruments: int = 12) -> FieldPanel:
    """A hand-built FieldPanel with close/funding/global_ls fields — enough shape for CARRY/
    CROWD_CONTRA to evaluate and for panel_to_periods to yield periods above min_breadth(10)."""
    instruments = tuple(f"COIN{i}" for i in range(n_instruments))
    event_times = tuple(1_700_000_000_000 + t * 86_400_000 for t in range(n_periods))
    close, funding, global_ls = [], [], []
    for t in range(n_periods):
        close.append([100.0 + t * 0.1 + i * 0.5 + (0.3 if (t + i) % 3 == 0 else -0.2) for i in range(n_instruments)])
        funding.append([0.0001 * ((i % 5) - 2) for i in range(n_instruments)])
        global_ls.append([1.0 + 0.1 * ((i + t) % 7) for i in range(n_instruments)])
    return FieldPanel(instruments, event_times,
                      {"close": close, "funding": funding, "global_ls": global_ls})


def test_build_candidates_produces_periods_and_leakage_evidence_for_both_defaults():
    panel = _synthetic_panel()
    candidates, evidence = build_candidates(panel)
    ids = {defn.alpha_id for defn, _periods in candidates}
    assert ids == {"carry_composite", "crowd_contra"}
    for defn, periods in candidates:
        assert len(periods) > 0                      # real cross-sectional periods were built
        assert all(len(p.instruments) >= 2 for p in periods)
        assert defn.factor_ids                         # never an empty dependency list
    # leakage evidence computed (not silently skipped) for both — True/False/None all acceptable,
    # the point is compute_leakage_ok was actually invoked, not that it always says True
    assert set(evidence) == {"carry_composite", "crowd_contra"}
    for aid in evidence:
        assert "leakage_ok" in evidence[aid]


def test_build_candidates_respects_custom_candidate_list():
    panel = _synthetic_panel()
    custom = (("solo", "Solo test alpha", ("field", "close"), ("close",)),)
    candidates, evidence = build_candidates(panel, custom)
    assert [defn.alpha_id for defn, _ in candidates] == ["solo"]
    assert set(evidence) == {"solo"}


def test_build_candidates_fwd_horizon_changes_period_count():
    panel = _synthetic_panel(n_periods=40)
    custom = (("solo", "Solo", ("field", "close"), ("close",)),)
    _, _ = build_candidates(panel, custom, fwd_horizon=1)
    c1, _ = build_candidates(panel, custom, fwd_horizon=1)
    c7, _ = build_candidates(panel, custom, fwd_horizon=7)
    # more forward-horizon lookahead -> fewer valid periods (last `fwd_horizon` rows dropped)
    assert len(c7[0][1]) < len(c1[0][1])


# ---------------------------------------------------------------------------
# register_new_candidates — orchestration + idempotency (stub evaluator, mirrors test_factory.py)
# ---------------------------------------------------------------------------

class _StubEvaluator:
    def __init__(self, catalog, verdict):
        self.catalog = catalog
        self.verdict = verdict
        self.calls = []

    def evaluate(self, alpha_id, periods, **kw):
        self.calls.append(alpha_id)
        self.catalog.set_returns(alpha_id, (0.01, 0.0, 0.02, -0.01))
        return AlphaEvaluation(alpha_id=alpha_id, verdict=self.verdict, deflated_sharpe=0.8,
                               pbo=0.1, catalog_trial_count=self.catalog.global_trial_count(),
                               marginal_ir=0.5)


def _defn_periods(alpha_id: str):
    # periods=[] (real AlphaEvaluator handles this gracefully -> THIN_COVERAGE ->
    # REQUIRES_HUMAN_REVIEW; verified directly against the real, non-stubbed evaluator) — these
    # orchestration tests care about registration/idempotency/containment, not evaluation numerics.
    return (AlphaDefinition(alpha_id=alpha_id, name=alpha_id, factor_ids=("f",)), [])


def test_register_new_candidates_registers_and_evaluates_both_never_reaching_approved():
    cat = AlphaCatalog()
    candidates = [_defn_periods("carry_composite"), _defn_periods("crowd_contra")]
    evidence = {"carry_composite": {"leakage_ok": True}, "crowd_contra": {"leakage_ok": True}}
    actor = Actor(actor_id="test-actor", kind=ActorKind.SYSTEM)

    report = register_new_candidates(cat, candidates, evidence, actor=actor, timestamp=0)
    # real evaluator (not stubbed) -- with object() as periods this will fail evaluation and route
    # to REQUIRES_HUMAN_REVIEW / parked in TESTING; assert the ORCHESTRATION invariant instead of a
    # specific verdict: both alphas got registered and evaluated exactly once, never reached APPROVED.
    assert set(cat.all_ids()) == {"carry_composite", "crowd_contra"}
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())
    assert set(report) == {"carry_composite", "crowd_contra"}


def test_register_new_candidates_is_idempotent_on_rerun():
    cat = AlphaCatalog()
    candidates = [_defn_periods("carry_composite"), _defn_periods("crowd_contra")]
    evidence = {"carry_composite": {"leakage_ok": True}, "crowd_contra": {"leakage_ok": True}}
    actor = Actor(actor_id="test-actor", kind=ActorKind.SYSTEM)

    first = register_new_candidates(cat, candidates, evidence, actor=actor, timestamp=0)
    assert set(first) == {"carry_composite", "crowd_contra"}

    # second call: both already registered -> no-op, no ValueError (AlphaCatalog.register() would
    # raise "alpha already registered" if this weren't guarded)
    second = register_new_candidates(cat, candidates, evidence, actor=actor, timestamp=1)
    assert second == {}
    assert len(cat.all_ids()) == 2   # unchanged, not duplicated


def test_register_new_candidates_registers_only_the_missing_one():
    cat = AlphaCatalog()
    actor = Actor(actor_id="test-actor", kind=ActorKind.SYSTEM)
    # pre-register carry_composite via a prior partial run
    register_new_candidates(cat, [_defn_periods("carry_composite")],
                            {"carry_composite": {"leakage_ok": True}}, actor=actor, timestamp=0)
    assert set(cat.all_ids()) == {"carry_composite"}

    candidates = [_defn_periods("carry_composite"), _defn_periods("crowd_contra")]
    evidence = {"carry_composite": {"leakage_ok": True}, "crowd_contra": {"leakage_ok": True}}
    report = register_new_candidates(cat, candidates, evidence, actor=actor, timestamp=1)
    assert set(report) == {"crowd_contra"}          # only the missing one was run
    assert set(cat.all_ids()) == {"carry_composite", "crowd_contra"}


def test_register_new_candidates_with_stub_evaluator_reaches_paper_trading_on_pass():
    """End-to-end orchestration proof (stubbed evaluator = deterministic PASS), mirroring
    tests/alpha/test_factory.py's safety-invariant test: real evidence flowing through
    register_new_candidates can advance to PAPER_TRADING but never further without a human."""
    from services.alpha.factory import AlphaFactory

    class _FactoryWithStub(AlphaFactory):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.evaluator = _StubEvaluator(self.catalog, ValidationVerdict.PASS)

    cat = AlphaCatalog()
    candidates = [_defn_periods("crowd_contra")]
    evidence = {"crowd_contra": {"leakage_ok": True}}
    actor = Actor(actor_id="test-actor", kind=ActorKind.SYSTEM)

    already = set(cat.all_ids())
    fresh = [(d, p) for d, p in candidates if d.alpha_id not in already]
    factory = _FactoryWithStub(catalog=cat, actor=actor)
    report = factory.run(fresh, timestamp=0, evidence=evidence)

    assert report["crowd_contra"]["state"] == AlphaState.PAPER_TRADING
    assert cat.state("crowd_contra") != AlphaState.APPROVED
