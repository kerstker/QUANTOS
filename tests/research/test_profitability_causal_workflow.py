import copy
from urllib.parse import urlparse,parse_qs
import pytest

from tools import profitability_causal_workflow as module
from tools.profitability_causal_capture import capture_information
from tools.profitability_study import write_new
from tools.btc_factory_research import verified
from tests.research.test_profitability_causal_information import receipts,HOUR


def capture(output,*,slot,clock):
    def fetch(url):
        query=parse_qs(urlparse(url).query);quote=(query.get('symbol') or query['pair'])[0][-4:]
        kind='hourly' if query.get('interval')==['1h'] else 'spot' if 'api.binance.com/api' in url else 'index' if 'indexPrice' in url else 'funding' if 'fundingRate' in url else 'oi' if 'openInterest' in url else 'perpetual'
        return receipts(slot,quote)[kind]['payload']
    return capture_information(output,slot=slot,fetch=fetch,clock=clock)


@pytest.fixture
def root(tmp_path,monkeypatch):
    source={k:'fixed' for k in ('python_source_sha256','dependency_lock_sha256','python_version','architecture')}
    monkeypatch.setattr(module,'source_identity',lambda:source)
    p0=tmp_path/'data/cache/profitability/1/p0';p0.mkdir(parents=True)
    write_new(p0/'result.json',{'decision':'P1_BOUNDED_INNER_COMPARISON_ALLOWED','economic_eligible':False})
    return tmp_path


def test_registration_recovery_actual_clock_and_no_repeated_capture(root):
    assert module.advance_information(root,1,clock=lambda:47*HOUR+1) is None
    registered=module.advance_information(root,1,register=True,clock=lambda:47*HOUR+1)
    assert registered['status']=='WAITING_WINDOW'
    folder=root/'data/cache/profitability/1/causal';plan=(folder/'plan.json').read_bytes()
    current=module.advance_information(root,1,clock=lambda:48*HOUR+1000,capture=capture)
    assert current['status']=='COLLECTING' and len(current['quotes'])==2
    value=verified(folder/'captures'/str(48*HOUR)/'USDC.json')
    assert value['available_ms']==48*HOUR+1000
    def unexpected(*a,**kw):raise AssertionError('same slot fetched twice')
    module.advance_information(root,1,clock=lambda:48*HOUR+2000,capture=unexpected)
    assert (folder/'plan.json').read_bytes()==plan
    current=module.advance_information(root,1,clock=lambda:49*HOUR+1000,capture=capture)
    assert [q['ready_rows'] for q in current['quotes']]==[1,1]


def test_collection_can_nest_under_orchestrator_but_same_worker_file_stays_exclusive(root):
    import threading
    from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
    with acquire_runtime_lock(root/'outer-study.lock') as outer:
        assert outer
        result=module.advance_information(root,1,register=True,clock=lambda:47*HOUR+1)
        assert result['status']=='WAITING_WINDOW'
    worker=root/'data/cache/profitability/1/causal/worker.lock'
    with acquire_runtime_lock(worker,process_lock=threading.Lock()) as same_file:
        assert same_file
        assert module.advance_information(root,1,clock=lambda:48*HOUR+1000)['status']=='BUSY'


