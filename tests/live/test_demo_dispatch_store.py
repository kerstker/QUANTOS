"""Durable one-time grant consumption precedes any eventual network dispatch."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from live.execution import demo_authority as boundary
from live.execution.demo_dispatch_store import DemoDispatchStore, DemoDispatchConflict
from services.validation import demo_submission as auth
from tests.live.test_demo_submission_verification import material,NOW,encoded


def configured(monkeypatch):
    private,registry,orders,context,envelope=material()
    context['executor_boot_sha256']=boundary.executor_boot_sha256()
    envelope['signature_base64url']=encoded(private.sign(auth.signing_payload(envelope)))
    monkeypatch.setattr(boundary,'_read_root_owned_json',lambda path:json.dumps(registry).encode())
    monkeypatch.setattr(boundary.os,'geteuid',lambda:2001)
    return private,orders,context,envelope


def reserve(store,orders,context,envelope):
    return store.reserve(json.dumps(envelope).encode(),expected_context=context,orders=orders,now_ms=NOW)


def test_committed_grant_and_exact_orders_survive_restart_and_cannot_replay(tmp_path,monkeypatch):
    _,orders,context,envelope=configured(monkeypatch);path=tmp_path/'dispatch.db'
    with DemoDispatchStore(path,create=True) as store:
        row=reserve(store,orders,context,envelope)
        assert row['payload']['orders']==orders
        assert row['payload']['envelope']==envelope
    with DemoDispatchStore(path) as reopened:
        assert reopened.snapshot()==[row]
        with pytest.raises(DemoDispatchConflict):reserve(reopened,orders,context,envelope)
        assert reopened.snapshot()==[row]


def test_different_approval_cannot_reuse_an_already_reserved_client_id(tmp_path,monkeypatch):
    private,orders,context,envelope=configured(monkeypatch)
    with DemoDispatchStore(tmp_path/'dispatch.db',create=True) as store:
        reserve(store,orders,context,envelope)
        envelope['payload']['approval_id']='d'*64
        envelope['signature_base64url']=encoded(private.sign(auth.signing_payload(envelope)))
        with pytest.raises(DemoDispatchConflict):reserve(store,orders,context,envelope)
        assert len(store.snapshot())==1


def test_invalid_signature_makes_no_reservation(tmp_path,monkeypatch):
    _,orders,context,envelope=configured(monkeypatch);envelope['signature_base64url']=encoded(b'\0'*64)
    with DemoDispatchStore(tmp_path/'dispatch.db',create=True) as store:
        with pytest.raises(auth.DemoSubmissionRejected):reserve(store,orders,context,envelope)
        assert store.snapshot()==[]


def test_database_failure_rolls_back_grant_and_all_order_reservations(tmp_path,monkeypatch):
    _,orders,context,envelope=configured(monkeypatch);path=tmp_path/'dispatch.db'
    with DemoDispatchStore(path,create=True) as store:
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TRIGGER test_disk_failure BEFORE INSERT ON demo_dispatch_reserved_orders BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END")
        with pytest.raises(DemoDispatchConflict):reserve(store,orders,context,envelope)
        assert store.snapshot()==[]
        with sqlite3.connect(path) as conn:conn.execute('DROP TRIGGER test_disk_failure')
        reserve(store,orders,context,envelope)
        assert len(store.snapshot())==1


def test_two_connections_can_consume_the_grant_only_once(tmp_path,monkeypatch):
    _,orders,context,envelope=configured(monkeypatch);path=tmp_path/'dispatch.db'
    with DemoDispatchStore(path,create=True),DemoDispatchStore(path):
        pass
    def submit():
        with DemoDispatchStore(path) as store:
            try:reserve(store,orders,context,envelope);return 'RESERVED'
            except DemoDispatchConflict:return 'BLOCKED'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:submit(),range(2)))
    assert sorted(results)==['BLOCKED','RESERVED']


def test_existing_reservation_cannot_hide_payload_or_index_tampering(tmp_path,monkeypatch):
    _,orders,context,envelope=configured(monkeypatch);path=tmp_path/'dispatch.db'
    with DemoDispatchStore(path,create=True) as store:reserve(store,orders,context,envelope)
    with sqlite3.connect(path) as conn:
        conn.execute('DROP TRIGGER demo_dispatch_reserved_orders_no_delete')
        conn.execute('DELETE FROM demo_dispatch_reserved_orders')
    with pytest.raises(DemoDispatchConflict):DemoDispatchStore(path)
