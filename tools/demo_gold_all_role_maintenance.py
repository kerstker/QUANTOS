"""Original maintenance window around complete native role capture.

Real service operations require the root operator. A durable RUNNING record
blocks restoration until all original identities are freshly observed drained.
Cleanup is never archive success, writer exclusion or production restoration.
"""
import json
from pathlib import Path
import re

from tools import demo_gold_all_role_capture as coordinator
from tools import demo_gold_file_leases as leases
from tools import demo_maintenance_window as window

native = coordinator.native
roster = coordinator.roster
require = window.require
NAME = 'gold-all-role-capture.json'
SCHEMA = 'GOLD_MAINTENANCE_ALL_ROLE_CAPTURE_V1'
FIELDS = set(('schema window_sha256 source_commit fixed_layout_sha256 process_scope_sha256 '
    'cut_request_sha256 public_bundle_sha256 phase worker_exit_verified '
    'native_four_role_archives_verified capture_result_sha256').split())


def _record(root, document):
    path = Path(root) / NAME
    if not path.exists() and not path.is_symlink():
        return None
    value = window.decode(window.read(path, 4096))
    require(type(value) is dict and set(value) == FIELDS and value['schema'] == SCHEMA
        and document['schema'] in window.GOLD_SCHEMAS
        and value['window_sha256'] == document['inhibitor_sha256']
        and value['source_commit'] == document['source_commit']
        and value['fixed_layout_sha256'] == document['fixed_layout_sha256']
        and value['phase'] in ('RUNNING', 'COMPLETE', 'FAILED_CLEANED')
        and value['worker_exit_verified'] is (value['phase'] == 'COMPLETE')
        and value['native_four_role_archives_verified'] is (value['phase'] == 'COMPLETE'),
        'WINDOW_GOLD_ALL_ROLE_RECORD')
    for name in ('process_scope_sha256', 'cut_request_sha256', 'public_bundle_sha256'):
        require(type(value[name]) is str and re.fullmatch('[a-f0-9]{64}', value[name]),
            'WINDOW_GOLD_ALL_ROLE_RECORD')
    digest = value['capture_result_sha256']
    require((value['phase'] == 'COMPLETE' and type(digest) is str and re.fullmatch('[a-f0-9]{64}', digest))
        or (value['phase'] != 'COMPLETE' and digest is None), 'WINDOW_GOLD_ALL_ROLE_RECORD')
    return value


def restore_guard(root, document):
    record = _record(root, document)
    require(record is None or record['phase'] != 'RUNNING', 'WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED')


def _bind(record, captured):
    require(record['source_commit'] == captured._public['source_commit']
        and record['fixed_layout_sha256'] == native._digest(captured.snapshot())
        and record['cut_request_sha256'] == native._digest(captured._cut)
        and record['public_bundle_sha256'] == native._digest(captured._public), 'WINDOW_GOLD_ALL_ROLE_CONTEXT')


def _capture_result(result, captured):
    require(type(result) is dict and result.get('schema') == 'GOLD_ALL_ROLE_NATIVE_CAPTURE_V1'
        and result.get('phase') == 'CLOSED'
        and result.get('roles') == ['executor', 'issuer', 'controller', 'research']
        and result.get('research_uids') == sorted(captured._public['registry']['research_uids'])
        and result.get('operation_sha256') == captured._cut['operation_sha256']
        and result.get('source_commit') == captured._public['source_commit']
        and result.get('fixed_layout_sha256') == native._digest(captured.snapshot())
        and result.get('cut_request_sha256') == native._digest(captured._cut)
        and result.get('public_bundle_sha256') == native._digest(captured._public)
        and all(result.get(key) is True for key in ('native_four_role_archives_verified',
            'worker_exit_verified', 'single_public_cut_bound', 'original_file_leases_verified'))
        and all(result.get(key) is False for key in ('writer_barrier_verified', 'whole_set_verified',
            'cross_role_business_join_verified', 'cross_namespace_atomic', 'economic_authority',
            'submission_enabled')), 'WINDOW_GOLD_ALL_ROLE_UNVERIFIED')
    joined, captures = result.get('restored_role_joins'), result.get('captures')
    require(type(joined) is dict and joined.get('schema') in ('GOLD_RESTORED_ROLE_JOINS_V1',
            'GOLD_RESTORED_ROLE_JOINS_V2', 'GOLD_RESTORED_ROLE_JOINS_V3')
        and all(joined.get(key) == result[key] for key in ('operation_sha256', 'source_commit',
            'fixed_layout_sha256', 'cut_request_sha256', 'public_bundle_sha256'))
        and all(joined.get(key) is True for key in ('issuer_executor_join_verified', 'retention_pair_checked'))
        and all(joined.get(key) is False for key in ('whole_set_verified', 'writer_barrier_verified',
            'cross_role_business_join_verified', 'cash_reconciled', 'mutation_permitted',
            'economic_authority', 'submission_enabled'))
        and type(captures) is dict and type(joined.get('archives')) is dict
        and set(joined['archives']) == {'executor', 'issuer', 'controller'}
        and all(type(captures.get(name)) is dict and type(binding) is dict
            and set(binding) == {'archive_manifest_sha256', 'inventory_sha256'}
            and all(binding[key] == captures[name].get(key) for key in binding)
            for name, binding in joined['archives'].items()), 'WINDOW_GOLD_ALL_ROLE_UNVERIFIED')
    if joined['schema'] in ('GOLD_RESTORED_ROLE_JOINS_V2', 'GOLD_RESTORED_ROLE_JOINS_V3'):
        coordinator.joins.require_cash_report(joined.get('cash_components'),
            public=captured._public, as_of_ms=captured._cut['opening_at_ms'])
    if joined['schema'] == 'GOLD_RESTORED_ROLE_JOINS_V3':
        originals = joined.get('research_archives')
        expected = {str(uid) for uid in captured._public['registry']['research_uids']}
        require(joined.get('research_restore_continuity_verified') is True
            and type(originals) is dict and set(originals) == expected
            and type(captures.get('research')) is dict and set(captures['research']) == expected
            and all(type(value) is dict and type(captures['research'][uid]) is dict
                and set(value) == {'archive_manifest_sha256', 'inventory_sha256'}
                and all(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}', value[key])
                    and value[key] == captures['research'][uid].get(key) for key in value)
                for uid, value in originals.items()), 'WINDOW_GOLD_RESEARCH_RESTORE_BINDING')


