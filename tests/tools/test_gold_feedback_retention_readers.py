"""Real original enrollment/SQLite/audit/claim readers on isolated custody roots.

Decision payloads below are controlled storage-contract fixtures, not evidence of
an executed account/cash episode. Native producer/publisher integration is separate.
"""
import os
import sqlite3
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_pilot_retention import enrolled as enrolled, bundle, packet, store, NOW, SOURCE
from tests.tools.test_gold_pilot_retention_integration import roots as original_roots
from tools import gold_pilot_retention as bridge


@pytest.fixture
def roots(enrolled, tmp_path, monkeypatch):
    value, root = original_roots.__wrapped__(enrolled, tmp_path, monkeypatch)
    # Map only the root caller/host ancestors for the original rotation reader.
    # Retention leaf UID/mode/metadata/nofollow checks continue unchanged.
    from tools import demo_supervisor as supervisor
    original = supervisor._require_protected
    hosts = {(p.stat().st_dev, p.stat().st_ino) for p in root.parents}
    def protected(info, *, directory):
        mode = info.st_mode
        if (info.st_dev, info.st_ino) in hosts:
            mode &= ~0o022
        owner = 0 if info.st_uid == os.getuid() else info.st_uid
        return original(SimpleNamespace(st_mode=mode, st_uid=owner), directory=directory)
    monkeypatch.setattr(supervisor, '_require_protected', protected)
    return value, root


def frame(value, *, at=NOW, runtime=None, settled=False, exported=True, no_entry=False):
    runtime = bundle() if runtime is None else runtime
    result = packet(value, at)
    result['bundle_sha256'] = _digest(runtime)
    result['decision'].update(bundle_sha256=_digest(runtime), action=None, draft=None,
                              execution_constraints=None)
    if no_entry:
        assert not settled
        result['decision'].update(status='NO_ENTRY_SIGNAL', position_flat=True)
    if settled:
        result['decision'].update(status='STRATEGY_EPISODE_SETTLED', position_flat=True)
        if exported:
            result['feedback'] = dict(status='BAR_STRATEGY_FEEDBACK_RETAINED',
                feedback_sha256='f' * 64, artifact_sha256='a' * 64,
                path='/untrusted/caller/path.json', cash_reconciled=True,
                submission_enabled=False, economic_authority=False)
    result['decision_sha256'] = _digest(result['decision'])
    with store(value) as db:
        claim = db.claim(bundle=runtime, now_ms=at)
        db.put(claim, 'decision', result['decision'])
        db.put(claim, 'result', result)
    return claim, result


def session(roots):
    value, root = roots
    return bridge.ControllerRetention(root, base_runtime=bundle(), source_commit=SOURCE,
        account=value.account, executor_uid=os.getuid())


def receipt(result):
    return dict(status='RESEARCH_FEEDBACK_DELIVERED',
        cycle_sha256=result['bundle_sha256'], feedback_sha256=result['feedback']['feedback_sha256'],
        source_verified=False, submission_enabled=False, economic_authority=False)


def test_discovery_reads_original_completed_result_and_audits_delivery_absence(roots):
    value, _ = roots
    claim, result = frame(value, settled=True)
    with session(roots) as current:
        before = current.store.metadata()
        current.enable_feedback_discovery()
        found = current.discover_feedback(current_runtime=bundle())
        assert found['status'] == 'PENDING_DELIVERY'
        assert found['claim'] == claim and found['result'] == result
        assert found['result_bytes'] == bridge.canonical(result)
        assert found['delivery_state'] == 'ABSENCE_VERIFIED'
        assert found['delivery'] is None and found['relation'] == 'CURRENT'
        assert current.store.metadata() == before


def test_discovery_does_not_mistake_a_later_nonsettled_frame_for_no_completion(roots):
    value, _ = roots
    claim, result = frame(value, settled=True)
    frame(value, at=NOW+1000)
    with session(roots) as current:
        current.enable_feedback_discovery()
        found = current.discover_feedback(current_runtime=bundle())
        assert found['result_sha256'] == _digest(result) and found['claim'] == claim


def test_known_result_exact_reader_never_claims_fast_absence_is_a_full_audit(roots):
    value, root = roots
    claim, result = frame(value, settled=True)
    with session(roots):
        pass
    found = bridge.read_feedback_exact(root, _digest(result), base_runtime=bundle(),
        source_commit=SOURCE, account=value.account, executor_uid=os.getuid(), expected_claim=claim)
    assert found['mode'] == 'EXACT' and found['result'] == result
    assert found['delivery_state'] == 'ABSENCE_UNVERIFIED'


def test_complete_nonsettled_and_real_empty_native_history_prove_none(roots):
    value, _ = roots
    with store(value):
        pass
    with session(roots) as current:
        current.enable_feedback_discovery()
        assert current.discover_feedback(current_runtime=bundle()) is None
        current.release()
        _, result = frame(value)
        current.retain(result)
        assert current.discover_feedback(current_runtime=bundle()) is None


def test_original_pre_retention_bootstrap_is_typed_and_does_not_allocate(roots):
    value, root = roots
    with session(roots) as current:
        current.enable_feedback_discovery()
        assert current.discover_feedback(current_runtime=bundle()) == dict(
            status='FEEDBACK_NOT_INITIALIZED', reason='ORIGINAL_RETENTION_NOT_STARTED', submission_enabled=False)
        assert not bridge.active(root) and not bridge.active(value.root)


