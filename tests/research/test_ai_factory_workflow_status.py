import pytest
from services.console.api.ai_factory import factory_payload
from services.research.ai_factory_store import FactoryStore


@pytest.mark.parametrize('failed', [False, True])
def test_completed_research_requires_runner_publication(tmp_path, monkeypatch, failed):
    from dataclasses import replace
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.run_kinds import RUN_KINDS
    from services.research_runner.runner import JobQueue
    from tools.btc_factory_research import seal
    from tools.cache_io import atomic_write_json
    def execute(params):
        if failed:
            raise ValueError('private backend exception')
        return {'state':'COMPLETED','attempt':1,'result':{}}
    monkeypatch.setitem(RUN_KINDS,'ai_factory_pipeline',replace(RUN_KINDS['ai_factory_pipeline'],runner=execute))
    db=tmp_path/'data/cache/research_runs.db'
    registry=RunRegistry(ResearchRunStore(str(db)))
    queue=JobQueue(registry,max_workers=1)
    run_id=queue.submit('ai_factory_pipeline',{'attempt':1},now=1)
    queue.join()
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
        store.c.execute("UPDATE sessions SET state='COMPLETED' WHERE attempt=1")
        store.event(1,'OPERATOR_ACTION',{'action':'resume','run_id':run_id})
        store.event(1,'OPERATOR_ACTION',{'action':'observe','run_id':'unrelated'})
        store.c.commit()
    dest=tmp_path/'data/cache/ai_factory/1/pipeline.json';dest.parent.mkdir()
    atomic_write_json(str(dest),seal({'status':'COMPLETED','stage':'COMPLETED'}))
    workflow=factory_payload(tmp_path,1)['workflow']
    assert workflow['status']==('FAILED' if failed else 'COMPLETED')
    assert workflow['can_resume'] is failed
    assert workflow['run_id']==run_id
    assert 'private' not in str(workflow)
    dest.unlink()
    assert factory_payload(tmp_path,1)['workflow']['status']!='COMPLETED'


def test_missing_runner_is_unknown_and_does_not_create_database(tmp_path):
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
    assert factory_payload(tmp_path,1)['workflow']['status']=='UNKNOWN'
    assert not (tmp_path/'data/cache/research_runs.db').exists()


@pytest.mark.parametrize('runner_status', ['queued','running'])
def test_active_retry_overrides_old_failed_pipeline(monkeypatch,tmp_path,runner_status):
    from types import SimpleNamespace
    from services.console.api import ai_factory_workflow as view
    from services.research.run_registry.contract import RUN_OWNER_RUNNER_INVOCATION
    run=SimpleNamespace(owner=RUN_OWNER_RUNNER_INVOCATION,kind='ai_factory_pipeline',params={'attempt':1},run_id='active',status=runner_status)
    monkeypatch.setattr(view.ResearchRunStore,'read_snapshot',lambda *args:run)
    monkeypatch.setattr(view,'validate_run_provenance',lambda *args:None)
    session={'attempt':1,'state':'COMPLETED','command':'run'}
    events=[{'kind':'OPERATOR_ACTION','payload':{'action':'resume','run_id':'active'}}]
    result=view.workflow_payload(tmp_path,session,events,{'status':'FAILED','stage':'ASSESSMENT'})
    assert result['status']==runner_status.upper() and not result['can_resume']
    run.params={'attempt':2}
    assert view.workflow_payload(tmp_path,session,events,None)['status']=='UNKNOWN'
    run.params={'attempt':1}
    def reject(*args):raise ValueError('integrity failure')
    monkeypatch.setattr(view,'validate_run_provenance',reject)
    assert view.workflow_payload(tmp_path,session,events,None)['status']=='UNKNOWN'
