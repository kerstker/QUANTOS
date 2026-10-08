"""Native original closure evidence reaches only its bounded diagnostic mirror.

Venue transport and OS custody use the existing isolated fixtures. Original
enrollment, account validation, immutable claims, audits and recovery are real.
"""
import os

import pytest

from tests.tools.test_gold_pilot_closure import (
    opening_case as opening_case, idle_empty_case as idle_empty_case, run, wait_child,
)
from tests.tools.test_gold_feedback_retention_readers import _native_feedback_controller
from tools.gold_pilot_retention import executor_retention
from tools.gold_pilot_retention_store import Retention, canonical


@pytest.fixture
def stale_idle(idle_empty_case, tmp_path, monkeypatch):
    from tools import gold_pilot_closure as closure
    f = idle_empty_case
    f.executor_root = f.root
    f.controller_root = tmp_path / 'controller'
    f.controller_root.mkdir(mode=0o700)
    f.original_request = closure.idle_request(f.base_runtime, f.window)
    child = os.fork()
    if child == 0:
        original = Retention.put
        def interrupted(self, claim, kind, value):
            result = original(self, claim, kind, value)
            if kind == 'origin':
                os._exit(77)
            return result
        monkeypatch.setattr(Retention, 'put', interrupted)
        try:
            run(f, f.original_request)
        except BaseException:
            os._exit(78)
        os._exit(79)
    assert wait_child(child) == 77
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    f.original_intent = decode((f.root / 'gold-pilot/closure-intent.json').read_bytes())
    f.failed_claim = f.original_intent['observation_claim']
    with executor_retention(f.root, f.base_runtime, source_commit=f.source_commit,
            account=f.account, uid=f.executor_uid) as store:
        f.original_origin = store.read_claim(f.failed_claim, 'origin')
        f.original_checkpoint = store.metadata()
    f.clock[0] = decode(f.original_origin)['completed_at_ms'] + 31001
    assert not (f.root / 'gold-pilot/stopped.json').exists()
    return f


def test_native_origin_only_inspection_is_read_only_and_request_bound(stale_idle, tmp_path, monkeypatch):
    from tools import gold_pilot_reobservation as retry
    f = stale_idle
    with _native_feedback_controller(f, tmp_path, monkeypatch) as owner:
        owner.enable_feedback_discovery()
        before = owner.store.metadata()
        view = owner.inspect_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0])
        assert set(view) == {'schema', 'candidate', 'reobservation', 'controller_checkpoint',
                            'executor_checkpoint', 'submission_enabled'}
        assert view['schema'] == 'GOLD_PILOT_REOBSERVATION_INSPECTION_V1'
        assert view['candidate']['status'] == 'ORIGIN_ONLY_STALE'
        assert view['candidate']['execution_state_verified'] is False
        assert view['reobservation'] is None and view['submission_enabled'] is False
        assert view['controller_checkpoint'] == before == owner.store.metadata()
        assert view['executor_checkpoint'] == f.original_checkpoint
        request = retry.request(f.base_runtime, f.original_request, view['candidate'])
        budget = owner.preflight_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0], request=request)
        assert budget['status'] == 'CAPACITY_AVAILABLE'
        assert budget['required']['claims'] == budget['required']['records'] == 2
        assert budget['required']['audit_events'] == 4
        assert owner.store.metadata() == before
        assert owner._feedback_source.read_claim(f.failed_claim, 'origin') == f.original_origin
        assert owner._feedback_source.read_claim(f.failed_claim, 'decision') is None
        assert owner._feedback_source.read_claim(f.failed_claim, 'result') is None


def new_request(f):
    from tools import gold_pilot_reobservation as retry
    with executor_retention(f.root, f.base_runtime, source_commit=f.source_commit,
            account=f.account, uid=f.executor_uid) as store:
        candidate = retry.inspect_stale_candidate(f.root, retention=store, base_runtime=f.base_runtime,
            runtime=f.base_runtime, source_commit=f.source_commit, account=f.account,
            executor_uid=f.executor_uid, now_ms=f.clock[0])
    return retry.request(f.base_runtime, f.original_request, candidate)


