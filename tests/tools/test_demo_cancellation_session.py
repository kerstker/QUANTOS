"""Two separate purposes share framing, never grants or automatic retries."""
import io
import json
import os
import pytest
from tools import demo_execution_session as cli
from tests.live.test_demo_cancellation_session import request
from tests.live.test_demo_cancellation_execution import ready,close,NOW,transport


def test_cancel_frame_rejects_submission_purpose_without_rewriting_envelope():
    grant={'schema':'DEMO_CANCELLATION_SESSION_GRANT_V1','proposal_sha256':'a'*64,'envelope':{'fixture':True}}
    raw=json.dumps(grant).encode()
    assert cli.parse_grant(raw,purpose='cancellation')==('a'*64,b'{"fixture":true}')
    with pytest.raises(ValueError):cli.parse_grant(raw)
    grant['schema']='DEMO_EXECUTION_SESSION_GRANT_V1'
    with pytest.raises(ValueError):cli.parse_grant(json.dumps(grant).encode(),purpose='cancellation')


def test_actual_pipe_cancel_keeps_existing_once_only_core_and_separate_result_history(tmp_path,monkeypatch):
    batch,args,stream,store,kwargs,envelope,_=ready(tmp_path,monkeypatch)
    monkeypatch.setattr(cli,'require_custody',lambda:tmp_path)
    monkeypatch.setattr(cli,'_open',lambda *a:{k:kwargs[k] for k in ('dispatch','recovery','oms')})
    monkeypatch.setattr(cli,'_open_cancel',lambda *a:store)
    monkeypatch.setattr(cli,'now_ms',lambda:NOW+50)
    calls=[]
    def http(*_):
        calls.append(1)
        return 200,json.dumps({'symbol':'BTCUSDC','clientOrderId':batch['orders'][0]['newClientOrderId'],
            'orderId':9007199254740993,'status':'CANCELED','executedQty':'0'}).encode()
    monkeypatch.setattr(transport,'_http_delete_once',http)
    monkeypatch.setattr(transport,'_http_once',lambda *_:pytest.fail('new order'))
    reader,writer=os.pipe()
    class Channel(io.StringIO):
        sent=False
        def flush(self):
            if self.sent:return
            proposal=json.loads(self.getvalue());assert proposal['schema']=='DEMO_CANCELLATION_SESSION_PROPOSAL_V1'
            grant={'schema':'DEMO_CANCELLATION_SESSION_GRANT_V1','proposal_sha256':proposal['proposal_sha256'],
                'envelope':json.loads(envelope)}
            os.write(writer,json.dumps(grant).encode()+b'\n');self.sent=True
    try:
        os.write(writer,request(batch)+b'\n');channel=Channel()
        assert cli.run(source_commit='c'*40,purpose='cancellation',input_fd=reader,output=channel)==0
        lines=channel.getvalue().splitlines();assert len(lines)==2
        result=json.loads(lines[1]);assert result['schema']=='DEMO_CANCELLATION_SESSION_RESULT_V1'
        assert result['status']=='ORIGINAL_ID_QUERY_RECOVERY_REQUIRED' and len(calls)==1
        assert len(list((tmp_path/'cancellation-session-results').glob('*.json')))==1
        assert not (tmp_path/'session-results').exists()
    finally:os.close(reader);os.close(writer);store.close();close(args,stream)


def test_missing_cancel_store_does_not_initialize_it(tmp_path):
    from contextlib import ExitStack
    with ExitStack() as stack:
        with pytest.raises(FileNotFoundError):cli._open_cancel(stack,tmp_path)
    assert not (tmp_path/'cancellations.sqlite3').exists()
