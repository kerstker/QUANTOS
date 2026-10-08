"""One scheduled strategy observation and optional approved Demo handoff.

The original controller owns observation, cash closure and research publication.
The original dispatcher alone consumes independently installed exact approvals.
This entrypoint creates no approval, task identity, signing key or execution log.
"""
import argparse
import json
import re
import time

from live.execution.demo_batch_risk import _digest
from tools import gold_strategy_controller as controller
from tools import gold_strategy_dispatch as dispatcher


def run(*, source_commit, execute_approved=False,require_pilot=False,on_approval_request=None,feedback_owner=None):
    if (type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}', source_commit) is None
            or type(execute_approved) is not bool or type(require_pilot) is not bool):
        raise ValueError('STRATEGY_TICK_ARGUMENTS')
    notice = {} if on_approval_request is None else {'on_approval_request': on_approval_request}
    if feedback_owner is not None: notice['feedback_owner'] = feedback_owner
    observation = controller.run(source_commit=source_commit, **notice)
    return handle_observation(observation, source_commit=source_commit, execute_approved=execute_approved,require_pilot=require_pilot)


def handle_observation(observation, *, source_commit, execute_approved,require_pilot=False):
    result = dict(schema='GOLD_STRATEGY_TICK_V1', source_commit=source_commit,
        status='OBSERVATION_REQUIRES_ATTENTION', observation=observation, execution=None,
        execution_requested=execute_approved, economic_authority=False)
    if observation.get('status') != 'DECISION_RETAINED':
        return result
    state = observation['decision']['status']
    pilot=observation['decision'].get('pilot_control')
    if require_pilot and (type(pilot) is not dict or pilot.get('status') not in ('ACTIVE','STOPPED')
            or pilot.get('submission_enabled') is not False
            or type(pilot.get('configuration_sha256')) is not str
            or re.fullmatch('[a-f0-9]{64}',pilot['configuration_sha256']) is None):
        result['status']='PILOT_CONFIGURATION_REQUIRED'
        return result
    closure_request = observation.get('closure_request')
    if ('idle_evidence' in observation or observation['decision'].get('closure_mode') == 'NO_EXECUTION_SINCE_ENROLLMENT'
            or type(closure_request) is dict and closure_request.get('schema') == 'GOLD_PILOT_IDLE_CLOSURE_INPUT_V1'
            or 'closure_request' in observation and type(closure_request) is not dict):
        from tools.gold_pilot_closure import validate_idle_result
        try:
            validate_idle_result(observation, observation.get('closure_request'))
        except (ValueError, KeyError, TypeError):
            result['status'] = 'PILOT_CLOSURE_REQUIRED'
        else:
            result['status'] = 'PILOT_CLOSED_WITHOUT_EXECUTION'
        return result
    if pilot and pilot.get('status')=='STOPPED' and state!='STRATEGY_EPISODE_SETTLED' and observation['decision'].get('action') not in ('REDUCE_LONG','REDUCE_SHORT'):
        result['status']=('PILOT_STOPPED' if observation['decision'].get('position_flat') is True
                          else 'PILOT_EXIT_REQUIRES_ATTENTION')
        return result
    if state == 'DECISION_REQUIRES_APPROVAL':
        result['status'] = 'OBSERVED_ACTION_REQUIRES_INDEPENDENT_APPROVAL'
        if execute_approved:
            execution = dispatch_retained(observation, source_commit=source_commit)
            result['execution'] = execution
            if not waiting_for_approval(execution, source_commit=source_commit, result_sha256=_digest(observation)):
                result['status'] = ('APPROVED_TASK_HANDOFF_COMPLETE' if execution.get('status') in
                    ('TASK_READY', 'QUERY_ONLY', 'EXECUTOR_RESULT_RECORDED') else 'DISPATCH_REQUIRES_ATTENTION')
    elif state == 'RECOVERY_REQUIRED':
        recovery = observation.get('recovery_observation', {})
        result['status'] = ('WAITING_ORIGINAL_RECONCILIATION' if
            recovery.get('new_order_submission_enabled') is False and recovery.get('status') in
            ('WAITING_QUERY_INTERVAL', 'RESIDENT_BUSY') else 'RECOVERY_REQUIRED')
    elif state == 'STRATEGY_EPISODE_SETTLED':
        if 'pilot_closure' in observation:
            from tools.gold_feedback_recovery import validate_pilot_closure
            try:
                validate_pilot_closure(observation)
            except (ValueError, KeyError, TypeError):
                return result
            result['status'] = 'PILOT_CLOSED_WITH_FEEDBACK'
            return result
        recovery = observation.get('feedback_recovery_control')
        if recovery is not None:
            from tools.gold_feedback_recovery import validate_recovery_control
            try:
                validate_recovery_control(observation)
            except ValueError:
                return result
            if recovery['closure_required']:
                result['status'] = 'PILOT_CLOSURE_REQUIRED'
                return result
        result['status'] = ('RESEARCH_FEEDBACK_DELIVERED' if
            observation.get('research_feedback', {}).get('status') == 'RESEARCH_FEEDBACK_DELIVERED'
            else 'SETTLED_FEEDBACK_PENDING')
    elif state in ('HOLDING', 'NO_ENTRY_SIGNAL'):
        result['status'] = 'OBSERVATION_COMPLETE'
    elif state == 'PILOT_STOPPED':
        result['status'] = 'PILOT_STOPPED'
    return result


