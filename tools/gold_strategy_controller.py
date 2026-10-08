"""Bounded controller tick for an installed gold strategy observation.

Uses existing protected configuration, controller lease and executor identity.
Retains a decision reference for independent admission. An optional in-process
callback can hand the retained decision to the original approved dispatcher;
the controller itself creates no approval or execution task.
"""
import argparse
import json
import os
import re
import subprocess
import time

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require, historical_policy_binding
from live.execution.demo_pre_submit import _retain
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools import demo_supervisor as supervisor, demo_task
from tools.demo_request_prepare import destination


def observation_configuration(source_commit):
    """Keep observing after grant expiry without obtaining send authority.

    Mirrors the existing portfolio reconciliation configuration: historical
    identity binding is sufficient for read-only work, never for task dispatch.
    """
    require(os.geteuid() == 0, 'SUPERVISOR_ROOT_REQUIRED')
    supervisor.verify_source(source_commit)
    registry = supervisor.validate_registry(load_json_without_duplicate_keys(
        supervisor._read_root_owned_json(supervisor.EXECUTOR_REGISTRY)))
    require(load_json_without_duplicate_keys(supervisor._read_root_owned_json(
        supervisor.ISSUER_REGISTRY)) == registry, 'SUPERVISOR_REGISTRY_MIRROR')
    policy = load_json_without_duplicate_keys(supervisor._read_root_owned_json(supervisor.ISSUER_POLICY))
    from live.execution.demo_gold_budget_scope import is_scope, historical_scope_binding
    from live.execution.demo_gold_future_template import SCHEMA as FUTURE_SCHEMA, historical_template_binding
    if is_scope(policy):historical_scope_binding(policy, registry)
    elif policy.get('schema') == FUTURE_SCHEMA:historical_template_binding(policy, registry)
    else:historical_policy_binding(policy, registry)
    require(policy['executor_release_commit'] == source_commit, 'SUPERVISOR_POLICY_RELEASE')
    supervisor._root_private_directory(supervisor.STATE)
    return registry, policy