def test_same_frame_result_and_delivery_append_refresh_without_new_claim(roots):
    value, _ = roots
    result = packet(value, NOW)
    result['decision'].update(status='STRATEGY_EPISODE_SETTLED', position_flat=True)
    result['decision_sha256'] = _digest(result['decision'])
    result['feedback'] = dict(status='BAR_STRATEGY_FEEDBACK_RETAINED', feedback_sha256='f'*64,
        artifact_sha256='a'*64, cash_reconciled=True, submission_enabled=False, economic_authority=False)
    with store(value) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        db.put(claim, 'decision', result['decision'])
    with session(roots) as current:
        current.enable_feedback_discovery()
        with pytest.raises(ValueError, match='INCOMPLETE_FRAME'):
            current.discover_feedback(current_runtime=bundle())
        current.release()
        with store(value) as db:
            db.put(claim, 'result', result)
        current.retain(result)
        found = current.discover_feedback(current_runtime=bundle())
        assert found['status'] == 'PENDING_DELIVERY' and found['claim'] == claim
        current.attach(result, 'delivery', receipt(result))
        found = current.discover_feedback(current_runtime=bundle())
        assert found['status'] == 'DELIVERED' and found['delivery'] == receipt(result)
        assert found['controller_checkpoint']['frames'] == found['executor_checkpoint']['frames'] == 1


def test_classification_reuses_verified_decisions_and_only_reads_candidate_large_result(roots, monkeypatch):
    value, _ = roots
    for index in range(3):
        frame(value, at=NOW+index*1000)
    _, result = frame(value, at=NOW+3000, settled=True)
    from tools.gold_pilot_retention_store import Retention
    with session(roots) as current:
        reads = []
        original = Retention._selected
        def selected(self, kind, identity, **kwargs):
            reads.append((self.role, kind, identity))
            return original(self, kind, identity, **kwargs)
        monkeypatch.setattr(Retention, '_selected', selected)
        current.enable_feedback_discovery()
        assert current.discover_feedback(current_runtime=bundle())['result'] == result
        assert len([r for r in reads if r[1] == 'result']) == 2
        assert len([r for r in reads if r[1] == 'decision']) == 5  # Classification plus exact candidate check.
        reads.clear()
        current.release()
        assert current.discover_feedback(current_runtime=bundle())['result'] == result
        assert len([r for r in reads if r[1] == 'result']) == 2
        assert len([r for r in reads if r[1] == 'decision']) == 1


def test_ambiguous_completions_never_select_latest_or_return_none(roots):
    value, _ = roots
    frame(value, settled=True)
    frame(value, at=NOW+1000, settled=True)
    with session(roots) as current:
        current.enable_feedback_discovery()
        with pytest.raises(ValueError, match='AMBIGUOUS_COMPLETION'):
            current.discover_feedback(current_runtime=bundle())


def test_same_result_digest_in_multiple_claims_requires_exact_claim(roots):
    value, root = roots
    first, result = frame(value, settled=True)
    with store(value) as db:
        second = db.claim(bundle=bundle(), now_ms=NOW+1000)
        db.put(second, 'decision', result['decision'])
        db.put(second, 'result', result)
    with session(roots):
        pass
    kwargs = dict(base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid())
    with pytest.raises(ValueError, match='AMBIGUOUS'):
        bridge.read_feedback_exact(root, _digest(result), **kwargs)
    assert bridge.read_feedback_exact(root, _digest(result), expected_claim=first, **kwargs)['claim'] == first
    assert bridge.read_feedback_exact(root, _digest(result), expected_claim=second, **kwargs)['claim'] == second


def damage_database(path, *, kind, action):
    with sqlite3.connect(path) as conn:
        name = 'records_no_delete' if action == 'delete' else 'records_no_update'
        sql = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
        conn.execute('DROP TRIGGER '+name)
        if action == 'delete':
            conn.execute('DELETE FROM records WHERE kind=?', (kind,))
        else:
            conn.execute('UPDATE records SET payload=? WHERE kind=?', (b'{}', kind))
        conn.execute(sql)


@pytest.mark.parametrize('damage', ['event', 'result_row', 'decision_bytes', 'executor_database', 'executor_marker'])
def test_discovery_cannot_infer_absence_from_missing_or_corrupted_originals(roots, damage):
    value, _ = roots
    frame(value)
    with session(roots) as current:
        if damage == 'event':
            next((value.root / bridge.AUDIT / 'events').iterdir()).unlink()
        elif damage == 'executor_database':
            (value.root / bridge.AREA / 'records.sqlite3').unlink()
        elif damage == 'executor_marker':
            (value.root / bridge.MARKER).unlink()
        else:
            damage_database(value.root / bridge.AREA / 'records.sqlite3',
                kind='result' if damage == 'result_row' else 'decision',
                action='delete' if damage == 'result_row' else 'update')
        with pytest.raises((ValueError, OSError)):
            current.enable_feedback_discovery()
            current.discover_feedback(current_runtime=bundle())


@pytest.mark.parametrize('field,value', [('cycle_sha256', '0'*64), ('feedback_sha256', '0'*64),
                                       ('submission_enabled', True), ('source_verified', True)])
def test_wrong_same_frame_delivery_is_rejected(roots, field, value):
    original, _ = roots
    _, result = frame(original, settled=True)
    with session(roots) as current:
        bad = receipt(result)
        bad[field] = value
        current.attach(result, 'delivery', bad)
        current.enable_feedback_discovery()
        with pytest.raises(ValueError, match='DELIVERY_BINDING'):
            current.discover_feedback(current_runtime=bundle())


def test_settled_without_export_is_explicitly_not_ready(roots):
    value, _ = roots
    _, result = frame(value, settled=True, exported=False)
    with session(roots) as current:
        current.enable_feedback_discovery()
        found = current.discover_feedback(current_runtime=bundle())
        assert found['status'] == 'FEEDBACK_NOT_RETAINED' and found['result'] == result


