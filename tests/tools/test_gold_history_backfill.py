import json
import pytest
from tests.research.test_gold_trade_observation import native_document
from tools.gold_history_backfill import backfill


def test_history_resume_deduplicates_and_preserves_failed_ranges(tmp_path):
    calls=[];fail=[True]
    def factory():
        def read(path,p):
            calls.append(path)
            if path.endswith('income') and fail[0]:raise ValueError('private detail must not escape')
            if path.endswith('userTrades'):return native_document()['fills']
            return []
        return read
    kw=dict(account_ref='a'*64,start_ms=1000,end_ms=2000,clock_ms=lambda:3000,reader_factory=factory)
    first=backfill(tmp_path,**kw)
    assert first['status']=='PARTIAL' and first['completed_tasks']==3
    assert first['fills']['fills'][0]['trade_id']=='123'
    assert 'private detail' not in json.dumps(first)
    fail[0]=False;calls.clear();second=backfill(tmp_path,**kw)
    assert second['status']=='QUERIED' and second['completed_tasks']==4
    assert calls==['/fapi/v1/income']*2
    assert len(second['fills']['fills'])==1 and second['history_complete'] is False
    saved=next((tmp_path/'tasks').glob('*.json'));saved.write_text('{}')
    with pytest.raises(ValueError,match='ARCHIVE_HASH_MISMATCH'):backfill(tmp_path,**kw)


def test_changing_history_is_not_marked_queried(tmp_path):
    count=[0]
    def factory():
        def read(path,p):
            if path.endswith('userTrades'):
                count[0]+=1;return native_document()['fills'] if count[0]==1 else []
            return []
        return read
    result=backfill(tmp_path,account_ref='a'*64,start_ms=1000,end_ms=2000,clock_ms=lambda:3000,reader_factory=factory)
    assert result['status']=='PARTIAL'
    assert result['fills']['fills']==[]


def test_retention_boundary_keeps_queryable_remainder_and_explicit_gap(tmp_path):
    from tools.gold_account_history import HistoryRequestFailure
    from tools.gold_history_backfill import DAY
    def factory():
        def read(path,p):
            if path.endswith('allOrders') and p['startTime']==1000:
                raise HistoryRequestFailure({'endpoint':path,'http_status':400,'exchange_code':-4166})
            return []
        return read
    result=backfill(tmp_path,account_ref='a'*64,start_ms=1000,end_ms=200000,
                    clock_ms=lambda:90*DAY+50000,reader_factory=factory)
    assert result['completed_tasks']==4
    assert result['unqueried_ranges']==[{'kind':'orders','start_ms':1000,'end_ms':110000,'reason':'RETENTION_BOUNDARY'}]
    assert result['history_complete'] is False