def _mark(root, observation, captured, phase, result=None):
    with window._locked(root) as (root, document):
        require(document['schema'] in window.GOLD_SCHEMAS and document['phase'] == 'PAUSED',
            'WINDOW_GOLD_PAUSED_REQUIRED')
        require(observation['schema'] == 'GOLD_PROCESS_METADATA_OBSERVATION_V1'
            and observation['status'] == 'NO_SCOPED_PROCESS_OBSERVED'
            and observation['window_sha256'] == document['inhibitor_sha256']
            and observation['source_commit'] == document['source_commit']
            and observation['fixed_layout_sha256'] == document['fixed_layout_sha256'],
            'WINDOW_GOLD_ALL_ROLE_CONTEXT')
        require(phase in ('RUNNING', 'COMPLETE', 'FAILED_CLEANED'), 'WINDOW_GOLD_ALL_ROLE_PHASE')
        if phase == 'COMPLETE':
            _capture_result(result, captured)
        else:
            require(result is None, 'WINDOW_GOLD_ALL_ROLE_PHASE')
        record = dict(schema=SCHEMA, window_sha256=document['inhibitor_sha256'],
            source_commit=document['source_commit'], fixed_layout_sha256=document['fixed_layout_sha256'],
            process_scope_sha256=observation['scope_sha256'],
            cut_request_sha256=native._digest(captured._cut), public_bundle_sha256=native._digest(captured._public),
            phase=phase, worker_exit_verified=phase == 'COMPLETE',
            native_four_role_archives_verified=phase == 'COMPLETE',
            capture_result_sha256=native._digest(result) if phase == 'COMPLETE' else None)
        _bind(record, captured)
        previous = _record(root, document)
        mutable = {'phase', 'worker_exit_verified', 'native_four_role_archives_verified', 'capture_result_sha256'}
        if previous is None:
            require(phase == 'RUNNING', 'WINDOW_GOLD_ALL_ROLE_PHASE')
            partial = window._gold_capture_record(root, document)
            require(partial is None or partial['phase'] != 'RUNNING', 'WINDOW_GOLD_CAPTURE_RECOVERY_REQUIRED')
            window.write_new(root / NAME, json.dumps(record, sort_keys=True).encode())
        else:
            require(previous['phase'] == 'RUNNING' and phase in ('COMPLETE', 'FAILED_CLEANED')
                and {k: v for k, v in previous.items() if k not in mutable}
                == {k: v for k, v in record.items() if k not in mutable}, 'WINDOW_GOLD_ALL_ROLE_PHASE')
            window.services._save(root / NAME, record)
        _record(root, document)


def _installed_scope(verified_layout, verified_scope, verified_public):
    require((verified_layout is None) != (verified_scope is None), 'WINDOW_GOLD_SCOPE_REQUIRED')
    return (roster.require_scope(verified_scope, verified_public=verified_public)
        if verified_scope is not None else roster.installed_scope(verified_layout, verified_public=verified_public))


