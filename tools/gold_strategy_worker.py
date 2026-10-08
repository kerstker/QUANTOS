"""One protected executor-side strategy observation and retained decision.

Accepts only content-addressed, executor-owned strategy inputs via the original
private pipe protocol. Uses the existing account capture and execution stores.
No policy installation, grant, task claim, order submission or credential input.
"""
import argparse
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sys
import time

from live.execution import demo_authority
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from live.execution.demo_pre_submit import _retain, _preparation_lease
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_execution_origin import load as load_origin
from tools import demo_executor_origin, gold_strategy_cycle, gold_bar_demo_cycle, gold_bar_worker_inputs
from tools.demo_execution_session import read_frame
from tools.demo_executor_stream import fixed_account
from tools.demo_issuer import private_pipe, verify_source, MAX_INPUT_BYTES
from tools.demo_request_prepare import destination
from tools.demo_resident_recovery import _check_path, _open, require_custody
from tools.gold_forward_review import review
from tools.gold_strategy_request import constraints_from_draft
from tools.gold_execution_identity import PreparedStrategy


def now_ms():
    return time.time_ns()//1000000


def strategy_inputs(root, bundle, *, source_commit, uid, prepared_strategy=None):
    _fields(bundle, 'schema source_commit observation_sha256 trial_manifest_sha256')
    require(bundle['schema'] == 'GOLD_STRATEGY_WORKER_INPUT_V1'
            and bundle['source_commit'] == source_commit, 'STRATEGY_WORKER_SOURCE')
    for key in ('observation_sha256', 'trial_manifest_sha256'):
        require(type(bundle[key]) is str and re.fullmatch('[a-f0-9]{64}', bundle[key]) is not None,
                'STRATEGY_WORKER_INPUT_ID')
    observation = root/'strategy-observations'/bundle['observation_sha256']
    trial = root/'strategy-trials'/bundle['trial_manifest_sha256']
    _check_path(root, uid, directory=True)
    for directory, names in ((observation, ('registration.json', 'events.jsonl', 'decisions.jsonl', 'result.json')),
        (trial, ('input.json', 'report.json', 'manifest.json', 'research-origin.json', 'trials.db', 'strategy-definition.json'))):
        _check_path(directory.parent, uid, directory=True)
        _check_path(directory, uid, directory=True)
        for name in names:
            _check_path(directory/name, uid, directory=False)
        if directory == trial:
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = trial/('trials.db'+suffix)
                require(not sidecar.exists() and not sidecar.is_symlink(), 'STRATEGY_WORKER_TRIAL_NOT_FROZEN')
    checked = review(observation, strategy_trial=trial,
                     **({'prepared_strategy': prepared_strategy} if prepared_strategy is not None else {}))
    require(checked['files']['result.json'] == bundle['observation_sha256']
            and checked['research_identity']['manifest_sha256'] == bundle['trial_manifest_sha256'],
            'STRATEGY_WORKER_ARCHIVE_BINDING')
    return observation, trial, checked


def capture_origin(*, root, source_commit, retention=None, claim=None):
    if retention is not None:
        require(retention.root == root, 'STRATEGY_WORKER_ORIGIN_PATH')
        report=demo_executor_origin.capture_for_pilot(symbol='XAUUSDT',source_commit=source_commit,
            retention=retention,claim=claim)
        require(report['status']=='CAPTURED','STRATEGY_WORKER_ORIGIN_UNAVAILABLE')
        return report
    captured = demo_executor_origin.run(symbol='XAUUSDT', source_commit=source_commit)
    require(captured['status'] == 'CAPTURED', 'STRATEGY_WORKER_ORIGIN_UNAVAILABLE')
    path = Path(captured['path'])
    require(path.parent == root/'execution-origins'/'XAUUSDT', 'STRATEGY_WORKER_ORIGIN_PATH')
    return load_origin(path)