def interrupt_successor(f, request, monkeypatch, phase):
    from tools import gold_pilot_reobservation as retry
    from live.execution import demo_gold_pilot as pilot
    child = os.fork()
    if child == 0:
        put, save, claim, retain = Retention.put, retry._save, Retention.claim, pilot.retain_once
        def stopped_put(self, frame, kind, value):
            result = put(self, frame, kind, value)
            failed = frame == f.failed_claim
            name = ('expired_' if failed else '') + kind
            if name == phase:
                os._exit(81)
            return result
        def stopped_save(root, name, value):
            result = save(root, name, value)
            if name == {'intent': retry.INTENT, 'admission': retry.ADMISSION,
                        'capture': retry.CAPTURE_WITNESS, 'capture_first': retry.CAPTURE,
                        'packet': 'closure-result.json'}.get(phase):
                os._exit(81)
            return result
        def stopped_claim(self, **kwargs):
            result = claim(self, **kwargs)
            if phase == 'claim':
                os._exit(81)
            return result
        def stopped_retain(path, value, **kwargs):
            result = retain(path, value, **kwargs)
            if phase == 'stop' and path.name == 'stopped.json':
                os._exit(81)
            return result
        monkeypatch.setattr(Retention, 'put', stopped_put)
        monkeypatch.setattr(Retention, 'claim', stopped_claim)
        monkeypatch.setattr(retry, '_save', stopped_save)
        monkeypatch.setattr(pilot, 'retain_once', stopped_retain)
        try:
            retry.execute(f.root, request, source_commit=f.source_commit, account=f.account,
                executor_uid=f.executor_uid, clock=lambda: f.clock[0],
                sleep=lambda seconds: f.clock.__setitem__(0, f.clock[0] + round(seconds * 1000)))
        except BaseException:
            import traceback
            (f.root.parent / 'reobservation-child-error.txt').write_text(traceback.format_exc())
            os._exit(82)
        os._exit(83)
    status = wait_child(child)
    assert status == 81, (status, (f.root.parent / 'reobservation-child-error.txt').read_text()
                         if (f.root.parent / 'reobservation-child-error.txt').exists() else '')


PHASES = [('intent', 'INTENT_ONLY'), ('expired_decision', 'EXPIRED_DECISION_RETAINED'),
          ('expired_result', 'EXPIRED_RETAINED'), ('claim', 'CLAIMED_UNBOUND'),
          ('admission', 'ADMITTED'), ('capture', 'CAPTURE_STARTED'), ('origin', 'ORIGIN_RETAINED'),
          ('stop', 'STOP_RETAINED'), ('packet', 'FINAL_PACKET_RETAINED'),
          ('decision', 'FINAL_DECISION_RETAINED'), ('result', 'COMPLETE')]


