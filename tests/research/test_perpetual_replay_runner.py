from dataclasses import replace
import json
from pathlib import Path
import runpy
import pytest
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research_runner.runner import JobQueue
from services.research_runner.run_kinds import validate_params
from tools import perpetual_replay_research as tool


def test_replay_runner_and_readonly_console_are_bound_and_idempotent(tmp_path,monkeypatch):
    fixture=runpy.run_path(str(Path(__file__).parents[1]/'execution/test_perpetual_public_replay.py'))
    capture=tmp_path/'capture.json';capture.write_text(json.dumps(fixture['capture_fixture']()))
    monkeypatch.setattr(tool,'CAPTURE',capture)
    monkeypatch.setattr(tool,'OUTPUT',tmp_path/'artifacts')
    monkeypatch.setattr(tool,'source_identity',lambda:{'commit':'fixture','python_source_sha256':'a'*64,'dependency_lock_sha256':'b'*64})
    store=ResearchRunStore(str(tmp_path/'runs.db'));reg=RunRegistry(store);queue=JobQueue(reg)
    run_id=queue.submit('perpetual_replay',{},1,invocation_id='fixed-invocation')
    queue.join()
    result=reg.get(run_id)
    assert result.status=='done',result.error
    assert result.verdict=='TECHNICAL_VALIDATION_ONLY'
    assert result.metrics['reconciliation_residual']=='0'
    assert queue.submit('perpetual_replay',{},2,invocation_id='fixed-invocation')==run_id
    from services.console.api import research_run_views as views
    monkeypatch.setattr(views,'_runs_registry',lambda:reg)
    payload=views.technical_replay(run_id)
    assert payload['authoritative'] is False
    assert payload['technical_replay']['metrics']['capture_id']==fixture['capture_fixture']()['capture_id']
    from fastapi.testclient import TestClient
    from services.console.main import app
    response = TestClient(app).get('/api/v1/console/technical-replay', params={'run_id': run_id})
    assert response.status_code == 200
    assert response.json()['technical_replay'] == payload['technical_replay']
    assert response.json()['snapshot_id']
    assert views._run_detail(result)['metrics']=={}  # legacy projection stays quarantined
    assert views._technical_replay_detail(replace(result,metrics={'reconciliation_residual':'9'})) is None


def test_missing_capture_becomes_error_not_done(tmp_path,monkeypatch):
    monkeypatch.setattr(tool,'CAPTURE',tmp_path/'absent.json')
    reg=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')));queue=JobQueue(reg)
    run_id=queue.submit('perpetual_replay',{},1,invocation_id='missing')
    queue.join()
    assert reg.get(run_id).status=='error'


@pytest.mark.parametrize('params',[{'path':'/tmp/x'},{'strategy_id':True},{'strategy_id':3},{'frequency_ms':0}])
def test_replay_parameters_rejected_before_queue(params):
    with pytest.raises(ValueError): validate_params('perpetual_replay',params)
