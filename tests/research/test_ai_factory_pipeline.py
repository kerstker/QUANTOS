import pytest
from tools import ai_factory_pipeline as tool
from tools.btc_factory_research import verified


def test_pipeline_stops_after_failed_stage_and_keeps_research(monkeypatch,tmp_path):
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path));dest=tmp_path/'data/cache/ai_factory/1';dest.mkdir(parents=True)
    research={'state':'COMPLETED','command':'run'}
    monkeypatch.setattr(tool,'run_factory',lambda p:research)
    def fail(p):raise ValueError('fixture unavailable')
    monkeypatch.setattr(tool,'run_assessment',fail)
    monkeypatch.setattr(tool,'run_observation',lambda p:pytest.fail('must not activate after diagnosis failure'))
    with pytest.raises(ValueError):tool.run_pipeline({'attempt':1})
    assert research['state']=='COMPLETED' and verified(dest/'pipeline.json')['stage']=='ASSESSMENT'


def test_pipeline_has_real_dependency_order(monkeypatch,tmp_path):
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path));(tmp_path/'data/cache/ai_factory/1').mkdir(parents=True)
    calls=[]
    monkeypatch.setattr(tool,'run_factory',lambda p:{'state':'COMPLETED','command':'run'})
    for name in ('run_assessment','run_portfolio','run_comparison','run_verification','run_observation','run_portfolio_observation','run_verdict','checkpoint'):
        monkeypatch.setattr(tool,name,lambda p,n=name:calls.append(n) or {'result_hash':'a'*64})
    tool.run_pipeline({'attempt':1})
    assert calls==['run_assessment','run_portfolio','run_comparison','run_verification','run_observation','run_portfolio_observation','run_verdict','checkpoint']


@pytest.mark.parametrize('kind',['ai_factory','ai_factory_pipeline','ai_factory_portfolio','ai_factory_portfolio_forward','ai_factory_verdict'])
def test_real_runner_accepts_each_factory_adapter_source(tmp_path,monkeypatch,kind):
    from dataclasses import replace
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.run_kinds import RUN_KINDS
    from services.research_runner.runner import JobQueue,validate_run_provenance
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    from tools.btc_factory_research import seal
    result=seal({'schema':'QUANTOS_FACTORY_PORTFOLIO_V1','authoritative':False,'economic_eligible':False}) if kind=='ai_factory_portfolio' else {'state':'COMPLETED','attempt':1,'result':{}}
    if kind=='ai_factory_portfolio_forward':result={'status':'WAITING_WINDOW','attempt':1}
    if kind=='ai_factory_verdict':result=seal({'schema':'QUANTOS_RESEARCH_VERDICT_V1','scope':'OUTER_EXPLANATION','authoritative':False,'economic_eligible':False,'search_feedback_allowed':False})
    spec=replace(RUN_KINDS[kind],runner=lambda params:result)
    monkeypatch.setitem(RUN_KINDS,kind,spec)
    queue=JobQueue(registry,max_workers=1)
    run_id=queue.submit(kind,{'attempt':1,**({'action':1} if kind=='ai_factory_portfolio_forward' else {})},now=1)
    queue.join()
    run=registry.get(run_id)
    assert run.status=='done',run.error
    validate_run_provenance(run,spec)
