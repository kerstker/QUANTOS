import json
import pytest
from services.data import kraken_client_order_lookup as lookup

NOW=1800000000000
REQUEST={'client_order_id':'a'*32,'not_before_ms':NOW,'deadline_ms':NOW+10000}


def reads(mode='closed'):
    row={'cl_ord_id':'a'*32,'opentm':'1800000000.001','status':'open'}
    opened={'ORDER-1':dict(row)} if mode in ('open','transition') else {}
    row['status']='canceled'
    closed={'ORDER-1':row} if mode in ('closed','transition') else {}
    values=[{'open':opened},{'closed':closed,'count':len(closed)}]
    return [{'endpoint':endpoint,'params':params,'account_ref_sha256':'b'*64,
             'requested_at_ms':NOW+20000+i*100,'received_at_ms':NOW+20050+i*100,
             'body':json.dumps({'error':[],'result':value})}
            for i,((endpoint,params),value) in enumerate(zip(lookup.requests(REQUEST),values))]


@pytest.mark.parametrize('mode',['open','closed','transition'])
def test_open_or_fast_closed_order_is_discovered_without_resend_permission(mode):
    result=lookup.assess(reads(mode),REQUEST,as_of_ms=NOW+20200)
    assert result['status']=='ORDER_ID_OBSERVED' and result['order_id']=='ORDER-1'
    assert result['resubmission_allowed'] is False and result['absence_proven'] is False


def test_empty_results_are_unknown_not_proof_of_non_submission():
    result=lookup.assess(reads('empty'),REQUEST,as_of_ms=NOW+20200)
    assert result['status']=='UNRESOLVED' and result['order_id'] is None


@pytest.mark.parametrize('change',['reused','partial_page','wrong_id','scope','account','clock'])
def test_ambiguous_or_incomplete_lookup_never_recovers_an_id(change):
    source=reads()
    raw=json.loads(source[1]['body'])
    if change=='reused':raw['result']['closed']['ORDER-2']=dict(raw['result']['closed']['ORDER-1']);raw['result']['count']=2
    if change=='partial_page':raw['result']['count']=51
    if change=='wrong_id':raw['result']['closed']['ORDER-1']['cl_ord_id']='c'*32
    source[1]['body']=json.dumps(raw)
    if change=='scope':source[1]['params']['closetime']='close'
    if change=='account':source[1]['account_ref_sha256']='c'*64
    if change=='clock':source[1]['received_at_ms']=NOW+30000
    try:result=lookup.assess(source,REQUEST,as_of_ms=NOW+20200)
    except ValueError:return
    assert result['status']=='UNRESOLVED'


def test_old_reused_client_id_is_excluded_by_exact_open_time():
    source=reads();raw=json.loads(source[1]['body'])
    raw['result']['closed']['ORDER-1']['opentm']='1799999999.999'
    source[1]['body']=json.dumps(raw)
    assert lookup.assess(source,REQUEST,as_of_ms=NOW+20200)['status']=='UNRESOLVED'


def test_same_exchange_id_cannot_change_creation_time_between_reads():
    source=reads('transition');raw=json.loads(source[1]['body'])
    raw['result']['closed']['ORDER-1']['opentm']='1799999999.999'
    source[1]['body']=json.dumps(raw)
    with pytest.raises(ValueError):lookup.assess(source,REQUEST,as_of_ms=NOW+20200)
