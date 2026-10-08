"""One original executor observation to close a delivered finite pilot.

Uses the existing private-pipe launcher and retained claims. This controller
never observes credentials, edits the original decision, or creates an order.
"""
import json
import os
import subprocess
import time

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode


def finish(*, source_commit, registry, policy, owner, current_runtime, result):
    from tools import gold_pilot_closure
    from tools.gold_feedback_recovery import original_result, delivery_check
    original = original_result(result)
    delivery_check(original, result.get('research_feedback'))
    request = gold_pilot_closure.request(current_runtime, original)
    return _finish(source_commit=source_commit, registry=registry, policy=policy,
        owner=owner, current_runtime=current_runtime, request=request, recovered=result)


def finish_idle(*, source_commit, registry, policy, owner, current_runtime, original_window):
    from tools import gold_pilot_closure
    request = gold_pilot_closure.idle_request(current_runtime, original_window)
    return _finish(source_commit=source_commit, registry=registry, policy=policy,
        owner=owner, current_runtime=current_runtime, request=request, recovered=None)


def _inspection(value):
    if value is None:
        return None
    _fields(value, 'schema candidate reobservation controller_checkpoint executor_checkpoint submission_enabled')
    require(value['schema'] == 'GOLD_PILOT_REOBSERVATION_INSPECTION_V1'
        and value['submission_enabled'] is False
        and ((type(value['candidate']) is dict and value['reobservation'] is None)
            or (value['candidate'] is None and type(value['reobservation']) is dict)),
        'PILOT_REOBSERVATION_INSPECTION')
    return value


def _transport_request(*, owner, current_runtime, request, source_commit, account, supervisor):
    try:
        inspection = _inspection(owner.inspect_reobservation(
            current_runtime=current_runtime, now_ms=supervisor.now_ms()))
        if inspection is None:
            return request, False
        from tools import gold_pilot_reobservation
        evidence = inspection['candidate'] or inspection['reobservation']
        transport = gold_pilot_reobservation.request(current_runtime, request, evidence)
        checked_at = supervisor.now_ms()
        budget = owner.preflight_reobservation(current_runtime=current_runtime,
            now_ms=checked_at, request=transport)
        _fields(budget, 'schema status source_commit account_ref_sha256 runtime_sha256 request_sha256 '
            'controller_checkpoint required checked_at_ms submission_enabled economic_authority')
        require(budget['schema'] == 'GOLD_PILOT_REOBSERVATION_CAPACITY_V1'
            and budget['status'] == 'CAPACITY_AVAILABLE'
            and budget['source_commit'] == source_commit and budget['account_ref_sha256'] == account
            and budget['runtime_sha256'] == _digest(current_runtime)
            and budget['request_sha256'] == _digest(transport)
            and type(budget['checked_at_ms']) is int and budget['checked_at_ms'] == checked_at
            and budget['submission_enabled'] is False and budget['economic_authority'] is False,
            'PILOT_REOBSERVATION_CAPACITY_BINDING')
        _fields(budget['required'], 'claims records audit_events payload_bytes claim_bytes audit_bytes '
            'database_bytes journal_bytes audit_allocated_bytes directory_bytes')
        require(all(type(value) is int and value >= 0 for value in budget['required'].values()),
            'PILOT_REOBSERVATION_CAPACITY_BOUNDS')
        return transport, True
    finally:
        owner.release()


def _completed_reobservation(*, owner, current_runtime, request, result, now_ms):
    final = _inspection(owner.inspect_reobservation(current_runtime=current_runtime, now_ms=now_ms))
    require(final is not None and final['candidate'] is None
        and final['reobservation']['status'] == 'COMPLETE'
        and final['reobservation']['intent_sha256'] == result['closure_intent_sha256']
        and _digest(final['reobservation']['request']) == _digest(request)
        and _digest(final['reobservation']['result']) == _digest(result),
        'PILOT_REOBSERVATION_FINAL_CHAIN')
    return final['reobservation']


