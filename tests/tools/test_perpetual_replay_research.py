from pathlib import Path
import json
import runpy
import pytest
from tools import perpetual_replay_research as tool
F=runpy.run_path(str(Path(__file__).parents[1]/'execution/test_perpetual_public_replay.py'))


def test_real_shape_replay_preserves_identity_and_reconciles():
    capture=F['capture_fixture']()
    result=tool.replay_capture(capture,release_identity={'commit':'fixture'})
    assert result['orders'][0]['status']=='FILLED'
    assert result['reconciliation']['residual']=='0'
    assert result==tool.replay_capture(capture,release_identity={'commit':'fixture'})
    assert result['dataset_manifest']['capture_id']==capture['capture_id']
    assert result['authoritative'] is False
    changed=tool.replay_capture(capture,release_identity={'commit':'different'})
    assert changed['result_hash']!=result['result_hash']


def test_fixed_runner_paths_missing_capture_never_serve_stale_result(tmp_path,monkeypatch):
    monkeypatch.setattr(tool,'CAPTURE',tmp_path/'absent.json')
    with pytest.raises(FileNotFoundError): tool.run_replay({})
    with pytest.raises(ValueError): tool.run_replay({'path':'/tmp/arbitrary'})


def test_runner_artifact_is_deterministic_and_append_once(tmp_path,monkeypatch):
    capture=tmp_path/'capture.json';capture.write_text(json.dumps(F['capture_fixture']()))
    monkeypatch.setattr(tool,'CAPTURE',capture)
    monkeypatch.setattr(tool,'OUTPUT',tmp_path/'results')
    monkeypatch.setattr(tool,'source_identity',lambda:{'commit':'fixture'})
    a=tool.run_replay({});b=tool.run_replay({})
    assert a==b
    file=next((tmp_path/'results').glob('*.json'))
    file.write_text('{}')
    with pytest.raises(ValueError,match='conflicting'): tool.run_replay({})


@pytest.mark.parametrize('params',[{'strategy_id':True},{'frequency_ms':True},{'strategy_id':3},{'frequency_ms':0}])
def test_bounded_parameters_reject_bool_and_invalid_values(params):
    with pytest.raises(ValueError): tool.replay_capture(F['capture_fixture'](),release_identity={'commit':'fixture'},**params)