@pytest.mark.parametrize('phase,status', PHASES)
@pytest.mark.parametrize('same_owner', [False, True])
def test_native_reobservation_each_durable_prefix_is_mirrored_under_exact_chain(
        stale_idle, tmp_path, monkeypatch, phase, status, same_owner):
    f = stale_idle
    request = new_request(f)
    owner = _native_feedback_controller(f, tmp_path, monkeypatch)
    try:
        owner.enable_feedback_discovery()
        owner.release()
        interrupt_successor(f, request, monkeypatch, phase)
        if not same_owner:
            owner.close()
            owner = _native_feedback_controller(f, tmp_path, monkeypatch)
            owner.enable_feedback_discovery()
        inspection = owner.inspect_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0])
        chain = inspection['reobservation']
        assert inspection['candidate'] is None and chain['status'] == status
        found = owner.discover_terminal_feedback(current_runtime=f.base_runtime)
        assert found['status'] == 'IDLE_CLOSURE' and found['result'] is None and found['delivery'] is None
        assert found['closure']['intent_sha256'] == chain['intent_sha256']
        assert found['closure']['claim'] == chain['claim']
        assert found['closure']['status'] == ('COMPLETE' if status == 'COMPLETE' else 'PENDING')
        assert owner._feedback_source.read_claim(f.failed_claim, 'origin') == f.original_origin
        if chain['expired_result'] is not None:
            assert owner.store.read_claim(f.failed_claim, 'result') == canonical(chain['expired_result'])
            owner.retain_reobservation_diagnostic(chain['expired_result'], current_runtime=f.base_runtime)
        if status == 'COMPLETE':
            f.clock[0] = f.window['ends_at_ms'] + 900001
            from tools import gold_pilot_reobservation as retry
            monkeypatch.setattr(retry, 'budget_bounds', lambda _: pytest.fail(
                'A completed original needs no hypothetical future record budget'))
            budget = owner.preflight_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0], request=request)
            assert not any(budget['required'].values())
            assert owner.store.metadata()['frames'] == owner._feedback_source.metadata()['frames'] == 2
        else:
            assert found['closure']['result'] is None
            budget = owner.preflight_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0], request=request)
            remaining = 2 if chain['expired_result'] is None else 1
            assert budget['required']['claims'] == budget['required']['records'] == remaining
            assert budget['required']['audit_events'] == remaining * 2
    finally:
        owner.close()


def test_lost_intent_with_only_expired_decision_is_rejected_before_constructor_mirror(
        stale_idle, tmp_path, monkeypatch):
    from tools import gold_pilot_reobservation as retry
    f = stale_idle
    interrupt_successor(f, new_request(f), monkeypatch, 'expired_decision')
    (f.root / 'gold-pilot' / retry.INTENT).unlink()
    with pytest.raises(ValueError, match='REOBSERVATION|DIAGNOSTIC|CLOSURE'):
        with _native_feedback_controller(f, tmp_path, monkeypatch):
            pass


@pytest.mark.parametrize('missing', ['closure-reobservation-intent.json',
    'closure-reobservation-admission.json', 'closure-reobservation-capture.json',
    'closure-reobservation-capture-witness.json', 'closure-intent.json', 'stopped.json'])
@pytest.mark.parametrize('same_owner', [False, True])
def test_same_owner_rejects_lost_fixed_chain_before_mirroring_completed_successor(
        stale_idle, tmp_path, monkeypatch, missing, same_owner):
    f = stale_idle
    request = new_request(f)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as owner:
        owner.enable_feedback_discovery()
        before = owner.store.metadata()
        owner.release()
        interrupt_successor(f, request, monkeypatch, 'result')
        (f.root / 'gold-pilot' / missing).unlink()
        if not same_owner:
            owner.close()
            with pytest.raises((ValueError, FileNotFoundError)):
                with _native_feedback_controller(f, tmp_path, monkeypatch):
                    pass
            return
        with pytest.raises((ValueError, FileNotFoundError)):
            owner.inspect_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0])
        assert owner.store.metadata() == before


@pytest.mark.parametrize('change', [dict(original_origin_sha256='f' * 64), dict(submission_enabled=0)])
def test_reobservation_request_tampering_cannot_reserve_controller_capacity(stale_idle, tmp_path, monkeypatch, change):
    f = stale_idle
    request = dict(new_request(f), **change)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as owner:
        owner.enable_feedback_discovery()
        before = owner.store.metadata()
        with pytest.raises(ValueError, match='REQUEST_CHANGED'):
            owner.preflight_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0], request=request)
        assert owner.store.metadata() == before


@pytest.fixture
def exhausted_frame_idle(request, monkeypatch):
    from tools import gold_pilot_retention_store as native
    original = native._binding
    # Synthetic one-frame capacity is declared before either root is created.
    # Real claims/audits/custody and the original capacity hash remain enforced.
    monkeypatch.setattr(native, '_binding', lambda *args, **kwargs:
        dict(original(*args, **kwargs), max_frames=1))
    return request.getfixturevalue('stale_idle')


