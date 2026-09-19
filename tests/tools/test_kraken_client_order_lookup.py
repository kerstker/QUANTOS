import json
import pytest
from tools import kraken_account_snapshot as account
from tests.data.test_kraken_client_order_lookup import REQUEST, NOW, reads
from tests.data.test_kraken_order_query import body
from tests.data.test_kraken_trade_query import trade_body


class Reader:
    account_ref_sha256='b'*64
    def __init__(self,mode='closed'):self.mode=mode;self.calls=[]
    def read(self,endpoint,params):
        self.calls.append(endpoint)
        if endpoint=='OpenOrders':return reads(self.mode)[0]['body'].encode()
        if endpoint=='ClosedOrders':
            if self.mode=='interrupt':raise KeyboardInterrupt
            return reads(self.mode)[1]['body'].encode()
        if endpoint=='QueryOrders':return body('canceled').encode()
        if endpoint=='QueryTrades':return json.dumps(trade_body()).encode()
        raise AssertionError(endpoint)


def test_client_id_recovery_persists_hint_then_uses_normal_order_and_trade_queries(tmp_path):
    client=Reader()
    result=account.track_orders(client,tmp_path,[],client_order_requests=[REQUEST],clock_ms=lambda:NOW+21000)
    assert result['status']=='ORDERS_OBSERVED' and result['order_ids']==['ORDER-1']
    assert client.calls==['OpenOrders','ClosedOrders','QueryOrders','QueryTrades']
    restarted=Reader('empty')
    result=account.track_orders(restarted,tmp_path,[],clock_ms=lambda:NOW+22000)
    assert result['status']=='ORDERS_OBSERVED'
    assert restarted.calls==['QueryOrders','QueryTrades']


def test_absence_is_unresolved_and_request_survives_restart(tmp_path):
    first=account.track_orders(Reader('empty'),tmp_path,[],client_order_requests=[REQUEST],clock_ms=lambda:NOW+21000)
    assert first['status']=='UNRESOLVED' and first['order_ids']==[]
    restored=account.track_orders(Reader(),tmp_path,[],clock_ms=lambda:NOW+22000)
    assert restored['status']=='ORDERS_OBSERVED' and restored['order_ids']==['ORDER-1']


def test_interrupted_lookup_cannot_leave_previous_success(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        account.track_orders(Reader('interrupt'),tmp_path,[],client_order_requests=[REQUEST],clock_ms=lambda:NOW+21000)
    assert json.loads((tmp_path/'order-recovery.json').read_text())['status']=='UNRESOLVED'
    assert account.track_orders(Reader(),tmp_path,[],clock_ms=lambda:NOW+22000)['status']=='ORDERS_OBSERVED'


@pytest.mark.parametrize('change',['config_deleted','cache_changed','request_changed'])
def test_registered_recovery_cannot_be_removed_or_rebound(tmp_path,change):
    account.track_orders(Reader(),tmp_path,[],client_order_requests=[REQUEST],clock_ms=lambda:NOW+21000)
    requests=None
    if change=='config_deleted':(tmp_path/'client-order-requests.json').unlink()
    if change=='cache_changed':
        path=next((tmp_path/'client-order-resolutions').glob('*.json'));value=json.loads(path.read_text())
        value['report']['order_id']='OTHER';path.write_text(json.dumps(value))
    if change=='request_changed':requests=[dict(REQUEST,deadline_ms=NOW+15000)]
    result=account.track_orders(Reader(),tmp_path,[],client_order_requests=requests,clock_ms=lambda:NOW+22000)
    assert result['status']=='UNRESOLVED'


def test_account_session_registers_requests_and_restart_keeps_tracking(tmp_path):
    from services.data.kraken_account import assess
    from tests.data.test_kraken_account import observations
    snapshot=assess(observations(),as_of_ms=NOW+500)
    snapshot.update(account_ref_sha256='b'*64,open_orders=[])
    snapshot['result_hash']=account.digest(snapshot)
    result=account.run_session(Reader(),tmp_path,client_order_requests=[REQUEST],
        collect_fn=lambda _:snapshot,clock_ms=lambda:NOW+21000)
    assert result['non_stable_cycles']==0
    client=Reader('empty')
    result=account.run_session(client,tmp_path,collect_fn=lambda _:snapshot,clock_ms=lambda:NOW+22000)
    assert result['non_stable_cycles']==0 and client.calls==['QueryOrders','QueryTrades']


def test_crash_after_discovery_archive_recovers_before_any_new_lookup(tmp_path,monkeypatch):
    from tools import kraken_client_order_lookup as lookup
    original=lookup.atomic_json
    def interrupt(path,value):
        original(path,value)
        if path.parent.name=='client-order-resolutions':raise KeyboardInterrupt
    monkeypatch.setattr(lookup,'atomic_json',interrupt)
    with pytest.raises(KeyboardInterrupt):
        account.track_orders(Reader(),tmp_path,[],client_order_requests=[REQUEST],clock_ms=lambda:NOW+21000)
    assert json.loads((tmp_path/'order-recovery.json').read_text())['status']=='UNRESOLVED'
    monkeypatch.setattr(lookup,'atomic_json',original)
    client=Reader('empty')
    result=account.track_orders(client,tmp_path,[],clock_ms=lambda:NOW+22000)
    assert result['status']=='ORDERS_OBSERVED' and client.calls==['QueryOrders','QueryTrades']