def require_current_decision(decision):
    if decision['status'] == 'DECISION_REQUIRES_APPROVAL':
        require(constraints_from_draft(decision['draft'], now_ms=now_ms()) == decision['execution_constraints'],
                'STRATEGY_WORKER_DECISION_CHANGED')


def reconcile_pending(root, *, stores, source_commit):
    """Query/project original pending orders before taking a new account view.

    Called outside the preparation lease: the original recovery loop owns its
    cycle and preparation locks, durable query pacing and original-ID queries.
    """
    if not stores['recovery'].pending():
        return None
    from live.execution.demo_resident_recovery import RecoveryLoop
    loop = RecoveryLoop(**{k: stores[k] for k in ('dispatch', 'recovery', 'oms', 'stream', 'event_store')},
        evidence_directory=root/'evidence', output_directory=root/'resident-recovery', source_commit=source_commit)
    report = loop.tick()
    require(report.get('new_order_submission_enabled') is False, 'STRATEGY_RECOVERY_ORDER_BOUNDARY')
    return report


def warm_strategy(root, bundle, *, source_commit, uid):
    _fields(bundle, 'schema source_commit trial_manifest_sha256')
    identity = bundle['trial_manifest_sha256']
    require(bundle['schema'] == 'GOLD_STRATEGY_WARMUP_INPUT_V1' and bundle['source_commit'] == source_commit
        and type(identity) is str and re.fullmatch('[a-f0-9]{64}', identity) is not None, 'STRATEGY_WORKER_WARMUP')
    trial = root/'strategy-trials'/identity
    for path in (root, trial.parent, trial): _check_path(path, uid, directory=True)
    for name in ('input.json', 'report.json', 'manifest.json', 'research-origin.json', 'trials.db', 'strategy-definition.json'):
        _check_path(trial/name, uid, directory=False)
    prepared = PreparedStrategy(trial)
    definition = prepared.definition(trial)
    require(definition['research_identity']['manifest_sha256'] == identity, 'STRATEGY_WORKER_WARMUP_BINDING')
    return prepared, definition


def retained_observation(observation, trial, bundle, *, uid, prepared):
    """Replay the completed original signal; never renew its entry deadline."""
    _check_path(observation, uid, directory=True)
    for name in ('registration.json', 'events.jsonl', 'decisions.jsonl', 'result.json'):
        _check_path(observation/name, uid, directory=False)
    registration = load_json_without_duplicate_keys((observation/'registration.json').read_bytes())
    require(registration.get('plan') == bundle['plan']
        and registration.get('network_mode') == 'PROTECTED_DEMO_NETWORK_V1',
        'STRATEGY_WORKER_RETAINED_PLAN')
    checked = review(observation, strategy_trial=trial, prepared_strategy=prepared)
    require(checked['observation_status'] == 'COMPLETE_OBSERVATION',
            'STRATEGY_WORKER_OBSERVATION_INCOMPLETE')
    return observation, trial, checked


def observe_inputs(root, bundle, *, source_commit, uid, prepared, cash_account=None):
    """Observe directly in the prepared executor, without a post-signal copy."""
    require(prepared is not None, 'STRATEGY_WORKER_PREPARATION_REQUIRED')
    _fields(bundle, 'schema source_commit trial_manifest_sha256 plan')
    identity = bundle['trial_manifest_sha256']
    require(bundle['schema'] == 'GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1'
        and bundle['source_commit'] == source_commit and type(identity) is str
        and re.fullmatch('[a-f0-9]{64}', identity) is not None, 'STRATEGY_WORKER_FRESH_INPUT')
    trial = root/'strategy-trials'/identity
    require(prepared.definition(trial)['research_identity']['manifest_sha256'] == identity,
            'STRATEGY_WORKER_FRESH_TRIAL')
    area = root/'strategy-observation-work'; area.mkdir(mode=0o700, exist_ok=True)
    _check_path(area, uid, directory=True)
    count = size = 0
    for directory in area.iterdir():
        _check_path(directory, uid, directory=True); count += 1
        for file in directory.iterdir():
            _check_path(file, uid, directory=False); size += file.stat().st_size
        require(count < 32 and size < 256*1024*1024, 'STRATEGY_WORKER_OBSERVATION_BUDGET')
    observation = area/_digest(bundle)
    if cash_account is not None:
        from tools.gold_strategy_cash import open_window
        open_window(root, bundle=bundle, account=cash_account, source_commit=source_commit,
                    observation_exists=observation.exists() or observation.is_symlink())
    # A completed capture identifies the original entry throughout its held
    # position. Later ticks replay it and capture a fresh account below. Partial
    # attempts fail verification and remain untouched; they never recapture.
    if observation.exists() or observation.is_symlink():
        return retained_observation(observation, trial, bundle, uid=uid, prepared=prepared)
    from tools.gold_forward_observe import observe
    result = asyncio.run(observe(bundle['plan'], observation, strategy_trial=trial,
                                 prepared_strategy=prepared, protected_network=True))
    require(result['status'] == 'COMPLETE_OBSERVATION', 'STRATEGY_WORKER_OBSERVATION_INCOMPLETE')
    return retained_observation(observation, trial, bundle, uid=uid, prepared=prepared)


