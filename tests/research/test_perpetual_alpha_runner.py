import pytest
import numpy as np
from decimal import Decimal as D
from dataclasses import replace
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research_runner.runner import JobQueue
from services.research_runner.run_kinds import validate_params
from tools import perpetual_alpha_research as tool


def test_missing_input_is_persisted_error_not_success(tmp_path,monkeypatch):
    monkeypatch.setattr(tool,'INPUT',tmp_path/'missing')
    monkeypatch.setattr(tool,'OUTPUT',tmp_path/'results')
    monkeypatch.setattr(tool,'TRIALS',tmp_path/'trials.db')
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue=JobQueue(registry)
    run=queue.submit('perpetual_alpha',{'attempt':1},1,invocation_id='one')
    queue.join()
    assert registry.get(run).status=='error'
    assert queue.submit('perpetual_alpha',{'attempt':1},2,invocation_id='one')==run
    assert not (tmp_path/'trials.db').exists()


@pytest.mark.parametrize('params',[{'path':'/tmp/x'},{'attempt':True},{'attempt':0},{'attempt':1001}])
def test_unbounded_research_requests_are_rejected(params):
    with pytest.raises(ValueError): validate_params('perpetual_alpha',params)


def test_queue_runs_bounded_search_and_console_verifies_provenance(tmp_path,monkeypatch):
    start=1735689900000;end=start+40*86400000
    times=np.arange(start,end+60000,60000,dtype=np.int64)
    days=np.arange(start-300000-60*86400000,end-300000,86400000,dtype=np.int64)
    market=dict(times=times,prices=np.full(len(times),100.),marks=np.full(len(times),100.),
                funding=[],start=start,end=end,daily_volume={int(t//86400000):D('10000000') for t in times})
    monkeypatch.setattr(tool,'load_data',lambda:(market,days,np.full((len(days),5),100.),
                        np.full(len(days),100.),np.zeros(len(days)),{'aligned_minutes':len(times)}))
    monkeypatch.setattr(tool,'OUTPUT',tmp_path/'results')
    monkeypatch.setattr(tool,'TRIALS',tmp_path/'trials.db')
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')));queue=JobQueue(registry)
    run=queue.submit('perpetual_alpha',{'attempt':1},1,invocation_id='bounded')
    queue.join();record=registry.get(run)
    assert record.status=='done',record.error
    assert record.metrics['trial_count']==78 and record.metrics['candidate_count']==11
    assert record.metrics['failed_cases']==0 and record.metrics['further_review_count']==0
    from services.console.api import research_run_views as views
    monkeypatch.setattr(views,'_runs_registry',lambda:registry)
    detail=views.technical_replay(run)
    assert detail['technical_replay']['authoritative'] is False
    assert detail['technical_replay']['metrics']['trial_count']==78
    assert len(detail['technical_replay']['series']['candidates'])==13
    assert views._technical_replay_detail(replace(record,metrics={'trial_count':1})) is None