def test_previous_completed_frame_is_exactly_the_original_rotation(roots):
    from tools import gold_bar_rotation as rotation
    value, root = roots
    _, result = frame(value, settled=True)
    delivered = receipt(result)
    with session(roots) as current:
        current.attach(result, 'delivery', delivered)
        current.enable_feedback_discovery()
        next_runtime = rotation.advance(root=root, base=bundle(), account=value.account,
            current=bundle(), result=dict(result, research_feedback=delivered))
        found = current.discover_feedback(current_runtime=next_runtime)
        assert found['relation'] == 'PREVIOUS' and found['status'] == 'DELIVERED'
        assert found['rotation_sha256'] is not None
        current.release()
        _, later = frame(value, at=NOW+1000, runtime=next_runtime)
        current.retain(later)
        assert current.discover_feedback(current_runtime=next_runtime)['result'] == result
        with pytest.raises(ValueError, match='SCOPE_CURRENT'):
            current.discover_feedback(current_runtime=bundle())


def test_discovery_releases_executor_reader_before_worker_and_refreshes_afterward(roots):
    value, _ = roots
    _, result = frame(value, settled=True)
    with session(roots) as current:
        current.enable_feedback_discovery()
        with pytest.raises(ValueError, match='RETENTION_BUSY'):
            with store(value):
                pass
        current.release()
        with store(value) as db:
            assert db.metadata()['frames'] == 1
        assert current.discover_feedback(current_runtime=bundle())['result'] == result


@pytest.mark.parametrize('field', ['source', 'root', 'account', 'executor_uid', 'base', 'executor_root'])
def test_discovery_owner_cannot_be_rebound_between_ticks(roots, monkeypatch, field):
    value, root = roots
    frame(value)
    with session(roots) as current:
        current.enable_feedback_discovery()
        assert current.discover_feedback(current_runtime=bundle()) is None
        current.release()
        if field == 'executor_root':
            monkeypatch.setattr(bridge.installer, 'EXECUTOR', root)
        elif field == 'base':
            current.base['episode_id'] = 'd' * 64
        else:
            setattr(current, field, dict(source='d'*40, root=value.root,
                                        account='d'*64, executor_uid=os.getuid()+1)[field])
        with pytest.raises(ValueError, match='FEEDBACK_OWNER_CHANGED'):
            current.discover_feedback(current_runtime=bundle())


def test_controller_witness_rejects_coherent_executor_rollback_before_returning_none(roots, tmp_path):
    import shutil
    value, _ = roots
    frame(value)
    backup = tmp_path / 'prior-executor-retention'
    backup.mkdir()
    names = (bridge.AREA, bridge.AUDIT, bridge.MARKER)
    for name in names:
        source = value.root / name
        if source.is_dir():
            shutil.copytree(source, backup / name)
        else:
            shutil.copy2(source, backup / name)
    frame(value, at=NOW+1000, settled=True)
    with session(roots):
        pass
    for name in names:
        target = value.root / name
        if target.is_dir():
            shutil.rmtree(target)
            shutil.copytree(backup / name, target)
        else:
            shutil.copy2(backup / name, target)
    with session(roots) as current:
        current.enable_feedback_discovery()
        with pytest.raises(ValueError, match='FEEDBACK_MIRROR_FRAME_COUNT'):
            current.discover_feedback(current_runtime=bundle())


def test_original_request_pending_enrollment_is_typed_without_new_history(roots, tmp_path, monkeypatch):
    value, root = roots
    pending = tmp_path / 'pending-executor'
    pending.mkdir(mode=0o700)
    from live.execution import demo_position_context as position
    monkeypatch.setattr(position, '_ROOT', pending)
    value.pilot.retain_once(pending / 'gold-pilot-request.json', value.request)
    monkeypatch.setattr(bridge.installer, 'EXECUTOR', pending)
    with session(roots) as current:
        current.enable_feedback_discovery()
        assert current.discover_feedback(current_runtime=bundle()) == dict(
            status='FEEDBACK_NOT_INITIALIZED', reason='PILOT_ENROLLMENT_PENDING', submission_enabled=False)
        assert not bridge.active(pending) and not bridge.active(root)