def _finish(*, source_commit, registry, policy, owner, current_runtime, request, recovered):
    from tools import gold_pilot_closure, gold_strategy_controller as controller
    from tools import demo_operating_bundle as installer
    from tools.gold_feedback_recovery import current_control
    supervisor = controller.supervisor
    idle = request['schema'] == gold_pilot_closure.IDLE_SCHEMA
    original = request.get('original_result')
    transport, retry = _transport_request(owner=owner, current_runtime=current_runtime, request=request,
        source_commit=source_commit, account=policy['account_ref_sha256'], supervisor=supervisor)
    raw = json.dumps(transport, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    require(len(raw) <= supervisor.MAX_INPUT_BYTES, 'PILOT_CLOSURE_REQUEST_BUDGET')
    process = None
    unit = 'quantos-demo-executor-'+os.urandom(8).hex()
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'SYSTEMD_COLORS': '0'}
    try:
        command = controller.demo_task.preparation_command(source_commit=source_commit, registry=registry, unit=unit)
        command[command.index('tools.demo_request_prepare')] = 'tools.gold_strategy_worker'
        # Cold native history review and the original 115-second account read
        # are outside every order TTL. The worker retains the original pilot's
        # ends_at_ms + 900000 limit for any new terminal observation.
        command[command.index('RuntimeMaxSec=155s')] = 'RuntimeMaxSec=450s'
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0, env=env, cwd='/')
        deadline = time.monotonic()+445
        supervisor._send(process.stdin, raw, deadline)
        closure = decode(supervisor.read_frame(process.stdout.fileno(), maximum=supervisor.MAX_INPUT_BYTES,
            deadline=deadline))
        if idle and type(closure) is dict and closure.get('status') == 'NOT_APPLICABLE':
            require(not retry, 'PILOT_REOBSERVATION_NOT_APPLICABLE')
            expected = dict(schema='GOLD_PILOT_IDLE_CLOSURE_RESULT_V1', status='NOT_APPLICABLE',
                reason='EXECUTION_SINCE_ENROLLMENT', source_commit=source_commit,
                bundle_sha256=_digest(current_runtime), request_sha256=_digest(request),
                original_pilot_window=request['original_pilot_window'],
                submission_enabled=False, economic_authority=False)
            require(_digest(closure) == _digest(expected), 'PILOT_IDLE_NOT_APPLICABLE_BINDING')
            supervisor._finish(process, deadline)
            return None
        controller.validate_result(closure, source_commit=source_commit,
            bundle_sha256=_digest(current_runtime), account=policy['account_ref_sha256'])
        decision = (gold_pilot_closure.validate_idle_result(closure, request) if idle
            else gold_pilot_closure.validate_result(closure, original))
        require(_digest(closure['closure_request']) == _digest(request), 'PILOT_CLOSURE_REQUEST_CHANGED')
        supervisor._finish(process, deadline)
        if retry:
            retained = _completed_reobservation(owner=owner, current_runtime=current_runtime,
                request=request, result=closure, now_ms=supervisor.now_ms())
            owner.retention.retain_reobservation_diagnostic(retained['expired_result'],
                current_runtime=current_runtime)
        owner.retention.retain(closure)
        owner.release()
        found = owner.discover(current_runtime=current_runtime, terminal=True)
        if idle:
            require(found is not None and found.get('status') == 'IDLE_CLOSURE'
                and found.get('result') is None and found.get('delivery') is None
                and _digest(found.get('closure_request')) == _digest(request), 'PILOT_IDLE_RETAINED_PROOF_MISSING')
        else:
            require(found is not None and found.get('result') == original
                and found.get('delivery') == recovered['research_feedback'], 'PILOT_CLOSURE_ORIGINAL_CHANGED')
        completion = found.get('closure')
        require(type(completion) is dict and completion.get('status') == 'COMPLETE'
            and _digest(completion.get('result')) == _digest(closure)
            and completion.get('intent_sha256') == closure['closure_intent_sha256'],
            'PILOT_CLOSURE_RETAINED_PROOF_MISSING')
        if retry:
            _completed_reobservation(owner=owner, current_runtime=current_runtime,
                request=request, result=closure, now_ms=supervisor.now_ms())
        owner.release()
        control = current_control(executor_root=installer.EXECUTOR, base_runtime=owner.binding['base_runtime'],
            source_commit=source_commit, account=policy['account_ref_sha256'],
            executor_uid=registry.get('executor_uid'), result=closure, now_ms=supervisor.now_ms())
        require(control == decision['pilot_control'] and control['status'] == 'STOPPED', 'PILOT_CLOSURE_STOP_CHANGED')
        return closure if idle else dict(original, research_feedback=recovered['research_feedback'], pilot_closure=closure)
    finally:
        owner.release()
        if process is not None:
            try:
                subprocess.run(['systemctl', 'stop', unit], env=env, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=2)
            for channel in (process.stdin, process.stdout):
                if channel is not None:
                    channel.close()
