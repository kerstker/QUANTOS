"""Audit roadmap P0 — durable experiment registry: cross-session DSR trial
counting, reproducible immutable evaluation artifacts, fail-closed load."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

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


def test_load_into_corrupt_alpha_row_fails_closed():
    # review ER-4: intra-row corruption (bad JSON) must raise ExperimentStoreError,
    # not surface as a raw JSON error / partial load
    store = SqliteExperimentStore(":memory:", create=True)
    reg = ExperimentRegistry(store)
    store._conn.execute(
        "INSERT INTO alpha_state(alpha_id, definition_json, state, returns_json, sharpe, pvalue) "
        "VALUES (?,?,?,?,?,?)", ("a1", "{not valid json", "DRAFT", "[]", None, None))
    store._conn.commit()
    with pytest.raises(ExperimentStoreError):
        reg.load_into(AlphaCatalog())


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


def test_rerecord_with_audit_log_is_idempotent_noop():
    # review ER-1: a no-op re-record with audit_log attached must NOT crash on a
    # duplicate audit event_id
    reg = _reg()
    log = GlobalAuditLog()
    rid1 = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10, audit_log=log)
    rid2 = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10, audit_log=log)
    assert rid1 == rid2
    assert len(log.events) == 1 and log.chain_valid()       # only the first insert audited


def test_evaluation_audit_intent_is_durable_until_explicit_recovery():
    reg = _reg()
    rid = reg.record_evaluation(
        _evaluation(), dataset_version="v1", recorded_ts=10
    )
    assert reg.pending_evaluation_audits(run_id=rid)

    log = GlobalAuditLog()
    assert reg.recover_evaluation_audits(
        log, dispatched_at=11, run_id=rid
    ) == 1
    assert reg.pending_evaluation_audits(run_id=rid) == ()
    assert len(log.events) == 1 and log.chain_valid()
    assert reg.evaluation_audit_chain_valid()


def test_evaluation_audit_publish_before_ack_is_idempotently_replayed(monkeypatch):
    reg = _reg()
    log = GlobalAuditLog()

    def crash_after_publish(stage):
        if stage == "after_publish":
            raise RuntimeError("injected publish-before-ack crash")

    monkeypatch.setattr(reg, "_audit_dispatch_checkpoint", crash_after_publish)
    with pytest.raises(RuntimeError, match="publish-before-ack"):
        reg.record_evaluation(
            _evaluation(),
            dataset_version="v1",
            recorded_ts=10,
            audit_log=log,
        )
    assert len(log.events) == 1
    assert reg.pending_evaluation_audits()

    monkeypatch.setattr(reg, "_audit_dispatch_checkpoint", lambda _stage: None)
    assert reg.recover_evaluation_audits(log, dispatched_at=10) == 1
    assert len(log.events) == 1
    assert reg.pending_evaluation_audits() == ()


def test_evaluation_audit_replay_rejects_conflicting_sink_event():
    reg = _reg()
    rid = reg.record_evaluation(
        _evaluation(), dataset_version="v1", recorded_ts=10
    )
    log = GlobalAuditLog()
    log.append(
        event_id=f"exp-{rid}",
        actor="attacker",
        action="EXPERIMENT_RECORDED",
        timestamp=10,
    )
    with pytest.raises(ExperimentStoreError, match="audit sink event conflict"):
        reg.recover_evaluation_audits(log, dispatched_at=10)
    assert reg.pending_evaluation_audits(run_id=rid)


def test_same_immutable_evaluation_links_to_each_real_search_session():
    reg = _reg()
    run_ids = []
    for search_id, timestamp in (("session-one", 10), ("session-two", 20)):
        reg.begin_search_attempt(
            search_id=search_id,
            search_kind="TEST",
            source="tests.research",
            dataset_version="v1",
            recorded_at=timestamp,
        )
        reg.register_search_candidates(
            search_id,
            ["same-candidate"],
            registered_at=timestamp,
        )
        run_ids.append(
            reg.record_evaluation(
                _evaluation(),
                dataset_version="v1",
                recorded_ts=timestamp,
                search_id=search_id,
                config_hash="same-candidate",
            )
        )
        reg.finish_search_attempt(
            search_id,
            status="COMPLETED",
            recorded_at=timestamp,
        )

    assert run_ids[0] == run_ids[1]
    assert len(reg.list_evaluations("a1")) == 1
    assert [row["run_id"] for row in reg.search_evaluations("session-one")] == [
        run_ids[0]
    ]
    assert [row["run_id"] for row in reg.search_evaluations("session-two")] == [
        run_ids[0]
    ]
    assert len(reg.pending_evaluation_audits()) == 1
    assert reg.search_history_valid()


def test_concurrent_search_links_share_first_artifact_audit_timestamp(tmp_path):
    path = tmp_path / "concurrent-evaluation.db"
    setup = ExperimentRegistry(SqliteExperimentStore(str(path), create=True))
    for search_id, timestamp in (("first-search", 100), ("second-search", 200)):
        setup.begin_search_attempt(
            search_id=search_id,
            search_kind="TEST",
            source="tests.research",
            dataset_version="v1",
            recorded_at=timestamp,
        )
        setup.register_search_candidates(
            search_id,
            ["same-candidate"],
            registered_at=timestamp,
        )

    first_holds_lock = Event()
    release_first = Event()
    second_started = Event()

    def record(search_id: str, timestamp: int, *, hold_lock: bool) -> str:
        store = SqliteExperimentStore(str(path), create=False)
        registry = ExperimentRegistry(store)
        if hold_lock:

            def pause_before_commit(_stage: str) -> None:
                first_holds_lock.set()
                assert release_first.wait(timeout=5)

            store._evaluation_write_checkpoint = pause_before_commit
        else:
            second_started.set()
        try:
            return registry.record_evaluation(
                _evaluation(),
                dataset_version="v1",
                recorded_ts=timestamp,
                search_id=search_id,
                config_hash="same-candidate",
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(record, "first-search", 100, hold_lock=True)
        assert first_holds_lock.wait(timeout=5)
        second = executor.submit(record, "second-search", 200, hold_lock=False)
        assert second_started.wait(timeout=5)
        release_first.set()
        run_ids = (first.result(timeout=5), second.result(timeout=5))

    assert run_ids[0] == run_ids[1]
    assert setup.get_evaluation(run_ids[0])["recorded_ts"] == 100
    audit = setup.pending_evaluation_audits(run_id=run_ids[0])
    assert len(audit) == 1
    assert audit[0]["timestamp"] == 100
    assert setup.search_evaluations("first-search")[0]["recorded_at"] == 100
    assert setup.search_evaluations("second-search")[0]["recorded_at"] == 200
    assert {
        setup.search_evaluations(search_id)[0]["config_hash"]
        for search_id in ("first-search", "second-search")
    } == {"same-candidate"}
    assert setup.search_history_valid()


def test_empty_reproducibility_hash_rejected():
    # review ER-2: empty repro -> run_id collision -> silent artifact loss; fail closed
    reg = _reg()
    ev = AlphaEvaluation(alpha_id="a1", verdict=ValidationVerdict.PASS, reproducibility_hash="")
    with pytest.raises(ValueError):
        reg.record_evaluation(ev, dataset_version="v1", recorded_ts=10)


def test_content_hash_is_deterministic_across_registries():
    r1, r2 = _reg(), _reg()
    h1 = r1.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    h2 = r2.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    assert r1.get_evaluation(h1)["content_hash"] == r2.get_evaluation(h2)["content_hash"]


# ---- Rank #12: the missing verify half — tamper detection on the write-once store ----

def test_verify_evaluation_true_for_untampered_record():
    reg = _reg()
    rid = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    assert reg.verify_evaluation(rid) is True


def test_verify_evaluation_detects_a_tampered_metric():
    reg = _reg()
    rid = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    # edit the stored evaluation_json directly (verdict flipped) WITHOUT updating content_hash
    row = reg.get_evaluation(rid)
    tampered = row["evaluation_json"].replace('"verdict": "PASS"', '"verdict": "FAIL"')
    assert tampered != row["evaluation_json"]                 # the edit actually landed
    reg._store._conn.execute("DROP TRIGGER evaluations_no_update")
    reg._store._conn.execute("UPDATE evaluations SET evaluation_json=? WHERE run_id=?", (tampered, rid))
    reg._store._conn.commit()
    assert reg.verify_evaluation(rid) is False                # recomputed hash != stored hash


def test_verify_evaluation_detects_a_tampered_content_hash():
    reg = _reg()
    rid = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    reg._store._conn.execute("DROP TRIGGER evaluations_no_update")
    reg._store._conn.execute("UPDATE evaluations SET content_hash='deadbeef' WHERE run_id=?", (rid,))
    reg._store._conn.commit()
    assert reg.verify_evaluation(rid) is False


def test_verify_evaluation_unknown_run_id_fails_closed():
    reg = _reg()
    assert reg.verify_evaluation("no-such-run") is False