def exchange(process, bundle, *, account, deadline, finish=True):
    supervisor._send(process.stdin, json.dumps(bundle, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode(), deadline)
    raw = supervisor.read_frame(process.stdout.fileno(), maximum=supervisor.MAX_INPUT_BYTES, deadline=deadline)
    result = load_json_without_duplicate_keys(raw)
    validate_result(result, source_commit=bundle['source_commit'], bundle_sha256=_digest(bundle), account=account)
    if finish: supervisor._finish(process, deadline)
    return result


def warmup_exchange(process, bundle, *, deadline):
    request = dict(schema='GOLD_STRATEGY_WARMUP_INPUT_V1', source_commit=bundle['source_commit'],
                   trial_manifest_sha256=bundle['trial_manifest_sha256'])
    supervisor._send(process.stdin, json.dumps(request, sort_keys=True, separators=(',', ':')).encode(), deadline)
    result = load_json_without_duplicate_keys(supervisor.read_frame(process.stdout.fileno(),
        maximum=supervisor.MAX_INPUT_BYTES, deadline=deadline))
    require(type(result) is dict and result.get('schema') == 'GOLD_STRATEGY_WARMUP_RESULT_V1'
        and result.get('status') == 'STRATEGY_PREPARED_IN_PROCESS'
        and result.get('source_commit') == bundle['source_commit'] and result.get('bundle_sha256') == _digest(request)
        and result.get('submission_enabled') is False and result.get('economic_authority') is False
        and type(result.get('strategy_definition_sha256')) is str
        and re.fullmatch('[a-f0-9]{64}', result['strategy_definition_sha256']) is not None,
        'STRATEGY_CONTROLLER_WARMUP_BINDING')
    return result


def validate_original_pilot_window(result, *, source_commit, account):
    """Bind optional executor scheduling evidence without altering its decision."""
    if 'original_pilot_window' not in result:
        return None
    window=result['original_pilot_window']
    control=result.get('decision',{}).get('pilot_control')
    require(type(window) is dict and set(window)==set('schema source_commit account_ref_sha256 '
        'request_sha256 configuration_sha256 started_at_ms ends_at_ms submission_enabled'.split()),
        'STRATEGY_CONTROLLER_ORIGINAL_WINDOW_FIELDS')
    require(window['schema']=='GOLD_PILOT_ORIGINAL_WINDOW_V1'
        and window['source_commit']==source_commit and window['account_ref_sha256']==account
        and window['submission_enabled'] is False
        and type(control) is dict and control.get('status') in ('ACTIVE','STOPPED')
        and control.get('submission_enabled') is False
        and window['configuration_sha256']==control.get('configuration_sha256'),
        'STRATEGY_CONTROLLER_ORIGINAL_WINDOW_BINDING')
    for key in ('account_ref_sha256','request_sha256','configuration_sha256'):
        require(type(window[key]) is str and re.fullmatch('[a-f0-9]{64}',window[key]) is not None,
            'STRATEGY_CONTROLLER_ORIGINAL_WINDOW_DIGEST')
    require(type(window['started_at_ms']) is int and type(window['ends_at_ms']) is int
        and 0<window['started_at_ms']<window['ends_at_ms']<=2**63-1
        and window['ends_at_ms']-window['started_at_ms']==86400000,
        'STRATEGY_CONTROLLER_ORIGINAL_WINDOW_TIME')
    return dict(window)


def validate_result(result, *, source_commit, bundle_sha256, account):
    require(type(result) is dict and result.get('schema') == 'GOLD_STRATEGY_WORKER_RESULT_V1'
        and result.get('status') == 'DECISION_RETAINED' and result.get('source_commit') == source_commit
        and result.get('bundle_sha256') == bundle_sha256
        and result.get('submission_enabled') is False and result.get('economic_authority') is False,
        'STRATEGY_CONTROLLER_WORKER_RESULT')
    decision = result.get('decision')
    require(type(decision) is dict and decision.get('schema') == 'GOLD_STRATEGY_CYCLE_DECISION_V1'
        and result.get('decision_sha256') == _digest(decision)
        and decision.get('source_commit') == source_commit
        and decision.get('bundle_sha256') == bundle_sha256 and decision.get('account_ref_sha256') == account
        and decision.get('submission_enabled') is False and decision.get('economic_authority') is False,
        'STRATEGY_CONTROLLER_DECISION_BINDING')
    validate_original_pilot_window(result,source_commit=source_commit,account=account)
    return decision


def retain_approval_request(result, *, bundle, policy, registry=None, base_runtime=None, retention=None):
    from live.execution.demo_issuer_policy import GOLD_BUDGET_POLICY_SCHEMAS
    from tools.gold_strategy_approval_request import prepare
    from live.execution.demo_gold_budget_scope import is_scope
    if ((policy.get('schema') not in GOLD_BUDGET_POLICY_SCHEMAS and not is_scope(policy))
            or result['decision']['status'] != 'DECISION_REQUIRES_APPROVAL'):
        return None
    request = prepare(result, runtime=bundle, policy=policy, now_ms=supervisor.now_ms(),
        registry=registry,base_runtime=base_runtime)
    if retention is None:_retain(destination(supervisor.STATE, 'strategy-approval-requests'), request)
    else:retention.attach(result,'approval',request)
    return request


def deliver_feedback(*, source_commit, cycle, exported):
    from tools.gold_feedback_delivery import deliver_feedback as publish
    return publish(source_commit=source_commit, cycle=cycle, exported=exported)


def review_stopped_bar_handoff(result):
    """A callback stop may mean completion or invalid pilot observation.

    Reuse the original observation check before considering a newly reachable
    rotation. The native rotation consumer still decides eligibility.
    """
    from live.execution.demo_gold_pilot import require_opening_allowed
    try:
        require_opening_allowed([], result['decision'].get('pilot_control'))
    except ValueError:
        return False
    return True


def advance_bar(*, root, base, account, current, result, executor_uid):
    from tools import gold_bar_rotation, demo_operating_bundle as installer
    from tools.gold_feedback_recovery import current_control
    last_checked = None
    def before_commit():
        nonlocal last_checked
        before = supervisor.now_ms()
        require(type(before) is int and (last_checked is None or before >= last_checked),
            'BAR_ROTATION_CLOCK_ROLLBACK')
        control = current_control(executor_root=installer.EXECUTOR, base_runtime=base,
            source_commit=base['source_commit'], account=account, executor_uid=executor_uid,
            result=result, now_ms=before)
        after = supervisor.now_ms()
        require(type(before) is int and type(after) is int and after >= before, 'BAR_ROTATION_CLOCK_ROLLBACK')
        last_checked = after
        if control is None or control['status'] != 'ACTIVE':
            return False
        require(type(control.get('ends_at_ms')) is int, 'BAR_ROTATION_CURRENT_WINDOW_REQUIRED')
        return after < control['ends_at_ms']
    return gold_bar_rotation.advance(root=root, base=base, account=account, current=current,
        result=result, before_commit=before_commit)


def finish_recovered_pilot(**arguments):
    from tools.gold_pilot_closure_controller import finish
    return finish(**arguments)


def finish_idle_pilot(**arguments):
    from tools.gold_pilot_closure_controller import finish_idle
    return finish_idle(**arguments)


def run(*, source_commit, ticks=1, on_decision=None, interval_seconds=0,
        on_approval_request=None, feedback_owner=None):
    """Drain an original completion before allowing another worker to start."""
    from tools.gold_feedback_recovery import FeedbackOwner, current_control, recovery_control, idle_window
    from tools import demo_operating_bundle as installer, gold_bar_rotation
    own = feedback_owner is None
    owner = FeedbackOwner() if own else feedback_owner
    phase = 'CONFIGURATION'
    previous_umask = os.umask(0o077)
    try:
        require(type(owner) is FeedbackOwner, 'FEEDBACK_OWNER_TYPE')
        continuous = on_decision is not None
        require(not continuous or callable(on_decision), 'STRATEGY_CONTROLLER_CALLBACK')
        require(on_approval_request is None or callable(on_approval_request), 'STRATEGY_CONTROLLER_APPROVAL_CALLBACK')
        require(type(ticks) is int and 1 <= ticks <= (60 if continuous else 4), 'STRATEGY_CONTROLLER_TICK_BUDGET')
        require(type(interval_seconds) is int and 0 <= interval_seconds <= 60, 'STRATEGY_CONTROLLER_INTERVAL')
        with supervisor.controller_lease():
            registry, policy = observation_configuration(source_commit)
            path = supervisor.STATE/'strategy-runtime.json'
            raw = supervisor._read_root_owned_json(path)
            base = load_json_without_duplicate_keys(raw)
            extras = {
                'GOLD_STRATEGY_WORKER_INPUT_V1': 'observation_sha256',
                'GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1': 'plan',
                'GOLD_BAR_RETAINED_EPISODE_INPUT_V1': 'observation_sha256 archive_source_commit entry_decision_ms',
                'GOLD_BAR_FRESH_EPISODE_INPUT_V1': 'archive_source_commit candidate seconds history_sha256',
                'GOLD_BAR_AUTO_EPISODE_INPUT_V1': 'archive_source_commit candidate seconds episode_id',
            }
            require(type(base) is dict and base.get('schema') in extras
                and base.get('source_commit') == source_commit, 'STRATEGY_CONTROLLER_CONFIG')
            _fields(base, 'schema source_commit trial_manifest_sha256 '+extras[base['schema']])
            current = base
            automatic = base['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
            if automatic:
                current = gold_bar_rotation.load(root=supervisor.STATE, base=base,
                    account=policy['account_ref_sha256'], read_only=True)
            phase = 'ORIGINAL_FEEDBACK_RECOVERY'
            owner.bind(root=supervisor.STATE, base_runtime=base, source_commit=source_commit,
                account=policy['account_ref_sha256'], registry=registry)
            result = owner.recover(current_runtime=current, deliver=deliver_feedback, include_previous=True)
            require(observation_configuration(source_commit) == (registry, policy)
                and supervisor._read_root_owned_json(path) == raw, 'STRATEGY_CONTROLLER_CONFIG_CHANGED')
            if result is not None:
                phase = 'ORIGINAL_PILOT_CONTROL'
                control = current_control(executor_root=installer.EXECUTOR, base_runtime=base,
                    source_commit=source_commit, account=policy['account_ref_sha256'],
                    executor_uid=registry.get('executor_uid'), result=result, now_ms=supervisor.now_ms())
                if result['bundle_sha256'] != _digest(current) and (control is None or control['status'] == 'ACTIVE'):
                    # Previous delivery is evidence, not another current tick.
                    # An active pilot continues ordinary observation exactly once.
                    result = None
                else:
                    result = recovery_control(result, control)
                    if 'feedback_recovery_control' in result:
                        phase = 'PILOT_CLOSURE_OBSERVATION'
                        try:
                            result = finish_recovered_pilot(source_commit=source_commit, registry=registry,
                                policy=policy, owner=owner, current_runtime=current, result=result)
                        except Exception:
                            result = dict(result, pilot_closure_error='ORIGINAL_CLOSURE_REQUIRES_ATTENTION')
                        require(observation_configuration(source_commit) == (registry, policy)
                            and supervisor._read_root_owned_json(path) == raw, 'STRATEGY_CONTROLLER_CONFIG_CHANGED')
            if result is None:
                phase = 'ORIGINAL_PILOT_WINDOW'
                window = idle_window(executor_root=installer.EXECUTOR, source_commit=source_commit,
                    account=policy['account_ref_sha256'], executor_uid=registry.get('executor_uid'),
                    now_ms=supervisor.now_ms())
                if window is not None:
                    phase = 'PILOT_IDLE_CLOSURE_OBSERVATION'
                    result = finish_idle_pilot(source_commit=source_commit, registry=registry,
                        policy=policy, owner=owner, current_runtime=current, original_window=window)
                    require(observation_configuration(source_commit) == (registry, policy)
                        and supervisor._read_root_owned_json(path) == raw, 'STRATEGY_CONTROLLER_CONFIG_CHANGED')
        if result is None:
            return _run_observation(source_commit=source_commit, ticks=ticks, on_decision=on_decision,
                interval_seconds=interval_seconds, on_approval_request=on_approval_request, feedback_owner=owner)
        if on_decision is not None:
            phase = 'DECISION_HANDOFF'
            require(type(on_decision(result)) is bool, 'STRATEGY_CONTROLLER_CALLBACK_RESULT')
        if (automatic and result['bundle_sha256'] == _digest(current)
                and not any(k in result for k in ('feedback_recovery_control', 'pilot_closure', 'closure_request'))):
            phase = 'BAR_EPISODE_ROTATION'
            with supervisor.controller_lease():
                next_registry, next_policy = observation_configuration(source_commit)
                require(next_registry == registry and next_policy['account_ref_sha256'] == policy['account_ref_sha256']
                    and supervisor._read_root_owned_json(path) == raw, 'STRATEGY_CONTROLLER_CONFIG_CHANGED')
                advance_bar(root=supervisor.STATE, base=base, account=policy['account_ref_sha256'],
                    current=current, result=result, executor_uid=registry.get('executor_uid'))
        if ticks == 1:
            return result
        from tools.gold_feedback_recovery import original_result
        original = original_result(result)
        return dict(schema='GOLD_STRATEGY_CONTROLLER_SESSION_V1', status='BOUNDED_SESSION_COMPLETE',
            source_commit=source_commit, warmup=None,
            results=[dict(result_sha256=_digest(original), decision_sha256=result['decision_sha256'],
                decision_status=result['decision']['status'])], submission_enabled=False, economic_authority=False)
    except Exception:
        return dict(schema='GOLD_STRATEGY_CONTROLLER_RESULT_V1', status='INPUTS_UNRESOLVED',
            phase=phase, source_commit=source_commit, submission_enabled=False, economic_authority=False)
    finally:
        if type(owner) is FeedbackOwner:
            owner.release()
            if own: owner.close()
        os.umask(previous_umask)


def _run_observation(*, source_commit, ticks=1, on_decision=None, interval_seconds=0,
                     on_approval_request=None, feedback_owner):
    previous = os.umask(0o077); process = None; unit = None; phase = 'CONFIGURATION'; retention = None
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'SYSTEMD_COLORS': '0'}
    try:
        continuous = on_decision is not None
        require(not continuous or callable(on_decision), 'STRATEGY_CONTROLLER_CALLBACK')
        require(on_approval_request is None or callable(on_approval_request), 'STRATEGY_CONTROLLER_APPROVAL_CALLBACK')
        require(type(ticks) is int and 1 <= ticks <= (60 if continuous else 4), 'STRATEGY_CONTROLLER_TICK_BUDGET')
        require(type(interval_seconds) is int and 0 <= interval_seconds <= 60,
                'STRATEGY_CONTROLLER_INTERVAL')
        final_handoff = None
        final_request = None
        with supervisor.controller_lease():
            registry, policy = observation_configuration(source_commit)
            path = supervisor.STATE/'strategy-runtime.json'
            raw = supervisor._read_root_owned_json(path)
            bundle = load_json_without_duplicate_keys(raw)
            fresh = bundle.get('schema') == 'GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1'
            bar_fresh = bundle.get('schema') in ('GOLD_BAR_FRESH_EPISODE_INPUT_V1', 'GOLD_BAR_AUTO_EPISODE_INPUT_V1')
            bar = bar_fresh or bundle.get('schema') == 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1'
            extra = 'observation_sha256 archive_source_commit entry_decision_ms' if bar else ('plan' if fresh else 'observation_sha256')
            if bar_fresh:
                extra = 'archive_source_commit candidate seconds '+('episode_id' if bundle['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1' else 'history_sha256')
            _fields(bundle, 'schema source_commit trial_manifest_sha256 '+extra)
            require(bundle['schema'] in ('GOLD_STRATEGY_WORKER_INPUT_V1', 'GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1',
                        'GOLD_BAR_RETAINED_EPISODE_INPUT_V1', 'GOLD_BAR_FRESH_EPISODE_INPUT_V1', 'GOLD_BAR_AUTO_EPISODE_INPUT_V1')
                    and bundle['source_commit'] == source_commit, 'STRATEGY_CONTROLLER_CONFIG')
            require(not bar_fresh or type(bundle['seconds']) is int and 1 <= bundle['seconds'] <= 900,
                    'STRATEGY_CONTROLLER_BAR_DURATION')
            require(not fresh or ticks == 1 or continuous, 'STRATEGY_CONTROLLER_FRESH_SINGLE_ATTEMPT')
            base_bundle = dict(bundle)
            rotate_bar = bundle['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
            if rotate_bar:
                from tools import gold_bar_rotation
                bundle = gold_bar_rotation.load(root=supervisor.STATE, base=base_bundle,
                    account=policy['account_ref_sha256'])
            needs_warmup = not bar and (fresh or ticks > 1 or continuous)
            multiple_frames = needs_warmup or ticks > 1 or continuous
            phase='PILOT_OBSERVATION_RETENTION'
            retention=feedback_owner.bind(root=supervisor.STATE,base_runtime=base_bundle,source_commit=source_commit,
                account=policy['account_ref_sha256'],registry=registry)
            if not retention.enabled:destination(supervisor.STATE,'strategy-decisions')
            retention.release()
            unit = 'quantos-demo-executor-'+os.urandom(8).hex()
            command = demo_task.preparation_command(source_commit=source_commit, registry=registry, unit=unit)
            command[command.index('tools.demo_request_prepare')] = 'tools.gold_strategy_worker'
            if multiple_frames or bar_fresh:
                # Fresh mode can finish the original cash collector after a
                # settled tick (30-second guard + bounded GET/retry budget).
                lifetime = 150+ticks*(450+450+interval_seconds) if continuous else (630 if fresh else 155*(ticks+1))
                if bar_fresh:
                    lifetime = 150+ticks*(bundle['seconds']+450+interval_seconds)
                command[command.index('RuntimeMaxSec=155s')] = f'RuntimeMaxSec={lifetime}s'
                if continuous: command.extend(['--frame-wait-seconds', '450'])
                command.extend(['--frames', str(ticks+int(needs_warmup))])
            phase = 'STRATEGY_OBSERVATION'
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0, env=env, cwd='/')
            warmup = None
            if needs_warmup:
                phase = 'STRATEGY_PREPARATION'
                warmup = warmup_exchange(process, bundle, deadline=time.monotonic()+150)
            results = []
            for index in range(ticks):
                if continuous and index:
                    if interval_seconds: time.sleep(interval_seconds)
                    # Independent exact-decision policies may rotate between
                    # observations. Custody, registry, account and runtime stay
                    # bound; each observation still checks its policy twice.
                    next_registry, next_policy = observation_configuration(source_commit)
                    require(next_registry == registry and next_policy['account_ref_sha256'] == policy['account_ref_sha256'],
                            'STRATEGY_CONTROLLER_IDENTITY_CHANGED')
                    policy = next_policy
                require(observation_configuration(source_commit) == (registry, policy)
                    and supervisor._read_root_owned_json(path) == raw, 'STRATEGY_CONTROLLER_CONFIG_CHANGED')
                phase = 'STRATEGY_OBSERVATION'
                result = exchange(process, bundle, account=policy['account_ref_sha256'], deadline=time.monotonic()+(bundle['seconds']+150 if bar_fresh else (450 if fresh else 150)),
                                  finish=index == ticks-1)
                if warmup is not None and result['decision']['status'] != 'RECOVERY_REQUIRED':
                    require(result['decision'].get('strategy_definition_sha256') == warmup['strategy_definition_sha256'],
                            'STRATEGY_CONTROLLER_PREPARED_DEFINITION_CHANGED')
                require(observation_configuration(source_commit) == (registry, policy)
                    and supervisor._read_root_owned_json(path) == raw, 'STRATEGY_CONTROLLER_CONFIG_CHANGED')
                phase = 'DECISION_REFERENCE_PERSISTENCE'
                retention.retain(result)
                retained_result_hash = _digest(result)
                phase = 'INDEPENDENT_APPROVAL_REQUEST_PERSISTENCE'
                request = retain_approval_request(result, bundle=bundle, policy=policy,registry=registry,base_runtime=base_bundle,
                    retention=retention)
                if index == ticks-1:
                    final_request = request
                exported = result.get('feedback')
                if type(exported) is dict and exported.get('status') in ('STRATEGY_FEEDBACK_RETAINED', 'BAR_STRATEGY_FEEDBACK_RETAINED'):
                    require(result['decision']['status'] == 'STRATEGY_EPISODE_SETTLED',
                            'STRATEGY_FEEDBACK_UNSETTLED')
                    phase = 'RESEARCH_FEEDBACK_PUBLICATION'
                    from tools.gold_feedback_delivery import FeedbackDeliveryPending
                    from tools.gold_feedback_recovery import delivery_check
                    retention.release()
                    try:
                        delivered = deliver_feedback(source_commit=source_commit, cycle=_digest(bundle), exported=exported)
                    except FeedbackDeliveryPending as exc:
                        from tools.demo_feedback_publish import pending_receipt
                        require(exc.receipt == pending_receipt(source_commit=source_commit, cycle=_digest(bundle),
                            artifact_sha256=exported['artifact_sha256'], feedback_sha256=exported['feedback_sha256']),
                            'FEEDBACK_PENDING_BINDING')
                        result = dict(result, feedback_delivery_pending=exc.receipt)
                    else:
                        delivery_check(result, delivered)
                        retention.attach(result,'delivery',delivered)
                        result = dict(result, research_feedback=delivered)
                retention.release()
                results.append(dict(result_sha256=retained_result_hash, decision_sha256=result['decision_sha256'],
                                    decision_status=result['decision']['status']))
                if continuous:
                    if index == ticks-1:
                        # exchange(finish=True) has verified the worker exited.
                        # The independent installer needs its original exclusive
                        # lease while the dispatcher waits for human approval.
                        final_handoff = result
                        break
                    phase = 'DECISION_HANDOFF'
                    keep_observing = on_decision(result)
                    require(type(keep_observing) is bool, 'STRATEGY_CONTROLLER_CALLBACK_RESULT')
                    if not keep_observing:
                        if rotate_bar and review_stopped_bar_handoff(result):
                            phase = 'BAR_EPISODE_ROTATION'
                            require(observation_configuration(source_commit) == (registry, policy)
                                and supervisor._read_root_owned_json(path) == raw,
                                'STRATEGY_CONTROLLER_CONFIG_CHANGED')
                            bundle = advance_bar(root=supervisor.STATE, base=base_bundle,
                                account=policy['account_ref_sha256'], current=bundle, result=result, executor_uid=registry.get('executor_uid'))
                        break
                if 'feedback_delivery_pending' in result:
                    break
                if rotate_bar:
                    phase = 'BAR_EPISODE_ROTATION'
                    bundle = advance_bar(root=supervisor.STATE, base=base_bundle,
                        account=policy['account_ref_sha256'], current=bundle, result=result, executor_uid=registry.get('executor_uid'))
            outcome = result if ticks == 1 else dict(schema='GOLD_STRATEGY_CONTROLLER_SESSION_V1', status='BOUNDED_SESSION_COMPLETE',
                source_commit=source_commit, warmup=warmup, results=results,
                submission_enabled=False, economic_authority=False)
        if final_request is not None and on_approval_request is not None:
            # Only the successfully completed final frame has an unlocked
            # handoff. Intermediate frames retain the shared lease and emit no
            # notice. The event is separate from immutable worker result bytes.
            phase = 'INDEPENDENT_APPROVAL_NOTICE'
            on_approval_request(dict(schema='GOLD_STRATEGY_APPROVAL_NOTICE_V1',
                status='INDEPENDENT_APPROVAL_REQUIRED',
                source_commit=final_request['source_commit'],
                stored_request_sha256=_digest(final_request),
                request_sha256=final_request['request_sha256'],
                result_sha256=final_request['result_sha256'],
                constraints_sha256=final_request['constraints_sha256'],
                budget_group_binding_sha256=final_request['budget_group_binding_sha256'],
                valid_until_ms=final_request['valid_until_ms'],
                worker_exit_verified=True, controller_shared_lease_released=True,
                approval_granted=False, submission_enabled=False, economic_authority=False))
        if final_handoff is not None:
            phase = 'DECISION_HANDOFF'
            keep_observing = on_decision(final_handoff)
            require(type(keep_observing) is bool, 'STRATEGY_CONTROLLER_CALLBACK_RESULT')
            if rotate_bar and (keep_observing or review_stopped_bar_handoff(final_handoff)):
                # Recheck after the unlocked handoff; another controller or
                # operator may have changed the installed observation inputs.
                phase = 'BAR_EPISODE_ROTATION'
                with supervisor.controller_lease():
                    next_registry, next_policy = observation_configuration(source_commit)
                    require(next_registry == registry
                        and next_policy['account_ref_sha256'] == policy['account_ref_sha256']
                        and supervisor._read_root_owned_json(path) == raw,
                        'STRATEGY_CONTROLLER_CONFIG_CHANGED')
                    advance_bar(root=supervisor.STATE, base=base_bundle,
                        account=policy['account_ref_sha256'], current=bundle, result=final_handoff, executor_uid=registry.get('executor_uid'))
        return outcome
    except Exception:
        return dict(schema='GOLD_STRATEGY_CONTROLLER_RESULT_V1', status='INPUTS_UNRESOLVED',
            phase=phase, source_commit=source_commit, submission_enabled=False, economic_authority=False)
    finally:
        if retention is not None:retention.release()
        if process is not None:
            try:
                subprocess.run(['systemctl', 'stop', unit], env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
            for channel in (process.stdin, process.stdout):
                if channel is not None: channel.close()
        os.umask(previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--ticks', type=int, default=1)
    args = parser.parse_args(argv)
    result = run(source_commit=args.source_commit, ticks=args.ticks)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('DECISION_RETAINED', 'BOUNDED_SESSION_COMPLETE') else 3


if __name__ == '__main__':
    raise SystemExit(main())