@pytest.mark.parametrize('schema', ['GOLD_BAR_FRESH_EPISODE_INPUT_V1', 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1'])
def test_nonauto_native_scope_remains_its_exact_fixed_runtime(roots, schema):
    value, root = roots
    runtime = dict(schema=schema, source_commit=SOURCE, trial_manifest_sha256='a'*64,
                   archive_source_commit='b'*40)
    if schema == 'GOLD_BAR_FRESH_EPISODE_INPUT_V1':
        runtime.update(candidate='channel', seconds=30, history_sha256='d'*64)
    else:
        runtime.update(observation_sha256='d'*64, entry_decision_ms=NOW)
    result = packet(value, NOW)
    result['bundle_sha256'] = _digest(runtime)
    result['decision'].update(bundle_sha256=_digest(runtime), status='STRATEGY_EPISODE_SETTLED', position_flat=True)
    result['decision_sha256'] = _digest(result['decision'])
    result['feedback'] = dict(status='BAR_STRATEGY_FEEDBACK_RETAINED', feedback_sha256='f'*64,
        artifact_sha256='a'*64, cash_reconciled=True, submission_enabled=False, economic_authority=False)
    with bridge.executor_retention(value.root, runtime, source_commit=SOURCE,
                                   account=value.account, uid=os.getuid()) as db:
        claim = db.claim(bundle=runtime, now_ms=NOW)
        db.put(claim, 'decision', result['decision'])
        db.put(claim, 'result', result)
    kwargs = dict(base_runtime=runtime, source_commit=SOURCE, account=value.account, executor_uid=os.getuid())
    with bridge.ControllerRetention(root, **kwargs) as current:
        current.enable_feedback_discovery()
        assert current.discover_feedback(current_runtime=runtime)['claim'] == claim
        with pytest.raises(ValueError, match='FEEDBACK_RUNTIME_CHANGED'):
            current.discover_feedback(current_runtime=dict(runtime, trial_manifest_sha256='0'*64))
    assert bridge.read_feedback_exact(root, _digest(result), expected_claim=claim, **kwargs)['result'] == result


def test_bootstrap_refuses_legacy_decision_evidence_and_native_marker_fragment(roots):
    value, root = roots
    legacy = root / 'strategy-decisions'
    legacy.mkdir(mode=0o700)
    (legacy / 'prior.json').write_bytes(bridge.canonical({'prior': True}))
    (legacy / 'prior.json').chmod(0o600)
    with session(roots) as current:
        with pytest.raises(ValueError, match='FEEDBACK_LEGACY_HISTORY_PRESENT'):
            current.enable_feedback_discovery()
    (legacy / 'prior.json').unlink()
    (value.root / bridge.MARKER).write_bytes(b'{}')
    (value.root / bridge.MARKER).chmod(0o600)
    with pytest.raises((ValueError, OSError, KeyError)):
        session(roots)


def test_legacy_runtime_does_not_invent_native_claims(roots):
    value, root = roots
    runtime = dict(schema='GOLD_STRATEGY_INPUT_V1', source_commit=SOURCE)
    with bridge.ControllerRetention(root, base_runtime=runtime, source_commit=SOURCE,
                                    account=value.account, executor_uid=os.getuid()) as current:
        with pytest.raises(ValueError, match='FEEDBACK_LEGACY_DISCOVERY_UNSUPPORTED'):
            current.enable_feedback_discovery()
    assert not bridge.active(root) and not bridge.active(value.root)


def test_unknown_episode_between_current_claims_cannot_be_ignored_as_no_feedback(roots):
    value, _ = roots
    frame(value)
    frame(value, at=NOW+1000, runtime=dict(bundle(), episode_id='0'*64), settled=True)
    frame(value, at=NOW+2000)
    with session(roots) as current:
        current.enable_feedback_discovery()
        with pytest.raises(ValueError, match='FEEDBACK_FRAME_SCOPE_SEQUENCE'):
            current.discover_feedback(current_runtime=bundle())


@pytest.mark.parametrize('unknown_prefix', [False, True])
def test_every_old_claim_must_belong_to_the_original_rotation_chain(roots, unknown_prefix):
    from tools import gold_bar_rotation as rotation
    value, root = roots
    if unknown_prefix:
        frame(value, runtime=dict(bundle(), episode_id='0'*64), settled=True)
    runtime = bundle()
    for index in range(2):
        result = packet(value, NOW+(index+1)*1000)
        result['bundle_sha256'] = _digest(runtime)
        result['decision'].update(bundle_sha256=_digest(runtime), status='NO_ENTRY_SIGNAL',
                                  position_flat=True, action=None, draft=None)
        result['decision_sha256'] = _digest(result['decision'])
        with store(value) as db:
            claim = db.claim(bundle=runtime, now_ms=NOW+(index+1)*1000)
            db.put(claim, 'decision', result['decision'])
            db.put(claim, 'result', result)
        with session(roots):
            pass
        runtime = rotation.advance(root=root, base=bundle(), account=value.account, current=runtime, result=result)
    frame(value, runtime=runtime, at=NOW+3000)
    with session(roots) as current:
        current.enable_feedback_discovery()
        if unknown_prefix:
            with pytest.raises(ValueError, match='FEEDBACK_FRAME_SCOPE_SEQUENCE'):
                current.discover_feedback(current_runtime=runtime)
        else:
            assert current.discover_feedback(current_runtime=runtime) is None


def test_retained_rotation_detects_recreated_native_history_missing_old_claim(roots):
    import shutil
    from tools import gold_bar_rotation as rotation
    value, root = roots
    result = packet(value, NOW)
    result['decision'].update(status='NO_ENTRY_SIGNAL', position_flat=True, action=None, draft=None)
    result['decision_sha256'] = _digest(result['decision'])
    with store(value) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        db.put(claim, 'decision', result['decision'])
        db.put(claim, 'result', result)
    with session(roots):
        pass
    current_runtime = rotation.advance(root=root, base=bundle(), account=value.account,
                                       current=bundle(), result=result)
    # Simulate both local retention histories lost while the independent,
    # original rotation and its capacity allocations remain intact.
    for location in (value.root, root):
        for name in (bridge.AREA, bridge.AUDIT):
            shutil.rmtree(location / name)
        (location / bridge.MARKER).unlink()
    frame(value, runtime=current_runtime, at=NOW+1000)
    with session(roots) as current:
        current.enable_feedback_discovery()
        with pytest.raises(ValueError, match='FEEDBACK_ROTATION_CLAIM_MISSING'):
            current.discover_feedback(current_runtime=current_runtime)


# Closure integration uses the actual executed/reconciled/cash native BAR fixture,
# unlike the controlled decision packets used by the storage cases above.
from tests.live.test_gold_feedback_recovery_fixture import (  # noqa: E402
    completed_bar_feedback as completed_bar_feedback,
    case as case,
    original_evidence as original_evidence,
)
from tests.tools.test_gold_pilot_closure import (  # noqa: E402
    opening_case as opening_case,
    idle_empty_case as idle_empty_case,
)


def _native_feedback_controller(f, tmp_path, monkeypatch, *, map_custody=True):
    from tests.tools.gold_controller_fixture import map_host_custody
    from tools import demo_operating_bundle as installer, gold_strategy_controller as controller
    monkeypatch.setattr(os, 'geteuid', os.getuid)
    if map_custody:
        map_host_custody(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, 'EXECUTOR', f.executor_root)
    monkeypatch.setattr(controller, 'observation_configuration',
        lambda source: (dict(executor_uid=f.executor_uid), dict(account_ref_sha256=f.account)))
    from tools.gold_pilot_install import _read_target
    monkeypatch.setattr(controller.supervisor, '_read_root_owned_json',
        lambda path: _read_target(path, uid=os.getuid()))
    return bridge.ControllerRetention(f.controller_root, base_runtime=f.base_runtime,
        source_commit=f.source_commit, account=f.account, executor_uid=f.executor_uid)


@pytest.mark.parametrize('phase,rotated', [(p, False) for p in ('intent', 'claim', 'origin', 'decision', 'result')] +
    [('claim', True), ('result', True)])
def test_real_closure_tail_is_readable_without_new_settled_or_claim(
        completed_bar_feedback, tmp_path, monkeypatch, phase, rotated):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention
    from tests.tools.test_gold_pilot_closure import configure_closure
    f = configure_closure(completed_bar_feedback, monkeypatch)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as current:
        current.enable_feedback_discovery()
        assert current.discover_feedback(current_runtime=f.base_runtime)['result_bytes'] == f.result_bytes
        current.attach(f.result, 'delivery', receipt(f.result))
    runtime = f.base_runtime
    if rotated:
        from tools.gold_bar_rotation import advance
        runtime = advance(root=f.controller_root, base=f.base_runtime, account=f.account,
            current=f.base_runtime, result=dict(f.result, research_feedback=receipt(f.result)))
    request = closure.request(runtime, f.result)
    f.clock[0] = f.result['original_pilot_window']['ends_at_ms'] + 1000

    def execute():
        return closure.execute(f.root, request, source_commit=f.source_commit, account=f.account,
            executor_uid=f.executor_uid, clock=lambda: f.clock[0],
            sleep=lambda seconds: f.clock.__setitem__(0, f.clock[0] + round(seconds * 1000)))

    child = os.fork()
    if child == 0:
        try:
            if phase == 'intent':
                original = closure._write_intent
                def interrupted(*args, **kwargs):
                    original(*args, **kwargs)
                    os._exit(77)
                monkeypatch.setattr(closure, '_write_intent', interrupted)
            elif phase == 'claim':
                original = Retention.claim
                def interrupted(*args, **kwargs):
                    original(*args, **kwargs)
                    os._exit(77)
                monkeypatch.setattr(Retention, 'claim', interrupted)
            else:
                original = Retention.put
                def interrupted(self, claim, kind, value):
                    answer = original(self, claim, kind, value)
                    if kind == phase:
                        os._exit(77)
                    return answer
                monkeypatch.setattr(Retention, 'put', interrupted)
            execute()
            os._exit(78)
        except BaseException:
            os._exit(79)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 77
    with _native_feedback_controller(f, tmp_path, monkeypatch, map_custody=False) as current:
        current.enable_feedback_discovery()
        found = current.discover_feedback(current_runtime=runtime)
        assert found['relation'] == ('PREVIOUS' if rotated else 'CURRENT')
        assert found['result_bytes'] == f.result_bytes and found['claim'] == f.claim
        assert found['delivery'] == receipt(f.result)
        expected = 'INTENT_ONLY' if phase == 'intent' else ('COMPLETE' if phase == 'result' else 'PENDING')
        assert found['closure']['status'] == expected
        assert found['closure']['claim']['ordinal'] == f.executor_checkpoint['frames'] + 1
        assert found['controller_checkpoint']['frames'] == f.executor_checkpoint['frames'] + (phase == 'result')
        assert found['executor_checkpoint']['frames'] == f.executor_checkpoint['frames'] + (phase != 'intent')
        if phase == 'claim' and not rotated:
            current.store.adopt(found['closure']['claim'])
            with pytest.raises(ValueError, match='FEEDBACK_CLOSURE_MIRROR_CHANGED'):
                current.discover_feedback(current_runtime=runtime)
        current.release()
        final = execute()
        current.retain(final)
        completed = current.discover_feedback(current_runtime=runtime)
        assert completed['result_bytes'] == f.result_bytes and completed['claim'] == f.claim
        assert completed['closure']['status'] == 'COMPLETE'
        assert completed['closure']['result'] == final
        assert completed['executor_checkpoint']['frames'] == f.executor_checkpoint['frames'] + 1
        assert completed['controller_checkpoint']['frames'] == f.executor_checkpoint['frames'] + 1


def test_classified_closure_evidence_cannot_be_hidden_by_a_later_ordinary_frame(roots):
    value, _ = roots
    _, result = frame(value, settled=True)
    closure = packet(value, NOW + 1000)
    closure['decision'].update(status='PILOT_STOPPED', action=None, draft=None,
        execution_constraints=None, closure_of_result_sha256=_digest(result))
    closure['decision_sha256'] = _digest(closure['decision'])
    with store(value) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW + 1000)
        db.put(claim, 'decision', closure['decision'])
        db.put(claim, 'result', closure)
    frame(value, at=NOW + 2000)
    with session(roots) as current:
        current.enable_feedback_discovery()
        with pytest.raises(ValueError, match='CLOSURE_INTENT_MISSING'):
            current.discover_feedback(current_runtime=bundle())


def _native_no_entry_after_settlement(f, runtime):
    """Original signal/cycle/collector produce NO_ENTRY on a new public bar.

    The public message and transport remain isolated fixture I/O. This helper
    does not claim to run a second complete FreshEpisode acquisition process.
    """
    import json
    from tools.gold_bar_demo_entry import BarDemoEntry
    from tools.gold_bar_demo_cycle import prepare
    from tools.gold_pilot_retention import executor_retention
    from tools.demo_executor_origin import capture_for_pilot
    from tools.gold_strategy_worker import original_pilot_window, require_current_decision
    from services.research.gold_bar_candidates import INTERVAL_MS
    warmup = tuple(f.engine._cursor.bars)
    engine = BarDemoEntry(f.engine._archive, candidate=f.engine._definition['candidate'],
        source_directory=f.engine._source, warmup=warmup, registered_at_ms=f.clock[0]+1,
        prepared_definition=f.engine._prepared_definition)
    start = warmup[-1].close_ms
    received = start + INTERVAL_MS + 20
    message = json.dumps(dict(stream='xauusdt@kline_15m', data=dict(e='kline', E=received,
        s='XAUUSDT', k=dict(t=start, T=start+INTERVAL_MS-1, s='XAUUSDT', i='15m', x=True,
                          o='100', h='101', l='99', c='100'))))
    engine.ingest(message, received_at_ms=received)
    f.clock[0] = received + 1
    with executor_retention(f.root, runtime, source_commit=f.source_commit,
            account=f.account, uid=f.executor_uid) as retained:
        claim = retained.claim(bundle=runtime, now_ms=f.clock[0])
        origin = capture_for_pilot(symbol='XAUUSDT', source_commit=f.source_commit,
            retention=retained, claim=claim)
        decision = prepare(engine=engine, entry_decision_ms=received, origin=origin,
            **f.stores, now_ms=f.clock[0])
        assert decision['status'] == 'NO_ENTRY_SIGNAL', decision
        decision.update(source_commit=f.source_commit, bundle_sha256=_digest(runtime),
            account_ref_sha256=f.account, origin_sha256=_digest(origin))
        require_current_decision(decision)
        result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
            source_commit=f.source_commit, bundle_sha256=_digest(runtime), decision=decision,
            decision_sha256=_digest(decision), submission_enabled=False, economic_authority=False,
            original_pilot_window=original_pilot_window(f.root, decision=decision,
                source_commit=f.source_commit, account=f.account))
        retained.put(claim, 'decision', decision)
        retained.put(claim, 'result', result)
    return result


