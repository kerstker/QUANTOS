"""Local independent approval of one retained gold decision's policy candidate.

The default command only reviews. Installation requires a sudo operator and
interactive confirmation, preserves the existing authority and shared budget,
and never signs, sends, installs keys, resets stores or starts services.
"""
import argparse
import json
import os
import pwd
import re
import sys
import uuid

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import (
    GOLD_BUDGET_POLICY_SCHEMAS,
    IssuerRejected,
    budget_group_binding,
    current_key,
    require,
    validate_policy,
)
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
from tools.gold_strategy_approval_request import review_retained,approval_configuration,is_scope,candidate_policy
from live.execution.demo_gold_budget_scope import budget_binding


def validate_candidate(candidate, *, policy, registry, now_ms):
    """Allow only the original exact-decision change, under the existing key."""
    scoped=is_scope(policy)
    if not scoped:
        validate_policy(policy)
        require(policy['schema'] in GOLD_BUDGET_POLICY_SCHEMAS, 'POLICY_INSTALL_GROUP')
    require(type(candidate) is dict
        and candidate.get('schema') == ('GOLD_STRATEGY_POLICY_CANDIDATE_V2' if scoped else 'GOLD_STRATEGY_POLICY_CANDIDATE_V1')
        and candidate.get('status') == 'POLICY_CANDIDATE_REQUIRES_INDEPENDENT_APPROVAL'
        and all(candidate.get(k) is False for k in
                ('approval_granted', 'submission_enabled', 'economic_authority')),
        'POLICY_INSTALL_CANDIDATE')
    for name in ('request_sha256', 'stored_request_sha256', 'policy_candidate_sha256',
                 'replaces_policy_sha256', 'budget_group_binding_sha256'):
        require(type(candidate.get(name)) is str
            and re.fullmatch('[a-f0-9]{64}', candidate[name]) is not None,
            'POLICY_INSTALL_IDENTITY')
    proposed = candidate.get('policy_candidate')
    validate_policy(proposed)
    expected=candidate_policy(policy,registry=registry,request_sha256=candidate['request_sha256'],
        constraints_sha256=proposed['bindings']['execution_constraints_sha256'],now_ms=now_ms)
    group=budget_binding(policy) if scoped else budget_group_binding(policy)
    require(proposed == expected
        and candidate['replaces_policy_sha256'] == _digest(policy)
        and candidate['policy_candidate_sha256'] == _digest(proposed)
        and candidate['budget_group_binding_sha256'] == group
        and budget_group_binding(proposed) == group,
        'POLICY_INSTALL_SCOPE_CHANGED')
    current_key(proposed, registry, now_ms)
    until = candidate.get('decision_valid_until_ms')
    require(type(now_ms) is int and type(until) is int
        and now_ms < until <= proposed['valid_until_ms'], 'POLICY_INSTALL_DECISION_EXPIRED')
    if scoped:
        from live.execution.demo_batch_risk import _fields
        from quantos.contracts.killswitch import KillSwitchState
        dispatch=candidate.get('dispatcher_configuration')
        _fields(dispatch,'schema source_commit account_ref_sha256 budget_group_binding_sha256 kill_switch')
        require(dispatch['schema']=='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1'
            and dispatch['source_commit']==policy['executor_release_commit']
            and dispatch['account_ref_sha256']==policy['account_ref_sha256']
            and dispatch['budget_group_binding_sha256']==group,'POLICY_INSTALL_DISPATCH_SCOPE')
        require(type(dispatch['kill_switch']) is dict
            and set(dispatch['kill_switch'])==set(KillSwitchState.model_fields),'POLICY_INSTALL_DISPATCH_STOP_FIELDS')
        state=KillSwitchState.model_validate_json(json.dumps(dispatch['kill_switch']),strict=True)
        require(not state.tripped and max(state.armed_at,state.reset_at)<=now_ms,'POLICY_INSTALL_DISPATCH_STOP_ACTIVE')
    return candidate