def test_actual_controller_preflight_refuses_one_frame_budget_without_get_or_claim(
        exhausted_frame_idle, tmp_path, monkeypatch):
    f = exhausted_frame_idle
    request = new_request(f)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as owner:
        owner.enable_feedback_discovery()
        before = owner.store.metadata(), owner._feedback_source.metadata()
        with pytest.raises(ValueError, match='FRAME_BUDGET'):
            owner.preflight_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0], request=request)
        assert (owner.store.metadata(), owner._feedback_source.metadata()) == before
        assert not (f.root / 'gold-pilot/closure-reobservation-intent.json').exists()


@pytest.mark.parametrize('damage', ['executor_audit', 'controller_audit', 'intent_runtime',
    'wrong_runtime', 'diagnostic_transplant'])
def test_partial_successor_requires_both_audits_and_the_exact_original_runtime(
        stale_idle, tmp_path, monkeypatch, damage):
    from tools import gold_pilot_reobservation as retry
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    from live.execution.demo_batch_risk import _digest
    f = stale_idle
    request = new_request(f)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as owner:
        owner.enable_feedback_discovery()
        owner.release()
        interrupt_successor(f, request, monkeypatch, 'origin')
        good = owner.inspect_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0])
        original = good['reobservation']['expired_result']
        if damage == 'diagnostic_transplant':
            with pytest.raises(ValueError, match='DIAGNOSTIC_CHANGED'):
                owner.retain_reobservation_diagnostic(dict(original, status='PILOT_STOPPED'),
                    current_runtime=f.base_runtime)
            return
        owner.release()
        runtime = f.base_runtime
        if damage.endswith('_audit'):
            native = owner._feedback_source if damage == 'executor_audit' else owner.store
            next((native.audit / 'events').iterdir()).unlink()
        elif damage == 'intent_runtime':
            path = f.root / 'gold-pilot' / retry.INTENT
            intent = decode(path.read_bytes())
            intent['subject']['runtime_sha256'] = 'f' * 64
            intent['subject_sha256'] = _digest(intent['subject'])
            path.write_bytes(canonical(intent))
        else:
            runtime = dict(runtime, episode_id='f' * 64)
        with pytest.raises(ValueError):
            owner.inspect_reobservation(current_runtime=runtime, now_ms=f.clock[0])


@pytest.mark.parametrize('dimension', ['max_total_bytes', 'max_audit_events', 'max_audit_bytes',
    'max_database_bytes', 'max_journal_bytes', 'max_audit_allocated_bytes', 'max_directory_bytes'])
def test_controller_capacity_rejects_insufficient_independent_physical_dimension(
        stale_idle, tmp_path, monkeypatch, dimension):
    from tools.gold_pilot_retention import _controller_capacity, _original
    from tools import gold_pilot_retention_store as native
    f = stale_idle
    with _native_feedback_controller(f, tmp_path, monkeypatch) as owner:
        owner.enable_feedback_discovery()
        db = owner.store.area / 'records.sqlite3'
        directories = (owner.store.area, owner.store.audit, owner.store.audit / 'events')
        limits = dict(max_total_bytes=1, max_audit_events=1, max_audit_bytes=1,
            max_database_bytes=max(db.stat().st_size, db.stat().st_blocks * 512) + native.PAGE_BYTES,
            max_journal_bytes=1, max_audit_allocated_bytes=native.EVENT_ALLOCATED_BYTES,
            max_directory_bytes=sum(max(p.stat().st_size, p.stat().st_blocks * 512) for p in directories) + 1)
    root = tmp_path / 'finite-controller'
    root.mkdir(mode=0o700)
    original = _original(f.root, f.base_runtime, uid=f.executor_uid, account=f.account)
    binding = native._binding
    # A separate synthetic capacity is fixed before this temporary store's
    # marker/DB/audits are created. No real binding is changed after creation.
    monkeypatch.setattr(native, '_binding', lambda *args, **kwargs:
        dict(binding(*args, **kwargs), **{dimension: limits[dimension]}))
    with native.Retention(root, original=original, base_runtime=f.base_runtime,
            uid=os.getuid(), role='controller') as store:
        before = store.metadata()
        with pytest.raises(ValueError, match='BUDGET'):
            _controller_capacity(store, claims=2, sizes=[2000, 5000])
        assert store.metadata() == before


