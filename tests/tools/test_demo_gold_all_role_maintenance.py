"""Full capture journal and recovery; virtual manager/custody/worker boundaries."""
from contextlib import contextmanager
import importlib
import json

import pytest

from tests.tools.test_demo_gold_maintenance_window import gold_window as gold_window
from tests.tools.test_demo_gold_research_handoff import request_case as request_case
from tests.tools.test_demo_gold_research_layout import configured as configured
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public
from tests.tools.test_demo_market_partition_install import book as book, env as env
from tests.tools.test_demo_maintenance_processes import proc as proc, process
from tools import demo_maintenance_window as window
from tools import demo_gold_namespace_checkpoint as native


def module():
    return importlib.import_module('tools.demo_gold_all_role_maintenance')


def original_record(f, observed):
    document = json.loads((f['root'] / 'window.json').read_text())
    captured = f['f']['handle']
    return dict(schema='GOLD_MAINTENANCE_ALL_ROLE_CAPTURE_V1',
        window_sha256=document['inhibitor_sha256'], source_commit=document['source_commit'],
        fixed_layout_sha256=document['fixed_layout_sha256'], process_scope_sha256=observed['scope_sha256'],
        cut_request_sha256=native._digest(captured._cut), public_bundle_sha256=native._digest(captured._public),
        phase='RUNNING', worker_exit_verified=False, native_four_role_archives_verified=False,
        capture_result_sha256=None)


def start_record(f):
    window.pause(f['root'])
    observed = window.check_gold_processes(f['root'], verified_layout=f['f']['handle'],
        verified_public=f['f']['configured']['handle'])
    record = original_record(f, observed)
    path = f['root'] / 'gold-all-role-capture.json'
    path.write_text(json.dumps(record))
    path.chmod(0o600)
    return record


def test_running_complete_role_capture_blocks_original_restore_before_service_actions(gold_window, proc):
    f = gold_window
    start_record(f)
    before = list(f['runtime'].calls)
    with pytest.raises(ValueError, match='WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED'):
        window.restore(f['root'])
    assert f['runtime'].calls == before


@pytest.mark.parametrize('field', ['schema', 'window_sha256', 'source_commit', 'worker_exit_verified'])
def test_foreign_or_fabricated_complete_role_journal_cannot_release_inhibition(gold_window, proc, field):
    f = gold_window
    value = start_record(f)
    value[field] = True if field == 'worker_exit_verified' else 'f' * 64
    path = f['root'] / 'gold-all-role-capture.json'
    path.write_text(json.dumps(value))
    before = list(f['runtime'].calls)
    with pytest.raises(ValueError, match='WINDOW_GOLD_ALL_ROLE_RECORD'):
        window.restore(f['root'])
    assert f['runtime'].calls == before


@pytest.mark.parametrize('busy', [False, True])
def test_inflight_recovery_needs_fresh_complete_uid_clearance_and_never_claims_archive(gold_window, proc, busy):
    api = module()
    f = gold_window
    start_record(f)
    if busy:
        process(proc, uid=f['f']['uid'])
        with pytest.raises(ValueError, match='WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED'):
            api.recover(f['root'], verified_layout=f['f']['handle'], verified_public=f['f']['configured']['handle'])
        assert json.loads((f['root'] / 'gold-all-role-capture.json').read_text())['phase'] == 'RUNNING'
        with pytest.raises(ValueError, match='WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED'):
            window.restore(f['root'])
    else:
        result = api.recover(f['root'], verified_layout=f['f']['handle'], verified_public=f['f']['configured']['handle'])
        assert result['status'] == 'GOLD_ALL_ROLE_CAPTURE_ABORTED_CLEANED'
        assert result['worker_exit_verified'] is result['production_data_archived'] is False
        assert result['native_four_role_archives_verified'] is result['whole_set_verified'] is False
        assert json.loads((f['root'] / 'gold-all-role-capture.json').read_text())['phase'] == 'FAILED_CLEANED'
        window.restore(f['root'])