def local_operator():
    """Establish the existing sudo/TTY gate before reading protected inputs."""
    require(os.geteuid() == 0 and sys.stdin.isatty() and sys.stdout.isatty(),
        'POLICY_INSTALL_LOCAL_OPERATOR_REQUIRED')
    raw = os.environ.get('SUDO_UID', '')
    require(raw.isascii() and raw.isdigit() and int(raw) > 0, 'POLICY_INSTALL_SUDO_OPERATOR_REQUIRED')
    operator = pwd.getpwuid(int(raw)).pw_name
    require(os.environ.get('SUDO_USER') == operator, 'POLICY_INSTALL_OPERATOR_IDENTITY')
    return operator


def operator_confirmation(candidate, operator):
    require(local_operator() == operator, 'POLICY_INSTALL_OPERATOR_CHANGED')
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    print('仅批准此黄金 Demo 决策。原账户、期限、限额和共享预算保持；不签发或下单。')
    if candidate['schema']=='GOLD_STRATEGY_POLICY_CANDIDATE_V2':
        print('首次范围交接同时安装所示固定派发配置；沿用原停机历史，仅消费已独立批准的精确决策。')
    expected = 'APPROVE GOLD ' + _digest(candidate)
    require(input('请输入 ' + expected + '\n').strip() == expected,
        'POLICY_INSTALL_NOT_CONFIRMED')
    return dict(schema='GOLD_STRATEGY_LOCAL_OPERATOR_APPROVAL_V1',
        candidate_sha256=_digest(candidate), operator_id=operator,
        approved_at_ms=supervisor.now_ms(), economic_authority=False)


def _same_inputs(candidate, registry, policy, *, source_commit, stored_request_sha256, old_raw,dispatcher_raw=None):
    require(approval_configuration(source_commit) == (registry, policy)
        and supervisor._read_root_owned_json(supervisor.ISSUER_POLICY) == old_raw,
        'POLICY_INSTALL_INPUTS_CHANGED')
    current = review_retained(source_commit=source_commit,
        stored_request_sha256=stored_request_sha256, prepare_candidate=True)
    require(current == candidate, 'POLICY_INSTALL_CANDIDATE_CHANGED')
    validate_candidate(current, policy=policy, registry=registry, now_ms=supervisor.now_ms())
    if is_scope(policy):
        path=supervisor.STATE/'strategy-dispatch.json'
        require((supervisor._read_root_owned_json(path)==dispatcher_raw if dispatcher_raw is not None
            else not path.exists() and not path.is_symlink()),'POLICY_INSTALL_DISPATCH_CHANGED')


