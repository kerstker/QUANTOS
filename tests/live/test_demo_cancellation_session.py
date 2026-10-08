"""Finite original-ID cancellation handoff with existing independent grants."""
import json
from pathlib import Path
import pytest
from live.execution.demo_cancellation_session import CancellationSession
from live.execution.demo_execution_session import SessionRejected
from tests.live.test_demo_cancellation_execution import ready,close,NOW,transport,executor


def request(batch,**patch):
    return json.dumps({'schema':'DEMO_CANCELLATION_SESSION_INPUT_V1',
        'reservation_sha256':batch['reservation_sha256'],
        'client_order_ids':[o['newClientOrderId'] for o in batch['orders']],**patch}).encode()


def session(batch,kwargs):
    return CancellationSession(**kwargs,evidence_directory=Path(batch['proposal_path']).parent)


@pytest.mark.parametrize('outcome',['canceled','timeout'])
def test_exact_original_cancel_once_and_no_cash_or_oms_from_response(tmp_path,monkeypatch,outcome):
    batch,args,stream,store,kwargs,envelope,_=ready(tmp_path,monkeypatch)
    current=session(batch,kwargs);calls=[]
    def http(*_):
        calls.append(1)
        if outcome=='timeout':raise TimeoutError('PRIVATE_HTTP_DETAIL')
        return 200,json.dumps({'symbol':'BTCUSDC','clientOrderId':batch['orders'][0]['newClientOrderId'],
            'orderId':9007199254740993,'status':'CANCELED','executedQty':'0'}).encode()
    monkeypatch.setattr(transport,'_http_delete_once',http)
    monkeypatch.setattr(transport,'_http_once',lambda *_:pytest.fail('new order'))
    try:
        before=args['oms'].orders()
        proposal=current.propose(request(batch),now_ms=NOW+50)
        assert proposal['status']=='WAITING_INDEPENDENT_GRANT'
        assert store.snapshot()==[] and calls==[]
        result=current.submit(proposal['proposal_sha256'],envelope)
        assert result['status']=='ORIGINAL_ID_QUERY_RECOVERY_REQUIRED' and result['cash_reconciled'] is False
        assert args['oms'].orders()==before and args['oms'].fills()==[] and len(calls)==1
        with pytest.raises(SessionRejected):current.submit(proposal['proposal_sha256'],envelope)
        assert len(calls)==1 and 'PRIVATE_HTTP_DETAIL' not in json.dumps(result)
        assert [r['kind'] for r in store.snapshot()]==['GRANT','CLAIM','RESULT']
    finally:store.close();close(args,stream)


@pytest.mark.parametrize('change',[{'proposal_path':'/untrusted/path'},{'reservation_sha256':'f'*64},
    {'client_order_ids':[]},{'client_order_ids':['QDforeign0001']},{'schema':'DEMO_EXECUTION_SESSION_INPUT_V1'}])
def test_scope_is_only_protected_original_reservation_and_explicit_ids(tmp_path,monkeypatch,change):
    batch,args,stream,store,kwargs,_,_=ready(tmp_path,monkeypatch)
    try:
        with pytest.raises(SessionRejected):session(batch,kwargs).propose(request(batch,**change),now_ms=NOW+50)
        assert store.snapshot()==[]
    finally:store.close();close(args,stream)


@pytest.mark.parametrize('failure',['restart','wrong_proposal','bad_signature','expired_query'])
def test_invalid_or_expired_grant_does_not_leave_a_retryable_session(tmp_path,monkeypatch,failure):
    batch,args,stream,store,kwargs,envelope,_=ready(tmp_path,monkeypatch)
    current=session(batch,kwargs)
    monkeypatch.setattr(transport,'_http_delete_once',lambda *_:pytest.fail('unexpected cancellation'))
    try:
        proposal=current.propose(request(batch),now_ms=NOW+50)
        identity=proposal['proposal_sha256']
        if failure=='restart':current.close();current=session(batch,kwargs)
        if failure=='wrong_proposal':identity='f'*64
        if failure=='bad_signature':
            raw=json.loads(envelope);raw['signature_base64url']='A'*86;envelope=json.dumps(raw).encode()
        if failure=='expired_query':monkeypatch.setattr(executor,'now_ms',lambda:NOW+6000)
        with pytest.raises(SessionRejected):current.submit(identity,envelope)
        with pytest.raises(SessionRejected):current.submit(proposal['proposal_sha256'],envelope)
        assert store.snapshot()==[]
    finally:store.close();close(args,stream)


def test_process_interruption_retains_cancel_claim_and_restarted_session_cannot_replay_it(tmp_path,monkeypatch):
    batch,args,stream,store,kwargs,envelope,_=ready(tmp_path,monkeypatch)
    current=session(batch,kwargs);calls=[]
    def crash(*_):calls.append(1);raise KeyboardInterrupt('isolated process interruption')
    monkeypatch.setattr(transport,'_http_delete_once',crash)
    try:
        proposal=current.propose(request(batch),now_ms=NOW+50)
        with pytest.raises(KeyboardInterrupt):current.submit(proposal['proposal_sha256'],envelope)
        assert [r['kind'] for r in store.snapshot()]==['GRANT','CLAIM']
        restarted=session(batch,kwargs)
        other=restarted.propose(request(batch),now_ms=NOW+50)
        with pytest.raises(SessionRejected):restarted.submit(other['proposal_sha256'],envelope)
        assert calls==[1] and args['oms'].fills()==[]
    finally:store.close();close(args,stream)
