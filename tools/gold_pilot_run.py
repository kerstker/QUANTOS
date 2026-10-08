"""Finite gold Demo supervisor over the original controller and dispatcher.

The executor-owned pilot determines the equity baseline, loss stop and actual
end time across restarts. This process adds only a bounded observation loop; it
cannot install a pilot, approve a strategy, extend a grant, or reset a stop.
"""
import argparse
import json
import re
import time

from tools import gold_strategy_tick as tick


CONTINUE = frozenset(('OBSERVATION_COMPLETE','OBSERVED_ACTION_REQUIRES_INDEPENDENT_APPROVAL',
    'APPROVED_TASK_HANDOFF_COMPLETE','WAITING_ORIGINAL_RECONCILIATION',
    'SETTLED_FEEDBACK_PENDING','RESEARCH_FEEDBACK_DELIVERED'))
CLOSING_SECONDS = 900


def run(*,source_commit,execute_approved=False,max_seconds=86400,interval_seconds=10,on_progress=None):
    if (type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}',source_commit) is None
            or type(execute_approved) is not bool
            or type(max_seconds) is not int or not 1<=max_seconds<=86400
            or type(interval_seconds) is not int or not 1<=interval_seconds<=60):
        raise ValueError('GOLD_PILOT_SUPERVISOR_ARGUMENTS')
    deadline=time.monotonic()+max_seconds
    full_day=max_seconds==86400
    original_window=None
    original_end=None
    cycles=0;last=None;status='SUPERVISOR_BUDGET_EXHAUSTED';complete=False
    from tools.gold_feedback_recovery import FeedbackOwner
    with FeedbackOwner() as feedback_owner:
        while time.monotonic()<deadline:
            # Each call reloads the original installed strategy inputs. No signal
            # means observe again, not invent a new signal or promote a candidate.
            notice={} if on_progress is None else {'on_approval_request':on_progress}
            last=tick.run_loop(source_commit=source_commit,execute_approved=execute_approved,
                max_ticks=1,interval_seconds=interval_seconds,require_pilot=True,feedback_owner=feedback_owner,**notice)
            cycles+=1
            status=last['status']
            observation=last['ticks'][-1].get('observation',{})
            decision=observation.get('decision',{})
            control=decision.get('pilot_control',{})
            stopped=control.get('status')=='STOPPED'
            flat=decision.get('position_flat') is True
            if status=='PILOT_STOPPED':
                if not (stopped and flat):status='PILOT_EXIT_REQUIRES_ATTENTION'
                # A stop after no orders is terminal; settled traded episodes take
                # the original feedback path below before claiming completion.
                if status=='PILOT_STOPPED' and decision.get('execution_history_empty') is not True:
                    status='PILOT_FEEDBACK_REQUIRES_ATTENTION'
                complete=status=='PILOT_STOPPED'
            elif status=='RESEARCH_FEEDBACK_DELIVERED' and stopped and flat:
                status='PILOT_STOPPED';complete=True
            elif status=='PILOT_CLOSED_WITH_FEEDBACK':
                from tools.gold_feedback_recovery import validate_pilot_closure
                try:
                    validate_pilot_closure(observation)
                except (ValueError, KeyError, TypeError):
                    status='PILOT_CLOSURE_REQUIRED'
                else:
                    status='PILOT_STOPPED';complete=True
            elif status=='PILOT_CLOSED_WITHOUT_EXECUTION':
                from tools.gold_pilot_closure import validate_idle_result
                try:
                    validate_idle_result(observation, observation.get('closure_request'))
                except (ValueError, KeyError, TypeError):
                    status='PILOT_CLOSURE_REQUIRED'
                else:
                    status='PILOT_STOPPED';complete=True
            # The original worker verified these records under executor custody.
            # No window is reconstructed from process startup or an approval TTL.
            try:
                window=tick.controller.validate_original_pilot_window(observation,
                    source_commit=source_commit,account=decision.get('account_ref_sha256'))
            except ValueError:
                window=None
                status='PILOT_ORIGINAL_WINDOW_INVALID'
                complete=False
            if full_day and (status in CONTINUE or complete):
                if window is None:
                    status='PILOT_ORIGINAL_WINDOW_REQUIRED'
                    complete=False
                elif original_window is not None and window!=original_window:
                    status='PILOT_ORIGINAL_WINDOW_CHANGED'
                    complete=False
                else:
                    wall_ms=time.time_ns()//1000000
                    if wall_ms<window['started_at_ms'] and original_window is None:
                        status='PILOT_ORIGINAL_WINDOW_INVALID'
                        complete=False
                    else:
                        original_window=window
                        projected_end=time.monotonic()+(window['ends_at_ms']-wall_ms)/1000
                        # A clock correction can shorten observation, never renew
                        # this process's original monotonic scheduling boundary.
                        # Keep a past end in the past: restarting after expiry
                        # cannot grant another complete closing interval.
                        original_end=projected_end if original_end is None else min(original_end,projected_end)
                        deadline=original_end+CLOSING_SECONDS
            if on_progress is not None:
                on_progress(dict(schema='GOLD_PILOT_SUPERVISOR_PROGRESS_V1',cycles=cycles,
                    status=status,pilot_complete=complete,execution_requested=execute_approved))
            if status not in CONTINUE:break
            remaining=deadline-time.monotonic()
            if remaining<=0:
                status='PILOT_CLOSING_BUDGET_EXHAUSTED' if full_day and original_window else 'SUPERVISOR_BUDGET_EXHAUSTED'
                break
            time.sleep(min(interval_seconds,remaining))
            status='PILOT_CLOSING_BUDGET_EXHAUSTED' if full_day and original_window else 'SUPERVISOR_BUDGET_EXHAUSTED'
    return dict(schema='GOLD_PILOT_SUPERVISOR_V1',status=status,cycles=cycles,
        source_commit=source_commit,pilot_complete=complete,last_session=last,
        execution_requested=execute_approved,economic_authority=False,original_pilot_window=original_window)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--execute-approved',action='store_true')
    parser.add_argument('--max-seconds',type=int,default=86400)
    parser.add_argument('--interval-seconds',type=int,default=10)
    args=vars(parser.parse_args(argv))
    result=run(**args,on_progress=lambda row:print(json.dumps(row,sort_keys=True),flush=True))
    print(json.dumps(result,sort_keys=True,allow_nan=False),flush=True)
    return 0 if result['pilot_complete'] else 3


if __name__=='__main__':raise SystemExit(main())