def waiting_for_approval(result, *, source_commit, result_sha256):
    """Only original approval waiting or an exact pre-materialization lease wait."""
    if result.get('status') == 'WAITING_INDEPENDENT_APPROVAL':
        return True
    return (result.get('status') == 'WAITING_CONTROLLER_MAINTENANCE'
        and result.get('schema') == 'GOLD_STRATEGY_DISPATCH_RESULT_V1'
        and result.get('phase') == 'CONTROLLER_LEASE'
        and result.get('source_commit') == source_commit
        and result.get('result_sha256') == result_sha256
        and 'task_sha256' in result and result['task_sha256'] is None
        and all(result.get(key) is False for key in ('retry_submission', 'submission_enabled', 'economic_authority')))


def dispatch_retained(observation, *, source_commit):
    """Wait only for this exact decision's independent approval, before expiry.

    Original approval waiting and an exact pre-materialization lease wait prove
    no task was materialized. All other outcomes, including a lost claim
    response, end this call. Both the first call and retries obey the deadline.
    """
    identity = _digest(observation)
    until = observation['decision']['execution_constraints']['valid_until_ms']
    if type(until) is not int or not 0 <= until <= 2**63-1:
        raise ValueError('STRATEGY_TICK_DECISION_DEADLINE')
    stop_at = time.monotonic()+30
    result = dict(schema='GOLD_STRATEGY_DISPATCH_RESULT_V1', status='WAITING_INDEPENDENT_APPROVAL',
        source_commit=source_commit, result_sha256=identity, valid_until_ms=until,
        task_sha256=None, retry_submission=False, submission_enabled=False, economic_authority=False)
    while True:
        if time.monotonic() >= stop_at or time.time_ns()//1_000_000 >= until:
            return result
        result = dispatcher.run(source_commit=source_commit, execute=True,
                                use_approved_policy=True, result_sha256=identity)
        if not waiting_for_approval(result, source_commit=source_commit, result_sha256=identity): return result
        remaining = min(stop_at-time.monotonic(), (until-time.time_ns()//1_000_000)/1000)
        if remaining <= 0: return result
        time.sleep(min(.1, remaining))


def run_loop(*, source_commit, execute_approved=False, max_ticks=1, interval_seconds=2,require_pilot=False,
             on_approval_request=None,feedback_owner=None):
    if (type(max_ticks) is not int or not 1 <= max_ticks <= 60
            or type(interval_seconds) is not int or not 1 <= interval_seconds <= 60):
        raise ValueError('STRATEGY_TICK_LOOP_BUDGET')
    if (type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}', source_commit) is None
            or type(execute_approved) is not bool or type(require_pilot) is not bool):
        raise ValueError('STRATEGY_TICK_ARGUMENTS')
    ticks = []
    def on_decision(observation):
        current = handle_observation(observation, source_commit=source_commit, execute_approved=execute_approved,require_pilot=require_pilot)
        ticks.append(current)
        state = current.get('observation', {}).get('decision', {}).get('status')
        if (current['status'] in ('OBSERVATION_REQUIRES_ATTENTION', 'RECOVERY_REQUIRED',
                                 'DISPATCH_REQUIRES_ATTENTION', 'RESEARCH_FEEDBACK_DELIVERED','SETTLED_FEEDBACK_PENDING','PILOT_CLOSED_WITH_FEEDBACK','PILOT_CLOSED_WITHOUT_EXECUTION','PILOT_CLOSURE_REQUIRED','PILOT_STOPPED','PILOT_CONFIGURATION_REQUIRED','PILOT_EXIT_REQUIRES_ATTENTION')
                or current['status'] == 'OBSERVED_ACTION_REQUIRES_INDEPENDENT_APPROVAL'
                or state == 'NO_ENTRY_SIGNAL'
                or state == 'DECISION_REQUIRES_APPROVAL' and not execute_approved):
            return False
        return True
    notice = {} if on_approval_request is None else {'on_approval_request': on_approval_request}
    if feedback_owner is not None: notice['feedback_owner'] = feedback_owner
    session = controller.run(source_commit=source_commit, ticks=max_ticks,
                             on_decision=on_decision, interval_seconds=interval_seconds, **notice)
    if session.get('status') not in ('DECISION_RETAINED', 'BOUNDED_SESSION_COMPLETE'):
        ticks.append(handle_observation(session, source_commit=source_commit, execute_approved=execute_approved,require_pilot=require_pilot))
    if not ticks:
        raise ValueError('STRATEGY_TICK_SESSION_EMPTY')
    return dict(schema='GOLD_STRATEGY_LOOP_V1', status=ticks[-1]['status'],
        source_commit=source_commit, ticks=ticks, tick_budget_exhausted=len(ticks) == max_ticks,
        execution_requested=execute_approved, economic_authority=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--execute-approved', action='store_true',
        help='Use the original Demo dispatcher for independently approved exact decisions')
    parser.add_argument('--max-ticks',type=int,default=1)
    parser.add_argument('--interval-seconds',type=int,default=2)
    parser.add_argument('--require-pilot',action='store_true',help='Require the executor-owned finite pilot control; never fall back to legacy handoff')
    args = parser.parse_args(argv)
    result = run_loop(**vars(args))
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 3 if result['status'] in ('OBSERVATION_REQUIRES_ATTENTION', 'DISPATCH_REQUIRES_ATTENTION',
                                   'RECOVERY_REQUIRED','PILOT_CONFIGURATION_REQUIRED','PILOT_EXIT_REQUIRES_ATTENTION','PILOT_CLOSURE_REQUIRED') else 0


if __name__ == '__main__':
    raise SystemExit(main())