@pytest.fixture
def capture_boundary(gold_window, proc, monkeypatch):
    api = module()
    f = gold_window
    original_prepare = window.prepare_gold
    # The old direct-window tests keep their historical journal. Complete capture
    # prepares its own scope window and seals actual stopped-data metadata.
    f['root'] = f['root'].with_name('complete-scope-window')
    events = []
    faults = {'raise': False, 'busy_after': False, 'lease_exit_raise': False, 'lease_exit_busy': False}
    @contextmanager
    def hold(**kw):
        events.append('LEASE_ENTER')
        try:
            yield type('LeaseBoundary', (), {'recheck': lambda self: events.append('LEASE_RECHECK')})()
        finally:
            events.append('LEASE_EXIT')
            if faults['lease_exit_busy']:
                process(proc, uid=f['f']['uid'])
            if faults['lease_exit_raise']:
                raise ValueError('INJECTED_LEASE_EXIT')
    monkeypatch.setattr(api.leases, 'hold', hold)
    captured = f['f']['handle']
    result = dict(schema='GOLD_ALL_ROLE_NATIVE_CAPTURE_V1', phase='CLOSED',
        operation_sha256=captured._cut['operation_sha256'], source_commit=captured._public['source_commit'],
        fixed_layout_sha256=native._digest(captured.snapshot()), cut_request_sha256=native._digest(captured._cut),
        public_bundle_sha256=native._digest(captured._public),
        roles=['executor', 'issuer', 'controller', 'research'], research_uids=[f['f']['uid']],
        native_four_role_archives_verified=True, worker_exit_verified=True, single_public_cut_bound=True,
        original_file_leases_verified=True, whole_set_verified=False, writer_barrier_verified=False,
        cross_role_business_join_verified=False, cross_namespace_atomic=False,
        economic_authority=False, submission_enabled=False)
    result['captures'] = {name: dict(archive_manifest_sha256='a' * 64, inventory_sha256='b' * 64)
        for name in ('executor', 'issuer', 'controller')}
    result['captures']['research'] = {}
    result['restored_role_joins'] = dict(schema='GOLD_RESTORED_ROLE_JOINS_V1',
        **{key: result[key] for key in ('operation_sha256', 'source_commit', 'fixed_layout_sha256',
            'cut_request_sha256', 'public_bundle_sha256')},
        archives={name: dict(value) for name, value in result['captures'].items() if name != 'research'},
        issuer_executor_join_verified=True, retention_pair_checked=True, whole_set_verified=False,
        writer_barrier_verified=False, cross_role_business_join_verified=False, cash_reconciled=False,
        mutation_permitted=False, economic_authority=False, submission_enabled=False)
    def dispatch(**kw):
        actual = kw['verified_layout']
        result['cut_opening_at_ms'] = actual._cut['opening_at_ms']
        original = dict(fixed_layout_sha256=native._digest(captured.snapshot()),
            cut_request_sha256=native._digest(captured._cut), public_bundle_sha256=native._digest(captured._public))
        current = dict(fixed_layout_sha256=native._digest(actual.snapshot()),
            cut_request_sha256=native._digest(actual._cut), public_bundle_sha256=native._digest(actual._public))
        joined = result.get('restored_role_joins')
        for key, value in current.items():
            if result.get(key) == original[key]:
                result[key] = value
            if type(joined) is dict and joined.get(key) == original[key]:
                joined[key] = value
        record = json.loads((f['root'] / 'gold-all-role-capture.json').read_text())
        assert record['phase'] == 'RUNNING' and record['capture_result_sha256'] is None
        events.append('DISPATCH')
        if faults['busy_after']:
            process(proc, uid=f['f']['uid'])
        if faults['raise']:
            raise ValueError('INJECTED_AFTER_DISPATCH')
        return dict(result)
    monkeypatch.setattr(api.coordinator, 'capture_all', dispatch)
    return dict(api=api, f=f, events=events, result=result, faults=faults, original_prepare=original_prepare)


