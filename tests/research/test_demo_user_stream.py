"""Private stream facts must survive duplicates, disconnects and process restart."""
import hashlib
import json
import sqlite3

import pytest

from services.research.demo_user_stream import UserStreamJournal, normalize_message

NOW = 1789159800000


def account_event():
    return {'e':'ACCOUNT_UPDATE','E':NOW,'T':NOW-1,'a':{'m':'FUNDING_FEE','S':'BTCUSDT',
        'B':[{'a':'USDT','wb':'100.00000001','cw':'100.00000001','bc':'-0.00000003'}],'P':[]}}


def test_partial_funding_update_preserves_exact_facts_without_inventing_flat_positions():
    raw=json.dumps(account_event())
    event=normalize_message(raw)
    assert event['payload']['a']['P']==[]
    assert event['payload']['a']['B'][0]['bc']=='-0.00000003'
    assert event['payload']['a']['S']=='BTCUSDT'
    assert event['raw_utf8']==raw
    assert event['response_sha256']==hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.parametrize('value', ['0','2',0,2])
def test_order_expiry_reason_preserves_exact_string_or_existing_integer_encoding(value):
    from tests.research.test_binance_order_receipts import message
    p=message(NOW,'NEW');p['o']['er']=value
    raw=json.dumps(p);event=normalize_message(raw)
    assert event['payload']['o']['er']==value and type(event['payload']['o']['er']) is type(value)
    assert event['raw_utf8']==raw


@pytest.mark.parametrize('value', [True, 0.0, '0.0', '01', '-1', '1e0', '', str(2**63)])
def test_expiry_reason_rejects_inexact_or_oversized_tokens(value):
    from tests.research.test_binance_order_receipts import message
    p=message(NOW,'NEW');p['o']['er']=value
    with pytest.raises(ValueError):normalize_message(json.dumps(p))


def test_balance_only_funding_update_preserves_omitted_position_field(tmp_path):
    body=account_event();body['a'].pop('P')
    raw=json.dumps(body);event=normalize_message(raw)
    assert 'P' not in event['payload']['a']
    with UserStreamJournal(tmp_path/'stream.db',account_ref_sha256='a'*64) as store:
        store.start_session(source_sha256='b'*64,at_ms=NOW)
        store.record('MESSAGE',at_ms=NOW,message=event)
        assert store.snapshot(as_of_ms=NOW)['event_counts']=={'ACCOUNT_UPDATE':1}
        assert store.verify()['records']==2


@pytest.mark.parametrize('change',['secret_field','float_amount','duplicate_asset','duplicate_key','null_positions','object_positions'])
def test_unsafe_or_ambiguous_payload_is_rejected(change):
    body=account_event()
    if change=='secret_field':body['apiKey']='a'*64
    elif change=='float_amount':body['a']['B'][0]['wb']=100.0
    elif change=='duplicate_asset':body['a']['B']*=2
    elif change=='null_positions':body['a']['P']=None
    elif change=='object_positions':body['a']['P']={}
    raw=json.dumps(body)
    if change=='duplicate_key':raw=raw.replace('"e": "ACCOUNT_UPDATE"','"e": "ACCOUNT_UPDATE", "e": "ORDER_TRADE_UPDATE"')
    with pytest.raises(ValueError):normalize_message(raw)


def test_expired_lease_never_persists_the_secret_token():
    raw=json.dumps({'e':'listenKeyExpired','E':NOW,'listenKey':'c'*64})
    event=normalize_message(raw)
    assert event['raw_utf8'] is None
    assert event['payload']=={'e':'listenKeyExpired','E':NOW}
    assert 'c'*64 not in json.dumps(event)


def test_stream_restart_and_duplicates_retain_facts_and_uncertain_coverage(tmp_path):
    path=tmp_path/'stream.db'
    with UserStreamJournal(path,account_ref_sha256='a'*64) as store:
        store.start_session(source_sha256='b'*64,at_ms=NOW)
        store.record('CONNECTED',at_ms=NOW+1)
        event=normalize_message(json.dumps(account_event()))
        store.record('MESSAGE',at_ms=NOW+2,message=event)
        store.record('MESSAGE',at_ms=NOW+3,message=event)
        assert store.verify()['records']==4
    with UserStreamJournal(path,account_ref_sha256='a'*64) as store:
        store.start_session(source_sha256='b'*64,at_ms=NOW+100)
        summary=store.snapshot(as_of_ms=NOW+101)
        assert summary['messages']==2 and summary['unique_messages']==1
        assert summary['gaps']==1
        assert summary['coverage_verified'] is False
        assert summary['cash_reconciliation']=='UNKNOWN'
        assert summary['last_gap']['reason']=='UNCLEAN_RESTART'
        with pytest.raises(sqlite3.DatabaseError):store.db.execute('DELETE FROM stream_records')
    with UserStreamJournal(path,read_only=True) as store:
        assert store.verify()['records']==6
        assert store.snapshot(as_of_ms=NOW+102)['messages']==2


def test_wrong_account_and_tampering_cannot_resume_as_valid(tmp_path):
    path=tmp_path/'stream.db'
    with UserStreamJournal(path,account_ref_sha256='a'*64) as store:
        store.start_session(source_sha256='b'*64,at_ms=NOW)
    with pytest.raises(ValueError):UserStreamJournal(path,account_ref_sha256='c'*64)
    with sqlite3.connect(path) as db:
        db.execute('DROP TRIGGER stream_records_no_update')
        db.execute("UPDATE stream_records SET body='{}' WHERE sequence=1")
    with pytest.raises(ValueError):
        with UserStreamJournal(path,account_ref_sha256='a'*64) as store:store.verify()


def test_missing_immutability_trigger_is_not_silently_repaired(tmp_path):
    path=tmp_path/'stream.db'
    with UserStreamJournal(path,account_ref_sha256='a'*64) as store:
        store.start_session(source_sha256='b'*64,at_ms=NOW)
    with sqlite3.connect(path) as db:
        db.execute('DROP TRIGGER stream_records_no_update')
    before=path.read_bytes()
    with pytest.raises(ValueError):UserStreamJournal(path,account_ref_sha256='a'*64)
    assert path.read_bytes()==before


def test_foreign_database_cannot_be_modified_when_opening_stream(tmp_path):
    path=tmp_path/'foreign.db'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE other(value TEXT)')
    before=path.read_bytes()
    with pytest.raises(ValueError):UserStreamJournal(path,account_ref_sha256='a'*64)
    assert path.read_bytes()==before


def test_export_is_bounded_and_retains_gap_at_window_boundary(tmp_path):
    from tests.research.test_binance_order_receipts import message
    # Existing raw receipt fixture defines the actual exchange-shaped order.
    raw=json.dumps(message(NOW,'NEW'))
    with UserStreamJournal(tmp_path/'stream.db',account_ref_sha256='a'*64) as store:
        store.start_session(source_sha256='b'*64,at_ms=NOW-100)
        store.record('GAP',at_ms=NOW-10,reason='STREAM_DISCONNECTED')
        store.record('CONNECTED',at_ms=NOW+1)
        store.record('MESSAGE',at_ms=NOW+2,message=normalize_message(raw))
        exported=store.export_order_messages(start_ms=NOW,end_ms=NOW+100)
        assert exported['messages'][0]['raw_utf8']==raw
        assert exported['preceding_gap']['reason']=='STREAM_DISCONNECTED'
        assert exported['coverage_verified'] is False
        with pytest.raises(ValueError):store.export_order_messages(start_ms=True,end_ms=NOW)
