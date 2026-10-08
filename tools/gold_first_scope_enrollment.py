"""Local, independently confirmed first Gold Scope from original BTC records.

No functional Gold policy, task, key or pilot is installed by this reader. The
original first-market worker proves the untouched partition and original book.
"""
import argparse
import hashlib
import json
import os
from contextlib import ExitStack

from live.execution.demo_issuer_policy import require, historical_policy_binding
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.validation.demo_submission import validate_registry, registry_sha256
from tools import demo_operating_bundle as installer, demo_operator_setup as setup
from tools import demo_successor_setup as successor, demo_supervisor as supervisor
from tools import gold_strategy_budget_enrollment as strategy
from tools.gold_strategy_policy_install import local_operator


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def verify_executor_issuer_history(rows, *, state, account_ref):
    """Rows must already be replayed by native IssuerStore.snapshot().

    A current valid empty issuer cannot explain an executor's old signed grant.
    This cannot recover evidence deleted from every independently retained copy.
    Unsigned reservations remain in the snapshot; they need no executor match.
    """
    reserved = {row['proposal_sha256']: row['payload'] for row in rows if row['kind'] == 'RESERVED'}
    signed = {}
    for row in rows:
        if row['kind'] != 'SIGNED':
            continue
        original = reserved.get(row['proposal_sha256'])
        require(original is not None, 'FIRST_SCOPE_ISSUER_SIGNATURE_WITHOUT_RESERVATION')
        if original['plan']['purpose'] == 'submission':
            identity = row['payload']['envelope']['payload']['approval_id']
            require(identity not in signed, 'FIRST_SCOPE_ISSUER_DUPLICATE_APPROVAL')
            signed[identity] = original
    require(bool(state['reservations']), 'FIRST_SCOPE_ISSUER_ORIGINAL_EXECUTION_REQUIRED')
    for row in state['reservations']:
        original = signed.get(row['approval_id'])
        require(original is not None and row['account_ref_sha256'] == account_ref
                and original['policy']['account_ref_sha256'] == account_ref,
                'FIRST_SCOPE_ISSUER_EXECUTION_UNEXPLAINED')
        plan = original['plan']; payload = row['payload']
        require(plan['context'] == payload['context'] and plan['orders'] == payload['orders']
                and all(order['symbol'] == 'BTCUSDC' for order in plan['orders']),
                'FIRST_SCOPE_ISSUER_EXECUTION_BINDING')


def require_issuer_history(expected, registry):
    from live.execution.demo_gold_first_scope_history import history_anchor
    from tools.gold_future_budget_materials import issuer_rows
    rows = issuer_rows(registry)
    require(history_anchor(rows) == expected, 'FIRST_SCOPE_ISSUER_HISTORY_CHANGED')
    return rows


def capture_issuer_history(registry, *, report, account_ref):
    from live.execution.demo_gold_first_scope_history import history_anchor
    from live.execution.demo_pre_submit import _state
    from tools.gold_future_budget_materials import issuer_rows
    from tools.gold_delegated_proof import _read_only_stores
    from tools.demo_resident_recovery import _check_path
    root = installer.EXECUTOR; uid = registry['executor_uid']
    require(root.is_absolute() and root.resolve() == root, 'FIRST_SCOPE_EXECUTOR_ROOT')
    _check_path(root, uid, directory=True)
    rows = issuer_rows(registry)
    with ExitStack() as stack:
        stores = _read_only_stores(stack, root, uid)
        state = _state(**stores)
        require(setup.digest(state) == report['legacy_state_sha256'], 'FIRST_SCOPE_EXECUTOR_REVIEW_CHANGED')
        verify_executor_issuer_history(rows, state=state, account_ref=account_ref)
        require(_state(**stores) == state and issuer_rows(registry) == rows,
                'FIRST_SCOPE_ISSUER_OR_EXECUTOR_CHANGED')
    _check_path(root, uid, directory=True)
    return history_anchor(rows)