def invoke(f):
    return f['api'].capture(f['f']['root'], verified_layout=f['f']['f']['handle'],
        verified_public=f['f']['f']['configured']['handle'], source_attachments={})


def test_full_capture_journal_is_durable_before_dispatch_and_completion_follows_exit(capture_boundary):
    f = capture_boundary
    original = dict(f['f']['runtime'].states)
    result = invoke(f)
    assert result['maintenance_window_restored'] is True
    record = json.loads((f['f']['root'] / 'gold-all-role-capture.json').read_text())
    assert record['phase'] == 'COMPLETE'
    assert record['worker_exit_verified'] is record['native_four_role_archives_verified'] is True
    assert record['capture_result_sha256'] == native._digest(f['result'])
    assert f['events'].index('LEASE_ENTER') < f['events'].index('DISPATCH') < f['events'].index('LEASE_EXIT')
    assert f['f']['runtime'].states == original


def test_shutdown_registry_flush_is_captured_after_service_pause(capture_boundary, monkeypatch):
    """Actual original-file rewrite during the explicitly virtual service stop."""
    from pathlib import Path
    f = capture_boundary
    configured = f['f']['f']['configured']
    registry = Path(configured['roots'][0]['root']) / 'registry.db'
    original_bytes = registry.read_bytes()
    before = registry.stat().st_mtime_ns
    factory = window.gold_backend
    observed = []
    after_stop_ms = configured['handle']._snapshot['captured_at_ms'] + 1

    def backend(*args, **kwargs):
        runtime = factory(*args, **kwargs)
        change = runtime.change

        def flush(action, name):
            change(action, name)
            if action == 'stop' and name == 'quantos-research-runner.service' and not observed:
                registry.write_bytes(original_bytes)
                observed.append(registry.stat().st_mtime_ns)
                monkeypatch.setattr(configured['f'].module.supervisor, 'now_ms', lambda: after_stop_ms)
        runtime.change = flush
        return runtime

    monkeypatch.setattr(window, 'gold_backend', backend)
    result = invoke(f)
    assert observed and observed[0] != before
    assert registry.read_bytes() == original_bytes
    assert result['maintenance_window_restored'] is True
    assert result['writer_barrier_verified'] is result['whole_set_verified'] is False
    assert result['cut_opening_at_ms'] == after_stop_ms


def test_failed_dispatch_only_records_cleaned_after_fresh_uid_check(capture_boundary):
    f = capture_boundary
    original = dict(f['f']['runtime'].states)
    f['faults']['raise'] = True
    with pytest.raises(ValueError, match='INJECTED_AFTER_DISPATCH'):
        invoke(f)
    record = json.loads((f['f']['root'] / 'gold-all-role-capture.json').read_text())
    assert record['phase'] == 'FAILED_CLEANED' and record['capture_result_sha256'] is None
    assert record['worker_exit_verified'] is record['native_four_role_archives_verified'] is False
    assert f['f']['runtime'].states == original


def test_residual_uid_after_dispatch_keeps_journal_running_and_services_inhibited(capture_boundary):
    f = capture_boundary
    f['faults']['busy_after'] = True
    with pytest.raises(ValueError, match='WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED'):
        invoke(f)
    assert json.loads((f['f']['root'] / 'gold-all-role-capture.json').read_text())['phase'] == 'RUNNING'
    assert not any(isinstance(row, tuple) and row[0] == 'start' for row in f['f']['runtime'].calls)


