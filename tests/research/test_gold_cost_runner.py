import json
import pytest

from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research_runner.runner import JobQueue
from services.research.trial_ledger import TrialLedger
from tests.tools.test_gold_cost_replay import inputs
from tools.gold_cost_inputs import dump_inputs
from tools import gold_cost_research as tool


def setup(tmp_path, monkeypatch, *, broken=False):
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    args = inputs()
    monkeypatch.setattr(tool, 'source_identity', lambda: args['release_identity'])
    source = tmp_path/'data/cache/gold_cost_replay/inputs'
    source.mkdir(parents=True)
    (source/'1.json').write_bytes(b'{}' if broken else dump_inputs(args))
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    return registry, JobQueue(registry), tmp_path/'data/cache/gold_cost_replay/results/1'


def test_real_queue_retains_input_ledger_and_cost_report(tmp_path, monkeypatch):
    reg, queue, dest = setup(tmp_path, monkeypatch)
    uid = queue.submit('gold_cost_replay', {'attempt': 1}, 1, invocation_id='cost-one')
    queue.join()
    run = reg.get(uid)
    assert run.status == 'done', run.error
    assert run.verdict == 'TECHNICAL_VALIDATION_ONLY'
    assert run.metrics['ending_equity'] == '1000.5733'
    manifest = json.loads((dest/'manifest.json').read_bytes())
    report = json.loads((dest/'report.json').read_bytes())
    assert report['simulation']['reconciliation']['residual'] == '0'
    assert manifest['terminal'] == 'PASSED' and manifest['authoritative'] is False
    with TrialLedger(str(dest/'trials.db'), create=False, read_only=True) as ledger:
        ledger.verify()
        assert [r.status for r in ledger.load(manifest['trial_id']).events] == ['PLANNED', 'RUNNING', 'PASSED']
    assert queue.submit('gold_cost_replay', {'attempt': 1}, 2, invocation_id='cost-one') == uid
    original = (dest/'manifest.json').read_bytes()
    duplicate = queue.submit('gold_cost_replay', {'attempt': 1}, 3, invocation_id='cost-two')
    queue.join()
    assert reg.get(duplicate).status == 'error'
    assert (dest/'manifest.json').read_bytes() == original


def test_invalid_input_is_retained_as_failed_trial(tmp_path, monkeypatch):
    reg, queue, dest = setup(tmp_path, monkeypatch, broken=True)
    uid = queue.submit('gold_cost_replay', {'attempt': 1}, 1, invocation_id='bad-cost')
    queue.join()
    assert reg.get(uid).status == 'error'
    manifest = json.loads((dest/'manifest.json').read_bytes())
    assert manifest['terminal'] == 'FAILED'
    assert (dest/'input.json').read_bytes() == b'{}'
    with TrialLedger(str(dest/'trials.db'), create=False, read_only=True) as ledger:
        assert ledger.load(manifest['trial_id']).status == 'FAILED'


def test_wrong_source_is_failed_without_running_simulation(tmp_path, monkeypatch):
    reg, queue, dest = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(tool, 'source_identity', lambda: {'commit': 'e'*40})
    def forbidden(**kwargs):
        pytest.fail('mismatched release reached simulation')
    monkeypatch.setattr(tool, 'replay_costs', forbidden)
    uid = queue.submit('gold_cost_replay', {'attempt': 1}, 1, invocation_id='wrong-source')
    queue.join()
    assert reg.get(uid).status == 'error'
    assert json.loads((dest/'manifest.json').read_bytes())['terminal'] == 'FAILED'


def test_interrupt_retains_running_trial_and_never_publishes_completion(tmp_path, monkeypatch):
    import sqlite3
    _, _, dest = setup(tmp_path, monkeypatch)
    def interrupted(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(tool, 'replay_costs', interrupted)
    with pytest.raises(KeyboardInterrupt):
        tool.run_replay({'attempt': 1})
    assert (dest/'input.json').is_file()
    assert not (dest/'manifest.json').exists()
    with sqlite3.connect(dest/'trials.db') as connection:
        assert connection.execute('SELECT status FROM trial_events ORDER BY event_index').fetchall() == [('PLANNED',), ('RUNNING',)]
    with pytest.raises(FileExistsError):
        tool.run_replay({'attempt': 1})


@pytest.mark.parametrize('params', [{}, {'attempt': True}, {'attempt': 0},
                                  {'attempt': 1001}, {'attempt': 1, 'path': '/tmp/x'}])
def test_no_freeform_job_parameters(params):
    with pytest.raises(ValueError):
        tool.run_replay(params)


def test_linked_input_cannot_be_archived(tmp_path, monkeypatch):
    _, _, dest = setup(tmp_path, monkeypatch)
    source = tmp_path/'data/cache/gold_cost_replay/inputs/1.json'
    target = tmp_path/'original.json'
    source.rename(target)
    source.symlink_to(target)
    with pytest.raises(ValueError, match='INVALID_GOLD_COST_PATH'):
        tool.run_replay({'attempt': 1})
    assert not dest.exists()


@pytest.mark.parametrize('tamper', [False, True])
def test_public_assembly_is_rechecked_in_actual_queue(tmp_path, monkeypatch, tamper):
    from dataclasses import replace
    from tests.tools.test_gold_public_cost_inputs import request
    from tools.gold_public_cost_inputs import assemble_inputs
    reg, queue, dest = setup(tmp_path, monkeypatch)
    args = assemble_inputs(**request())
    if tamper:
        args['frames'] = (replace(args['frames'][0], quotes=()), *args['frames'][1:])
    source = tmp_path/'data/cache/gold_cost_replay/inputs/1.json'
    source.write_bytes(dump_inputs(args))
    uid = queue.submit('gold_cost_replay', {'attempt': 1}, 1, invocation_id='public-cost')
    queue.join()
    record = reg.get(uid)
    assert record.status == ('error' if tamper else 'done'), record.error
    if not tamper:
        assert record.metrics['outcome'] == 'OPEN_AT_END'
        assert record.metrics['residual'] == '0'
    assert json.loads((dest/'manifest.json').read_bytes())['terminal'] == ('FAILED' if tamper else 'PASSED')
