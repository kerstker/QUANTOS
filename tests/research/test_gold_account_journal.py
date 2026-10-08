import json
import sqlite3

import pytest

from services.research.gold_account_journal import AccountJournal, normalize_event, reconcile_trades


def account_event():
    return {'e':'ACCOUNT_UPDATE','E':1000,'T':999,'a':{'m':'FUNDING_FEE',
        'B':[{'a':'USDT','wb':'100','cw':'100','bc':'-0.01'}]}}


def trade_event():
    return {'e':'ORDER_TRADE_UPDATE','E':1001,'T':1000,'o':{
        's':'XAUUSDT','i':42,'t':7,'T':1000,'S':'BUY','ps':'BOTH',
        'x':'TRADE','X':'PARTIALLY_FILLED','o':'LIMIT','q':'0.02','p':'4000',
        'l':'0.01','z':'0.01','L':'4000','m':False,'R':False,
        'N':'USDT','n':'0.02','rp':'0'}}


def test_partial_updates_and_lease_tokens_are_not_account_state():
    event=normalize_event(json.dumps(account_event()))
    assert 'P' not in event['payload']['a']
    expired=normalize_event(json.dumps({'e':'listenKeyExpired','E':1002,'listenKey':'private-token'}))
    assert 'private-token' not in json.dumps(expired)
    assert expired['payload']=={'e':'listenKeyExpired','E':1002}


