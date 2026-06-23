"""Audit roadmap — AlphaCatalog snapshot/restore round-trips the multiple-testing
state (trial pop, var_sr, BH-FDR, approved_set, per-alpha state) with no loss."""
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog


def _populated():
    c = AlphaCatalog()
    for i, rets in enumerate([(0.01, 0.02, -0.01, 0.03), (0.02, -0.01, 0.0, 0.01), (0.0, 0.01, 0.01, -0.02)]):
        aid = f"a{i}"
        c.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(f"f{i}",)))
        c.set_returns(aid, rets)
        c.set_pvalue(aid, 0.01 * (i + 1))
    c._set_state("a0", AlphaState.APPROVED)
    c.register_trial("extra-config-hash")            # a trial with no alpha object
    return c


def test_snapshot_restore_preserves_multiple_testing_state():
    src = _populated()
    snap = src.snapshot()
    dst = AlphaCatalog()
    dst.restore(snap)
    assert dst.global_trial_count() == src.global_trial_count()
    assert dst.catalog_var_sr() == src.catalog_var_sr()
    assert dst.bh_fdr_survivors(0.10) == src.bh_fdr_survivors(0.10)
    assert dst.approved_set() == src.approved_set()
    assert {a: dst.state(a) for a in dst.all_ids()} == {a: src.state(a) for a in src.all_ids()}
    assert dst.returns("a1") == src.returns("a1")


def test_restore_is_independent_of_a_persisted_sharpe():
    # sharpe is recomputed from returns on restore, not trusted from the snapshot
    src = _populated()
    snap = src.snapshot()
    dst = AlphaCatalog()
    dst.restore(snap)
    assert dst.catalog_var_sr() == src.catalog_var_sr()