def start_scope_pilot_before_capture(root,*,account,source_commit):
    """Freeze the installed V1/V2 pilot baseline before a long bar wait.

    AUTO capture capacity requires completed original enrollment for either
    request schema. The original enrollment retains all account/state gates.
    """
    from live.execution import demo_gold_pilot as pilot, demo_gold_pilot_enrollment as enrollment
    path=root/'gold-pilot-request.json'
    if not path.exists() and not path.is_symlink():return
    request=pilot.read(path)
    enrollment._request_check(request,source_commit=source_commit,account=account)
    area=root/'gold-pilot'
    if (area/'enrollment.json').exists() or (area/'enrollment.json').is_symlink():
        enrollment.completed_configuration(area,request=request);return
    with ExitStack() as stack:
        stores=_open(stack,root)
        reconcile_pending(root,stores=stores,source_commit=source_commit)
        origin=capture_origin(root=root,source_commit=source_commit)
        require(origin['account_ref_sha256']==account,'STRATEGY_WORKER_ACCOUNT')
        with _preparation_lease(stores['dispatch']):
            enrollment.enroll_requested(root=root,source_commit=source_commit,origin=origin,
                **{name:stores[name] for name in ('dispatch','recovery','oms')},now_ms=now_ms())


def original_pilot_window(root,*,decision,source_commit,account):
    """Read the original completed enrollment as executor; never create one.

    Older observations without an enrollment request carry no scheduling
    extension. The full-day supervisor requires this evidence explicitly.
    """
    from live.execution import demo_gold_pilot as pilot, demo_gold_pilot_enrollment as enrollment
    control=decision.get('pilot_control')
    if control is None:
        return None
    path=root/'gold-pilot-request.json'
    if not path.exists() and not path.is_symlink():
        return None
    request=pilot.read(path)
    enrollment._request_check(request,source_commit=source_commit,account=account)
    config=enrollment.completed_configuration(root/'gold-pilot',request=request)
    require(type(control) is dict and control.get('status') in ('ACTIVE','STOPPED')
        and control.get('submission_enabled') is False
        and control.get('configuration_sha256')==_digest(config)
        and decision.get('source_commit')==source_commit
        and decision.get('account_ref_sha256')==account==config['account_ref_sha256'],
        'STRATEGY_WORKER_ORIGINAL_WINDOW_BINDING')
    return dict(schema='GOLD_PILOT_ORIGINAL_WINDOW_V1',source_commit=source_commit,
        account_ref_sha256=account,request_sha256=_digest(request),configuration_sha256=_digest(config),
        started_at_ms=config['started_at_ms'],ends_at_ms=config['ends_at_ms'],submission_enabled=False)