def capture(root, *, verified_public, source_attachments, verified_layout=None, verified_scope=None):
    """Run the complete original cut in the existing exclusive operator window."""
    from tools.demo_maintenance_operator import exclusive
    scope = _installed_scope(verified_layout, verified_scope, verified_public)
    scope_arguments = dict(verified_scope=scope, verified_public=verified_public)
    with exclusive():
        window.prepare_gold_scope(root, **scope_arguments)
        dispatched = False
        try:
            window.pause(root)
            captured, stopped_public = window.seal_gold_scope_layout(root, **scope_arguments)
            arguments = dict(verified_layout=captured, verified_public=stopped_public)
            before = window.check_gold_processes(root, **arguments)
            require(before['status'] == 'NO_SCOPED_PROCESS_OBSERVED', 'WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED')
            with leases.hold(**arguments) as held:
                _mark(root, before, captured, 'RUNNING')
                dispatched = True
                result = coordinator.capture_all(**arguments, source_attachments=source_attachments, file_leases=held)
                after = window.check_gold_processes(root, **arguments)
                require(after['status'] == 'NO_SCOPED_PROCESS_OBSERVED', 'WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED')
                held.recheck()
                captured.recheck()
                _capture_result(result, captured)
            # The lease context can fail during its final recheck/release. Keep
            # RUNNING until it returns, then freshly check every original UID.
            after = window.check_gold_processes(root, **arguments)
            require(after['status'] == 'NO_SCOPED_PROCESS_OBSERVED', 'WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED')
            captured.recheck()
            _mark(root, after, captured, 'COMPLETE', result)
        except BaseException:
            if dispatched:
                with window._locked(root) as (locked, document):
                    pending = _record(locked, document)
                if pending['phase'] == 'RUNNING':
                    observed = window.check_gold_processes(root, **arguments)
                    require(observed['status'] == 'NO_SCOPED_PROCESS_OBSERVED', 'WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED')
                    _mark(root, observed, captured, 'FAILED_CLEANED')
            raise
        finally:
            window.restore(root)
        return dict(result, data_snapshot_after_service_pause=True,
            cut_opening_at_ms=captured._cut['opening_at_ms'],
            installed_scope_sha256=native._digest(scope.snapshot()),
            maintenance_window_restored=True, maintenance_window_sha256=after['window_sha256'],
            process_scope_sha256=after['scope_sha256'])


def recover(root, *, verified_public, verified_layout=None, verified_scope=None):
    """Clean a drained interrupted capture without certifying archive completion."""
    from tools.demo_maintenance_operator import exclusive
    with exclusive():
        with window._locked(root) as (locked, document):
            require(document['schema'] in window.GOLD_SCHEMAS, 'WINDOW_GOLD_PAUSED_REQUIRED')
            scoped = document['schema'] == window.GOLD_SCOPE_SCHEMA
        if scoped:
            scope = _installed_scope(verified_layout, verified_scope, verified_public)
            arguments = dict(verified_scope=scope, verified_public=verified_public)
            observed = window.check_gold_scope_processes(root, **arguments)
            require(observed['status'] == 'NO_SCOPED_PROCESS_OBSERVED', 'WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED')
            with window._locked(root) as (locked, document):
                scope.recheck()
                require(document['schema'] == window.GOLD_SCOPE_SCHEMA and document['phase'] == 'PAUSED'
                    and document['fixed_scope_sha256'] == native._digest(scope.snapshot()),
                    'WINDOW_GOLD_SCOPE_CONTEXT')
                record = _record(locked, document)
                require(record is not None and record['phase'] == 'RUNNING', 'WINDOW_GOLD_ALL_ROLE_PHASE')
                record.update(phase='FAILED_CLEANED', worker_exit_verified=False,
                    native_four_role_archives_verified=False, capture_result_sha256=None)
                window.services._save(locked / NAME, record)
                _record(locked, document)
            return _aborted_result()
        require(verified_scope is None, 'WINDOW_GOLD_SCOPE_REQUIRED')
        captured = roster.require_captured(verified_layout, verified_public=verified_public)
        arguments = dict(verified_layout=captured, verified_public=verified_public)
        with window._locked(root) as (locked, document):
            record = _record(locked, document)
            require(record is not None and record['phase'] == 'RUNNING', 'WINDOW_GOLD_ALL_ROLE_PHASE')
            _bind(record, captured)
        window.pause(root)
        observed = window.check_gold_processes(root, **arguments)
        require(observed['status'] == 'NO_SCOPED_PROCESS_OBSERVED', 'WINDOW_GOLD_ALL_ROLE_RECOVERY_REQUIRED')
        _mark(root, observed, captured, 'FAILED_CLEANED')
        return _aborted_result()


def _aborted_result():
    return dict(status='GOLD_ALL_ROLE_CAPTURE_ABORTED_CLEANED', worker_exit_verified=False,
        native_four_role_archives_verified=False, production_data_archived=False,
        writer_barrier_verified=False, whole_set_verified=False, cross_role_business_join_verified=False,
        economic_authority=False, submission_enabled=False)
