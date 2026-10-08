"""Local sudo/TTY entrypoint for reviewed feedback configuration and recovery.

Only the four fixed feedback files and the explicitly shown service active set
are installed. No archive, source installation, broker or order approval is given.
An interrupted operation retains its original source, human approval and history.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import select
import signal
import sys
import time

from scripts.render_demo_feedback_units import render
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_feedback_upgrade as materials_contract
from tools import demo_feedback_upgrade_files as files_tx
from tools import demo_feedback_upgrade_transaction as transaction
from tools import demo_maintenance_operator as maintenance
from tools import demo_maintenance_services as services
from tools import demo_maintenance_window as window
from tools import demo_supervisor as supervisor
from tools.demo_namespace_checkpoint import canonical
from tools.demo_retention_transaction import directory, read, require, sync, write_new
from tools.gold_strategy_policy_install import local_operator

SCHEMA = 'DEMO_FEEDBACK_UPGRADE_OPERATOR_V1'
ROOT_PARENT = Path('/var/tmp')
MAXIMUM = 1024 * 1024


def _now():
    return time.time_ns() // 1000000


def _admit(source_commit):
    operator = local_operator()
    maintenance._admit(source_commit)
    return operator


def _base(operation):
    require(type(operation) is str and re.fullmatch('[a-f0-9]{64}', operation),
            'FEEDBACK_OPERATOR_OPERATION')
    path = ROOT_PARENT / ('quantos-feedback-upgrade-' + operation)
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'FEEDBACK_OPERATOR_PATH')
    return path


def _files(files, source_commit):
    # These absent preimages are only a pure format check, never a host claim.
    materials_contract.review(files=files, preimages=dict.fromkeys(materials_contract.TARGETS),
                              release_commit=source_commit)
    return files


def capture_consumer(*, source_commit):
    from tools.demo_feedback_upgrade_consumer_capture import capture
    return capture(source_commit=source_commit)


def _active(journal, activate):
    require(type(activate) is bool, 'FEEDBACK_OPERATOR_ACTIVATION')
    result = {name for name, row in journal['units'].items() if row['active'] == 'active'}
    if activate:
        result.add(services.FEEDBACK_UNITS[-1])
    return sorted(result)


def _summary(operation, source_commit):
    return {'schema': SCHEMA, 'operation_id': operation, 'source_commit': source_commit,
            'status': 'RECOVERY_REQUIRED', 'review_sha256': None, 'services_restored': False,
            'submission_enabled': False, 'economic_authority': False}


def _save(base, result):
    write_new(base / ('result-' + secrets.token_hex(8) + '.json'), canonical(result))
    return result


def _restore_original(root):
    with maintenance.exclusive():
        window.restore(root)


def _approval(review, operator):
    require(local_operator() == operator, 'FEEDBACK_OPERATOR_IDENTITY_CHANGED')
    return {'schema': transaction.APPROVAL, 'purpose': transaction.PURPOSE,
            'review_sha256': review['review_sha256'],
            'operation_sha256': review['operation_sha256'], 'source_commit': review['source_commit'],
            'operator_id': operator, 'approved_at_ms': _now(),
            'service_activation_authorized': True, 'submission_enabled': False,
            'economic_authority': False}


def start(*, source_commit, files, activate_feedback_timer, confirm):
    operator = _admit(source_commit)
    _files(files, source_commit)
    require(type(activate_feedback_timer) is bool, 'FEEDBACK_OPERATOR_ACTIVATION')
    operation = secrets.token_hex(32)
    base = _base(operation)
    base.mkdir(mode=0o700)
    sync(base.parent)
    run = {'schema': SCHEMA, 'purpose': transaction.PURPOSE, 'operation_sha256': operation,
           'source_commit': source_commit, 'operator_id': operator,
           'activate_feedback_timer': activate_feedback_timer,
           'files_base64': {name: base64.b64encode(raw).decode() for name, raw in files.items()}}
    write_new(base / 'run.json', canonical(run))
    root = base / 'window'
    result = _summary(operation, source_commit)
    print(json.dumps(result | {'status': 'PREPARING_WINDOW'}, sort_keys=True), flush=True)
    try:
        with maintenance.exclusive():
            window.prepare(root, operation)
            window.pause(root)
        with maintenance.exclusive(), supervisor.controller_lease(maintenance=True):
            captured = files_tx.capture()
            consumer_snapshot = capture_consumer(source_commit=source_commit)
            active = _active(services.inspect_journal(root / 'services.json'), activate_feedback_timer)
        review = transaction.prepare(root, source_commit=source_commit,
            operation_sha256=operation, files=files, captured=captured,
            approved_active=active, consumer_snapshot=consumer_snapshot)
        result['review_sha256'] = review['review_sha256']
        write_new(base / 'prepared.json', canonical(review))
        if confirm(review, operator) is not True:
            transaction.cancel_prepared(root, expected_digest=review['review_sha256'])
            _restore_original(root)
            require(_canceled_state(root, transaction._load(root / transaction.AREA / 'review.json')) == 'RESTORED',
                    'FEEDBACK_OPERATOR_RESTORE_INCOMPLETE')
            result.update(status='UPGRADE_DECLINED', services_restored=True)
        else:
            approval = _approval(review, operator)
            write_new(base / 'approval.json', canonical(approval))
            receipt = transaction.advance(root, expected_digest=review['review_sha256'],
                                          action='apply', approval=approval)
            require(receipt['status'] == 'COMPLETED', 'FEEDBACK_OPERATOR_INCOMPLETE')
            result.update(status='COMPLETED', services_restored=True)
    except (Exception, KeyboardInterrupt):
        # No generic restore after an upgrade marker: the old service journal
        # cannot represent a partially published new configuration.
        area = root / transaction.AREA
        if not area.exists() and not area.is_symlink() and (root / 'window.json').exists():
            try:
                _restore_original(root)
                _unprepared_restored(root, operation)
                result.update(status='PREPARATION_FAILED', services_restored=True)
            except (Exception, KeyboardInterrupt):
                pass
    return _save(base, result)


def _load_run(base, *, source_commit, operation, operator):
    directory(base)
    value = decode(read(base / 'run.json', MAXIMUM))
    require(type(value) is dict and set(value) == {'schema', 'purpose', 'operation_sha256',
            'source_commit', 'operator_id', 'activate_feedback_timer', 'files_base64'} and
            value['schema'] == SCHEMA and value['purpose'] == transaction.PURPOSE and
            value['operation_sha256'] == operation and value['source_commit'] == source_commit and
            value['operator_id'] == operator and type(value['activate_feedback_timer']) is bool and
            type(value['files_base64']) is dict and
            set(value['files_base64']) == set(materials_contract.TARGETS), 'FEEDBACK_OPERATOR_RUN_BINDING')
    files = {name: base64.b64decode(body, validate=True) for name, body in value['files_base64'].items()}
    _files(files, source_commit)
    return value


def _review_for_recovery(root, run):
    review = transaction._load(root / transaction.AREA / 'review.json')
    require(review.get('schema') == transaction.SCHEMA and
            review.get('source_commit') == run['source_commit'] and
            review.get('operation_sha256') == run['operation_sha256'] and
            review.get('files_base64') == run['files_base64'] and
            review.get('approved_active') == _active(review['original_journal'], run['activate_feedback_timer']),
            'FEEDBACK_OPERATOR_REVIEW_BINDING')
    identity = transaction._digest(review)
    prepared = root.parent / 'prepared.json'
    if prepared.exists() or prepared.is_symlink():
        require(decode(read(prepared, MAXIMUM)).get('review_sha256') == identity,
                'FEEDBACK_OPERATOR_REVIEW_BINDING')
    return review, identity


def _restored_services(root, document):
    """Check the current manager state while the caller holds all window locks."""
    journal = services.inspect_journal(root / 'services.json')
    require(document['phase'] == 'RESTORED' and journal['phase'] == 'RESTORED',
            'FEEDBACK_OPERATOR_RESTORE_INCOMPLETE')
    backend = window.backend()
    observed = services._observations(journal, backend)
    for name, original in journal['units'].items():
        current = observed[name]['active']
        if original['active'] == 'active':
            require(current == 'active' or name.endswith('.service') and
                    name != 'quantos-demo-executor-stream.service' and current == 'inactive',
                    'FEEDBACK_OPERATOR_RESTORE_INCOMPLETE')
        else:
            require(current in {'inactive', 'failed'} and backend.idle(name),
                    'FEEDBACK_OPERATOR_UNEXPECTED_ACTIVITY')


def _unprepared_restored(root, operation):
    with maintenance.exclusive(), supervisor.controller_lease(maintenance=True), window._locked(root) as (_, document):
        area = root / transaction.AREA
        approval = root.parent / 'approval.json'
        require(not area.exists() and not area.is_symlink() and
                not approval.exists() and not approval.is_symlink() and
                document['operation_sha256'] == operation, 'FEEDBACK_OPERATOR_WINDOW_BINDING')
        _restored_services(root, document)


def _canceled_state(root, review):
    """Re-enter ordinary restoration only after a proven unchanged cancellation."""
    area = root / transaction.AREA
    if not (area / 'state.json').exists() and not (area / 'state.json').is_symlink():
        return None  # Native cancel must establish whether preparation is complete.
    with maintenance.exclusive(), supervisor.controller_lease(maintenance=True), window._locked(root) as (_, document):
        require(transaction._load(area / 'review.json') == review, 'FEEDBACK_OPERATOR_REVIEW_BINDING')
        if transaction._state(area, review) != 'CANCELED_UNCHANGED':
            return None
        require(not (area / 'approval.json').exists(), 'FEEDBACK_OPERATOR_CANCEL_APPROVED')
        require(document['phase'] in {'PAUSED', 'RELEASING', 'RESTORING', 'RESTORED'} and
                document == review['window'] | {'phase': document['phase']},
                'FEEDBACK_OPERATOR_WINDOW_BINDING')
        journal = services.inspect_journal(root / 'services.json')
        require(journal['phase'] in {'PAUSED', 'RESTORING', 'RESTORED'} and
                journal == review['original_journal'] | {'phase': journal['phase']},
                'FEEDBACK_OPERATOR_JOURNAL_BINDING')
        transaction.restore_guard(root)  # Full four-target BEFORE custody, no approval.
        if document['phase'] != 'RESTORED':
            return 'CANCELED'
        _restored_services(root, document)
        return 'RESTORED'


def recover(*, source_commit, operation, action):
    require(action in {'apply', 'rollback'}, 'FEEDBACK_OPERATOR_ACTION')
    operator = _admit(source_commit)
    base = _base(operation)
    run = _load_run(base, source_commit=source_commit, operation=operation, operator=operator)
    root = base / 'window'
    result = _summary(operation, source_commit)
    area = root / transaction.AREA
    if not area.exists() and not area.is_symlink():
        require(not (base / 'approval.json').exists(), 'FEEDBACK_OPERATOR_REVIEW_MISSING')
        try:
            _restore_original(root)
            _unprepared_restored(root, operation)
            result.update(status='CANCELED_UNCHANGED', services_restored=True)
        except (Exception, KeyboardInterrupt):
            pass
        return _save(base, result)
    review, identity = _review_for_recovery(root, run)
    result['review_sha256'] = identity
    approval_path = base / 'approval.json'
    approval = None
    if approval_path.exists() or approval_path.is_symlink():
        approval = decode(read(approval_path, MAXIMUM))
        require(approval.get('review_sha256') == identity and approval.get('operator_id') == operator,
                'FEEDBACK_OPERATOR_APPROVAL_BINDING')
        transaction._approval(approval, review)
    try:
        if approval is None:
            canceled = _canceled_state(root, review)
            if canceled is None:
                transaction.cancel_prepared(root, expected_digest=identity)
            if canceled != 'RESTORED':
                _restore_original(root)
            require(_canceled_state(root, review) == 'RESTORED',
                    'FEEDBACK_OPERATOR_RESTORE_INCOMPLETE')
            result.update(status='CANCELED_UNCHANGED', services_restored=True)
        else:
            receipt = transaction.advance(root, expected_digest=identity, action=action, approval=approval)
            expected = 'COMPLETED' if action == 'apply' else 'ROLLED_BACK'
            require(receipt['status'] == expected, 'FEEDBACK_OPERATOR_INCOMPLETE')
            result.update(status=expected, services_restored=True)
    except (Exception, KeyboardInterrupt):
        pass
    return _save(base, result)


def confirm_upgrade(review, operator):
    require(local_operator() == operator, 'FEEDBACK_OPERATOR_IDENTITY_CHANGED')
    visible = {key: review[key] for key in ('source_commit', 'operation_sha256', 'review_sha256',
               'old_scope', 'new_scope', 'original_active', 'approved_active', 'targets', 'recovery_actions')}
    print(json.dumps(visible, ensure_ascii=False, indent=2), flush=True)
    expected = 'UPGRADE FEEDBACK ' + review['review_sha256']
    print('仅更新所示四份反馈配置并恢复所示服务；同一操作可显式续做或恢复原配置和原活动集合。', flush=True)
    print('确认请输入 ' + expected + '（等待上限180秒）：', flush=True)
    if not select.select([sys.stdin], [], [], 180)[0]:
        return False
    return sys.stdin.readline(160).strip() == expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--upgrade-reviewed', action='store_true')
    mode.add_argument('--resume', metavar='OPERATION_ID')
    mode.add_argument('--rollback', metavar='OPERATION_ID')
    parser.add_argument('--research-state-root')
    for name in ('executor-uid', 'research-uid', 'research-gid'):
        parser.add_argument('--' + name, type=int)
    parser.add_argument('--activate-feedback-timer', action='store_true')
    args = parser.parse_args(argv)
    configuration = [args.research_state_root, args.executor_uid, args.research_uid, args.research_gid]
    if args.upgrade_reviewed and any(value is None for value in configuration):
        parser.error('upgrade requires the publication root and all three UIDs/GID')
    if not args.upgrade_reviewed and (any(value is not None for value in configuration) or args.activate_feedback_timer):
        parser.error('recovery uses only the original saved configuration')
    previous_umask = os.umask(0o077)
    handlers = {}

    def interrupted(signum, frame):
        raise KeyboardInterrupt()

    try:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            handlers[sig] = signal.signal(sig, interrupted)
        if args.upgrade_reviewed:
            files = {name: raw.encode() for name, raw in render(release_commit=args.source_commit,
                research_state_root=args.research_state_root, executor_uid=args.executor_uid,
                research_uid=args.research_uid, research_gid=args.research_gid).items()}
            result = start(source_commit=args.source_commit, files=files,
                activate_feedback_timer=args.activate_feedback_timer, confirm=confirm_upgrade)
        else:
            result = recover(source_commit=args.source_commit, operation=args.resume or args.rollback,
                             action='apply' if args.resume else 'rollback')
        public = maintenance._public_result(result)
        print(json.dumps(result | {'summary_path': public}, sort_keys=True), flush=True)
        return 0 if result['services_restored'] and result['status'] in {
            'COMPLETED', 'ROLLED_BACK', 'CANCELED_UNCHANGED', 'UPGRADE_DECLINED'} else 3
    except (Exception, KeyboardInterrupt):
        print(json.dumps({'schema': SCHEMA, 'status': 'OPERATOR_INCOMPLETE',
                          'submission_enabled': False, 'economic_authority': False}), flush=True)
        return 3
    finally:
        for sig, old in handlers.items():
            signal.signal(sig, old)
        os.umask(previous_umask)


if __name__ == '__main__':
    raise SystemExit(main())