def run(*, source_commit, input_fd=None, output=None, prepared_slot=None, frame_wait_seconds=15, bar_sessions=None):
    channel = sys.stdout if output is None else output
    previous = os.umask(0o077); phase = 'CONFIGURATION'; bundle_hash = None; retention = None
    try:
        require(type(frame_wait_seconds) is int and 1 <= frame_wait_seconds <= 450,
                'STRATEGY_WORKER_FRAME_WAIT')
        verify_source(source_commit)
        root, account = fixed_account()
        registry = demo_authority.load_registry_for_executor(); uid = registry['executor_uid']
        require(os.geteuid() == os.getegid() == uid and set(os.getgroups()) <= {uid},
                'STRATEGY_WORKER_IDENTITY')
        require(require_custody() == root, 'STRATEGY_WORKER_STATE_ROOT')
        fd = sys.stdin.fileno() if input_fd is None else input_fd
        private_pipe(fd); private_pipe(channel.fileno())
        phase = 'STRATEGY_INPUT'
        bundle = load_json_without_duplicate_keys(read_frame(fd, maximum=MAX_INPUT_BYTES,
                                                            deadline=time.monotonic()+frame_wait_seconds))
        bundle_hash = _digest(bundle)
        if type(bundle) is dict and bundle.get('schema') == 'GOLD_PILOT_CLOSURE_REOBSERVATION_INPUT_V1':
            from tools.gold_pilot_reobservation import execute
            phase = 'ORIGINAL_PILOT_REOBSERVATION'
            result = execute(root,bundle,source_commit=source_commit,account=account,
                executor_uid=uid,clock=now_ms,sleep=time.sleep)
            print(json.dumps(result,sort_keys=True,separators=(',', ':'),allow_nan=False),file=channel,flush=True)
            return 0
        if type(bundle) is dict and bundle.get('schema') in ('GOLD_PILOT_CLOSURE_INPUT_V1','GOLD_PILOT_IDLE_CLOSURE_INPUT_V1'):
            from tools.gold_pilot_closure import execute
            phase = 'ORIGINAL_PILOT_CLOSURE'
            result = execute(root,bundle,source_commit=source_commit,account=account,
                executor_uid=uid,clock=now_ms,sleep=time.sleep)
            print(json.dumps(result,sort_keys=True,separators=(',', ':'),allow_nan=False),file=channel,flush=True)
            return 0
        if type(bundle) is dict and bundle.get('schema') == 'GOLD_STRATEGY_WARMUP_INPUT_V1':
            require(prepared_slot is not None and prepared_slot[0] is None, 'STRATEGY_WORKER_WARMUP_SEQUENCE')
            prepared, definition = warm_strategy(root, bundle, source_commit=source_commit, uid=uid)
            prepared_slot[0] = (bundle['trial_manifest_sha256'], prepared)
            print(json.dumps(dict(schema='GOLD_STRATEGY_WARMUP_RESULT_V1', status='STRATEGY_PREPARED_IN_PROCESS',
                source_commit=source_commit, bundle_sha256=bundle_hash,
                strategy_definition_sha256=definition['result_hash'], submission_enabled=False,
                economic_authority=False), sort_keys=True), file=channel, flush=True)
            return 0
        prepared = None
        if prepared_slot is not None and prepared_slot[0] is not None:
            require(type(bundle) is dict and bundle.get('trial_manifest_sha256') == prepared_slot[0][0],
                    'STRATEGY_WORKER_PREPARED_TRIAL_CHANGED')
            prepared = prepared_slot[0][1]
        fresh = type(bundle) is dict and bundle.get('schema') == 'GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1'
        bar_fresh = type(bundle) is dict and bundle.get('schema') in ('GOLD_BAR_FRESH_EPISODE_INPUT_V1', 'GOLD_BAR_AUTO_EPISODE_INPUT_V1')
        bar = bar_fresh or (type(bundle) is dict and bundle.get('schema') == 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1')
        if bar:
            from tools.gold_strategy_budget_enrollment import runtime_check
            runtime_check(bundle,source_commit)
            if not bar_fresh:
                require(prepared is None, 'BAR_WORKER_LEGACY_PREPARATION')
                engine, checked = gold_bar_worker_inputs.load(root, bundle, source_commit=source_commit, uid=uid)
                entry_decision_ms = bundle['entry_decision_ms']
            phase='SCOPE_PILOT_BEFORE_CAPTURE'
            start_scope_pilot_before_capture(root,account=account,source_commit=source_commit)
        phase='PILOT_OBSERVATION_RETENTION'
        from tools.gold_pilot_retention import executor_retention, claim_observation
        retention=executor_retention(root,bundle,source_commit=source_commit,account=account,uid=uid)
        if retention is not None:
            retention_claim=claim_observation(retention,bundle,clock=now_ms,sleep=time.sleep)
        phase='STRATEGY_INPUT'
        if bar:
            require(prepared is None, 'BAR_WORKER_LEGACY_PREPARATION')
            if bar_fresh:
                if bar_sessions is not None:
                    engine, checked, entry_decision_ms, bar_recheck = bar_sessions.frame(root, bundle,
                        source_commit=source_commit, uid=uid, account=account)
                else:
                    from tools.gold_bar_fresh_inputs import FreshEpisode
                    episode = FreshEpisode(root, bundle, source_commit=source_commit, uid=uid, cash_account=account)
                    engine, checked = episode.engine, episode.checked
                    entry_decision_ms = episode.entry_decision_ms
                    bar_recheck = episode.recheck
        elif fresh:
            phase = 'FRESH_FORWARD_OBSERVATION'
            observation, trial, checked = observe_inputs(root, bundle, source_commit=source_commit, uid=uid,
                                                        prepared=prepared, cash_account=account)
        else:
            observation, trial, checked = strategy_inputs(root, bundle, source_commit=source_commit, uid=uid,
                                                           prepared_strategy=prepared)
        if prepared is None and not bar:
            prepared = PreparedStrategy(trial)
        target = destination(root, 'strategy-decisions') if retention is None else None
        feedback = None
        with ExitStack() as stack:
            stores = _open(stack, root)
            phase = 'ORIGINAL_ORDER_RECONCILIATION'
            recovery_observation = reconcile_pending(root, stores=stores, source_commit=source_commit)
            phase = 'ACCOUNT_OBSERVATION'
            origin = capture_origin(root=root, source_commit=source_commit,
                **({'retention':retention,'claim':retention_claim} if retention is not None else {}))
            require(origin['account_ref_sha256'] == account, 'STRATEGY_WORKER_ACCOUNT')
            phase = 'STRATEGY_DECISION'
            with _preparation_lease(stores['dispatch']):
                from live.execution.demo_gold_pilot_enrollment import enroll_requested
                phase = 'PILOT_ENROLLMENT'
                enroll_requested(root=root, source_commit=source_commit, origin=origin,
                    **{k: stores[k] for k in ('dispatch', 'recovery', 'oms')}, now_ms=now_ms())
                phase = 'STRATEGY_DECISION'
                if bar:
                    decision = gold_bar_demo_cycle.prepare(engine=engine,
                        entry_decision_ms=entry_decision_ms, origin=origin,
                        **{k: stores[k] for k in ('dispatch', 'recovery', 'oms')}, now_ms=now_ms())
                    rechecked = (bar_recheck() if bar_fresh else
                        gold_bar_worker_inputs.load(root, bundle, source_commit=source_commit, uid=uid)[1])
                else:
                    decision = gold_strategy_cycle.prepare(observation, strategy_trial=trial, origin=origin,
                        **{k: stores[k] for k in ('dispatch', 'recovery', 'oms')}, now_ms=now_ms(), prepared_strategy=prepared)
                    rechecked = (retained_observation(observation, trial, bundle, uid=uid, prepared=prepared)[2] if fresh else
                        strategy_inputs(root, bundle, source_commit=source_commit, uid=uid, prepared_strategy=prepared)[2])
                require(rechecked == checked,
                        'STRATEGY_WORKER_INPUT_CHANGED')
                decision.update(source_commit=source_commit, bundle_sha256=bundle_hash,
                                account_ref_sha256=account, origin_sha256=_digest(origin))
                if bar_fresh and 'continuation_sha256' in checked:
                    decision['bar_continuation_sha256'] = checked['continuation_sha256']
                phase = 'DECISION_DEADLINE'
                require_current_decision(decision)
                phase = 'DECISION_PERSISTENCE'
                if retention is None:_retain(target, decision)
                else:retention.put(retention_claim,'decision',decision)
                if bar and decision['status'] == 'STRATEGY_EPISODE_SETTLED':
                    feedback = dict(status='BAR_CASH_FEEDBACK_REQUIRED', cash_capture_status='PRE_SIGNAL_WINDOW_REQUIRED',
                        cash_reconciled=False, submission_enabled=False)
                    if bar_fresh:
                        from tools.gold_bar_feedback import export_completed
                        phase = 'BAR_RECONCILIATION_FEEDBACK'
                        feedback = export_completed(root, bundle=bundle, engine=engine, entry_decision_ms=entry_decision_ms,
                            bundle_sha256=bundle_hash, account=account, source_commit=source_commit,
                            stores=stores, as_of_ms=now_ms())
                elif decision['status'] == 'STRATEGY_EPISODE_SETTLED':
                    from tools.gold_strategy_feedback import export_retained
                    phase = 'STRATEGY_CASH_FEEDBACK'
                    if fresh:
                        from tools.gold_strategy_cash import close_window
                        close_window(root, bundle=bundle, account=account, source_commit=source_commit)
                    feedback = export_retained(root, observation_directory=observation,
                        strategy_trial=trial, bundle_sha256=bundle_hash, account=account,
                        source_commit=source_commit, stores=stores, as_of_ms=now_ms())
        phase = 'PILOT_ORIGINAL_WINDOW'
        pilot_window=original_pilot_window(root,decision=decision,source_commit=source_commit,account=account)
        phase = 'HANDOFF_DEADLINE'
        require_current_decision(decision)
        result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
            source_commit=source_commit, bundle_sha256=bundle_hash,
            decision_sha256=_digest(decision), decision=decision,
            submission_enabled=False, economic_authority=False)
        if pilot_window is not None:
            result['original_pilot_window']=pilot_window
        if feedback is not None:
            result['feedback'] = feedback
        if recovery_observation is not None:
            result['recovery_observation'] = recovery_observation
        if retention is not None:
            phase='RESULT_PERSISTENCE'
            retention.put(retention_claim,'result',result)
            retention.close();retention=None
        print(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False), file=channel, flush=True)
        return 0
    except Exception:
        result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='INPUTS_UNRESOLVED', phase=phase,
            source_commit=source_commit, bundle_sha256=bundle_hash,
            submission_enabled=False, economic_authority=False)
        try:
            print(json.dumps(result, sort_keys=True, separators=(',', ':')), file=channel, flush=True)
        except (OSError, ValueError):
            pass
        return 3
    finally:
        if retention is not None:retention.close()
        os.umask(previous)


def serve(*, source_commit, frames, input_fd=None, output=None, frame_wait_seconds=15):
    """Bounded private-pipe session with one owned continuation per bar episode."""
    require(type(frames) is int and 1 <= frames <= 61, 'STRATEGY_WORKER_FRAME_BUDGET')
    from tools.gold_bar_sessions import BarSessions
    slot = [None]
    with BarSessions() as bar_sessions:
        for _ in range(frames):
            code = run(source_commit=source_commit, input_fd=input_fd, output=output, prepared_slot=slot,
                       frame_wait_seconds=frame_wait_seconds, bar_sessions=bar_sessions)
            if code != 0: return code
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--frames', type=int, default=1)
    parser.add_argument('--frame-wait-seconds', type=int, default=15)
    args = parser.parse_args(argv)
    return serve(source_commit=args.source_commit, frames=args.frames, frame_wait_seconds=args.frame_wait_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
