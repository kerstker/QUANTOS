import json
from services.research.gold_monitor_view import activity_view, validate_view
from tests.research.test_gold_account_journal import account_event, trade_event


def test_partial_updates_preserve_baseline_and_redact_identifiers():
    baseline={'completed_at_ms':900,'reads':[{'kind':'positions','data':[
        {'symbol':'XAUUSDT','positionSide':'BOTH','positionAmt':'1','entryPrice':'4000'}]}]}
    rows=[{'kind':'EVENT','at_ms':1000,'data':{'payload':account_event()}},
          {'kind':'EVENT','at_ms':1001,'data':{'payload':trade_event()}}]
    a=activity_view(baseline,rows)
    assert a['positions'][0]['quantity']=='1'
    assert a['recent_trades'][0]['quantity']=='0.01'
    assert 'trade_id' not in json.dumps(a) and 'order_id' not in json.dumps(a)
    assert a['position_complete'] is False


def test_view_freshness_does_not_create_authentication_or_history():
    from services.research.gold_account_journal import AccountJournal
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as directory:
        with AccountJournal(Path(directory)/'j.db',account_ref='a'*64) as j:
            j.start(at_ms=1000,source_sha256='b'*64);j.lifecycle('CONNECTED',at_ms=1001)
            v={'schema':'GOLD_MONITOR_VIEW_V1','as_of_ms':1001,'summary':j.summary(as_of_ms=1001),'recovery':None}
            assert validate_view(v,now_ms=1002)['status']=='CONNECTED'
            assert validate_view(v,now_ms=200000)['status']=='STALE'


def test_conflicting_stream_does_not_display_a_chosen_position():
    p=account_event();p['a']['P']=[{'s':'XAUUSDT','ps':'BOTH','pa':'1','ep':'4000'}]
    old=account_event();old['E']=900;old['a']['P']=[{'s':'XAUUSDT','ps':'BOTH','pa':'2','ep':'4000'}]
    rows=[{'kind':'EVENT','at_ms':1001,'data':{'payload':p}},
          {'kind':'EVENT','at_ms':1002,'data':{'payload':old}}]
    assert activity_view({'completed_at_ms':800,'reads':[]},rows)['positions']==[]


def test_rest_recovered_trades_are_visible_without_duplicate_counting():
    from tests.research.test_gold_trade_observation import document
    f=document();f['fills'][0].update(trade_id='7',order_id='42',time_ms=1000,price='4000',quantity='0.01',commission='0.02')
    rows=[{'kind':'EVENT','at_ms':1001,'data':{'payload':trade_event()}}]
    a=activity_view({'completed_at_ms':800,'reads':[]},rows,rest_fills=f)
    assert len(a['recent_trades'])==1
    rows=[]
    assert len(activity_view({'completed_at_ms':800,'reads':[]},rows,rest_fills=f)['recent_trades'])==1
