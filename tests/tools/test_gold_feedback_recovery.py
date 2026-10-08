"""Original enrollment, two native stores and claim-bound delivery recovery.

These tests isolate the publisher transport and worker boundary. They do not
stand in for a complete bar/cash episode or a production role transition.
"""
from copy import deepcopy
import os

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_feedback_retention_readers import (
    roots as roots, enrolled as enrolled, frame, bundle, receipt, NOW, SOURCE,
)


def owner_for(roots):
    from tools.gold_feedback_recovery import FeedbackOwner
    value, root = roots
    owner = FeedbackOwner()
    owner.bind(root=root, base_runtime=bundle(), source_commit=SOURCE,
        account=value.account, registry={'executor_uid': os.getuid()})
    return owner


def test_pending_retries_original_result_and_same_claim_without_new_observation(roots):
    from tools.gold_feedback_delivery import FeedbackDeliveryPending
    from tools.demo_feedback_publish import pending_receipt
    value, _ = roots
    claim, result = frame(value, settled=True)
    calls = []
    def deliver(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise FeedbackDeliveryPending(pending_receipt(source_commit=SOURCE,
                cycle=result['bundle_sha256'], artifact_sha256=result['feedback']['artifact_sha256'],
                feedback_sha256=result['feedback']['feedback_sha256']))
        return receipt(result)
    with owner_for(roots) as owner:
        first = owner.recover(current_runtime=bundle(), deliver=deliver)
        assert first['decision'] == result['decision']
        assert first['feedback_delivery_pending']['status'] == 'RESEARCH_FEEDBACK_DELIVERY_PENDING'
        assert 'research_feedback' not in first
        assert owner.retention.store.lock is None and owner.retention._feedback_source.lock is None
        second = owner.recover(current_runtime=bundle(), deliver=deliver)
        assert second == dict(result, research_feedback=receipt(result))
        view = owner.discover(current_runtime=bundle())
        assert view['claim'] == claim and view['result'] == result
        assert view['controller_checkpoint']['frames'] == view['executor_checkpoint']['frames'] == 1
        assert view['delivery'] == receipt(result)
    assert calls == [dict(source_commit=SOURCE, cycle=_digest(bundle()), exported=result['feedback'])] * 2
    with owner_for(roots) as restarted:
        assert restarted.recover(current_runtime=bundle(),
            deliver=lambda **kw: pytest.fail('already delivered result republished')) == second


def test_publisher_runs_without_native_locks_and_malformed_receipt_is_not_persisted(roots):
    value, _ = roots
    _, result = frame(value, settled=True)
    with owner_for(roots) as owner:
        def deliver(**kwargs):
            assert owner.retention.store.lock is None
            assert owner.retention._feedback_source.lock is None
            return dict(receipt(result), feedback_sha256='0' * 64)
        with pytest.raises(ValueError, match='DELIVERY_BINDING'):
            owner.recover(current_runtime=bundle(), deliver=deliver)
        assert owner.discover(current_runtime=bundle())['delivery'] is None


def test_owner_rejects_account_runtime_registry_or_root_rebinding(roots):
    value, root = roots
    frame(value, settled=True)
    with owner_for(roots) as owner:
        for change in (dict(account='0'*64), dict(base_runtime=dict(bundle(), seconds=31)),
                       dict(registry={'executor_uid': os.getuid(), 'changed': True}), dict(root=root/'other')):
            args = dict(root=root, base_runtime=bundle(), source_commit=SOURCE,
                account=value.account, registry={'executor_uid': os.getuid()})
            args.update(change)
            with pytest.raises(ValueError, match='OWNER_BINDING'):
                owner.bind(**args)


def test_generic_publication_error_is_not_reclassified_as_retryable(roots):
    value, _ = roots
    frame(value, settled=True)
    def deliver(**kwargs):
        raise ValueError('ARTIFACT_CORRUPTED')
    with owner_for(roots) as owner:
        with pytest.raises(ValueError, match='ARTIFACT_CORRUPTED'):
            owner.recover(current_runtime=bundle(), deliver=deliver)
        assert owner.retention.store.lock is None


def test_current_control_reads_original_stop_and_never_creates_one_at_expiry(roots):
    from tools.gold_feedback_recovery import current_control
    from tools.gold_pilot_retention_store import canonical
    value, _ = roots
    _, result = frame(value, settled=True)
    args = dict(executor_root=value.root, base_runtime=bundle(), source_commit=SOURCE,
        account=value.account, executor_uid=os.getuid(), result=result)
    assert current_control(**args, now_ms=NOW+1)['status'] == 'ACTIVE'
    path = value.root/'gold-pilot'/'stopped.json'
    assert current_control(**args, now_ms=value.config['ends_at_ms'])['status'] == 'EXPIRED'
    assert not path.exists()
    stop = dict(schema='GOLD_DEMO_PILOT_STOP_V1', status='STOPPED', reason='LOSS_LIMIT',
        configuration_sha256=_digest(value.config), account_ref_sha256=value.account,
        observed_at_ms=NOW+1, observed_equity='0', origin_sha256='9'*64, submission_enabled=False)
    path.write_bytes(canonical(stop)); path.chmod(0o600)
    assert current_control(**args, now_ms=NOW+2) == stop
    changed = deepcopy(stop); changed['observed_equity'] = value.config['initial_equity']
    path.write_bytes(canonical(changed))
    with pytest.raises(ValueError, match='STOP_RECORD'):
        current_control(**args, now_ms=NOW+2)


def test_controller_recovers_before_launch_and_rotates_delivered_episode_once(roots, monkeypatch):
    from tools import gold_strategy_controller as controller, gold_bar_rotation
    from tools.gold_pilot_retention_store import canonical
    value, root = roots
    _, original = frame(value, settled=True)
    monkeypatch.setattr(controller.supervisor, 'STATE', root)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: NOW+1)
    (root/'strategy-runtime.json').write_bytes(canonical(bundle()))
    (root/'strategy-runtime.json').chmod(0o600)
    monkeypatch.setattr(controller.subprocess, 'Popen', lambda *a, **kw: pytest.fail('recovery launched worker'))
    calls = []
    monkeypatch.setattr(controller, 'deliver_feedback', lambda **kw: calls.append(kw) or receipt(original))
    callbacks = []
    with owner_for(roots) as owner:
        def callback(row):
            assert owner.retention.store.lock is None and owner.retention._feedback_source.lock is None
            with controller.supervisor.controller_lease(maintenance=True):
                callbacks.append(row)
            return False
        found = controller.run(source_commit=SOURCE, feedback_owner=owner,
            on_decision=callback)
        assert found == dict(original, research_feedback=receipt(original))
        assert callbacks == [found] and len(calls) == 1
        scope = gold_bar_rotation.feedback_scope(root=root, base=bundle(), account=value.account,
            current=gold_bar_rotation.load(root=root, base=bundle(), account=value.account))
        assert scope['rotation_result'] == found
        assert owner.recover(current_runtime=scope['current'],
            deliver=lambda **kw: pytest.fail('previous result republished')) is None


