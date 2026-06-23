"""Audit roadmap P0 — durable experiment registry: cross-session DSR trial
counting, reproducible immutable evaluation artifacts, fail-closed load."""
import pytest

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.monitoring.audit_log import GlobalAuditLog
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import (
    SqliteExperimentStore, ExperimentStoreError,
)


def _populated():
    c = AlphaCatalog()
    for i, rets in enumerate([(0.01, 0.02, -0.01, 0.03), (0.02, -0.01, 0.0, 0.01)]):
        aid = f"a{i}"
        c.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(f"f{i}",)))
        c.set_returns(aid, rets)
        c.set_pvalue(aid, 0.02 * (i + 1))
    c._set_state("a0", AlphaState.APPROVED)
    return c


def _reg(create=True):
    return ExperimentRegistry(SqliteExperimentStore(":memory:", create=create))


def test_save_then_load_round_trips_multiple_testing_state():
    src = _populated()
    reg = _reg()
    reg.save(src, now=1)
    dst = AlphaCatalog()
    reg.load_into(dst)
    assert dst.global_trial_count() == src.global_trial_count()
    assert dst.catalog_var_sr() == src.catalog_var_sr()
    assert dst.bh_fdr_survivors(0.10) == src.bh_fdr_survivors(0.10)
    assert dst.approved_set() == src.approved_set()


def test_trial_count_is_cumulative_and_dedup_across_save_load():
    reg = _reg()
    c = _populated()                 # 2 distinct definition trials
    reg.save(c, now=1)
    dst = AlphaCatalog()
    reg.load_into(dst)
    # a NEW alpha_id but a0's factors -> same definition_hash (config), must NOT inflate
    dst.register(AlphaDefinition(alpha_id="a0bis", name="a0bis", factor_ids=("f0",)))
    dst.register(AlphaDefinition(alpha_id="a9", name="a9", factor_ids=("f9",)))   # genuinely new config
    reg.save(dst, now=2)
    final = AlphaCatalog()
    reg.load_into(final)
    assert final.global_trial_count() == 3       # 2 + 1 new (the re-seen config did not inflate)


def test_load_into_uninitialized_store_fails_closed():
    reg = ExperimentRegistry(SqliteExperimentStore(":memory:", create=False))
    with pytest.raises(ExperimentStoreError):
        reg.load_into(AlphaCatalog())            # never silently empties the trial pop


def _evaluation():
    return AlphaEvaluation(alpha_id="a1", verdict=ValidationVerdict.PASS,
                           deflated_sharpe=0.8, pbo=0.1, catalog_trial_count=3,
                           reproducibility_hash="repro-1")


def test_record_evaluation_is_reproducible_and_immutable():
    reg = _reg()
    rid1 = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    rid2 = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    assert rid1 == rid2                                   # deterministic run_id
    stored = reg.get_evaluation(rid1)
    assert stored["verdict"] == "PASS" and stored["content_hash"]
    assert reg.list_evaluations("a1")[0]["run_id"] == rid1


def test_record_evaluation_emits_audit_event():
    reg = _reg()
    log = GlobalAuditLog()
    rid = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10, audit_log=log)
    assert log.chain_valid() and len(log.events) == 1
    ev = log.events[0]
    assert ev.action == "EXPERIMENT_RECORDED" and ev.metadata["run_id"] == rid


def test_content_hash_is_deterministic_across_registries():
    r1, r2 = _reg(), _reg()
    h1 = r1.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    h2 = r2.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    assert r1.get_evaluation(h1)["content_hash"] == r2.get_evaluation(h2)["content_hash"]