@pytest.mark.parametrize('failure',[False,True])
def test_resident_consumes_information_only_success_and_failure_reports(root,monkeypatch,failure):
    from tools import ai_factory_daemon
    now=47*HOUR+1
    module.advance_information(root,1,register=True,clock=lambda:now)
    monkeypatch.setattr(module.time,'time_ns',lambda:now*1000000)
    from services.research.ai_factory_store import FactoryStore
    from tools.ai_factory_checkpoint import restore_checkpoint
    with FactoryStore(root/'data/cache/ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3})
    if failure:
        def failed(*a,**kw):raise ValueError('retained source mismatch')
        monkeypatch.setattr(module,'advance_information',failed)
    result=ai_factory_daemon.tick(root,now_ms=now)
    assert result['status']==('ERROR' if failure else 'RUNNING')
    assert result['attempts'][0]['status']==('ERROR' if failure else 'WAITING_WINDOW')
    assert result['attempts'][0]['kind']=='information'
    assert result['checkpoint']['status']=='VERIFIED_RESEARCH_CHECKPOINT'
    restored=root/'restored'
    restore_checkpoint(root/'data/cache/ai_factory_checkpoint.zip',restored)
    assert (restored/'profitability/1/causal/plan.json').read_bytes()==(root/'data/cache/profitability/1/causal/plan.json').read_bytes()
    again=ai_factory_daemon.tick(root,now_ms=now)
    assert again['checkpoint']['status']=='VERIFIED_DAILY_CHECKPOINT'
    assert verified(root/'data/cache/ai_factory/resident.json')['status']==result['status']


def test_failed_public_receipts_remain_visible_without_backfill(root):
    module.advance_information(root,1,register=True,clock=lambda:47*HOUR+1)
    def failed(output,*,slot,clock):
        def network(url):raise OSError('unavailable')
        return capture_information(output,slot=slot,clock=clock,fetch=network)
    module.advance_information(root,1,clock=lambda:48*HOUR+1000,capture=failed)
    current=module.advance_information(root,1,clock=lambda:49*HOUR+1000,capture=capture)
    assert current['quotes'][0]['missing_slots']==[48*HOUR]
    assert current['quotes'][0]['ready_rows']==0
    folder=root/'data/cache/profitability/1/causal'
    assert len(list((folder/'captures'/str(48*HOUR)).glob('*.failure.json')))==12
    plan=verified(folder/'plan.json')
    def no_study(*a,**kw):raise AssertionError('incomplete panel cannot run research')
    result=module.advance_information(root,1,clock=lambda:plan['mature_ms'],study=no_study)
    assert result['status']=='WINDOW_FINISHED'
    assert result['quotes'][0]['status']=='STOP_INCOMPLETE_CAUSAL_WINDOW' and result['study'] is None


def test_resume_rechecks_source_receipts_and_source_identity(root,monkeypatch):
    module.advance_information(root,1,register=True,clock=lambda:47*HOUR+1)
    module.advance_information(root,1,clock=lambda:48*HOUR+1000,capture=capture)
    folder=root/'data/cache/profitability/1/causal';path=folder/'captures'/str(48*HOUR)/'USDC-hourly.json'
    before=path.read_bytes();path.write_bytes(before.replace(b'124',b'125'))
    with pytest.raises(ValueError):module.advance_information(root,1,clock=lambda:48*HOUR+2000)
    assert verified(folder/'status.json')['status']=='ERROR'
    path.write_bytes(before)
    changed=copy.deepcopy(module.source_identity());changed['python_source_sha256']='changed'
    monkeypatch.setattr(module,'source_identity',lambda:changed)
    with pytest.raises(ValueError,match='implementation'):module.advance_information(root,1,clock=lambda:48*HOUR+2000)


def test_collector_keeps_currency_failures_independent(tmp_path):
    slot=48*HOUR
    def fetch(url):
        query=parse_qs(urlparse(url).query);quote=(query.get('symbol') or query['pair'])[0][-4:]
        if quote=='USDT':raise OSError('one unavailable venue product')
        kind='hourly' if query.get('interval')==['1h'] else 'spot' if 'api.binance.com/api' in url else 'index' if 'indexPrice' in url else 'funding' if 'fundingRate' in url else 'oi' if 'openInterest' in url else 'perpetual'
        return receipts(slot)[kind]['payload']
    rows,failures=capture_information(tmp_path/'capture',slot=slot,fetch=fetch,clock=lambda:slot+1000)
    assert [r['quote'] for r in rows]==['USDC'] and all(r['quote']=='USDT' for r in failures)


def test_completed_window_runs_each_currency_once_and_keeps_failed_stage(root,monkeypatch):
    from services.research.profitability_causal_information import observation
    module.advance_information(root,1,register=True,clock=lambda:47*HOUR+1)
    folder=root/'data/cache/profitability/1/causal';plan=verified(folder/'plan.json')
    data={q:[observation(receipts(t,q),slot=t,quote=q,issued_ms=t+1000)
        for t in range(plan['warmup_ms'],plan['end_slot_ms']+HOUR,HOUR)] for q in plan['quotes']}
    monkeypatch.setattr(module,'_observations',lambda output,plan,quote:data[quote]);calls=[]
    def study(output,plan,collected):
        quote=collected['quote'];calls.append(quote);output.mkdir()
        (output/'P3').mkdir()
        result={'status':'COMPLETED_TECHNICAL_ONLY','decision':'STOP_NO_INCREMENT','trials_attempted':4,'selected_id':0,'cases':[]}
        write_new(output/'P3/result.json',result)
        if quote=='USDT':raise RuntimeError('P4 failed after P3 completed')
        result={'status':'COMPLETED_TECHNICAL_ONLY','quote':quote,'plan_hash':plan['result_hash'],
                'results':{'P3':verified(output/'P3/result.json')},'economic_eligible':False}
        write_new(output/'result.json',result);return result
    result=module.advance_information(root,1,clock=lambda:plan['mature_ms'],study=study)
    assert calls==['USDC','USDT'] and result['study']['results']['P3']['trials_attempted']==4
    assert result['usdt_study']['status']=='STOP_STUDY_FAILED'
    assert result['usdt_study']['results']['P3']['trials_attempted']==4
    module.advance_information(root,1,clock=lambda:plan['mature_ms']+1000,study=study)
    assert calls==['USDC','USDT']
    data['USDC'][-1]['price_features'][0]+=.01
    with pytest.raises(ValueError,match='panel changed'):module.advance_information(root,1,clock=lambda:plan['mature_ms']+2000,study=study)


def test_transient_requests_retry_with_retained_attempts_and_actual_receipts(tmp_path):
    from tools import profitability_causal_capture as collector
    slot=48*HOUR;calls={}
    def fetch(url):
        calls[url]=calls.get(url,0)+1
        if calls[url]==1:raise TimeoutError('transient')
        query=parse_qs(urlparse(url).query);quote=(query.get('symbol') or query['pair'])[0][-4:]
        kind='hourly' if query.get('interval')==['1h'] else 'spot' if 'api.binance.com/api' in url else 'index' if 'indexPrice' in url else 'funding' if 'fundingRate' in url else 'oi' if 'openInterest' in url else 'perpetual'
        return receipts(slot,quote)[kind]['payload']
    destination=tmp_path/'capture'
    rows,failures=collector.capture_information(destination,slot=slot,fetch=fetch,clock=lambda:slot+1000,sleep=lambda _:None)
    assert len(rows)==2 and not failures and set(calls.values())=={2}
    result=verified(destination/'result.json')
    assert result['request_attempts']==24 and result['recovered_requests']==12
    assert len(list((destination/'attempts').glob('*.json')))==24
    assert all(r['issued_ms']==slot+1000 for r in rows)


def test_requests_that_cross_decision_deadline_do_not_become_observations(tmp_path):
    from tools import profitability_causal_capture as collector
    slot=48*HOUR;now=[slot+239000];calls=[]
    def fetch(url):
        calls.append(url);now[0]=slot+300000
        raise TimeoutError('late')
    rows,failures=collector.capture_information(tmp_path/'late',slot=slot,fetch=fetch,clock=lambda:now[0],sleep=lambda _:None)
    assert not rows and failures
    assert len(calls)<=3
    assert any(f.get('category')=='DEADLINE_EXCEEDED' for f in failures)


def test_invalid_payload_is_not_retried(tmp_path):
    from tools import profitability_causal_capture as collector
    calls=[]
    def malformed(url):calls.append(url);return None
    rows,failures=collector.capture_information(tmp_path/'invalid',slot=48*HOUR,fetch=malformed,clock=lambda:48*HOUR+1000,sleep=lambda _:None)
    assert not rows and len(calls)==12
    assert all(f.get('category')=='INVALID_RESPONSE' for f in failures if 'kind' in f)


def test_scheduler_resumes_only_unfinished_current_capture(root):
    module.advance_information(root,1,register=True,clock=lambda:47*HOUR+1)
    folder=root/'data/cache/profitability/1/causal/captures'/str(48*HOUR)
    folder.mkdir(parents=True)
    called=[]
    def unfinished(output,*,slot,clock):called.append(slot)
    module.advance_information(root,1,clock=lambda:48*HOUR+245000,capture=unfinished)
    assert called==[48*HOUR]
    module.advance_information(root,1,clock=lambda:48*HOUR+300000,capture=unfinished)
    assert called==[48*HOUR]


def test_interrupted_request_reservation_survives_resume(tmp_path,monkeypatch):
    from tools import profitability_causal_capture as collector
    class SerialPool:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def map(self,fn,values):return map(fn,values)
    monkeypatch.setattr(collector,'ThreadPoolExecutor',SerialPool)
    slot=48*HOUR;calls=[];interrupted=[False]
    def fetch(url):
        calls.append(url)
        if 'api.binance.com/api' in url and not interrupted[0]:
            interrupted[0]=True;raise KeyboardInterrupt()
        query=parse_qs(urlparse(url).query);quote=(query.get('symbol') or query['pair'])[0][-4:]
        kind='hourly' if query.get('interval')==['1h'] else 'spot' if 'api.binance.com/api' in url else 'index' if 'indexPrice' in url else 'funding' if 'fundingRate' in url else 'oi' if 'openInterest' in url else 'perpetual'
        return receipts(slot,quote)[kind]['payload']
    dest=tmp_path/'capture'
    with pytest.raises(KeyboardInterrupt):collector.capture_information(dest,slot=slot,fetch=fetch,clock=lambda:slot+1000)
    original=(dest/'USDC-hourly.json').read_bytes()
    rows,failures=collector.capture_information(dest,slot=slot,fetch=fetch,clock=lambda:slot+2000)
    assert len(rows)==2 and not failures
    assert (dest/'USDC-hourly.json').read_bytes()==original
    assert sum('interval=1h' in url and 'USDC' in url for url in calls)==1
    assert verified(dest/'result.json')['request_attempts']==13
    assert (dest/'attempts/reservations/USDC-spot-1.json').exists()
    assert (dest/'attempts/USDC-spot-2.json').exists()


def test_status_exposes_latest_capture_attempt_diagnostics(root):
    module.advance_information(root,1,register=True,clock=lambda:47*HOUR+1)
    result=module.advance_information(root,1,clock=lambda:48*HOUR+1000,capture=capture)
    assert result['collection']['slot_ms']==48*HOUR
    assert result['collection']['request_attempts']==12
    assert result['collection']['status']=='COMPLETE' and result['collection']['failures']==[]


def test_rate_limit_holds_other_requests_until_retry_after(tmp_path,monkeypatch):
    from tools import profitability_causal_capture as collector
    from tools.profitability_market_capture import PublicRequestError
    class SerialPool:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def map(self,fn,values):return map(fn,values)
    monkeypatch.setattr(collector,'ThreadPoolExecutor',SerialPool)
    slot=48*HOUR;elapsed=[0.0];calls=[]
    def fetch(url):
        calls.append(elapsed[0])
        if len(calls)==1:raise PublicRequestError('HTTP_ERROR',http_status=429,retry_after_seconds=9)
        return None
    collector.capture_information(tmp_path/'rate',slot=slot,fetch=fetch,
        clock=lambda:slot+1000+int(elapsed[0]*1000),monotonic=lambda:elapsed[0],
        sleep=lambda seconds:elapsed.__setitem__(0,elapsed[0]+seconds))
    assert calls[0]==0 and min(calls[1:])>=9


def test_saving_receipt_failure_is_not_retried_as_network(tmp_path,monkeypatch):
    from tools import profitability_causal_capture as collector
    original=collector.write_new
    def disk_error(path,value):
        if path.name=='USDC-hourly.json':raise OSError('disk unavailable')
        return original(path,value)
    monkeypatch.setattr(collector,'write_new',disk_error)
    with pytest.raises(OSError,match='disk unavailable'):
        capture(tmp_path/'capture',slot=48*HOUR,clock=lambda:48*HOUR+1000)
    attempts=tmp_path/'capture/attempts/reservations'
    assert len(list(attempts.glob('USDC-hourly-*.json')))==1
    assert not (tmp_path/'capture/result.json').exists()
