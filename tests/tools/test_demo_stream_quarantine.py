"""Rejected financial evidence stays private; lease/credential bytes never persist."""
import asyncio
import hashlib
import json
import os

import pytest

from tools import demo_executor_stream as worker
from tests.research.test_demo_user_stream import NOW, account_event
from tests.research.test_demo_user_stream_collector import Socket
from services.research.demo_user_stream import UserStreamJournal


def run(tmp_path,monkeypatch,raw):
    with UserStreamJournal(tmp_path/'user-stream.sqlite3',account_ref_sha256='a'*64):pass
    monkeypatch.setattr(worker,'require_custody',lambda:tmp_path)
    monkeypatch.setattr(worker,'_check_path',lambda *a,**k:None)
    monkeypatch.setattr(worker,'now_ms',lambda:NOW+100)
    async def lease(method):return 'x'*64
    monkeypatch.setattr(worker,'lease_child',lease)
    monkeypatch.setattr(worker,'connect_socket',lambda token:Socket([raw]))
    asyncio.run(worker.run(source_commit='b'*40,stop=asyncio.Event(),policy=worker.CapturePolicy(seconds=1,max_messages=1)))
    with UserStreamJournal(tmp_path/'user-stream.sqlite3',read_only=True) as store:
        assert store.snapshot(as_of_ms=NOW+100)['gaps']==1
        assert store.snapshot(as_of_ms=NOW+100)['messages']==0
    return list((tmp_path/'receipt-reconciliation/rejected-stream').glob('*.json'))


def test_unrecognized_numeric_financial_field_preserves_exact_evidence_and_gap(tmp_path,monkeypatch,caplog):
    event=account_event();event['a']['B'][0]['future']= '0.125'
    raw=json.dumps(event,indent=1)
    paths=run(tmp_path,monkeypatch,raw)
    assert len(paths)==1
    report=json.loads(paths[0].read_bytes())
    assert report['raw_utf8']==raw and report['status']=='RETAINED_FOR_OFFLINE_REVIEW'
    assert report['response_sha256']==hashlib.sha256(raw.encode()).hexdigest()
    assert report['account_ref_sha256']=='a'*64 and report['economic_authority'] is False
    assert os.stat(paths[0]).st_mode & 0o777 == 0o600
    assert raw not in caplog.text


@pytest.mark.parametrize('raw',[
    '{"e":"listenKeyExpired","E":"bad","listenKey":"secret-lease"}',
    '{"e":"ACCOUNT_UPDATE","E":1,"T":1,"apiKey":"secret-api-key"}',
    '{"e":"ACCOUNT_UPDATE","E":1,"T":1,"a":{"token":"secret-token"}}',
    '{"e":"ACCOUNT_UPDATE","E":1,"e":"secret-duplicate"}',
    '{invalid secret',
])
def test_secret_or_unparseable_rejection_retains_hash_only(tmp_path,monkeypatch,raw):
    paths=run(tmp_path,monkeypatch,raw)
    assert len(paths)==1
    report=json.loads(paths[0].read_bytes())
    assert report['raw_utf8'] is None and report['status']=='HASH_ONLY_UNSAFE_PAYLOAD'
    assert 'secret-' not in paths[0].read_text()


def test_full_quarantine_still_retains_stream_gap_and_emits_fixed_error(tmp_path,monkeypatch,caplog):
    from tools import demo_stream_quarantine
    monkeypatch.setattr(demo_stream_quarantine,'MAX_RECORDS',0)
    event=account_event();event['unexpected']=1
    assert run(tmp_path,monkeypatch,json.dumps(event))==[]
    assert 'REJECTION_RETENTION_UNAVAILABLE' in caplog.text