def test_journal_restarts_keep_gap_and_duplicate_evidence(tmp_path):
    path=tmp_path/'journal.db'
    with AccountJournal(path,account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
        j.lifecycle('CONNECTED',at_ms=1000)
        j.message(json.dumps(account_event()),at_ms=1001)
        j.message(json.dumps(trade_event()),at_ms=1002)
        j.message(json.dumps(trade_event()),at_ms=1003)
        j.lifecycle('STOPPED',at_ms=1004)
        report=j.summary(as_of_ms=1005)
        assert report['unique_trades']==1 and report['duplicate_events']==1
        assert report['status']=='STOPPED' and report['history_complete'] is False
    with AccountJournal(path,account_ref='a'*64) as j:
        j.start(at_ms=1010,source_sha256='c'*64)
        j.lifecycle('CONNECTED',at_ms=1011)
        report=j.summary(as_of_ms=1012)
        assert report['gap_count']==1
        assert report['reconciliation']=='REQUIRED'
        assert report['unique_trades']==1
    with AccountJournal(path,account_ref='a'*64,read_only=True) as j:
        assert j.summary(as_of_ms=1012)==report
        with pytest.raises(ValueError):j.lifecycle('STOPPED',at_ms=1013)
    assert path.stat().st_mode & 0o777 == 0o600


def test_identity_switch_and_clock_regression_are_rejected(tmp_path):
    path=tmp_path/'journal.db'
    with AccountJournal(path,account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
        with pytest.raises(ValueError):j.lifecycle('CONNECTED',at_ms=999)
    with pytest.raises(ValueError):AccountJournal(path,account_ref='c'*64)


def test_conflicting_trade_is_kept_and_blocks_reconciliation(tmp_path):
    with AccountJournal(tmp_path/'journal.db',account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
        j.lifecycle('CONNECTED',at_ms=1001)
        j.message(json.dumps(trade_event()),at_ms=1002)
        changed=trade_event();changed['o']['L']='4001'
        j.message(json.dumps(changed),at_ms=1003)
        report=j.summary(as_of_ms=1003)
        assert report['trade_conflicts']==1 and report['status']=='DATA_CONFLICT'


def test_rejected_messages_leave_digest_only_and_gap(tmp_path):
    with AccountJournal(tmp_path/'journal.db',account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
        assert j.message('{"secret":"private-value"}',at_ms=1001) is False
        assert j.summary(as_of_ms=1001)['gap_count']==1
    assert b'private-value' not in (tmp_path/'journal.db').read_bytes()


def test_schema_damage_is_not_silently_repaired(tmp_path):
    path=tmp_path/'journal.db'
    with AccountJournal(path,account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
    with sqlite3.connect(path) as db:db.execute('DROP TRIGGER records_no_delete')
    with pytest.raises(ValueError):AccountJournal(path,account_ref='a'*64)


def test_stream_and_rest_trade_comparison_does_not_double_count_or_claim_cash_reconciliation(tmp_path):
    from tests.research.test_gold_trade_observation import document
    d=document();d['fills'][0].update(trade_id='7',order_id='42',time_ms=1000,
        price='4000',quantity='0.01',commission='0.02')
    with AccountJournal(tmp_path/'journal.db',account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
        j.message(json.dumps(trade_event()),at_ms=1002)
        r=reconcile_trades(j,d)
        assert r['status']=='MATCHING_OBSERVED_TRADES'
        assert r['matched']==1 and r['cash_reconciled'] is False
        assert r['history_complete'] is False
        d['fills'][0]['commission']='0.03'
        assert reconcile_trades(j,d)['status']=='MISMATCH'


def test_rest_only_and_stream_only_trades_remain_visible(tmp_path):
    from tests.research.test_gold_trade_observation import document
    with AccountJournal(tmp_path/'journal.db',account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
        j.message(json.dumps(trade_event()),at_ms=1002)
        r=reconcile_trades(j,document())
        assert r['rest_only']==1 and r['stream_only']==1 and r['status']=='MISSING_TRADES'


def test_malformed_and_out_of_order_events_do_not_refresh_good_coverage(tmp_path):
    with AccountJournal(tmp_path/'journal.db',account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
        j.message(json.dumps(account_event()),at_ms=1001)
        older=account_event();older['E']=900
        j.message(json.dumps(older),at_ms=1002)
        assert j.summary(as_of_ms=1003)['out_of_order_events']==1
        assert j.summary(as_of_ms=200000)['status']=='STALE'


def test_atomic_restart_and_unclean_exit_do_not_erase_prefix(tmp_path):
    path=tmp_path/'journal.db'
    with AccountJournal(path,account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
        j.message(json.dumps(account_event()),at_ms=1001)
    with AccountJournal(path,account_ref='a'*64) as j:
        j.start(at_ms=1010,source_sha256='b'*64)
        assert j.summary(as_of_ms=1011)['gap_count']==1
        assert j.summary(as_of_ms=1011)['event_count']==1


def test_second_writer_rejected_but_reader_allowed(tmp_path):
    path=tmp_path/'journal.db'
    with AccountJournal(path,account_ref='a'*64):
        with pytest.raises(ValueError,match='JOURNAL_IN_USE'):
            AccountJournal(path,account_ref='a'*64)
        with AccountJournal(path,account_ref='a'*64,read_only=True):pass
    with AccountJournal(path,account_ref='a'*64):pass


def test_insecure_existing_journal_rejected(tmp_path):
    path=tmp_path/'journal.db'
    with AccountJournal(path,account_ref='a'*64):pass
    path.chmod(0o644)
    with pytest.raises(ValueError,match='PRIVATE_JOURNAL_REQUIRED'):
        AccountJournal(path,account_ref='a'*64)


@pytest.mark.parametrize('payload',[
 {'e':'ALGO_UPDATE','E':1000,'T':999,'o':{'aid':12,'s':'XAUUSDT','S':'SELL','ps':'BOTH','X':'TRIGGERED','ai':'42','q':'0.01','tp':'3990','cp':False}},
 {'e':'CONDITIONAL_ORDER_TRIGGER_REJECT','E':1000,'T':999,'or':{'s':'XAUUSDT','i':42,'r':'private text'}},
 {'e':'TRADE_LITE','E':1000,'T':999,'s':'XAUUSDT','i':42,'t':7,'S':'BUY','m':False,'q':'0.01','p':'4000','l':'0.01','L':'4000'},
 {'e':'MARGIN_CALL','E':1000,'cw':'10','p':[{'s':'XAUUSDT','ps':'BOTH','pa':'1','mt':'cross','iw':'0','mp':'4000','up':'-10','mm':'20'}]},
])
def test_extended_events_are_recorded_without_claiming_additional_fills(payload,tmp_path):
    normalized=normalize_event(json.dumps(payload))
    assert normalized['payload']['e']==payload['e']
    assert 'private text' not in json.dumps(normalized)
    with AccountJournal(tmp_path/'journal.db',account_ref='a'*64) as j:
        j.start(at_ms=1000,source_sha256='b'*64)
        assert j.message(json.dumps(payload),at_ms=1001)
        assert j.summary(as_of_ms=1001)['unique_trades']==0


def test_nontrade_minus_one_trade_id_and_optional_order_fields():
    p=trade_event();p['o'].update(x='NEW',X='NEW',t=-1,l='0',z='0',L='0',sp='3990',cp=True)
    assert normalize_event(json.dumps(p))['payload']['o']['sp']=='3990'
