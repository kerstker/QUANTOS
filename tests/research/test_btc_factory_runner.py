"""Whole-policy accounting, durable cases and explicit interruption recovery."""
import json
from decimal import Decimal as D
import numpy as np
import pytest
from tools import btc_factory_research as tool
from services.research.trial_ledger import TrialLedger


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    days=np.arange(1704067200000,1704067200000+400*tool.DAY,tool.DAY,dtype=np.int64)
    start=int(days[300]+tool.DAY+5*tool.MINUTE);end=start+2*tool.DAY
    times=np.arange(start,end+tool.MINUTE,tool.MINUTE,dtype=np.int64)
    market=dict(times=times,prices=np.full(len(times),100.),marks=np.full(len(times),100.),
                funding=[],start=start,end=end,daily_volume={int(t//tool.DAY):D('10000000') for t in times})
    rng=np.random.default_rng(4);x=rng.normal(size=(len(days),5));y=np.full(len(days),.01)
    monkeypatch.setattr(tool,'load_data',lambda **kwargs:(market,days,None,None,None,{}))
    monkeypatch.setattr(tool,'load_panel',lambda path:(days,x,y,{}))
    monkeypatch.setattr(tool,'source_identity',lambda:{'commit':'test','python_source_sha256':'a'*64})
    return tmp_path/'data/cache/btc_factory/results/1'


def test_cases_share_ledger_and_resume_without_recalculation(tmp_path,monkeypatch):
    dest=setup(tmp_path,monkeypatch)
    result=tool.run_factory({'attempt':1})
    assert len(result['cases'])==48
    assert all(c['status']=='PASSED' for c in result['cases'])
    assert all(c['metrics']['reconciliation_residual']=='0' for c in result['cases'])
    assert all(c['metrics']['net_pnl']==0 for c in result['cases'] if c['policy']=='CASH')
    assert all(c['metrics']['fees']!='0' for c in result['cases'] if c['policy']=='PASSIVE25')
    first=(dest/'result.json').read_bytes()
    monkeypatch.setattr(tool,'replay',lambda **kwargs:pytest.fail('completed scenario replayed'))
    assert tool.run_factory({'attempt':1,'resume':True})==result
    assert (dest/'result.json').read_bytes()==first
    with pytest.raises(ValueError,match='exists'):tool.run_factory({'attempt':1})


def test_interruption_retains_completed_cases_and_continues_only_remaining(tmp_path,monkeypatch):
    dest=setup(tmp_path,monkeypatch)
    original=tool.replay;calls=[]
    def interrupted(**kwargs):
        calls.append(1)
        if len(calls)==3:raise KeyboardInterrupt()
        return original(**kwargs)
    monkeypatch.setattr(tool,'replay',interrupted)
    with pytest.raises(KeyboardInterrupt):tool.run_factory({'attempt':1})
    assert len(list((dest/'cases').glob('*.json')))==2
    monkeypatch.setattr(tool,'replay',original)
    result=tool.run_factory({'attempt':1,'resume':True})
    assert len(result['cases'])==48
    assert all(c['status']=='PASSED' for c in result['cases'])
    case=next((dest/'cases').glob('*.json'))
    body=json.loads(case.read_text());body['metrics']['net_pnl']=99
    case.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='hash'):tool.run_factory({'attempt':1,'resume':True})


def test_missing_input_is_registered_and_failed_cases_are_not_retried(tmp_path,monkeypatch):
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    with pytest.raises(ValueError):tool.run_factory({'attempt':1})
    root=tmp_path/'data/cache/btc_factory/results'
    plan=json.loads((root/'1/plan.json').read_text())
    with TrialLedger(str(root/'trials.db'),create=False) as ledger:
        assert all(ledger.load(r['trial_id']).status=='FAILED' for r in plan['trials'])
    assert (root/'1/failure.json').is_file()


def test_factory_queue_and_console_contract(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    from dataclasses import replace
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research_runner.runner import JobQueue
    from services.console.api import research_run_views as views
    from fastapi.testclient import TestClient
    from services.console.main import app
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue=JobQueue(registry)
    run_id=queue.submit('btc_factory',{'attempt':1},1,invocation_id='factory')
    queue.join();record=registry.get(run_id)
    assert record.status=='done',record.error
    monkeypatch.setattr(views,'_runs_registry',lambda:registry)
    body=TestClient(app).get('/api/v1/console/technical-replay',params={'run_id':run_id}).json()
    assert len(body['technical_replay']['factory_report']['cases'])==48
    assert 'factory_report_json' not in body['technical_replay']['metrics']
    assert views._technical_replay_detail(replace(record,metrics={})) is None
