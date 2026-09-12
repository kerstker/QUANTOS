from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import pytest
from live.execution.demo_cancel_store import DemoCancelStore,DemoCancelConflict
from live.execution import demo_authority
from tests.live.test_demo_cancel_verification import cancellation_material,NOW


def configured(monkeypatch):
    private,registry,commands,context,envelope,_=cancellation_material()
    context['executor_boot_sha256']=demo_authority.executor_boot_sha256()
    from tests.live.test_demo_submission_verification import encoded
    from services.validation.demo_cancellation import signing_payload
    envelope['signature_base64url']=encoded(private.sign(signing_payload(envelope)))
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:registry)
    return registry,commands,context,json.dumps(envelope).encode()


def test_cancel_claim_is_durable_before_transport_and_cannot_repeat_after_restart(tmp_path,monkeypatch):
    _,commands,context,envelope=configured(monkeypatch);path=tmp_path/'cancel.db'
    with DemoCancelStore(path,create=True) as store:
        grant=store.reserve(envelope,context=context,commands=commands,now_ms=NOW)
        claim=store.claim(grant['record_hash'],commands[0]['origClientOrderId'],request_sha256='a'*64,now_ms=NOW+1)
    with DemoCancelStore(path) as store:
        assert len(store.snapshot())==2
        with pytest.raises(DemoCancelConflict):store.reserve(envelope,context=context,commands=commands,now_ms=NOW+2)
        with pytest.raises(DemoCancelConflict):store.claim(grant['record_hash'],commands[0]['origClientOrderId'],request_sha256='b'*64,now_ms=NOW+2)
        outcome={'status':'UNKNOWN','http_status':None,'response_sha256':None,'venue_status':None,'executed_quantity':None}
        store.result(claim['record_hash'],outcome=outcome,now_ms=NOW+2)
        before=store.snapshot();store.result(claim['record_hash'],outcome=outcome,now_ms=NOW+2)
        assert store.snapshot()==before
    original=path.read_bytes()
    with DemoCancelStore(path,read_only=True) as store:
        assert len(store.snapshot())==3
        with pytest.raises(DemoCancelConflict):store.reserve(envelope,context=context,commands=commands,now_ms=NOW+2)
    assert path.read_bytes()==original


def test_cancel_reservation_race_has_one_winner(tmp_path,monkeypatch):
    _,commands,context,envelope=configured(monkeypatch);path=tmp_path/'cancel.db'
    with DemoCancelStore(path,create=True):pass
    def reserve():
        with DemoCancelStore(path) as store:
            try:store.reserve(envelope,context=context,commands=commands,now_ms=NOW);return True
            except DemoCancelConflict:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(lambda _:reserve(),range(2)))==[False,True]


def test_invalid_grant_missing_original_id_and_storage_failure_do_not_create_claim(tmp_path,monkeypatch):
    registry,commands,context,envelope=configured(monkeypatch);path=tmp_path/'cancel.db'
    with DemoCancelStore(path,create=True) as store:
        from services.validation.demo_submission import DemoSubmissionRejected
        registry['keys'][0]['status']='REVOKED'
        with pytest.raises(DemoSubmissionRejected):store.reserve(envelope,context=context,commands=commands,now_ms=NOW)
        assert store.snapshot()==[]
        registry['keys'][0]['status']='ACTIVE'
        grant=store.reserve(envelope,context=context,commands=commands,now_ms=NOW)
        with pytest.raises(DemoCancelConflict):store.claim(grant['record_hash'],'QDforeign0001',request_sha256='a'*64,now_ms=NOW+1)
        with sqlite3.connect(path) as conn:conn.execute("CREATE TRIGGER test_disk_failure BEFORE INSERT ON demo_cancel_events WHEN NEW.kind='CLAIM' BEGIN SELECT RAISE(ABORT,'simulated'); END")
        with pytest.raises(DemoCancelConflict):store.claim(grant['record_hash'],commands[0]['origClientOrderId'],request_sha256='a'*64,now_ms=NOW+1)
        assert store.snapshot()==[grant]


def test_cancel_chain_corruption_is_not_treated_as_an_empty_journal(tmp_path,monkeypatch):
    _,commands,context,envelope=configured(monkeypatch);path=tmp_path/'cancel.db'
    with DemoCancelStore(path,create=True) as store:store.reserve(envelope,context=context,commands=commands,now_ms=NOW)
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TRIGGER demo_cancel_no_update');conn.execute("UPDATE demo_cancel_events SET payload='{}'")
    with pytest.raises(DemoCancelConflict):DemoCancelStore(path)