def install_approved(*, source_commit, stored_request_sha256):
    """One atomic policy replacement after human confirmation; no execution call."""
    operator = local_operator()
    with supervisor.controller_lease(maintenance=True):
        registry, policy = approval_configuration(source_commit)
        candidate = review_retained(source_commit=source_commit,
            stored_request_sha256=stored_request_sha256, prepare_candidate=True)
        validate_candidate(candidate, policy=policy, registry=registry, now_ms=supervisor.now_ms())
        old_raw = supervisor._read_root_owned_json(supervisor.ISSUER_POLICY)
        require(load_json_without_duplicate_keys(old_raw) == policy, 'POLICY_INSTALL_PREIMAGE_CHANGED')
        dispatcher=supervisor.STATE/'strategy-dispatch.json';dispatcher_raw=None
        if is_scope(policy) and (dispatcher.exists() or dispatcher.is_symlink()):
            dispatcher_raw=supervisor._read_root_owned_json(dispatcher)
            require(load_json_without_duplicate_keys(dispatcher_raw)==candidate['dispatcher_configuration'],
                'POLICY_INSTALL_EXISTING_DISPATCH_CONFLICT')
        approval = operator_confirmation(candidate, operator)
        _same_inputs(candidate, registry, policy, source_commit=source_commit,
            stored_request_sha256=stored_request_sha256, old_raw=old_raw,dispatcher_raw=dispatcher_raw)
        area = supervisor.ISSUER_POLICY.parent / 'strategy-approvals'
        if not area.exists() and not area.is_symlink():
            installer.directory(area, 0, 0)
        supervisor._root_private_directory(area)
        entries = list(area.iterdir())
        require(len(entries) < 4096, 'POLICY_INSTALL_HISTORY_BUDGET')
        for entry in entries:
            require(re.fullmatch('[a-f0-9]{64}', entry.name) is not None, 'POLICY_INSTALL_HISTORY_IDENTITY')
            supervisor._root_private_directory(entry)
        stage = area / _digest(candidate)
        require(not stage.exists() and not stage.is_symlink(), 'POLICY_INSTALL_ALREADY_RETAINED')
        installer.directory(stage, 0, 0)
        installer.write_new(stage / 'previous-policy.json', old_raw)
        installer.write_new(stage / 'candidate.json', installer.canonical(candidate))
        installer.write_new(stage / 'operator-approval.json', installer.canonical(approval))
        if is_scope(policy):
            installer.write_new(stage/('previous-dispatch.json' if dispatcher_raw is not None else 'previous-dispatch.absent'),
                dispatcher_raw if dispatcher_raw is not None else b'ABSENT\n')
            installer.write_new(stage/'dispatcher-candidate.json',installer.canonical(candidate['dispatcher_configuration']))
        temporary = supervisor.ISSUER_POLICY.parent / ('.gold-policy-' + uuid.uuid4().hex)
        body = installer.canonical(candidate['policy_candidate'])
        installer.write_new(temporary, body, gid=registry['issuer_uid'], mode=0o640)
        # All slow reads and the interactive prompt precede this final expiry
        # check. Original issuer and executor gates independently check again.
        _same_inputs(candidate, registry, policy, source_commit=source_commit,
            stored_request_sha256=stored_request_sha256, old_raw=old_raw,dispatcher_raw=dispatcher_raw)
        if is_scope(policy) and dispatcher_raw is None:
            dispatcher_raw=installer.canonical(candidate['dispatcher_configuration'])
            installer.write_new(dispatcher,dispatcher_raw)
            _same_inputs(candidate, registry, policy, source_commit=source_commit,
                stored_request_sha256=stored_request_sha256, old_raw=old_raw,dispatcher_raw=dispatcher_raw)
        os.replace(temporary, supervisor.ISSUER_POLICY)
        fd = os.open(supervisor.ISSUER_POLICY.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        require(supervisor._read_root_owned_json(supervisor.ISSUER_POLICY) == body,
            'POLICY_INSTALL_READBACK_CHANGED')
        if is_scope(policy):require(supervisor._read_root_owned_json(dispatcher)==dispatcher_raw,
            'POLICY_INSTALL_DISPATCH_READBACK_CHANGED')
        result = dict(schema='GOLD_STRATEGY_POLICY_INSTALLATION_V1',
            status='POLICY_INSTALLED_REQUIRES_ORIGINAL_GATES', source_commit=source_commit,
            stored_request_sha256=stored_request_sha256, candidate_sha256=_digest(candidate),
            previous_policy_sha256=_digest(policy),
            installed_policy_sha256=candidate['policy_candidate_sha256'],
            budget_group_binding_sha256=candidate['budget_group_binding_sha256'],
            operator_approval_sha256=_digest(approval), installed=True,
            orders_submitted=False, economic_authority=False)
        installer.write_new(stage / 'result.json', installer.canonical(result))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--stored-request-sha256', required=True)
    parser.add_argument('--install-approved', action='store_true',
        help='Require independent local sudo/TTY confirmation before replacing only the exact policy.')
    args = parser.parse_args(argv)
    previous = os.umask(0o077)
    try:
        if args.install_approved:
            result = install_approved(source_commit=args.source_commit,
                stored_request_sha256=args.stored_request_sha256)
        else:
            result = review_retained(source_commit=args.source_commit,
                stored_request_sha256=args.stored_request_sha256, prepare_candidate=True)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, IssuerRejected) and str(exc).startswith('POLICY_INSTALL_') \
            else 'POLICY_INSTALL_VALIDATION_FAILED'
        print(json.dumps(dict(status='POLICY_INSTALLATION_INCOMPLETE', reason=reason,
            installed=None if args.install_approved else False,
            orders_submitted=False, economic_authority=False)))
        return 3
    finally:
        os.umask(previous)


if __name__ == '__main__':
    raise SystemExit(main())
