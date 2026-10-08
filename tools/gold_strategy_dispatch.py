"""Consume a retained strategy decision under an independently installed policy.

An explicit root-owned reference or opted-in fixed dispatcher scope selects
exact existing decision bytes. The latter follows the current independently
installed exact-decision policy. Neither mode authors approval declarations or
replaces issuer policy, budgets or keys.
Execution, when requested, goes through the original claimed task runner.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import (
    require, budget_group_binding, GOLD_GROUP_POLICY_SCHEMAS, GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA,
)
from quantos.contracts.killswitch import KillSwitchState
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_supervisor as supervisor, demo_task, demo_portfolio_checkpoint as checkpoint
from tools.gold_strategy_controller import validate_result
from tools.gold_strategy_request import constraints_from_draft, task_from_draft

PROFILE = Path('/etc/quantos/demo-issuer/profile.json')


def runtime_for_dispatch(base,*,source_commit,account,root):
    """Resolve the existing bar rotation read-only; admit no new input shape."""
    schema=base.get('schema')
    bar=schema in ('GOLD_BAR_RETAINED_EPISODE_INPUT_V1','GOLD_BAR_FRESH_EPISODE_INPUT_V1',
        'GOLD_BAR_AUTO_EPISODE_INPUT_V1')
    fresh=schema=='GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1'
    extra='plan' if fresh else 'observation_sha256'
    if bar:
        extra='observation_sha256 archive_source_commit entry_decision_ms'
        if schema!='GOLD_BAR_RETAINED_EPISODE_INPUT_V1':
            extra='archive_source_commit candidate seconds '+('episode_id'
                if schema=='GOLD_BAR_AUTO_EPISODE_INPUT_V1' else 'history_sha256')
    _fields(base,'schema source_commit trial_manifest_sha256 '+extra)
    require((bar or schema in ('GOLD_STRATEGY_WORKER_INPUT_V1','GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1'))
        and base['source_commit']==source_commit,'STRATEGY_DISPATCH_RUNTIME')
    if bar:
        for key in ('trial_manifest_sha256','archive_source_commit','observation_sha256','history_sha256','episode_id'):
            if key in base:
                length=40 if key=='archive_source_commit' else 64
                require(type(base[key]) is str and re.fullmatch('[a-f0-9]{'+str(length)+'}',base[key]),
                    'STRATEGY_DISPATCH_BAR_IDENTITY')
        if schema=='GOLD_BAR_RETAINED_EPISODE_INPUT_V1':
            require(type(base['entry_decision_ms']) is int and 0<=base['entry_decision_ms']<=2**63-1,
                'STRATEGY_DISPATCH_BAR_TIME')
        else:
            require(base['candidate'] in ('channel','trend','reversion')
                and type(base['seconds']) is int and 1<=base['seconds']<=900,'STRATEGY_DISPATCH_BAR_PLAN')
    if schema=='GOLD_BAR_AUTO_EPISODE_INPUT_V1':
        from tools.gold_bar_rotation import load
        return load(root=root,base=base,account=account,read_only=True)
    return base


def review_bar_runtime(*,root,runtime,decision,source_commit,uid):
    """Rebuild original archive/prefix evidence without capturing or renewing it."""
    require(decision['draft']['schema'] in ('GOLD_BAR_DEMO_ENTRY_DRAFT_V1','GOLD_BAR_DEMO_EXIT_DRAFT_V1')
        and decision['strategy_definition_sha256']==decision['execution_constraints']['strategy_definition_sha256'],
        'STRATEGY_DISPATCH_BAR_DECISION')
    if runtime['schema']!='GOLD_BAR_RETAINED_EPISODE_INPUT_V1':
        from tools.gold_strategy_approval_request import review_bar_prefix
        return review_bar_prefix(root=root,runtime=runtime,decision=decision,source_commit=source_commit,uid=uid)
    from tools.gold_bar_worker_inputs import load
    require('bar_continuation_sha256' not in decision,'STRATEGY_DISPATCH_RETAINED_BAR_PREFIX')
    engine,checked=load(root,runtime,source_commit=source_commit,uid=uid)
    signal=engine.signal_at(runtime['entry_decision_ms'])
    require(engine.verified_definition()['definition_hash']==decision['strategy_definition_sha256']
        and _digest(signal)==decision['bar_signal_sha256'],'STRATEGY_DISPATCH_BAR_ORIGINAL_CHANGED')
    observed=decision['observed_at_ms']
    require(type(observed) is int and engine._cursor.last_received<=observed
        and all(bar.available_ms<=observed for bar in engine._cursor.bars),'STRATEGY_DISPATCH_BAR_FUTURE_INPUT')
    return dict(original=checked,definition_hash=decision['strategy_definition_sha256'],
        bar_signal_sha256=decision['bar_signal_sha256'],submission_enabled=False)


def approved_reference(source_commit, policy, *, result_sha256=None):
    """Select only the exact decision pinned by the installed issuer policy.

    The persistent dispatcher configuration delegates consumption, not approval.
    This function never writes/replaces policy or changes the shared budget.
    A targeted caller selects one retained result; run verifies that result's
    runtime, strategy and exact constraints before materializing any task.
    """
    from tools import gold_pilot_retention as retention
    from live.execution.demo_gold_future_template import SCHEMA as FUTURE_SCHEMA, template_group_binding
    future=policy['schema']==FUTURE_SCHEMA
    require(future or policy['schema'] in GOLD_GROUP_POLICY_SCHEMAS, 'STRATEGY_DISPATCH_GROUP_REQUIRED')
    binding=template_group_binding(policy) if future else budget_group_binding(policy)
    require(not future or result_sha256 is not None,'STRATEGY_DISPATCH_FUTURE_TARGET_REQUIRED')
    path = supervisor.STATE/'strategy-dispatch.json'
    raw = supervisor._read_root_owned_json(path)
    config = decode(raw)
    _fields(config, 'schema source_commit account_ref_sha256 budget_group_binding_sha256 kill_switch')
    require(config['schema'] == 'GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1'
        and config['source_commit'] == source_commit
        and config['account_ref_sha256'] == policy['account_ref_sha256']
        and config['budget_group_binding_sha256'] == binding,
        'STRATEGY_DISPATCH_GROUP_CHANGED')
    if result_sha256 is not None:
        require(type(result_sha256) is str and re.fullmatch('[a-f0-9]{64}', result_sha256) is not None,
                'STRATEGY_DISPATCH_RESULT_ID')
        body = retention.read_controller_result(supervisor.STATE, result_sha256,
            source_commit=source_commit, account=policy['account_ref_sha256'])
        result = decode(body)
        require(type(result) is dict and _digest(result) == result_sha256,
                'STRATEGY_DISPATCH_RESULT_CHANGED')
        matches = [result_sha256]
    else:
        matches = retention.controller_result_ids(supervisor.STATE, source_commit=source_commit,
            account=policy['account_ref_sha256'], constraints_sha256=policy['bindings']['execution_constraints_sha256'])
    require(len(matches) == 1, 'STRATEGY_DISPATCH_APPROVED_RESULT_UNAVAILABLE_OR_AMBIGUOUS')
    return path, raw, dict(schema='GOLD_STRATEGY_TASK_APPROVAL_REFERENCE_V1',
        source_commit=source_commit, result_sha256=matches[0], issuer_policy_sha256=_digest(policy),
        kill_switch=config['kill_switch'])


def pending_scope_reference(source_commit,registry,policy,identity):
    """Wait on a retained exact request; Scope never reaches task materialization."""
    from tools.gold_strategy_approval_request import prepare,approval_configuration
    from tools import gold_pilot_retention as retention
    require(type(identity) is str and re.fullmatch('[a-f0-9]{64}',identity),'STRATEGY_DISPATCH_RESULT_ID')
    runtime_path=supervisor.STATE/'strategy-runtime.json';runtime_raw=supervisor._read_root_owned_json(runtime_path)
    base=decode(runtime_raw)
    def effective():return runtime_for_dispatch(base,source_commit=source_commit,
        account=policy['account_ref_sha256'],root=supervisor.STATE)
    runtime=effective()
    result_raw=retention.read_controller_result(supervisor.STATE,identity,
        source_commit=source_commit,account=policy['account_ref_sha256'])
    result=decode(result_raw)
    require(_digest(result)==identity,'STRATEGY_DISPATCH_RESULT_CHANGED')
    request=prepare(result,runtime=runtime,base_runtime=base,policy=policy,registry=registry,now_ms=supervisor.now_ms())
    request_identity=_digest(request)
    request_raw=retention.read_controller_approval(supervisor.STATE,request_identity,
        source_commit=source_commit,account=policy['account_ref_sha256'])
    require(decode(request_raw)==request,'STRATEGY_DISPATCH_SCOPE_REQUEST_CHANGED')
    require(approval_configuration(source_commit)==(registry,policy)
        and supervisor._read_root_owned_json(runtime_path)==runtime_raw
        and retention.read_controller_result(supervisor.STATE,identity,
            source_commit=source_commit,account=policy['account_ref_sha256'])==result_raw
        and retention.read_controller_approval(supervisor.STATE,request_identity,
            source_commit=source_commit,account=policy['account_ref_sha256'])==request_raw
        and effective()==runtime,'STRATEGY_DISPATCH_SCOPE_INPUTS_CHANGED')
    prepare(result,runtime=runtime,base_runtime=base,policy=policy,registry=registry,now_ms=supervisor.now_ms())
    return dict(schema='GOLD_STRATEGY_DISPATCH_RESULT_V1',status='WAITING_INDEPENDENT_APPROVAL',
        source_commit=source_commit,result_sha256=identity,valid_until_ms=request['valid_until_ms'],
        task_sha256=None,retry_submission=False,submission_enabled=False,economic_authority=False)


def export_delegated_proof(*,source_commit,registry,runtime,identity,enable_sha256,valid_until_ms):
    """Request original evidence through the existing isolated executor role."""
    from tools.demo_execution_session import read_frame
    from services.validation.demo_strategy_constraints import delegated_proof_digest
    unit='quantos-demo-executor-'+os.urandom(8).hex()
    command=demo_task.preparation_command(source_commit=source_commit,registry=registry,unit=unit)
    command[command.index('tools.demo_request_prepare')]='tools.gold_delegated_proof'
    remaining=(valid_until_ms-supervisor.now_ms())/1000
    require(remaining>0,'STRATEGY_DELEGATED_PROOF_EXPIRED')
    deadline=time.monotonic()+min(15,remaining)
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','SYSTEMD_COLORS':'0'}
    process=None
    try:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,bufsize=0,env=env,cwd='/')
        request=dict(schema='GOLD_DELEGATED_PROOF_INPUT_V1',source_commit=source_commit,
            runtime=runtime,result_sha256=identity,enable_sha256=enable_sha256)
        supervisor._send(process.stdin,json.dumps(request,sort_keys=True,separators=(',',':'),allow_nan=False).encode(),deadline)
        raw=read_frame(process.stdout.fileno(),maximum=supervisor.MAX_INPUT_BYTES,deadline=deadline)
        proof=decode(raw)
        delegated_proof_digest(proof)
        require(proof['source_commit']==source_commit and proof['enable_sha256']==enable_sha256,
            'STRATEGY_DELEGATED_PROOF_IDENTITY')
        supervisor._finish(process,deadline)
        return proof
    finally:
        if process is not None:
            try:subprocess.run(['systemctl','stop',unit],env=env,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,timeout=10)
            except (OSError,subprocess.TimeoutExpired):pass
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            for channel in (process.stdin,process.stdout):
                if channel is not None:channel.close()


def run(*, source_commit, execute=False, use_approved_policy=False, result_sha256=None):
    from tools import gold_pilot_retention as retention
    previous = os.umask(0o077); phase = 'CONFIGURATION'; task_hash = None
    try:
        require(type(execute) is bool and type(use_approved_policy) is bool, 'STRATEGY_DISPATCH_MODE')
        require(result_sha256 is None or use_approved_policy, 'STRATEGY_DISPATCH_TARGET_MODE')
        phase = 'CONTROLLER_LEASE'
        with supervisor.controller_lease():
            phase = 'CONFIGURATION'
            if use_approved_policy and result_sha256 is not None:
                from tools.gold_strategy_approval_request import approval_configuration,is_scope
                registry,policy=approval_configuration(source_commit)
                if is_scope(policy):return pending_scope_reference(source_commit,registry,policy,result_sha256)
            else:registry, policy = supervisor.configuration(source_commit)
            from live.execution.demo_gold_future_template import SCHEMA as FUTURE_SCHEMA
            future=policy['schema']==FUTURE_SCHEMA
            require(not future or use_approved_policy,'STRATEGY_DISPATCH_FUTURE_TARGET_REQUIRED')
            if use_approved_policy:
                approval_path, approval_raw, approval = approved_reference(source_commit, policy, result_sha256=result_sha256)
            else:
                approval_path = supervisor.STATE/'strategy-task-approval.json'
                approval_raw = supervisor._read_root_owned_json(approval_path)
                approval = decode(approval_raw)
            _fields(approval, 'schema source_commit result_sha256 issuer_policy_sha256 kill_switch')
            require(approval['schema'] == 'GOLD_STRATEGY_TASK_APPROVAL_REFERENCE_V1'
                and approval['source_commit'] == source_commit
                and approval['issuer_policy_sha256'] == _digest(policy), 'STRATEGY_DISPATCH_APPROVAL')
            identity = approval['result_sha256']
            require(type(identity) is str and re.fullmatch('[a-f0-9]{64}', identity) is not None,
                    'STRATEGY_DISPATCH_RESULT_ID')
            runtime_path = supervisor.STATE/'strategy-runtime.json'
            runtime_raw = supervisor._read_root_owned_json(runtime_path)
            base_runtime = decode(runtime_raw)
            def current_runtime():
                return runtime_for_dispatch(base_runtime,source_commit=source_commit,
                    account=policy['account_ref_sha256'],root=supervisor.STATE)
            runtime=current_runtime()
            bar=runtime['schema'].startswith('GOLD_BAR_')
            phase = 'RETAINED_DECISION'
            result_raw=retention.read_controller_result(supervisor.STATE,identity,
                source_commit=source_commit,account=policy['account_ref_sha256'])
            result=decode(result_raw)
            require(_digest(result) == identity, 'STRATEGY_DISPATCH_RESULT_CHANGED')
            decision = validate_result(result, source_commit=source_commit, bundle_sha256=_digest(runtime),
                                       account=policy['account_ref_sha256'])
            require(decision.get('status') == 'DECISION_REQUIRES_APPROVAL', 'STRATEGY_DISPATCH_NO_ACTION')
            profile_raw = supervisor._read_root_owned_json(PROFILE)
            profile = decode(profile_raw)
            kill = KillSwitchState.model_validate_json(json.dumps(approval['kill_switch']), strict=True)
            at = supervisor.now_ms()
            require(decision['execution_constraints'] == constraints_from_draft(decision['draft'], now_ms=at),
                    'STRATEGY_DISPATCH_CONSTRAINTS_CHANGED')
            require(not future or bar,'STRATEGY_DISPATCH_FUTURE_BAR_REQUIRED')
            delegated=future
            if result_sha256 is not None:
                require(policy['budget_group']['strategy_definition_sha256'] ==
                        decision['execution_constraints']['strategy_definition_sha256'], 'STRATEGY_DISPATCH_STRATEGY_CHANGED')
                if future or policy['bindings']['execution_constraints_sha256'] != _digest(decision['execution_constraints']):
                    delegated=use_approved_policy and (future or policy['schema']==GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA)
                    if not delegated:
                        return dict(schema='GOLD_STRATEGY_DISPATCH_RESULT_V1', status='WAITING_INDEPENDENT_APPROVAL',
                            source_commit=source_commit, result_sha256=identity,
                            valid_until_ms=decision['execution_constraints']['valid_until_ms'],
                            task_sha256=None, retry_submission=False, submission_enabled=False, economic_authority=False)
            prefix=None
            if bar and not delegated:
                from tools.gold_strategy_approval_request import executor_evidence_root
                phase='ORIGINAL_BAR_PREFIX'
                prefix=review_bar_runtime(root=executor_evidence_root(registry),runtime=runtime,
                    decision=decision,source_commit=source_commit,uid=registry['executor_uid'])
                require(review_bar_runtime(root=executor_evidence_root(registry),runtime=runtime,
                    decision=decision,source_commit=source_commit,uid=registry['executor_uid'])==prefix,
                    'STRATEGY_DISPATCH_BAR_PREFIX_CHANGED')
            delegated_inputs={}
            if delegated:
                from live.execution.demo_delegated_inputs import installed_context,resolve
                require(bar,'STRATEGY_DELEGATED_BAR_REQUIRED')
                phase='DELEGATION_ENABLE'
                trust=installed_context(policy=policy,registry=registry,now_ms=supervisor.now_ms(),role='controller')
                require(trust['enable']['original_binding']['dispatcher_sha256']==_digest(decode(approval_raw)),
                    'STRATEGY_DELEGATED_DISPATCHER_CHANGED')
                phase='EXECUTOR_DELEGATED_PROOF'
                proof=export_delegated_proof(source_commit=source_commit,registry=registry,runtime=runtime,
                    identity=identity,enable_sha256=_digest(trust['enable']),
                    valid_until_ms=decision['execution_constraints']['valid_until_ms'])
                admitted=resolve(proof,policy=policy,orders=decision['draft']['orders'],
                    constraints=decision['execution_constraints'],context=trust,now_ms=supervisor.now_ms())
                require(admitted['result_sha256']==identity,'STRATEGY_DELEGATED_RESULT_CHANGED')
                require(installed_context(policy=policy,registry=registry,now_ms=supervisor.now_ms(),role='controller')==trust,
                    'STRATEGY_DELEGATED_ENABLE_CHANGED')
                delegated_inputs=dict(delegated_admission=proof,delegation_context=trust)
            phase = 'ORIGINAL_TASK_MATERIALIZATION'
            require(supervisor.configuration(source_commit) == (registry, policy)
                and supervisor._read_root_owned_json(approval_path) == approval_raw
                and supervisor._read_root_owned_json(runtime_path) == runtime_raw
                and supervisor._read_root_owned_json(PROFILE) == profile_raw
                and retention.read_controller_result(supervisor.STATE,identity,
                    source_commit=source_commit,account=policy['account_ref_sha256'])==result_raw
                and current_runtime()==runtime
                and (not use_approved_policy or result_sha256 is not None
                    or retention.controller_result_ids(supervisor.STATE,source_commit=source_commit,
                        account=policy['account_ref_sha256'],
                        constraints_sha256=policy['bindings']['execution_constraints_sha256'])==[identity]),
                'STRATEGY_DISPATCH_CONFIG_CHANGED')
            # Prefix reconstruction can consume a short signal's whole lifetime.
            # The final task must still use that original deadline.
            task = task_from_draft(decision['draft'], profile=profile, issuer_policy=policy,
                                  kill_state=kill, source_commit=source_commit, now_ms=supervisor.now_ms(),**delegated_inputs)
            task_hash = _digest(task)
            # Existing materializer only consumes allocation.source_commit;
            # reuse its canonical task bytes, custody, limits and claim barrier.
            materialized_at=supervisor.now_ms()
            status = checkpoint.materialize(task, dict(schema='GOLD_STRATEGY_DISPATCH_RESULT_V1',
                result_sha256=identity, task_sha256=task_hash, submission_enabled=False, economic_authority=False),
                allocation={'source_commit': source_commit}, policy=policy, now_ms=materialized_at,
                **({'delegation_context':trust} if delegated else {}))
            finished_at=supervisor.now_ms()
            require(type(finished_at) is int and finished_at>=materialized_at,'STRATEGY_DISPATCH_CLOCK_ROLLBACK')
            # Validation and durable writes consume the original task lifetime.
            # Preserve retained bytes/claims when that lifetime has ended.
            if status['status']=='TASK_READY' and finished_at>=task['valid_until_ms']:
                status.update(status='EXPIRED_QUERY_ONLY')
            if execute and status['status'] == 'TASK_READY':
                phase = 'ORIGINAL_TASK'
                execution = demo_task.run(source_commit=source_commit, task_sha256=task_hash)
                status.update(status=execution['status'], execution=execution)
            return status
    except Exception as exc:
        if (phase == 'CONTROLLER_LEASE' and type(exc) is ValueError
                and str(exc) == 'CONTROLLER_MAINTENANCE_BUSY'):
            # No installed inputs or task were consumed in this invocation.
            # The caller may wait only inside the original decision lifetime.
            return dict(schema='GOLD_STRATEGY_DISPATCH_RESULT_V1', status='WAITING_CONTROLLER_MAINTENANCE',
                phase=phase, source_commit=source_commit, result_sha256=result_sha256,
                task_sha256=None, retry_submission=False, submission_enabled=False, economic_authority=False)
        return dict(schema='GOLD_STRATEGY_DISPATCH_RESULT_V1', status='INPUTS_UNRESOLVED', phase=phase,
            source_commit=source_commit, task_sha256=task_hash, retry_submission=False,
            submission_enabled=False, economic_authority=False)
    finally:
        os.umask(previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--result-sha256', help='Consume only this retained decision under the independent policy')
    parser.add_argument('--use-approved-policy', action='store_true',
        help='Consume the exact installed policy within the independently configured shared budget scope')
    args = parser.parse_args(argv)
    result = run(source_commit=args.source_commit, execute=args.execute,
                 use_approved_policy=args.use_approved_policy, result_sha256=args.result_sha256)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('TASK_READY', 'QUERY_ONLY', 'EXECUTOR_RESULT_RECORDED') else 3


if __name__ == '__main__':
    raise SystemExit(main())