def test_terminal_history_reads_original_cash_completion_after_no_entry_rotation(
        completed_bar_feedback, tmp_path, monkeypatch):
    from tools import gold_bar_rotation as rotation
    f = completed_bar_feedback
    with _native_feedback_controller(f, tmp_path, monkeypatch) as current:
        current.enable_feedback_discovery()
        current.attach(f.result, 'delivery', receipt(f.result))
        current.release()
        runtime = rotation.advance(root=f.controller_root, base=f.base_runtime, account=f.account,
            current=f.base_runtime, result=dict(f.result, research_feedback=receipt(f.result)))
        no_entry = _native_no_entry_after_settlement(f, runtime)
        current.retain(no_entry)
        current.release()
        runtime = rotation.advance(root=f.controller_root, base=f.base_runtime, account=f.account,
            current=runtime, result=no_entry)
        assert current.discover_feedback(current_runtime=runtime) is None
        found = current.discover_terminal_feedback(current_runtime=runtime)
        assert found['status'] == 'DELIVERED' and found['mode'] == 'TERMINAL_HISTORY'
        assert found['relation'] == 'HISTORICAL_TERMINAL'
        assert found['result'] == f.result and found['result_bytes'] == f.result_bytes
        assert found['claim'] == f.claim and found['delivery'] == receipt(f.result)
        assert found['executor_checkpoint']['frames'] == f.executor_checkpoint['frames'] + 1
        assert found['controller_checkpoint']['frames'] == f.executor_checkpoint['frames'] + 1
        before = found['controller_checkpoint'], found['executor_checkpoint']
        current.release()
        found_again = current.discover_terminal_feedback(current_runtime=runtime)
        assert (found_again['controller_checkpoint'], found_again['executor_checkpoint']) == before
        assert rotation.load(root=f.controller_root, base=f.base_runtime, account=f.account, read_only=True) == runtime
        from tests.tools.test_gold_pilot_closure import configure_closure, run
        from tools import gold_pilot_closure as closure
        configure_closure(f, monkeypatch)
        current.release()
        f.clock[0] = f.result['original_pilot_window']['ends_at_ms'] + 1000
        completed = run(f, closure.request(runtime, f.result))
        current.retain(completed)
        terminal = current.discover_terminal_feedback(current_runtime=runtime)
        assert terminal['result_bytes'] == f.result_bytes and terminal['claim'] == f.claim
        assert terminal['closure']['status'] == 'COMPLETE' and terminal['closure']['result'] == completed
        assert terminal['executor_checkpoint']['frames'] == before[1]['frames'] + 1
        assert rotation.load(root=f.controller_root, base=f.base_runtime, account=f.account, read_only=True) == runtime


