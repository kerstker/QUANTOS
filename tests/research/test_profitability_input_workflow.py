from services.research.ai_factory_store import FactoryStore
from services.console.api.profitability_views import profitability_payload
from tools.profitability_study import write_new
from tools import profitability_input_workflow as module


def pair(identity):
    trace={'action_hash':identity,'symbol':'BTCUSDC','orders':[{'id':'a','quantity':'1','side':'BUY',
           'events':[{'at_ms':1,'state':'SENT','filled':'0'},{'at_ms':2,'state':'ACK','filled':'0'}]}]}
    return {'observed':trace,'simulated':trace}


def test_new_input_revision_does_not_overwrite_previous_failed_attempt(tmp_path,monkeypatch):
    cache=tmp_path/'data/cache';output=cache/'profitability/1';output.mkdir(parents=True)
    database=cache/'ai_factory/factory.db'
    with FactoryStore(database) as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'});store.update(1,'COMPLETED',{})
        first=store.add_profitability_input(1,'trajectory',pair('a'*64),'test')
    real=module.import_trajectories
    def fail(source,destination):
        destination.mkdir();write_new(destination/'failure.json',{'reason':'SIMULATED_IO_FAILURE','economic_eligible':False})
        raise ValueError('retained failed attempt')
    monkeypatch.setattr(module,'import_trajectories',fail)
    assert module.run_inputs(cache,output,1,tmp_path/'input',execution_only=True)['p5']['status']=='FAILED'
    with FactoryStore(database) as store:store.add_profitability_input(1,'trajectory',pair('b'*64),'test')
    monkeypatch.setattr(module,'import_trajectories',real)
    assert module.run_inputs(cache,output,1,tmp_path/'input',execution_only=True)['p5']['status']=='UNVERIFIED_TRAJECTORY_DIAGNOSTIC'
    assert (output/'inputs/trajectory'/first['input_hash']/'failure.json').exists()
    view=profitability_payload(tmp_path,1)
    assert view['p5']['status']=='UNVERIFIED_TRAJECTORY_DIAGNOSTIC'
    assert [r['result_status'] for r in view['inputs']]==['FAILED','UNVERIFIED_TRAJECTORY_DIAGNOSTIC']


def test_queued_job_keeps_the_requested_revision_when_a_new_version_arrives(tmp_path,monkeypatch):
    cache=tmp_path/'data/cache';database=cache/'ai_factory/factory.db'
    with FactoryStore(database) as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'});store.update(1,'COMPLETED',{})
        first=store.add_profitability_input(1,'trajectory',pair('a'*64),'test')
        second=store.add_profitability_input(1,'trajectory',pair('b'*64),'test')
    monkeypatch.setattr(module,'runtime_state_root',lambda:tmp_path)
    result=module.run_input_version({'attempt':1,'input_kind':2,'revision':1})
    assert result['p5']['action_hash']=='a'*64
    output=cache/'profitability/1/inputs/trajectory'
    assert (output/first['input_hash']/'result.json').exists()
    assert not (output/second['input_hash']/'result.json').exists()


def test_imported_information_counts_legacy_search_attempts(tmp_path,monkeypatch):
    from tests.research.test_profitability_basis_panel import observation
    cache=tmp_path/'data/cache';output=cache/'profitability/1'
    legacy=output/'p3_study';legacy.mkdir(parents=True)
    (legacy/'inner-0.started.json').write_text('{}')
    (legacy/'inner-1.started.json').write_text('{}')
    with FactoryStore(cache/'ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
        store.update(1,'COMPLETED',{})
        store.add_profitability_input(1,'basis',{'quote':'USDC','observations':[observation(3600000)]},'test')
    seen=[]
    def capture(*args,**kwargs):
        seen.append(kwargs['prior_trials_attempted']);return {'status':'TEST_ONLY'}
    monkeypatch.setattr(module,'run_p3',capture)
    module.run_inputs(cache,output,1,tmp_path/'input',execution_only=False)
    assert seen==[2]
