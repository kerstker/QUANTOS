from copy import deepcopy
import json

import pytest

from live.execution.demo_batch_risk import _digest


def bundle():
    return dict(schema='GOLD_BAR_AUTO_EPISODE_INPUT_V1', source_commit='c'*40,
        trial_manifest_sha256='b'*64, archive_source_commit='e'*40,
        candidate='channel', seconds=30, episode_id='a'*64)


def outcome(current, status='NO_ENTRY_SIGNAL'):
    decision = dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', source_commit='c'*40,
        bundle_sha256=_digest(current), account_ref_sha256='d'*64, status=status,
        position_flat=True, pilot_control={'status': 'ACTIVE', 'fixture_initial_equity': '5000'},
        submission_enabled=False, economic_authority=False)
    result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
        source_commit='c'*40, bundle_sha256=_digest(current), decision_sha256=_digest(decision),
        decision=decision, submission_enabled=False, economic_authority=False)
    if status == 'STRATEGY_EPISODE_SETTLED':
        result['feedback'] = dict(status='BAR_STRATEGY_FEEDBACK_RETAINED',
            cash_reconciled=True, feedback_sha256='f'*64, submission_enabled=False, economic_authority=False)
        result['research_feedback'] = dict(status='RESEARCH_FEEDBACK_DELIVERED',
            cycle_sha256=_digest(current), feedback_sha256='f'*64, source_verified=False,
            submission_enabled=False, economic_authority=False)
    return result


def setup(tmp_path, monkeypatch):
    from tools import gold_bar_rotation as rotation, demo_supervisor as supervisor
    from tools import demo_operating_bundle as installer
    executor=tmp_path/'executor';executor.mkdir(mode=0o700)
    monkeypatch.setattr(installer,'EXECUTOR',executor)
    monkeypatch.setattr(supervisor, '_root_private_directory', lambda _: None)
    monkeypatch.setattr(supervisor, '_read_root_owned_json', lambda path: path.read_bytes())
    return rotation, dict(root=tmp_path, base=bundle(), account='d'*64)


@pytest.mark.parametrize('status', ['NO_ENTRY_SIGNAL', 'STRATEGY_EPISODE_SETTLED'])
def test_next_episode_is_durable_and_preserves_strategy_and_pilot(tmp_path, monkeypatch, status):
    rotation, args = setup(tmp_path, monkeypatch)
    current = rotation.load(**args)
    result = outcome(current, status)
    original = deepcopy(result)
    next_bundle = rotation.advance(**args, current=current, result=result)
    assert rotation.load(**args) == next_bundle
    assert next_bundle['episode_id'] != current['episode_id']
    assert {k:v for k,v in next_bundle.items() if k != 'episode_id'} == {
        k:v for k,v in current.items() if k != 'episode_id'}
    assert result == original
    assert rotation.advance(**args, current=current, result=result) == next_bundle
    files = list((tmp_path/'strategy-bar-rotation'/_digest(args['base'])).glob('*.json'))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved['result']['decision']['pilot_control'] == original['decision']['pilot_control']


@pytest.mark.parametrize('damage', ['holding', 'stopped', 'not_flat', 'undelivered'])
def test_rotation_waits_for_flat_active_and_published_feedback(tmp_path, monkeypatch, damage):
    rotation, args = setup(tmp_path, monkeypatch)
    current = rotation.load(**args)
    result = outcome(current, 'STRATEGY_EPISODE_SETTLED')
    if damage == 'holding':
        result['decision']['status'] = 'HOLDING'
    elif damage == 'stopped':
        result['decision']['pilot_control']['status'] = 'STOPPED'
    elif damage == 'not_flat':
        result['decision']['position_flat'] = False
    else:
        result.pop('research_feedback')
    result['decision_sha256'] = _digest(result['decision'])
    assert rotation.advance(**args, current=current, result=result) == current
    assert rotation.load(**args) == current


@pytest.mark.parametrize('damage', ['bytes', 'missing_first', 'account', 'branch'])
def test_rotation_rejects_changed_or_incomplete_history(tmp_path, monkeypatch, damage):
    rotation, args = setup(tmp_path, monkeypatch)
    current = rotation.load(**args)
    current = rotation.advance(**args, current=current, result=outcome(current))
    rotation.advance(**args, current=current, result=outcome(current))
    files = list((tmp_path/'strategy-bar-rotation'/_digest(args['base'])).glob('*.json'))
    files.sort(key=lambda p: json.loads(p.read_text())['ordinal'])
    if damage == 'bytes':
        files[0].write_text('{}')
    elif damage == 'missing_first':
        files[0].unlink()
    elif damage == 'account':
        args['account'] = 'e'*64
    else:
        data = json.loads(files[0].read_text())
        data['result']['decision']['pilot_control']['extra'] = 'branch'
        (files[0].parent/(_digest(data)+'.json')).write_text(json.dumps(data))
    with pytest.raises(ValueError):
        rotation.load(**args)


def test_rotation_budget_preserves_completed_history(tmp_path, monkeypatch):
    rotation, args = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(rotation, 'MAX_ROTATIONS', 1)
    current = rotation.load(**args)
    following = rotation.advance(**args, current=current, result=outcome(current))
    with pytest.raises(ValueError, match='STORAGE_BUDGET'):
        rotation.advance(**args, current=following, result=outcome(following))
    assert rotation.load(**args) == following


def test_read_only_rotation_lookup_never_creates_state(tmp_path, monkeypatch):
    rotation, args = setup(tmp_path, monkeypatch)
    assert rotation.load(**args, read_only=True) == args['base']
    assert {p.name for p in tmp_path.iterdir()} == {'executor'}
    active = rotation.advance(**args, current=args['base'], result=outcome(args['base']))
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*.json')}
    assert rotation.load(**args, read_only=True) == active
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*.json')} == before
