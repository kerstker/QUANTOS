"""Phase 6 step 13 — factory orchestration + the hard safety invariant: the
automated factory can NEVER auto-approve."""
import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, AlphaEvaluation
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.factory import AlphaFactory


class _StubEvaluator:
    """Returns a canned verdict so we test orchestration, not eval numerics."""
    def __init__(self, catalog, verdict):
        self.catalog = catalog
        self.verdict = verdict

    def evaluate(self, alpha_id, periods, **kw):
        self.catalog.set_returns(alpha_id, (0.01, 0.0, 0.02, -0.01))
        return AlphaEvaluation(alpha_id=alpha_id, verdict=self.verdict, deflated_sharpe=0.8,
                               pbo=0.1, catalog_trial_count=self.catalog.global_trial_count(),
                               marginal_ir=0.5)


def _factory(verdict):
    cat = AlphaCatalog()
    return AlphaFactory(catalog=cat, evaluator=_StubEvaluator(cat, verdict)), cat


def _cands(n=1):
    return [(AlphaDefinition(alpha_id=f"a{i}", name=f"A{i}", factor_ids=(f"f{i}",)), object())
            for i in range(n)]


def test_pass_advances_to_paper_but_never_approved():
    fac, cat = _factory(ValidationVerdict.PASS)
    report = fac.run(_cands(1))
    assert report["a0"]["state"] == AlphaState.PAPER_TRADING
    # the safety invariant: nothing is APPROVED
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())


def test_fail_is_rejected():
    fac, cat = _factory(ValidationVerdict.FAIL)
    fac.run(_cands(1))
    assert cat.state("a0") == AlphaState.REJECTED


def test_requires_human_review_is_parked_in_testing():
    fac, cat = _factory(ValidationVerdict.REQUIRES_HUMAN_REVIEW)
    fac.run(_cands(1))
    assert cat.state("a0") == AlphaState.TESTING        # parked for a human, not auto-rejected


def test_factory_must_run_as_non_human_actor():
    with pytest.raises(ValueError):
        AlphaFactory(actor=Actor(actor_id="alice", kind=ActorKind.HUMAN))


def test_factory_run_is_deterministic():
    r1, c1 = _factory(ValidationVerdict.PASS)
    r2, c2 = _factory(ValidationVerdict.PASS)
    out1 = {k: v["state"] for k, v in r1.run(_cands(3)).items()}
    out2 = {k: v["state"] for k, v in r2.run(_cands(3)).items()}
    assert out1 == out2 and all(s == AlphaState.PAPER_TRADING for s in out1.values())
