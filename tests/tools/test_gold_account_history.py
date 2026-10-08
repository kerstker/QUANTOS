import pytest
from tools.gold_account_history import fetch_window


def test_time_partition_keeps_same_millisecond_without_skipping():
    calls=[]
    def read(path,params):
        calls.append(dict(params))
        if params['startTime']==1000 and params['endTime']==1003:
            return [{'time':1001,'id':1},{'time':1001,'id':2}]
        if params['startTime']==1000:return [{'time':1001,'id':1}]
        return [{'time':1002,'id':2}]
    # Partition responses may change; fetcher never advances by max timestamp+1.
    rows,evidence=fetch_window(read,'/fapi/v1/userTrades',1000,1004,limit=2)
    assert len(rows)==2 and len(calls)==3
    assert calls[1]['endTime']+1==calls[2]['startTime']


def test_saturated_millisecond_and_budget_are_incomplete():
    def read(*a):return [{'time':1000,'id':1},{'time':1000,'id':2}]
    with pytest.raises(ValueError,match='SATURATED_MILLISECOND'):
        fetch_window(read,'/fapi/v1/userTrades',1000,1001,limit=2)
    with pytest.raises(ValueError,match='PAGE_BUDGET'):
        fetch_window(read,'/fapi/v1/userTrades',1000,1004,limit=2,max_pages=1)


def test_income_pages_keep_time_range_and_detect_repeated_page():
    def read(path,p):return [{'time':1000,'tranId':1}]*2
    with pytest.raises(ValueError,match='REPEATED_PAGE'):
        fetch_window(read,'/fapi/v1/income',1000,2000,limit=2)


def test_endpoint_and_outside_rows_refused():
    with pytest.raises(ValueError):fetch_window(lambda *a:[], '/fapi/v1/order',1000,2000)
    with pytest.raises(ValueError):fetch_window(lambda *a:[{'time':999}],'/fapi/v1/userTrades',1000,2000)


def test_two_pass_history_detects_delayed_income_and_keeps_both():
    from tools.gold_account_history import collect_history
    count=0
    def read(path,p):
        nonlocal count
        if path.endswith('income'):
            count+=1
            return [] if count==1 else [{'symbol':'','incomeType':'TRANSFER','income':'1','asset':'USDT',
                                         'time':1500,'tranId':1,'tradeId':''}]
        return []
    result=collect_history(read,account_ref='a'*64,start_ms=1000,end_ms=2000,exported_ms=3000)
    assert result['status']=='CHANGED_DURING_READ'
    assert len(result['passes'])==2 and result['history_complete'] is False


def test_signed_reader_is_get_only_and_diagnostics_do_not_include_secrets(monkeypatch):
    import urllib.request
    import urllib.error
    import io
    from tools.gold_account_history import signed_reader, HistoryRequestFailure
    class Opener:
        def open(self,request,timeout):
            assert request.method=='GET'
            assert request.full_url.startswith('https://fapi.binance.com/fapi/v1/income?')
            raise urllib.error.HTTPError(request.full_url,400,'secret-message',{},io.BytesIO(b'{"code":-2015,"msg":"secret-message"}'))
    monkeypatch.setattr(urllib.request,'build_opener',lambda *a:Opener())
    read=signed_reader('fake-key','fake-secret',clock_ms=lambda:1000,pause=lambda x:None)
    with pytest.raises(HistoryRequestFailure) as caught:read('/fapi/v1/income',{'startTime':1,'endTime':2,'limit':1000,'page':1})
    assert caught.value.diagnostic=={'endpoint':'/fapi/v1/income','http_status':400,'exchange_code':-2015}
    assert 'secret' not in str(caught.value)


def test_unexpected_page_shape_has_redacted_diagnostic():
    from tools.gold_account_history import HistoryContractFailure
    with pytest.raises(HistoryContractFailure) as failure:
        fetch_window(lambda *a:{'private':'secret','orders':[]},'/fapi/v1/allAlgoOrders',1000,2000)
    assert failure.value.diagnostic=={'endpoint':'/fapi/v1/allAlgoOrders','reason':'INVALID_HISTORY_PAGE','shape':'OBJECT'}
    assert 'secret' not in str(failure.value)


def test_missing_history_time_is_reported_without_raw_rows():
    from tools.gold_account_history import collect_history,HistoryContractFailure
    def read(path,params):return [{'algoId':1,'private':'secret'}] if path.endswith('allAlgoOrders') else []
    with pytest.raises(HistoryContractFailure) as caught:
        collect_history(read,account_ref='a'*64,start_ms=1000,end_ms=2000,exported_ms=3000)
    assert caught.value.diagnostic['missing_field']=='createTime'
    assert 'private' not in str(caught.value.diagnostic)


def test_algo_order_states_retain_old_creation_with_explicit_scope_evidence():
    row={'createTime':999,'updateTime':1500,'algoId':1}
    rows,proof=fetch_window(lambda *a:[row],'/fapi/v1/allAlgoOrders',1000,2000)
    assert rows==[row]
    assert proof[0]['outside_creation_window']==1
    assert proof[0]['scope']=='ORDER_STATE_OBSERVATIONS'
    # Without a trustworthy creation-time partition, a full page cannot be
    # recursively split and silently presented as complete.
    with pytest.raises(ValueError,match='ORDER_STATE_PAGE_SATURATED'):
        fetch_window(lambda *a:[row],'/fapi/v1/allAlgoOrders',1000,2000,limit=1)
    for path in ('/fapi/v1/userTrades','/fapi/v1/income','/fapi/v1/allOrders'):
        with pytest.raises(ValueError,match='HISTORY_OUTSIDE_WINDOW'):
            fetch_window(lambda *a:[{'time':999}],path,1000,2000)
