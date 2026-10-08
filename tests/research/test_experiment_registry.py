"""Audit roadmap P0 — durable experiment registry: cross-session DSR trial
counting, reproducible immutable evaluation artifacts, fail-closed load."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event

import pytest

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.canonical import canonical_sha256
from quantos.contracts.hashing import short_hash, stable_hash
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.evaluation_evidence import evaluation_reproducibility_hash
from services.monitoring.audit_log import GlobalAuditLog
from services.research.experiment_registry.registry import (
    ExperimentRegistry,
    prepare_evaluation_artifact,
)
from services.research.experiment_registry.store import (
    SqliteExperimentStore, ExperimentStoreError,
)
from tests.alpha.fixtures import bound_alpha_evaluation, seed_catalog_evidence


_EVALUATION_DEFINITION = AlphaDefinition(alpha_id="a1", name="a1", factor_ids=("f1",))
_EVALUATION_CONFIG_HASH = _EVALUATION_DEFINITION.definition_hash


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


def test_load_for_evaluation_preserves_historical_definition_bound_returns():
    registry = _reg()
    source = _populated()
    registry.save(source, now=1)

    catalog = AlphaCatalog()
    context_hash = registry.load_for_evaluation(catalog)
    assert len(context_hash) == 64
    repeated = AlphaCatalog()
    assert registry.load_for_evaluation(repeated) == context_hash
    definition = AlphaDefinition(
        alpha_id="new",
        name="new",
        factor_ids=("new-factor",),
    )
    catalog.register(definition)
    catalog.set_returns("new", (0.012, -0.004, 0.018, 0.001))

    evidence = catalog.evidence("new")
    assert evidence is not None
    assert evidence.trial_count == 3
    assert len(evidence.sharpe_observations) == 3
    assert definition.definition_hash in {
        item.observation_id for item in evidence.return_observations
    }


def _evaluation():
    catalog = AlphaCatalog()
    catalog.register(_EVALUATION_DEFINITION)
    seed_catalog_evidence(catalog, "a1")
    catalog.register_trial(stable_hash({"third_catalog_trial": True}))
    return bound_alpha_evaluation(
        catalog,
        "a1",
        reproducibility_hash="repro-1",
    )


def test_prepare_evaluation_artifact_preserves_exact_legacy_bytes_and_hashes():
    evaluation = _evaluation()
    payload = evaluation.model_dump()
    expected_json = json.dumps(payload, sort_keys=True, default=str)
    expected_content_hash = stable_hash(
        {"evaluation": payload, "dataset_version": "v1"}
    )
    expected_run_id = short_hash(
        {
            "alpha_id": evaluation.alpha_id,
            "repro": evaluation.reproducibility_hash,
            "dataset_version": "v1",
        }
    )

    write = prepare_evaluation_artifact(evaluation, "v1", 10)

    assert write.run_id == expected_run_id
    assert write.content_hash == expected_content_hash
    assert write.evaluation_json.encode("utf-8") == expected_json.encode("utf-8")
    assert write.audit_intent is not None
    assert write.audit_intent.event_id == f"exp-{expected_run_id}"
    assert write.audit_intent.timestamp == 10
    assert dict(write.audit_intent.metadata) == {
        "run_id": expected_run_id,
        "content_hash": expected_content_hash,
        "verdict": "PASS",
    }


def test_record_evaluation_is_reproducible_and_immutable():
    reg = _reg()
    rid1 = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    rid2 = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    assert rid1 == rid2                                   # deterministic run_id
    stored = reg.get_evaluation(rid1)
    assert stored["verdict"] == "REQUIRES_HUMAN_REVIEW" and stored["content_hash"]
    payload = json.loads(stored["evaluation_json"])
    assert stored["evidence_status"] == "DIAGNOSTIC_ONLY"
    assert stored["selection_authority"] is False
    assert stored["invalidation_code"] == "INVALIDATED_PERP_LEDGER_V1"
    assert "INVALIDATED_PERP_LEDGER_V1" in payload["reasons"]
    assert reg.list_evaluations("a1")[0]["run_id"] == rid1


def test_self_described_backtest_authority_cannot_persist_a_pass():
    evaluation = {
        **_evaluation().model_dump(),
        "evidence_status": "AUTHORITATIVE",
        "authoritative_backtest_record": {
            "status": "AUTHORITATIVE",
            "run_id": "self-signed",
        },
    }
    reg = _reg()

    run_id = reg.record_evaluation(
        evaluation,
        dataset_version="v1",
        recorded_ts=10,
    )

    stored = reg.get_evaluation(run_id)
    assert stored["verdict"] == "REQUIRES_HUMAN_REVIEW"
    payload = json.loads(stored["evaluation_json"])
    assert stored["evidence_status"] == "DIAGNOSTIC_ONLY"
    assert "evidence_status" not in payload
    assert "authoritative_backtest_record" not in payload


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
            [_EVALUATION_CONFIG_HASH],
            registered_at=timestamp,
        )
        run_ids.append(
            reg.record_evaluation(
                _evaluation(),
                dataset_version="v1",
                recorded_ts=timestamp,
                search_id=search_id,
                config_hash=_EVALUATION_CONFIG_HASH,
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
            [_EVALUATION_CONFIG_HASH],
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
                config_hash=_EVALUATION_CONFIG_HASH,
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
    } == {_EVALUATION_CONFIG_HASH}
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
    tampered = row["evaluation_json"].replace(
        '"verdict": "REQUIRES_HUMAN_REVIEW"',
        '"verdict": "FAIL"',
    )
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


def test_verify_evaluation_replays_pbo_after_attacker_recomputes_row_hash():
    reg = _reg()
    rid = reg.record_evaluation(_evaluation(), dataset_version="v1", recorded_ts=10)
    row = reg.get_evaluation(rid)
    payload = json.loads(row["evaluation_json"])
    payload["pbo_evidence"]["performance_matrix"][0][0] += 1.0
    tampered_json = json.dumps(payload, sort_keys=True, default=str)
    tampered_hash = stable_hash({"evaluation": payload, "dataset_version": "v1"})
    reg._store._conn.execute("DROP TRIGGER evaluations_no_update")
    reg._store._conn.execute(
        "UPDATE evaluations SET evaluation_json=?, content_hash=? WHERE run_id=?",
        (tampered_json, tampered_hash, rid),
    )
    reg._store._conn.commit()
    assert reg.verify_evaluation(rid) is False


def test_verify_evaluation_replays_catalog_dsr_after_all_generic_hashes_change():
    reg = _reg()
    rid = reg.record_evaluation(
        _evaluation(), dataset_version="v1", recorded_ts=10
    )
    row = reg.get_evaluation(rid)
    payload = json.loads(row["evaluation_json"])
    forged_dsr = 1.0 - payload["catalog_evidence"]["deflated_sharpe"]
    payload["catalog_evidence"]["deflated_sharpe"] = forged_dsr
    payload["deflated_sharpe"] = forged_dsr
    payload["catalog_evidence_hash"] = canonical_sha256(
        payload["catalog_evidence"]
    )
    payload["reproducibility_hash"] = ""
    forged = AlphaEvaluation.model_validate(payload)
    payload["reproducibility_hash"] = evaluation_reproducibility_hash(
        forged, dataset_version="v1"
    )
    tampered_json = json.dumps(payload, sort_keys=True, default=str)
    tampered_hash = stable_hash(
        {"evaluation": payload, "dataset_version": "v1"}
    )
    reg._store._conn.execute("DROP TRIGGER evaluations_no_update")
    reg._store._conn.execute(
        "UPDATE evaluations SET evaluation_json=?, content_hash=? WHERE run_id=?",
        (tampered_json, tampered_hash, rid),
    )
    reg._store._conn.commit()
    assert reg.verify_evaluation(rid) is False


def test_record_evaluation_rejects_foreign_search_candidate_binding():
    reg = _reg()
    with pytest.raises(ValueError, match="not bound"):
        reg.record_evaluation(
            _evaluation(),
            dataset_version="v1",
            recorded_ts=10,
            config_hash="f" * 64,
        )


def test_verify_evaluation_unknown_run_id_fails_closed():
    reg = _reg()
    assert reg.verify_evaluation("no-such-run") is False