def _rotated_storage_history(roots, settled=(True, False), *, duplicate=False):
    from tools import gold_bar_rotation as rotation
    value, root = roots
    runtime = bundle()
    retained = []
    at = NOW
    for index, is_settled in enumerate(settled):
        claim, result = frame(value, at=at, runtime=runtime, settled=is_settled,
                              no_entry=not is_settled)
        at += 1000
        if duplicate and index == 0:
            with store(value) as db:
                repeated = db.claim(bundle=runtime, now_ms=at)
                db.put(repeated, 'decision', result['decision'])
                db.put(repeated, 'result', result)
            at += 1000
        with session(roots) as current:
            if is_settled:
                current.attach(result, 'delivery', receipt(result))
        outcome = dict(result, research_feedback=receipt(result)) if is_settled else result
        runtime = rotation.advance(root=root, base=bundle(), account=value.account,
            current=runtime, result=outcome)
        retained.append((claim, result))
    return runtime, retained


def test_terminal_history_selects_latest_original_settlement_and_never_allocates(roots):
    from tools import gold_bar_rotation as rotation
    value, root = roots
    runtime, retained = _rotated_storage_history(roots, (True, True, False))
    expected_claim, expected = retained[1]
    with session(roots) as current:
        current.enable_feedback_discovery()
        before = current.store.metadata(), current._feedback_source.metadata()
        assert current.discover_feedback(current_runtime=runtime) is None
        found = current.discover_terminal_feedback(current_runtime=runtime)
        assert found['result'] == expected and found['claim'] == expected_claim
        assert found['rotation_sha256'] == rotation.feedback_terminal(root=root, base=bundle(),
            account=value.account, current=runtime)['rotation_sha256']
        assert (current.store.metadata(), current._feedback_source.metadata()) == before


