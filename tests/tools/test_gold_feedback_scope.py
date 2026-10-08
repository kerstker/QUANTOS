"""Original rotation views distinguish pending feedback from terminal history."""
from copy import deepcopy

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_bar_rotation import setup, outcome


def image(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def test_empty_rotation_feedback_scope_is_read_only(tmp_path, monkeypatch):
    rotation, args = setup(tmp_path, monkeypatch)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*'))
    value = rotation.feedback_scope(**args, current=args['base'])
    assert value == dict(current=args['base'], previous=None, rotation_result=None, rotation_sha256=None)
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*')) == before


def test_feedback_scope_returns_exact_previous_episode_and_original_result(tmp_path, monkeypatch):
    rotation, args = setup(tmp_path, monkeypatch)
    first = rotation.advance(**args, current=args['base'], result=outcome(args['base']))
    result = outcome(first, 'STRATEGY_EPISODE_SETTLED')
    current = rotation.advance(**args, current=first, result=result)
    before = image(tmp_path)
    value = rotation.feedback_scope(**args, current=current)
    assert value['current'] == current and value['previous'] == first
    assert value['rotation_result'] == result
    assert value['rotation_sha256'] in {p.stem for p in tmp_path.rglob('*.json')}
    assert value['rotation_sha256'] != _digest(result)
    assert image(tmp_path) == before
    wrong = deepcopy(current)
    wrong['episode_id'] = 'f' * 64
    with pytest.raises(ValueError, match='BAR_FEEDBACK_SCOPE_CURRENT'):
        rotation.feedback_scope(**args, current=wrong)
    assert image(tmp_path) == before


def test_feedback_scope_does_not_skip_corrupt_older_rotation(tmp_path, monkeypatch):
    rotation, args = setup(tmp_path, monkeypatch)
    current = rotation.advance(**args, current=args['base'], result=outcome(args['base']))
    first, = tmp_path.rglob('*.json')
    current = rotation.advance(**args, current=current, result=outcome(current))
    first.write_bytes(b'{}')
    before = image(tmp_path)
    with pytest.raises(ValueError):
        rotation.feedback_scope(**args, current=current)
    assert image(tmp_path) == before


def test_feedback_history_proves_all_original_episode_identities_read_only(tmp_path, monkeypatch):
    rotation, args = setup(tmp_path, monkeypatch)
    assert rotation.feedback_history(**args, current=args['base']) == (_digest(args['base']),)
    first = rotation.advance(**args, current=args['base'], result=outcome(args['base']))
    current = rotation.advance(**args, current=first, result=outcome(first))
    before = image(tmp_path)
    assert rotation.feedback_history(**args, current=current) == tuple(map(_digest, (args['base'], first, current)))
    with pytest.raises(ValueError, match='BAR_FEEDBACK_SCOPE_CURRENT'):
        rotation.feedback_history(**args, current=first)
    assert image(tmp_path) == before


def test_terminal_feedback_keeps_latest_delivered_settled_across_no_signal_rotations(tmp_path, monkeypatch):
    rotation, args = setup(tmp_path, monkeypatch)
    assert rotation.feedback_terminal(**args, current=args['base']) is None
    first = outcome(args['base'], 'STRATEGY_EPISODE_SETTLED')
    current = rotation.advance(**args, current=args['base'], result=first)
    for _ in range(3):
        current = rotation.advance(**args, current=current, result=outcome(current))
    before = image(tmp_path)
    view = rotation.feedback_terminal(**args, current=current)
    assert view['result'] == first and set(view) == {'result', 'rotation_sha256'}
    assert view['rotation_sha256'] in {p.stem for p in tmp_path.rglob('*.json')}
    assert image(tmp_path) == before
    second = outcome(current, 'STRATEGY_EPISODE_SETTLED')
    current = rotation.advance(**args, current=current, result=second)
    assert rotation.feedback_terminal(**args, current=current)['result'] == second
    with pytest.raises(ValueError, match='BAR_FEEDBACK_SCOPE_CURRENT'):
        rotation.feedback_terminal(**args, current=args['base'])
    path = next(p for p in tmp_path.rglob('*.json') if p.stem == view['rotation_sha256'])
    path.write_bytes(b'{}')
    with pytest.raises(ValueError):
        rotation.feedback_terminal(**args, current=current)
