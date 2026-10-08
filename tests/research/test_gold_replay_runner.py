import json
from dataclasses import replace
import pytest
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research_runner.runner import JobQueue
from services.research_runner.run_kinds import validate_params
from tests.tools.test_gold_signal_replay import archive
from tools import gold_replay_research as tool


def setup(tmp_path,monkeypatch,document):
    source=tmp_path/'inputs';source.mkdir()
    if document is not None:(source/'1.json').write_text(json.dumps(document))
    monkeypatch.setattr(tool,'INPUTS',source)
    monkeypatch.setattr(tool,'OUTPUT',tmp_path/'outputs')
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    return registry,JobQueue(registry)


def test_registered_replay_is_idempotent_and_technical_projection_is_bound(tmp_path,monkeypatch):
    reg,queue=setup(tmp_path,monkeypatch,archive())
    run_id=queue.submit('gold_replay',{'attempt':1},1,invocation_id='gold-one')
    queue.join();r=reg.get(run_id)
    assert r.status=='done',r.error
    assert r.metrics['signal_count']==1 and r.metrics['decision_count']==2
    assert r.verdict=='TECHNICAL_VALIDATION_ONLY'
    assert queue.submit('gold_replay',{'attempt':1},2,invocation_id='gold-one')==run_id
    from services.console.api import research_run_views as views
    assert views._technical_replay_detail(r)['authoritative'] is False
    assert views._technical_replay_detail(replace(r,metrics={'signal_count':99})) is None
    assert (tmp_path/'outputs/1/manifest.json').is_file()


@pytest.mark.parametrize('missing',[True,False])
def test_missing_or_incomplete_input_becomes_failed_job(tmp_path,monkeypatch,missing):
    d=archive();d['packets']=d['packets'][:4]
    reg,queue=setup(tmp_path,monkeypatch,None if missing else d)
    run_id=queue.submit('gold_replay',{'attempt':1},1,invocation_id='gold-failure')
    queue.join()
    assert reg.get(run_id).status=='error'
    if not missing:
        report=json.loads((tmp_path/'outputs/1/report.json').read_bytes())
        assert report['status']=='INPUT_INCOMPLETE' and len(report['records'])==1


@pytest.mark.parametrize('params',[{}, {'attempt':True},{'attempt':0},{'attempt':1001},{'attempt':1,'path':'/tmp/private'}])
def test_parameter_boundary(params):
    with pytest.raises(ValueError):tool.run_replay(params)
    if params:
        with pytest.raises(ValueError):validate_params('gold_replay',params)


def test_new_invocation_cannot_replace_a_retained_attempt(tmp_path,monkeypatch):
    reg,queue=setup(tmp_path,monkeypatch,archive())
    first=queue.submit('gold_replay',{'attempt':1},1,invocation_id='first')
    queue.join()
    original=(tmp_path/'outputs/1/manifest.json').read_bytes()
    second=queue.submit('gold_replay',{'attempt':1},2,invocation_id='second')
    queue.join()
    assert reg.get(first).status=='done' and reg.get(second).status=='error'
    assert (tmp_path/'outputs/1/manifest.json').read_bytes()==original


def test_linked_input_is_rejected_before_archive_creation(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch,None)
    target=tmp_path/'outside.json';target.write_text(json.dumps(archive()))
    (tmp_path/'inputs/1.json').symlink_to(target)
    with pytest.raises(ValueError,match='LINKED_GOLD_RESEARCH_PATH'):
        tool.run_replay({'attempt':1})
    assert not (tmp_path/'outputs').exists()


def test_gold_replay_uses_configured_state_root_not_release_directory(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    release = tmp_path / 'release'
    release.mkdir()
    source = state / 'data/cache/gold_replay/inputs'
    source.mkdir(parents=True)
    (source / '1.json').write_text(json.dumps(archive()))
    monkeypatch.chdir(release)
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(state))
    registry = RunRegistry(ResearchRunStore(str(state / 'runs.db')))
    queue = JobQueue(registry)
    run_id = queue.submit('gold_replay', {'attempt': 1}, 1, invocation_id='isolated-gold')
    queue.join()
    result = registry.get(run_id)
    assert result.status == 'done', result.error
    assert result.metrics['signal_count'] == 1
    assert (state / 'data/cache/gold_replay/results/1/manifest.json').is_file()
    assert list(release.iterdir()) == []
