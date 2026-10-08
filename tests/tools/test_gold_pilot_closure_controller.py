"""Controller routing into terminal observation, with explicit worker isolation."""
import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_feedback_retention_readers import (
    roots as roots, enrolled as enrolled, frame, bundle, receipt, SOURCE,
    packet, store, NOW,
)
from tests.tools.test_gold_feedback_recovery import owner_for


def no_entry_frame(value, runtime, at):
    result = packet(value, at)
    result['bundle_sha256'] = _digest(runtime)
    result['decision'].update(bundle_sha256=_digest(runtime), status='NO_ENTRY_SIGNAL',
        position_flat=True, action=None, draft=None, execution_constraints=None)
    result['decision_sha256'] = _digest(result['decision'])
    with store(value) as db:
        claim = db.claim(bundle=runtime, now_ms=at)
        db.put(claim, 'decision', result['decision'])
        db.put(claim, 'result', result)
    return result


@pytest.mark.parametrize('rotated', [0, 1, 4])
def test_expired_delivered_feedback_uses_closure_before_any_strategy_worker(roots, monkeypatch, rotated):
    from tools import gold_strategy_controller as controller, gold_bar_rotation
    from tools.gold_pilot_retention_store import canonical
    value, root = roots
    claim, original = frame(value, settled=True)
    monkeypatch.setattr(controller.supervisor, 'STATE', root)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: value.config['ends_at_ms'])
    path = root/'strategy-runtime.json'
    path.write_bytes(canonical(bundle())); path.chmod(0o600)
    monkeypatch.setattr(controller, 'deliver_feedback', lambda **kw: pytest.fail('republished delivered feedback'))
    monkeypatch.setattr(controller.subprocess, 'Popen', lambda *a, **kw: pytest.fail('ordinary worker started'))
    calls = []
    def terminal(**kwargs):
        # The dedicated worker and its returned proof are isolated here. Native
        # cash/closure tests cover the actual executor, stop and custody checks.
        assert kwargs['owner'].retention.store.lock is None
        assert kwargs['owner'].retention._feedback_source.lock is None
        calls.append(kwargs)
        return dict(original, research_feedback=receipt(original), pilot_closure={'isolated_terminal': True})
    monkeypatch.setattr(controller, 'finish_recovered_pilot', terminal, raising=False)
    with owner_for(roots) as owner:
        delivered = owner.recover(current_runtime=bundle(), deliver=lambda **kw: receipt(original))
        current = (gold_bar_rotation.advance(root=root, base=bundle(), account=value.account,
            current=bundle(), result=delivered) if rotated else bundle())
        for index in range(1, rotated):
            row = no_entry_frame(value, current, NOW+index*1000)
            owner.retention.retain(row); owner.release()
            current = gold_bar_rotation.advance(root=root, base=bundle(), account=value.account,
                current=current, result=row)
        before = gold_bar_rotation.feedback_scope(root=root, base=bundle(), account=value.account, current=current)
        found = controller.run(source_commit=SOURCE, feedback_owner=owner)
        assert len(calls) == 1, found
        assert calls[0]['current_runtime'] == current
        assert calls[0]['result']['decision'] == original['decision']
        assert found['pilot_closure'] == {'isolated_terminal': True}
        assert gold_bar_rotation.feedback_scope(root=root, base=bundle(), account=value.account, current=current) == before
        view = owner.discover(current_runtime=current, terminal=True)
        assert view['result_sha256'] == _digest(original) and view['claim'] == claim
        assert view['executor_checkpoint']['frames'] == view['controller_checkpoint']['frames'] == max(1, rotated)


def test_tick_and_supervisor_require_validated_closure_to_finish_original_active_result(roots, monkeypatch):
    from tools import gold_strategy_tick as tick, gold_pilot_run as runner, gold_feedback_recovery as recovery
    value, _ = roots
    _, original = frame(value, settled=True)
    observation = dict(original, research_feedback=receipt(original), pilot_closure={'isolated_terminal': True})
    checked = []
    def validate(row):
        assert row is observation
        checked.append(row)
        return dict(status='PILOT_STOPPED', position_flat=True, pilot_control=dict(status='STOPPED'))
    monkeypatch.setattr(recovery, 'validate_pilot_closure', validate)
    current = tick.handle_observation(observation, source_commit=SOURCE, execute_approved=False, require_pilot=True)
    assert current['status'] == 'PILOT_CLOSED_WITH_FEEDBACK'
    monkeypatch.setattr(runner.tick, 'run_loop', lambda **kw: dict(status=current['status'], ticks=[current]))
    result = runner.run(source_commit=SOURCE, max_seconds=60)
    assert result['pilot_complete'] is True and result['status'] == 'PILOT_STOPPED'
    assert checked == [observation, observation]
    assert original['decision']['pilot_control']['status'] == 'ACTIVE'


def test_supervisor_rejects_claimed_closure_without_original_proof(monkeypatch):
    from tools import gold_pilot_run as runner
    from tests.tools.test_gold_pilot_run import observed
    row = observed('PILOT_CLOSED_WITH_FEEDBACK')
    monkeypatch.setattr(runner.tick, 'run_loop', lambda **kw: row)
    result = runner.run(source_commit=SOURCE, max_seconds=60)
    assert result['pilot_complete'] is False
    assert result['status'] == 'PILOT_CLOSURE_REQUIRED'


def test_active_historical_feedback_is_evidence_and_continues_observation(roots, monkeypatch):
    from tools import gold_strategy_controller as controller, gold_bar_rotation
    from tools.gold_pilot_retention_store import canonical
    value, root = roots
    _, original = frame(value, settled=True)
    monkeypatch.setattr(controller.supervisor, 'STATE', root)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: NOW+3000)
    path = root/'strategy-runtime.json'
    path.write_bytes(canonical(bundle())); path.chmod(0o600)
    calls = []
    def observe(**kwargs):
        calls.append(kwargs)
        return {'isolated_ordinary_observation': True}
    monkeypatch.setattr(controller, '_run_observation', observe)
    monkeypatch.setattr(controller, 'finish_recovered_pilot', lambda **kw: pytest.fail('closed active pilot'))
    monkeypatch.setattr(controller, 'deliver_feedback', lambda **kw: pytest.fail('republished historical feedback'))
    with owner_for(roots) as owner:
        delivered = owner.recover(current_runtime=bundle(), deliver=lambda **kw: receipt(original))
        current = gold_bar_rotation.advance(root=root, base=bundle(), account=value.account,
            current=bundle(), result=delivered)
        row = no_entry_frame(value, current, NOW+1000)
        owner.retention.retain(row); owner.release()
        current = gold_bar_rotation.advance(root=root, base=bundle(), account=value.account,
            current=current, result=row)
        assert owner.recover(current_runtime=current, deliver=controller.deliver_feedback) is None
        assert controller.run(source_commit=SOURCE, feedback_owner=owner) == {'isolated_ordinary_observation': True}
        assert len(calls) == 1 and calls[0]['feedback_owner'] is owner
        view = owner.discover(current_runtime=current, terminal=True)
        assert view['result'] == original and view['delivery'] == receipt(original)
        assert view['executor_checkpoint']['frames'] == 2
