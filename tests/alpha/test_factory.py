"""Phase 6 step 13 — factory orchestration + the hard safety invariant: the
automated factory can NEVER auto-approve."""
import pytest

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, AlphaEvaluation
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.factory import AlphaFactory
from tests.alpha.fixtures import catalog_bound_evaluation


class _StubEvaluator:
    """Returns a canned verdict so we test orchestration, not eval numerics."""
    def __init__(self, catalog, verdict):
        self.catalog = catalog
        self.verdict = verdict

    def prepare_catalog_observation(self, alpha_id, periods):
        del periods
        offset = 0.001 * (len(self.catalog.all_ids()) + 1)
        self.catalog.set_returns(
            alpha_id, (0.01 + offset, 0.0, 0.02 + offset, -0.01)
        )

    def evaluate(self, alpha_id, periods, **kw):
        pbo_evidence = kw.get("pbo_evidence")
        if self.catalog.evidence(alpha_id) is None:
            return AlphaEvaluation(
                alpha_id=alpha_id,
                verdict=self.verdict,
                catalog_trial_count=self.catalog.global_trial_count(),
                catalog_trial_set_hash=self.catalog.trial_set_hash(),
            )
        return catalog_bound_evaluation(
            self.catalog,
            alpha_id,
            verdict=self.verdict,
            pbo_evidence=pbo_evidence,
        )


def _factory(verdict):
    cat = AlphaCatalog()
    return AlphaFactory(catalog=cat, evaluator=_StubEvaluator(cat, verdict)), cat


def _cands(n=1):
    return [(AlphaDefinition(alpha_id=f"a{i}", name=f"A{i}", factor_ids=(f"f{i}",)), object())
            for i in range(n)]


def test_pass_advances_to_paper_but_never_approved():
    fac, cat = _factory(ValidationVerdict.PASS)
    report = fac.run(_real_cands(2))
    assert report["rc0"]["state"] == AlphaState.PAPER_TRADING
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
    out1 = {k: v["state"] for k, v in r1.run(_real_cands(3)).items()}
    out2 = {k: v["state"] for k, v in r2.run(_real_cands(3)).items()}
    assert out1 == out2 and all(s == AlphaState.PAPER_TRADING for s in out1.values())


# --- real-evaluator integration: PBO wiring + fail-closed evidence (audit P0/P1) ---
from services.alpha.panel_backtest import PanelPeriod


def _seeded_catalog():
    cat = AlphaCatalog()
    for i, rets in enumerate([(0.01, 0.02, -0.01, 0.03), (0.02, -0.01, 0.0, 0.01)]):
        cat.register(AlphaDefinition(alpha_id=f"seed{i}", name="S", factor_ids=(f"sf{i}",)))
        cat.set_returns(f"seed{i}", rets)
    return cat


def _real_cands(n=3, n_periods=15, n_inst=12):
    out = []
    for j in range(n):
        insts = tuple(f"i{k}" for k in range(n_inst))
        periods = []
        for t in range(n_periods):
            signals = tuple(float((k + j) % n_inst) for k in range(n_inst))
            fwd = tuple(0.004 * (k - (n_inst - 1) / 2) + 0.002 * (((k * 5 + t * 3 + j) % 7) - 3)
                        for k in range(n_inst))
            periods.append(PanelPeriod(instruments=insts, signals=signals, forward_returns=fwd, event_time=t))
        out.append((AlphaDefinition(alpha_id=f"rc{j}", name=f"R{j}", factor_ids=(f"rf{j}",)), periods))
    return out


def test_factory_parks_unassessed_candidates_for_human():
    # the autonomous factory cannot assert no-leakage/regime from PanelPeriods alone,
    # so without supplied evidence each candidate fails closed to human review.
    fac = AlphaFactory(catalog=_seeded_catalog())   # real AlphaEvaluator
    report = fac.run(_real_cands(3))
    for aid in ("rc0", "rc1", "rc2"):
        ev = report[aid]["evaluation"]
        assert ev.verdict == ValidationVerdict.REQUIRES_HUMAN_REVIEW
        assert any("NOT_ASSESSED" in r for r in ev.reasons)
        assert report[aid]["state"] == AlphaState.TESTING


def test_factory_computes_and_stamps_a_real_batch_pbo():
    fac = AlphaFactory(catalog=_seeded_catalog())
    ev_map = {aid: {"leakage_ok": True, "regime_profitable_frac": 1.0} for aid in ("rc0", "rc1", "rc2")}
    report = fac.run(_real_cands(3), evidence=ev_map)
    pbos = [report[aid]["evaluation"].pbo for aid in ("rc0", "rc1", "rc2")]
    assert all(0.0 <= p <= 1.0 for p in pbos)            # a real PBO, not the old 0.0 default-pass
    assert len({round(p, 9) for p in pbos}) == 1          # one batch-level selection PBO, shared
    for aid in ("rc0", "rc1", "rc2"):                     # evidence present -> not parked for that
        assert all("NOT_ASSESSED" not in r for r in report[aid]["evaluation"].reasons)
        assert report[aid]["evaluation"].pbo_evidence is not None
        assert report[aid]["evaluation"].pbo_evidence_hash


def test_factory_freezes_complete_catalog_before_first_dsr_verdict():
    fac = AlphaFactory(catalog=_seeded_catalog())
    evidence = {
        aid: {"leakage_ok": True, "regime_profitable_frac": 1.0}
        for aid in ("rc0", "rc1", "rc2")
    }
    report = fac.run(_real_cands(3), evidence=evidence)
    catalog_evidence = [
        report[aid]["evaluation"].catalog_evidence
        for aid in ("rc0", "rc1", "rc2")
    ]
    assert all(item is not None for item in catalog_evidence)
    assert len(
        {item.catalog_snapshot_hash for item in catalog_evidence if item}
    ) == 1
    assert {
        item.trial_count for item in catalog_evidence if item
    } == {fac.catalog.global_trial_count()}