def _original_inputs(source_commit):
    """Read root-owned originals without requiring an old policy's new source."""
    require(os.geteuid() == 0, 'FIRST_SCOPE_ROOT_REQUIRED')
    successor.verify_source(source_commit)
    supervisor._root_private_directory(supervisor.STATE)
    watched = {}

    def read(path):
        body = supervisor._read_root_owned_json(path)
        watched[path] = body
        return decode(body)

    registry = validate_registry(read(installer.REGISTRY))
    require(read(supervisor.ISSUER_REGISTRY) == registry, 'FIRST_SCOPE_REGISTRY_MIRROR')
    policy = read(supervisor.ISSUER_POLICY)
    profile = read(installer.CONFIG / 'profile.json')
    historical_policy_binding(policy, registry)
    require(policy['schema'] == 'DEMO_ISSUER_POLICY_V1' and policy['scope']['symbol'] == 'BTCUSDC',
            'FIRST_SCOPE_ORIGINAL_BTC_REQUIRED')
    stage = successor.active_setup()
    supervisor._root_private_directory(stage)
    original = dict(spec=read(stage / 'spec.json'), result=read(stage / 'result.json'))
    spec = setup.spec_check(original['spec']); result = original['result']
    require(spec['symbol'] == 'BTCUSDC' and spec['source_commit'] == result['source_commit']
            == policy['executor_release_commit'] and setup.digest(spec) == result['spec_sha256']
            and spec['account_ref_sha256'] == policy['account_ref_sha256']
            and result['schema'] == 'DEMO_OPERATOR_SETUP_RESULT_V1'
            and result['status'] == 'INSTALLED_PENDING_CYCLE'
            and result['orders_submitted'] is False and result['economic_authority'] is False,
            'FIRST_SCOPE_ORIGINAL_SETUP')
    approval = read(stage / 'operator-enrollment.json')
    setup._approval_check(approval, spec, approval['approved_at_ms'])
    require(setup.digest(approval) == result['enrollment_sha256'], 'FIRST_SCOPE_ORIGINAL_OPERATOR')
    manifest = installer.manifest_check(read(stage / 'bundle' / 'manifest.json'))
    require(manifest['source_commit'] == spec['source_commit']
            and result['task_sha256'] in manifest['tasks'], 'FIRST_SCOPE_ORIGINAL_MANIFEST')
    for name, value in (('policy.json', policy), ('profile.json', profile), ('registry.json', registry)):
        retained = read(stage / 'bundle' / name)
        require(retained == value and _sha(watched[stage / 'bundle' / name]) == manifest['files'][name],
                'FIRST_SCOPE_ORIGINAL_CONFIGURATION')
    pending = successor.SETUP / 'pending.json'
    require(not pending.exists() and not pending.is_symlink(), 'FIRST_SCOPE_PENDING_ENROLLMENT')
    runtime = read(supervisor.STATE / 'strategy-runtime.json')
    strategy.runtime_check(runtime, source_commit)
    definition = strategy.load_definition(installer.EXECUTOR, runtime,
        source_commit=source_commit, uid=registry['executor_uid'])
    stop = setup._stop_image(path=installer.EXECUTOR / 'kill-switch.sqlite3')
    return dict(registry=registry, policy=policy, profile=profile, original_setup=original,
                runtime=runtime, definition=definition), watched, stage, stop


def capture_original_inputs(*, source_commit):
    """Caller holds the original maintenance lease; this grants no permission."""
    from tools import gold_first_scope_materials as compiler
    inputs, watched, stage, stop = _original_inputs(source_commit)
    cycle = setup.digest(inputs['original_setup']['result'])
    report, origin = successor.first_gold_review_for(source_commit, inputs['registry'], cycle, with_origin=True)
    inputs['issuer_history'] = capture_issuer_history(inputs['registry'], report=report,
        account_ref=inputs['policy']['account_ref_sha256'])
    request = compiler.prepare_first_scope(**inputs, review=report, origin=origin,
        source_commit=source_commit, stop_sha256=_sha(stop), now_ms=installer.now_ms())
    require(successor.active_setup() == stage
            and all(supervisor._read_root_owned_json(path) == body for path, body in watched.items())
            and setup._stop_image(path=installer.EXECUTOR / 'kill-switch.sqlite3') == stop,
            'FIRST_SCOPE_ORIGINAL_INPUTS_CHANGED')
    require(strategy.load_definition(installer.EXECUTOR, inputs['runtime'], source_commit=source_commit,
            uid=inputs['registry']['executor_uid']) == inputs['definition'], 'FIRST_SCOPE_DEFINITION_CHANGED')
    require_issuer_history(inputs['issuer_history'], inputs['registry'])
    return dict(inputs, request=request, review=report, origin=origin, original_cycle_sha256=cycle)


def operator_confirmation(request, cycle, operator):
    from live.execution import demo_gold_first_scope as contract
    require(local_operator() == operator, 'FIRST_SCOPE_OPERATOR_CHANGED')
    contract.validate_request(request)
    require(request['original_cycle_sha256'] == cycle, 'FIRST_SCOPE_CONFIRMATION_CYCLE')
    print(json.dumps(dict(request=request, duration_seconds=86400, installation_allowance_seconds=300,
                          original_cycle_sha256=cycle), ensure_ascii=False, indent=2))
    print('独立登记首次黄金 Scope：24小时、300秒安装余量；不批准订单，不安装功能交易任务。')
    expected = 'APPROVE FIRST GOLD SCOPE ' + request['request_sha256']
    require(input('请输入 ' + expected + '\n').strip() == expected, 'FIRST_SCOPE_NOT_CONFIRMED')
    require(local_operator() == operator, 'FIRST_SCOPE_OPERATOR_CHANGED')
    now = installer.now_ms()
    value = dict(schema=contract.APPROVAL_SCHEMA, purpose=contract.PURPOSE,
        request_sha256=request['request_sha256'], original_cycle_sha256=cycle, operator_id=operator,
        approved_at_ms=now, duration_seconds=86400, installation_allowance_seconds=300,
        execution_approval_granted=False, economic_authority=False)
    return contract.validate_approval(value, request=request, now_ms=now)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args(argv)
    local_operator()
    with successor.cycle_lock(installer.CONTROLLER / 'functional-cycle', maintenance=True):
        value = capture_original_inputs(source_commit=args.source_commit)
    print(json.dumps(dict(request=value['request'], status='FIRST_GOLD_SCOPE_CONFIRMATION_REQUIRED',
        installed=False, orders_submitted=False, submission_enabled=False, economic_authority=False), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
