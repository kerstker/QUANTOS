"""A durable send claim survives uncertainty and cannot become a second POST."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from live.execution.demo_dispatch_store import DemoDispatchStore, DemoDispatchConflict
from tests.live.test_demo_dispatch_store import configured,reserve


def seeded(path,monkeypatch):
    _,orders,context,envelope=configured(monkeypatch)
    store=DemoDispatchStore(path,create=True)
    record=reserve(store,orders,context,envelope)
    return store,record,orders[0]


def claim(store,record,order):
    return store.claim_send(reservation_sha256=record['record_hash'],client_order_id=order['newClientOrderId'],
                            request_sha256='a'*64,preflight_sha256='b'*64,now_ms=record['payload']['reserved_at_ms']+1)


def test_claim_is_durable_before_response_and_never_refunded(tmp_path,monkeypatch):
    path=tmp_path/'dispatch.db';store,reservation,order=seeded(path,monkeypatch)
    first=claim(store,reservation,order);store.close()
    with DemoDispatchStore(path) as reopened:
        assert reopened.wire_snapshot()==[first]
        with pytest.raises(DemoDispatchConflict):claim(reopened,reservation,order)
        assert len(reopened.wire_snapshot())==1


def test_send_claim_race_has_exactly_one_winner(tmp_path,monkeypatch):
    path=tmp_path/'dispatch.db';store,reservation,order=seeded(path,monkeypatch);store.close()
    def attempt():
        with DemoDispatchStore(path) as s:
            try:claim(s,reservation,order);return 'CLAIMED'
            except DemoDispatchConflict:return 'BLOCKED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _:attempt(),range(2)))==['BLOCKED','CLAIMED']


def test_result_is_retained_once_and_conflicts_or_foreign_claims_are_rejected(tmp_path,monkeypatch):
    store,reservation,order=seeded(tmp_path/'dispatch.db',monkeypatch)
    with store:
        first=claim(store,reservation,order)
        outcome={'status':'UNKNOWN','http_status':503,'response_sha256':'c'*64,'code':None,'order':None,'reason':'HTTP_UNCERTAIN'}
        result=store.record_wire_result(claim_sha256=first['record_hash'],outcome=outcome,now_ms=first['at_ms']+1)
        assert store.record_wire_result(claim_sha256=first['record_hash'],outcome=outcome,now_ms=first['at_ms']+1)==result
        assert len(store.wire_snapshot())==2
        with pytest.raises(DemoDispatchConflict):
            store.record_wire_result(claim_sha256='d'*64,outcome=outcome,now_ms=first['at_ms']+1)
        changed=deepcopy(outcome);changed['http_status']=502
        with pytest.raises(DemoDispatchConflict):
            store.record_wire_result(claim_sha256=first['record_hash'],outcome=changed,now_ms=first['at_ms']+1)
        with pytest.raises(DemoDispatchConflict):claim(store,reservation,order)


def test_unknown_reservations_and_rewritten_send_chain_fail_closed(tmp_path,monkeypatch):
    store,reservation,order=seeded(tmp_path/'dispatch.db',monkeypatch)
    with store:
        with pytest.raises(DemoDispatchConflict):claim(store,dict(reservation,record_hash='f'*64),order)
        claim(store,reservation,order)
        store._conn.execute('DROP TRIGGER demo_wire_events_no_update')
        store._conn.execute("UPDATE demo_wire_events SET at_ms=at_ms+1");store._conn.commit()
        with pytest.raises(DemoDispatchConflict):store.wire_snapshot()