def test_expired_recovery_delivers_but_requires_original_closure_without_rotation(roots, monkeypatch):
    from tools import gold_strategy_controller as controller, gold_strategy_tick as tick, gold_bar_rotation
    from tools.gold_pilot_retention_store import canonical
    value, root = roots
    _, original = frame(value, settled=True)
    monkeypatch.setattr(controller.supervisor, 'STATE', root)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: value.config['ends_at_ms'])
    (root/'strategy-runtime.json').write_bytes(canonical(bundle()))
    (root/'strategy-runtime.json').chmod(0o600)
    monkeypatch.setattr(controller, 'finish_recovered_pilot',
        lambda **kw: (_ for _ in ()).throw(ValueError('ISOLATED_CLOSURE_UNAVAILABLE')))
    monkeypatch.setattr(controller, 'deliver_feedback', lambda **kw: receipt(original))
    with owner_for(roots) as owner:
        found = controller.run(source_commit=SOURCE, feedback_owner=owner)
        assert found['research_feedback'] == receipt(original)
        assert found['decision'] == original['decision']
        assert found['feedback_recovery_control']['current_control']['status'] == 'EXPIRED'
        assert tick.handle_observation(found, source_commit=SOURCE, execute_approved=False,
            require_pilot=True)['status'] == 'PILOT_CLOSURE_REQUIRED'
        assert gold_bar_rotation.load(root=root, base=bundle(), account=value.account) == bundle()
        assert not (value.root/'gold-pilot'/'stopped.json').exists()