def test_terminal_no_settlement_returns_none_only_after_original_history_audit(roots):
    runtime, _ = _rotated_storage_history(roots, (False, False))
    with session(roots) as current:
        current.enable_feedback_discovery()
        assert current.discover_terminal_feedback(current_runtime=runtime) is None


def test_terminal_same_digest_in_multiple_native_claims_is_ambiguous(roots):
    runtime, _ = _rotated_storage_history(roots, duplicate=True)
    with session(roots) as current:
        current.enable_feedback_discovery()
        with pytest.raises(ValueError, match='FEEDBACK_TERMINAL_RESULT_MISSING_OR_AMBIGUOUS'):
            current.discover_terminal_feedback(current_runtime=runtime)


@pytest.mark.parametrize('role,kind', [('executor', 'result'), ('executor', 'decision'),
                                     ('controller', 'result'), ('controller', 'delivery')])
def test_terminal_exact_slots_validate_old_payload_bytes_beyond_nearby_scope(roots, role, kind):
    value, root = roots
    runtime, retained = _rotated_storage_history(roots)
    original_claim, _ = retained[0]
    target = value.root if role == 'executor' else root
    with sqlite3.connect(target / bridge.AREA / 'records.sqlite3') as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='records_no_update'").fetchone()[0]
        conn.execute('DROP TRIGGER records_no_update')
        body = conn.execute('SELECT payload FROM records WHERE ordinal=? AND kind=?',
            (original_claim['ordinal'], kind)).fetchone()[0]
        changed = body.replace(b'"submission_enabled":false', b'"submission_enabled":true ', 1)
        assert len(changed) == len(body) and changed != body
        conn.execute('UPDATE records SET payload=? WHERE ordinal=? AND kind=?',
            (changed, original_claim['ordinal'], kind))
        conn.execute(sql)
    with session(roots) as current:
        current.enable_feedback_discovery()
        # Large unselected historical bytes are intentionally checked lazily.
        assert current.discover_feedback(current_runtime=runtime) is None
        with pytest.raises(ValueError):
            current.discover_terminal_feedback(current_runtime=runtime)


@pytest.mark.parametrize('phase', ['intent', 'claim', 'origin', 'stop', 'saved', 'decision', 'result'])
def test_idle_zero_frame_closure_has_independent_typed_discovery(
        idle_empty_case, tmp_path, monkeypatch, phase):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention
    from tests.tools.test_gold_pilot_closure import run, wait_child
    from live.execution import demo_gold_pilot as pilot
    f = idle_empty_case
    f.executor_root = f.root
    f.controller_root = tmp_path / 'controller'
    f.controller_root.mkdir(mode=0o700)
    request = closure.idle_request(f.base_runtime, f.window)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as current:
        current.enable_feedback_discovery()
        assert current.discover_feedback(current_runtime=f.base_runtime)['status'] == 'FEEDBACK_NOT_INITIALIZED'
    child = os.fork()
    if child == 0:
        try:
            if phase == 'intent':
                original = closure._write_intent
                def interrupted(*args, **kwargs):
                    original(*args, **kwargs)
                    os._exit(77)
                monkeypatch.setattr(closure, '_write_intent', interrupted)
            elif phase == 'claim':
                original = Retention.claim
                def interrupted(*args, **kwargs):
                    original(*args, **kwargs)
                    os._exit(77)
                monkeypatch.setattr(Retention, 'claim', interrupted)
            elif phase == 'stop':
                original = pilot.observe
                def interrupted(*args, **kwargs):
                    original(*args, **kwargs)
                    os._exit(77)
                monkeypatch.setattr(pilot, 'observe', interrupted)
            elif phase == 'saved':
                original = pilot.retain_once
                def interrupted(path, *args, **kwargs):
                    answer = original(path, *args, **kwargs)
                    if path.name == 'closure-result.json':
                        os._exit(77)
                    return answer
                monkeypatch.setattr(pilot, 'retain_once', interrupted)
            else:
                original = Retention.put
                def interrupted(self, claim, kind, value):
                    answer = original(self, claim, kind, value)
                    if kind == phase:
                        os._exit(77)
                    return answer
                monkeypatch.setattr(Retention, 'put', interrupted)
            run(f, request)
            os._exit(78)
        except BaseException:
            os._exit(79)
    assert wait_child(child) == 77
    with _native_feedback_controller(f, tmp_path, monkeypatch, map_custody=False) as current:
        current.enable_feedback_discovery()
        found = current.discover_terminal_feedback(current_runtime=f.base_runtime)
        assert set(found) == {'status', 'mode', 'result', 'delivery', 'closure', 'closure_request',
                              'controller_checkpoint', 'executor_checkpoint', 'submission_enabled'}
        assert found['status'] == 'IDLE_CLOSURE' and found['mode'] == 'IDLE'
        assert found['result'] is found['delivery'] is None and found['submission_enabled'] is False
        assert found['closure_request'] == request
        expected = 'INTENT_ONLY' if phase == 'intent' else ('COMPLETE' if phase == 'result' else 'PENDING')
        assert found['closure']['status'] == expected and found['closure']['claim']['ordinal'] == 1
        assert found['controller_checkpoint']['frames'] == (phase == 'result')
        assert found['executor_checkpoint']['frames'] == (phase != 'intent')
        assert current.discover_feedback(current_runtime=f.base_runtime) == found
        current.release()
        completed = run(f, request)
        current.retain(completed)
        final = current.discover_terminal_feedback(current_runtime=f.base_runtime)
        assert final['closure']['status'] == 'COMPLETE' and final['closure']['result'] == completed
        assert final['executor_checkpoint']['frames'] == final['controller_checkpoint']['frames'] == 1
        current.release()
        calls = list(f.read_calls)
        assert run(f, request) == completed and f.read_calls == calls