@pytest.mark.parametrize('rewrite_before_recovery', [False, True])
def test_new_scope_interrupted_capture_recovers_without_claiming_archive(
        capture_boundary, proc, rewrite_before_recovery):
    import shutil
    f = capture_boundary
    f['faults']['busy_after'] = True
    with pytest.raises(ValueError, match='WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED'):
        invoke(f)
    if rewrite_before_recovery:
        from pathlib import Path
        configured = f['f']['f']['configured']
        registry = Path(configured['roots'][0]['root']) / 'registry.db'
        registry.write_bytes(registry.read_bytes())
    fake_process = proc / '101'
    assert (fake_process / 'status').is_file()
    shutil.rmtree(fake_process)
    result = f['api'].recover(f['f']['root'], verified_layout=f['f']['f']['handle'],
        verified_public=f['f']['f']['configured']['handle'])
    assert result['production_data_archived'] is result['worker_exit_verified'] is False
    assert json.loads((f['f']['root'] / 'gold-all-role-capture.json').read_text())['phase'] == 'FAILED_CLEANED'
    window.restore(f['f']['root'])


@pytest.mark.parametrize('kind', ['full', 'research'])
@pytest.mark.parametrize('busy', [False, True])
def test_lease_context_must_finish_before_success_and_final_uid_check(capture_boundary, monkeypatch, kind, busy):
    f = capture_boundary
    if busy:
        f['faults']['lease_exit_busy'] = True
    else:
        f['faults']['lease_exit_raise'] = True
    if kind == 'research':
        from tools import demo_gold_research_launcher as research
        def partial(**kw):
            return dict(worker_exit_verified=True, original_file_leases_verified=True)
        monkeypatch.setattr(research, 'capture_all', partial)
        def call():
            return window.capture_gold_research(f['f']['root'],
                verified_layout=f['f']['f']['handle'], verified_public=f['f']['f']['configured']['handle'],
                source_attachments={})
        name = 'gold-research-capture.json'
    else:
        def call():
            return invoke(f)
        name = 'gold-all-role-capture.json'
    with pytest.raises(ValueError, match='RECOVERY_REQUIRED' if busy else 'INJECTED_LEASE_EXIT'):
        call()
    record = json.loads((f['f']['root'] / name).read_text())
    assert record['phase'] == ('RUNNING' if busy else 'FAILED_CLEANED')
    assert record['worker_exit_verified'] is False
    if busy:
        assert not any(isinstance(row, tuple) and row[0] == 'start' for row in f['f']['runtime'].calls)


@pytest.mark.parametrize('damage', ['uid', 'cut', 'scope', 'exit', 'missing_join',
    'join_cut', 'join_archive', 'join_authority'])
def test_partial_or_foreign_success_result_is_rejected_and_cannot_mark_complete(capture_boundary, damage):
    f = capture_boundary
    if damage == 'missing_join':
        f['result'].pop('restored_role_joins', None)
    elif damage == 'join_cut':
        f['result']['restored_role_joins']['cut_request_sha256'] = 'f' * 64
    elif damage == 'join_archive':
        f['result']['restored_role_joins']['archives']['issuer']['archive_manifest_sha256'] = 'f' * 64
    elif damage == 'join_authority':
        f['result']['restored_role_joins']['economic_authority'] = True
    elif damage == 'uid':
        f['result']['research_uids'] = []
    elif damage == 'cut':
        f['result']['cut_request_sha256'] = 'f' * 64
    elif damage == 'scope':
        f['result']['schema'] = 'GOLD_RESEARCH_CAPTURE_SET_V1'
    else:
        f['result']['worker_exit_verified'] = False
    with pytest.raises(ValueError, match='WINDOW_GOLD_ALL_ROLE_UNVERIFIED'):
        invoke(f)
    assert json.loads((f['f']['root'] / 'gold-all-role-capture.json').read_text())['phase'] == 'FAILED_CLEANED'


@pytest.mark.parametrize('damage', [None, 'missing', 'cut', 'account', 'source', 'overclaim', 'authority',
    'inactive_episodes', 'inactive_partial', 'partial_empty'])
