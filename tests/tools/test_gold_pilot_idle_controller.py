"""Original window discovery and control routing, with executor isolation."""
from copy import deepcopy
import os

import pytest

from tests.tools.test_gold_feedback_retention_readers import (
    roots as roots, enrolled as enrolled, bundle, packet, SOURCE, NOW,
)
from tests.tools.test_gold_feedback_recovery import owner_for


def arguments(value):
    return dict(executor_root=value.root, source_commit=SOURCE, account=value.account,
        executor_uid=os.getuid())


def test_idle_window_is_original_read_only_and_only_due_at_original_end(enrolled):
    from tools.gold_feedback_recovery import idle_window
    value = enrolled
    before = {p: p.read_bytes() for p in (value.root/'gold-pilot').rglob('*') if p.is_file()}
    assert idle_window(**arguments(value), now_ms=NOW+1) is None
    expected = packet(value, NOW)['original_pilot_window']
    assert idle_window(**arguments(value), now_ms=value.config['ends_at_ms']) == expected
    assert idle_window(**arguments(value), now_ms=value.config['ends_at_ms']+910000) == expected
    assert {p: p.read_bytes() for p in (value.root/'gold-pilot').rglob('*') if p.is_file()} == before
    assert not (value.root/'gold-pilot/stopped.json').exists()


@pytest.mark.parametrize('damage', ['request', 'receipt', 'intent', 'configuration'])
def test_idle_window_rejects_changed_original_records(enrolled, damage):
    from tools.gold_feedback_recovery import idle_window
    from tools.gold_pilot_retention_store import canonical
    value = enrolled
    names = dict(request=value.root/'gold-pilot-request.json', receipt=value.root/'gold-pilot/enrollment.json',
        intent=value.root/'gold-pilot/enrollment-intent.json', configuration=value.root/'gold-pilot/configuration.json')
    names[damage].write_bytes(canonical({}))
    with pytest.raises(ValueError):
        idle_window(**arguments(value), now_ms=value.config['ends_at_ms'])


@pytest.mark.parametrize('answer', ['complete', 'not_applicable', 'invalid'])
def test_expired_without_feedback_probes_idle_before_ordinary_worker(roots, monkeypatch, answer):
    from tools import gold_strategy_controller as controller
    from tools.gold_pilot_retention_store import canonical
    value, root = roots
    monkeypatch.setattr(controller.supervisor, 'STATE', root)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: value.config['ends_at_ms'])
    path = root/'strategy-runtime.json'
    path.write_bytes(canonical(bundle())); path.chmod(0o600)
    calls = []
    original = packet(value, NOW)
    isolated = dict(original, closure_request={'schema': 'GOLD_PILOT_IDLE_CLOSURE_INPUT_V1'}, idle_evidence={})
    def finish(**kwargs):
        calls.append(('idle', kwargs))
        assert kwargs['original_window'] == original['original_pilot_window']
        if answer == 'invalid':
            raise ValueError('ORIGINAL_IDLE_EVIDENCE_INVALID')
        return None if answer == 'not_applicable' else isolated
    def observe(**kwargs):
        calls.append(('ordinary', kwargs))
        return {'isolated_exit_observation': True}
    monkeypatch.setattr(controller, 'finish_idle_pilot', finish, raising=False)
    monkeypatch.setattr(controller, '_run_observation', observe)
    monkeypatch.setattr(controller, 'deliver_feedback', lambda **kw: pytest.fail('idle published feedback'))
    with owner_for(roots) as owner:
        found = controller.run(source_commit=SOURCE, feedback_owner=owner)
        assert calls[0][0] == 'idle', calls
        if answer == 'not_applicable':
            assert found == {'isolated_exit_observation': True}
            assert [v[0] for v in calls] == ['idle', 'ordinary']
        elif answer == 'invalid':
            assert found['status'] == 'INPUTS_UNRESOLVED'
            assert found['phase'] == 'PILOT_IDLE_CLOSURE_OBSERVATION'
            assert len(calls) == 1
        else:
            assert found == isolated and len(calls) == 1


def test_tick_and_supervisor_validate_idle_proof_independently(monkeypatch):
    from tools import gold_strategy_tick as tick, gold_pilot_run as runner, gold_pilot_closure as closure
    from tests.tools.test_gold_pilot_run import observed
    observation = deepcopy(observed('PILOT_STOPPED')['ticks'][0]['observation'])
    observation['closure_request'] = {'schema': 'GOLD_PILOT_IDLE_CLOSURE_INPUT_V1'}
    observation['idle_evidence'] = {'isolated': True}
    observation['decision'].update(execution_history_empty=False,
        closure_mode='NO_EXECUTION_SINCE_ENROLLMENT')
    checked = []
    def validate(result, req):
        assert result is observation and req is observation['closure_request']
        checked.append(result)
        return result['decision']
    monkeypatch.setattr(closure, 'validate_idle_result', validate)
    value = tick.handle_observation(observation, source_commit=SOURCE, execute_approved=False, require_pilot=True)
    assert value['status'] == 'PILOT_CLOSED_WITHOUT_EXECUTION'
    monkeypatch.setattr(runner.tick, 'run_loop', lambda **kw: dict(status=value['status'], ticks=[value]))
    result = runner.run(source_commit=SOURCE, max_seconds=60)
    assert result['pilot_complete'] is True and result['status'] == 'PILOT_STOPPED'
    assert len(checked) == 2


def test_invalid_idle_proof_cannot_use_generic_flat_stop(monkeypatch):
    from tools import gold_strategy_tick as tick
    from tests.tools.test_gold_pilot_run import observed
    observation = observed('PILOT_STOPPED')['ticks'][0]['observation']
    observation['idle_evidence'] = {}
    value = tick.handle_observation(observation, source_commit=SOURCE, execute_approved=False, require_pilot=True)
    assert value['status'] == 'PILOT_CLOSURE_REQUIRED'


@pytest.mark.parametrize('value', [None, [], 'unknown'])
def test_malformed_closure_request_is_not_a_generic_success(value):
    from tools import gold_strategy_tick as tick
    from tests.tools.test_gold_pilot_run import observed
    observation = observed('PILOT_STOPPED', stop=True)['ticks'][0]['observation']
    observation['closure_request'] = value
    result = tick.handle_observation(observation, source_commit=SOURCE, execute_approved=False, require_pilot=True)
    assert result['status'] == 'PILOT_CLOSURE_REQUIRED'
