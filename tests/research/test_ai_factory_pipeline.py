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
    for name in ('run_assessment','run_comparison','run_verification','run_observation','checkpoint'):
        monkeypatch.setattr(tool,name,lambda p,n=name:calls.append(n) or {'result_hash':'a'*64})
    tool.run_pipeline({'attempt':1})
    assert calls==['run_assessment','run_comparison','run_verification','run_observation','checkpoint']