def test_pilot_owns_one_lazy_feedback_session_across_ticks_and_closes_on_failure(monkeypatch):
    from tools import gold_pilot_run as runner
    from tests.tools.test_gold_pilot_run import observed
    owners = []
    def tick(**kwargs):
        owners.append(kwargs['feedback_owner'])
        if len(owners) == 2:
            raise ValueError('ORIGINAL_INPUT_CORRUPTED')
        return observed()
    monkeypatch.setattr(runner.tick, 'run_loop', tick)
    monkeypatch.setattr(runner.time, 'sleep', lambda _: None)
    with pytest.raises(ValueError, match='ORIGINAL_INPUT_CORRUPTED'):
        runner.run(source_commit=SOURCE, max_seconds=60)
    assert owners[0] is owners[1] and owners[0].closed
    assert owners[0].retention is None  # Lazy owner performs no I/O in this isolated loop.


@pytest.mark.parametrize('crash', ['attachment', 'rotation'])
def test_persisted_delivery_or_rotation_survives_lost_response_without_new_claim(roots, monkeypatch, crash):
    from tools import gold_strategy_controller as controller, gold_bar_rotation
    from tools.gold_pilot_retention_store import canonical
    value, root = roots
    claim, original = frame(value, settled=True)
    monkeypatch.setattr(controller.supervisor, 'STATE', root)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: NOW+1)
    (root/'strategy-runtime.json').write_bytes(canonical(bundle()))
    (root/'strategy-runtime.json').chmod(0o600)
    monkeypatch.setattr(controller.subprocess, 'Popen', lambda *a, **kw: pytest.fail('recovery launched worker'))
    publications = []
    monkeypatch.setattr(controller, 'deliver_feedback', lambda **kw: publications.append(kw) or receipt(original))
    with owner_for(roots) as owner:
        if crash == 'attachment':
            attach = owner.retention.attach
            def lost(*args):
                attach(*args)
                raise RuntimeError('LOST_DELIVERY_ACK')
            monkeypatch.setattr(owner.retention, 'attach', lost)
        else:
            advance = gold_bar_rotation.advance
            def lost(**kwargs):
                advance(**kwargs)
                raise RuntimeError('LOST_ROTATION_ACK')
            monkeypatch.setattr(gold_bar_rotation, 'advance', lost)
        failed = controller.run(source_commit=SOURCE, feedback_owner=owner)
        assert failed['status'] == 'INPUTS_UNRESOLVED'
    if crash == 'rotation':
        monkeypatch.setattr(gold_bar_rotation, 'advance', advance)
    with owner_for(roots) as restarted:
        current = gold_bar_rotation.load(root=root, base=bundle(), account=value.account)
        found = restarted.discover(current_runtime=current)
        assert found['claim'] == claim and found['delivery'] == receipt(original)
        assert found['executor_checkpoint']['frames'] == found['controller_checkpoint']['frames'] == 1
        restarted.release()
        if crash == 'attachment':
            assert controller.run(source_commit=SOURCE, feedback_owner=restarted) == dict(original, research_feedback=receipt(original))
        else:
            assert restarted.recover(current_runtime=current, deliver=lambda **kw: pytest.fail('republished')) is None
        active = gold_bar_rotation.load(root=root, base=bundle(), account=value.account)
        scope = gold_bar_rotation.feedback_scope(root=root, base=bundle(), account=value.account, current=active)
        assert scope['previous'] == bundle() and scope['rotation_result'] == dict(original, research_feedback=receipt(original))
    assert len(publications) == 1