def test_cash_report_v2_keeps_original_cut_and_cannot_claim_unproved_cash(capture_boundary, damage):
    from copy import deepcopy
    f = capture_boundary
    captured = f['f']['f']['handle']
    result = deepcopy(f['result'])
    joined = result['restored_role_joins']
    joined['schema'] = 'GOLD_RESTORED_ROLE_JOINS_V2'
    cash = dict(schema='GOLD_RESTORED_CASH_COMPONENTS_V1',
        source_commit=captured._public['source_commit'],
        account_ref_sha256=captured._public['account_ref_sha256'], as_of_ms=captured._cut['opening_at_ms'],
        status='NOT_STARTED', episodes=[], partial=[], technical_cash_components_verified=False,
        reported_cash_replay_verified=False, cash_reconciled=False, writer_barrier_verified=False,
        whole_set_verified=False, mutation_permitted=False, submission_enabled=False, economic_authority=False)
    joined['cash_components'] = cash
    if damage == 'missing':
        joined.pop('cash_components')
    elif damage == 'cut':
        cash['as_of_ms'] += 1
    elif damage == 'account':
        cash['account_ref_sha256'] = 'f' * 64
    elif damage == 'source':
        cash['source_commit'] = 'f' * 40
    elif damage == 'overclaim':
        cash['reported_cash_replay_verified'] = True
    elif damage == 'authority':
        cash['economic_authority'] = True
    elif damage == 'inactive_episodes':
        cash['episodes'] = [dict(bundle_sha256='a' * 64, cash=None, reported_capture=None, feedback=None)]
    elif damage == 'inactive_partial':
        cash['partial'] = [dict(status='EXECUTION_CONTEXT_REQUIRED')]
    elif damage == 'partial_empty':
        cash['status'] = 'PARTIAL'
    if damage is None:
        module()._capture_result(result, captured)
    else:
        with pytest.raises(ValueError, match='CASH_REPORT'):
            module()._capture_result(result, captured)


@pytest.mark.parametrize('damage', [None, 'missing', 'extra', 'hash', 'unbound', 'continuity'])
def test_v3_research_restorations_must_bind_every_original_uid(capture_boundary, damage):
    from copy import deepcopy
    f = capture_boundary
    captured = f['f']['f']['handle']
    result = deepcopy(f['result'])
    joined = result['restored_role_joins']
    joined.update(schema='GOLD_RESTORED_ROLE_JOINS_V3', research_restore_continuity_verified=True,
        research_archives={str(uid): dict(archive_manifest_sha256='c' * 64, inventory_sha256='d' * 64)
            for uid in captured._public['registry']['research_uids']})
    result['captures']['research'] = deepcopy(joined['research_archives'])
    joined['cash_components'] = dict(schema='GOLD_RESTORED_CASH_COMPONENTS_V1',
        source_commit=captured._public['source_commit'], account_ref_sha256=captured._public['account_ref_sha256'],
        as_of_ms=captured._cut['opening_at_ms'], status='NOT_STARTED', episodes=[], partial=[],
        technical_cash_components_verified=False, reported_cash_replay_verified=False,
        cash_reconciled=False, writer_barrier_verified=False, whole_set_verified=False,
        mutation_permitted=False, submission_enabled=False, economic_authority=False)
    uid = next(iter(joined['research_archives']))
    if damage == 'missing':
        joined['research_archives'].pop(uid)
    elif damage == 'extra':
        joined['research_archives']['2147483646'] = dict(joined['research_archives'][uid])
    elif damage == 'hash':
        joined['research_archives'][uid]['inventory_sha256'] = 'f' * 64
    elif damage == 'unbound':
        result['captures']['research'].clear()
    elif damage == 'continuity':
        joined['research_restore_continuity_verified'] = False
    if damage is None:
        module()._capture_result(result, captured)
    else:
        with pytest.raises(ValueError, match='RESEARCH_RESTORE_BINDING'):
            module()._capture_result(result, captured)