def test_idle_closure_missing_intent_and_fixed_result_never_becomes_ordinary_none(
        idle_empty_case, tmp_path, monkeypatch):
    from tools import gold_pilot_closure as closure
    from tests.tools.test_gold_pilot_closure import run
    f = idle_empty_case
    f.executor_root = f.root
    f.controller_root = tmp_path / 'controller'
    f.controller_root.mkdir(mode=0o700)
    request = closure.idle_request(f.base_runtime, f.window)
    completed = run(f, request)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as current:
        current.enable_feedback_discovery()
        assert current.discover_terminal_feedback(current_runtime=f.base_runtime)['closure']['result'] == completed
        (f.root / 'gold-pilot/closure-intent.json').unlink()
        (f.root / 'gold-pilot/closure-result.json').unlink()
        with pytest.raises(ValueError, match='FEEDBACK_CLOSURE_INTENT_MISSING'):
            current.discover_terminal_feedback(current_runtime=f.base_runtime)


def test_same_bootstrap_owner_cold_audits_first_partial_idle_frame(
        idle_empty_case, tmp_path, monkeypatch):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention
    from tests.tools.test_gold_pilot_closure import run, wait_child
    f = idle_empty_case
    f.executor_root = f.root
    f.controller_root = tmp_path / 'controller'
    f.controller_root.mkdir(mode=0o700)
    request = closure.idle_request(f.base_runtime, f.window)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as current:
        current.enable_feedback_discovery()
        assert current.discover_feedback(current_runtime=f.base_runtime)['status'] == 'FEEDBACK_NOT_INITIALIZED'
        current.release()
        child = os.fork()
        if child == 0:
            original = Retention.claim
            def interrupted(*args, **kwargs):
                original(*args, **kwargs)
                os._exit(77)
            monkeypatch.setattr(Retention, 'claim', interrupted)
            try:
                run(f, request)
            except BaseException:
                os._exit(79)
            os._exit(78)
        assert wait_child(child) == 77
        found = current.discover_terminal_feedback(current_runtime=f.base_runtime)
        assert found['status'] == 'IDLE_CLOSURE' and found['closure']['status'] == 'PENDING'
        assert found['executor_checkpoint']['frames'] == 1 and found['controller_checkpoint']['frames'] == 0


def test_original_closure_witness_rejects_both_native_histories_missing(
        idle_empty_case, tmp_path, monkeypatch):
    import shutil
    from tools import gold_pilot_closure as closure
    from tests.tools.test_gold_pilot_closure import run
    f = idle_empty_case
    f.executor_root = f.root
    f.controller_root = tmp_path / 'controller'
    f.controller_root.mkdir(mode=0o700)
    run(f, closure.idle_request(f.base_runtime, f.window))
    with _native_feedback_controller(f, tmp_path, monkeypatch) as current:
        current.enable_feedback_discovery()
        assert current.discover_terminal_feedback(current_runtime=f.base_runtime)['status'] == 'IDLE_CLOSURE'
    for root in (f.root, f.controller_root):
        for name in (bridge.AREA, bridge.AUDIT):
            shutil.rmtree(root / name)
        (root / bridge.MARKER).unlink()
    assert (f.root / 'gold-pilot/closure-intent.json').is_file()
    with pytest.raises(ValueError, match='FEEDBACK_HISTORY_MISSING'):
        with _native_feedback_controller(f, tmp_path, monkeypatch, map_custody=False) as current:
            current.enable_feedback_discovery()
            current.discover_terminal_feedback(current_runtime=f.base_runtime)


def test_existing_owner_recovers_original_result_after_child_output_crash(
        idle_empty_case, tmp_path, monkeypatch):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention
    from tests.tools.test_gold_pilot_closure import run, wait_child
    f = idle_empty_case
    f.executor_root = f.root
    f.controller_root = tmp_path / 'controller'
    f.controller_root.mkdir(mode=0o700)
    request = closure.idle_request(f.base_runtime, f.window)
    with bridge.executor_retention(f.root, f.base_runtime, source_commit=f.source_commit,
            account=f.account, uid=f.executor_uid) as original:
        assert original.metadata()['frames'] == 0
    with _native_feedback_controller(f, tmp_path, monkeypatch) as current:
        current.enable_feedback_discovery()
        assert current.store is not None
        assert current.discover_feedback(current_runtime=f.base_runtime) is None
        current.release()
        child = os.fork()
        if child == 0:
            original = Retention.put
            def interrupted(self, claim, kind, value):
                answer = original(self, claim, kind, value)
                if kind == 'result':
                    os._exit(77)
                return answer
            monkeypatch.setattr(Retention, 'put', interrupted)
            try:
                run(f, request)
            except BaseException:
                os._exit(79)
            os._exit(78)
        assert wait_child(child) == 77
        found = current.discover_terminal_feedback(current_runtime=f.base_runtime)
        assert found['status'] == 'IDLE_CLOSURE' and found['closure']['status'] == 'COMPLETE'
        claim, result = found['closure']['claim'], found['closure']['result']
        assert current.store.read_claim(claim, 'result') == bridge.canonical(result)
        assert found['controller_checkpoint']['frames'] == found['executor_checkpoint']['frames'] == 1
        current.release()
        calls = list(f.read_calls)
        assert run(f, request) == result and f.read_calls == calls
        assert current.discover_terminal_feedback(current_runtime=f.base_runtime) == found