def test_callback_crossing_original_deadline_cannot_advance_next_episode(roots, monkeypatch):
    from tools import gold_strategy_controller as controller, gold_bar_rotation
    from tools.gold_pilot_retention_store import canonical
    value, root = roots
    _, original = frame(value, settled=True)
    clock = [NOW+1]
    monkeypatch.setattr(controller.supervisor, 'STATE', root)
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: clock[0])
    (root/'strategy-runtime.json').write_bytes(canonical(bundle()))
    (root/'strategy-runtime.json').chmod(0o600)
    monkeypatch.setattr(controller, 'deliver_feedback', lambda **kw: receipt(original))
    def callback(row):
        clock[0] = value.config['ends_at_ms']
        return False
    with owner_for(roots) as owner:
        result = controller.run(source_commit=SOURCE, feedback_owner=owner, on_decision=callback)
        assert result['research_feedback'] == receipt(original)
        assert gold_bar_rotation.load(root=root, base=bundle(), account=value.account) == bundle()


@pytest.mark.parametrize('during', ['control_read', 'rotation_read', 'allocation'])
def test_rotation_checks_current_clock_after_slow_original_reads(roots, monkeypatch, during):
    from tools import gold_strategy_controller as controller, gold_bar_rotation, gold_bar_pilot_capacity as capacity
    from tests.tools.test_gold_feedback_retention_readers import session
    value, root = roots
    _, result = frame(value, settled=True)
    with session(roots):
        pass
    clock = [value.config['ends_at_ms']-1]
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: clock[0])
    if during == 'control_read':
        read = capacity._read
        def slow(*args, **kwargs):
            value = read(*args, **kwargs)
            if args[0].name == 'configuration.json':
                clock[0] += 2
            return value
        monkeypatch.setattr(capacity, '_read', slow)
    elif during == 'rotation_read':
        read = gold_bar_rotation._load
        def slow(*args, **kwargs):
            value = read(*args, **kwargs)
            clock[0] += 2
            return value
        monkeypatch.setattr(gold_bar_rotation, '_load', slow)
    else:
        allocate = capacity.allocate_rotation
        areas = []
        def slow(*args, **kwargs):
            value = allocate(*args, **kwargs)
            areas.append(args[0])
            clock[0] += 2
            return value
        monkeypatch.setattr(capacity, 'allocate_rotation', slow)
    args = dict(root=root, base=bundle(), account=value.account, current=bundle(),
        result=dict(result, research_feedback=receipt(result)), executor_uid=os.getuid())
    if during == 'allocation':
        with pytest.raises(ValueError, match='BAR_ROTATION_WINDOW_CHANGED'):
            controller.advance_bar(**args)
        assert len(areas) == 1 and list(areas[0].iterdir()) == []
    else:
        assert controller.advance_bar(**args) == bundle()


def test_clock_floor_survives_durable_allocation_between_checks(roots, monkeypatch):
    from tools import gold_strategy_controller as controller
    from tests.tools.test_gold_feedback_retention_readers import session
    value, root = roots
    _, result = frame(value, settled=True)
    with session(roots):
        pass
    times = iter([NOW+100, NOW+101, NOW+50, NOW+51])
    monkeypatch.setattr(controller.supervisor, 'now_ms', lambda: next(times))
    with pytest.raises(ValueError, match='CLOCK_ROLLBACK'):
        controller.advance_bar(root=root, base=bundle(), account=value.account, current=bundle(),
            result=dict(result, research_feedback=receipt(result)), executor_uid=os.getuid())