def test_failed_successor_origin_is_retained_exhausted_tail_without_fake_result(
        stale_idle, tmp_path, monkeypatch):
    import subprocess
    from tools import gold_pilot_reobservation as retry, demo_executor_origin as origin
    f = stale_idle
    request = new_request(f)
    attempts = []
    def timeout(command, **kwargs):
        assert command[3] == 'tools.demo_executor_origin' and kwargs['timeout'] == 115
        attempts.append(tuple(command))
        raise subprocess.TimeoutExpired(command, 115)
    monkeypatch.setattr(origin.subprocess, 'run', timeout)
    def execute():
        return retry.execute(f.root, request, source_commit=f.source_commit, account=f.account,
            executor_uid=f.executor_uid, clock=lambda: f.clock[0], sleep=lambda _: None)
    with pytest.raises(ValueError, match='CAPTURE_FAILED'):
        execute()
    with _native_feedback_controller(f, tmp_path, monkeypatch) as owner:
        owner.enable_feedback_discovery()
        chain = owner.inspect_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0])['reobservation']
        assert chain['status'] == 'ORIGIN_FAILED' and chain['origin_status'] == 'FAILED'
        assert owner._feedback_source.metadata()['frames'] == 2
        assert owner.store.metadata()['frames'] == 1
        assert owner._feedback_source.read_claim(chain['claim'], 'decision') is None
        assert owner._feedback_source.read_claim(chain['claim'], 'result') is None
        assert owner.discover_terminal_feedback(current_runtime=f.base_runtime)['closure']['result'] is None
    with pytest.raises(ValueError, match='CAPTURE_FAILED'):
        execute()
    assert len(attempts) == 1 and not (f.root / 'gold-pilot/stopped.json').exists()


def test_second_capture_witness_alone_forbids_new_retention_bootstrap(idle_empty_case, tmp_path, monkeypatch):
    f = idle_empty_case
    f.executor_root = f.root
    f.controller_root = tmp_path / 'controller'
    f.controller_root.mkdir(mode=0o700)
    path = f.root / 'gold-pilot/closure-reobservation-capture-witness.json'
    path.write_bytes(b'{}')
    path.chmod(0o600)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as owner:
        with pytest.raises(ValueError, match='HISTORY_MISSING'):
            owner.enable_feedback_discovery()


@pytest.mark.parametrize('same_owner', [False, True])
def test_first_capture_witness_only_is_unknown_and_cannot_be_mirrored_or_completed(
        stale_idle, tmp_path, monkeypatch, same_owner):
    f = stale_idle
    request = new_request(f)
    with _native_feedback_controller(f, tmp_path, monkeypatch) as owner:
        owner.enable_feedback_discovery()
        before = owner.store.metadata()
        owner.release()
        interrupt_successor(f, request, monkeypatch, 'capture_first')
        if not same_owner:
            owner.close()
            with pytest.raises(ValueError, match='UNKNOWN_CAPTURE_OUTCOME'):
                with _native_feedback_controller(f, tmp_path, monkeypatch):
                    pass
        else:
            with pytest.raises(ValueError, match='UNKNOWN_CAPTURE_OUTCOME'):
                owner.inspect_reobservation(current_runtime=f.base_runtime, now_ms=f.clock[0])
            assert owner.store.metadata() == before
        assert not (f.root / 'gold-pilot/stopped.json').exists()
