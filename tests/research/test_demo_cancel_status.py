from copy import deepcopy
import pytest
from services.research import demo_cancel_status as status
from live.execution.demo_cancel_store import DemoCancelStore
from tests.live.test_demo_cancel_store import configured,NOW


def test_cancel_state_never_treats_response_as_recovered_order(tmp_path,monkeypatch):
    _,commands,context,envelope=configured(monkeypatch)
    with DemoCancelStore(tmp_path/'cancel.db',create=True) as store:
        assert status.build_status(store.snapshot(),observed_at_ms=NOW) is None
        grant=store.reserve(envelope,context=context,commands=commands,now_ms=NOW)
        raw=status.build_status(store.snapshot(),observed_at_ms=NOW)
        assert raw['phase']=='RESERVED_AWAITING_ATTEMPTS' and raw['claimed_orders']==0
        claim=store.claim(grant['record_hash'],commands[0]['origClientOrderId'],request_sha256='a'*64,now_ms=NOW+1)
        raw=status.build_status(store.snapshot(),observed_at_ms=NOW+1)
        assert raw['orders'][0]['status']=='CLAIMED_RESPONSE_UNKNOWN' and raw['unresolved_responses']==1
        store.result(claim['record_hash'],outcome={'status':'ORDER_STATE_OBSERVED','http_status':200,'response_sha256':'b'*64,
            'venue_status':'CANCELED','executed_quantity':'0.000000000000000001'},now_ms=NOW+2)
        raw=status.build_status(store.snapshot(),observed_at_ms=NOW+2)
        assert raw['orders'][0]['status']=='RESPONSE_OBSERVED' and raw['response_orders']==1
        assert raw['phase']=='ORIGINAL_ID_QUERY_RECOVERY_REQUIRED' and raw['cash_reconciled'] is False
        assert raw['orders'][0]['venue_order_id']=='9007199254740993'
        assert raw['orders'][0]['executed_quantity']=='0.000000000000000001'
        path=status.save(tmp_path/'display',raw)
        assert status.load_latest(tmp_path/'display')==raw
        for key,value in [('claimed_orders',0),('response_orders',0),('unresolved_responses',1),('cash_reconciled',True),('phase','COMPLETED')]:
            invalid=deepcopy(raw);invalid[key]=value
            with pytest.raises(ValueError):status.validate(invalid)
        path.write_text('{}')
        with pytest.raises(ValueError):status.load_latest(tmp_path/'display')
