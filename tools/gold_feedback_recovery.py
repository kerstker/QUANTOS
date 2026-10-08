"""Recover original completed feedback without another observation or claim.

The owner is local to one bounded controller/pilot invocation. Native history
checks remain authoritative; no cache, retry journal, or parallel ledger is
persisted here. All store leases are released before the publisher child.
"""
from copy import deepcopy
from pathlib import Path

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_operating_bundle as installer
from tools.gold_feedback_delivery import FeedbackDeliveryPending


class FeedbackOwner:
    def __init__(self):
        self.retention = None
        self.binding = None
        self.closed = False

    def bind(self, *, root, base_runtime, source_commit, account, registry):
        require(not self.closed, 'FEEDBACK_OWNER_CLOSED')
        binding = dict(root=str(Path(root).absolute()), executor_root=str(installer.EXECUTOR),
            base_runtime=deepcopy(base_runtime), source_commit=source_commit,
            account=account, registry=deepcopy(registry))
        require(self.binding is None or binding == self.binding, 'FEEDBACK_OWNER_BINDING')
        if self.retention is None:
            from tools.gold_pilot_retention import ControllerRetention
            current = ControllerRetention(root, base_runtime=base_runtime, source_commit=source_commit,
                account=account, executor_uid=registry.get('executor_uid'))
            try:
                if current.enabled:
                    current.enable_feedback_discovery()
            except BaseException:
                current.close()
                raise
            self.retention, self.binding = current, binding
        return self.retention

    def discover(self, *, current_runtime, terminal=False):
        require(not self.closed and self.retention is not None, 'FEEDBACK_OWNER_NOT_BOUND')
        require(type(terminal) is bool, 'FEEDBACK_TERMINAL_OPTION')
        if self.retention.enabled:
            if terminal:
                return self.retention.discover_terminal_feedback(current_runtime=current_runtime)
            return self.retention.discover_feedback(current_runtime=current_runtime)
        from tools.gold_feedback_legacy import discover_feedback
        b = self.binding
        return discover_feedback(root=Path(b['root']), executor_root=Path(b['executor_root']),
            base_runtime=b['base_runtime'], current_runtime=current_runtime,
            source_commit=b['source_commit'], account=b['account'],
            executor_uid=b['registry'].get('executor_uid'))

    def recover(self, *, current_runtime, deliver, include_previous=False):
        """Return the original result plus delivery, or no pending completion.

        A verified previous or historical rotation is never republished. Callers
        may request its original result for terminal control review; that review
        still must establish an actual stop or expiry before closing. Ordinary
        recovery returns no pending completion. A typed storage failure is diagnostic output,
        never a successful delivery attachment.
        """
        require(type(include_previous) is bool, 'FEEDBACK_PREVIOUS_OPTION')
        try:
            found = self.discover(current_runtime=current_runtime, terminal=include_previous)
            if found is None or found['status'] in ('FEEDBACK_NOT_INITIALIZED', 'IDLE_CLOSURE'):
                return None
            if found['relation'] in ('PREVIOUS', 'HISTORICAL_TERMINAL'):
                require(found['status'] == 'DELIVERED', 'FEEDBACK_PREVIOUS_NOT_DELIVERED')
                return dict(found['result'], research_feedback=found['delivery']) if include_previous else None
            require(found['status'] in ('PENDING_DELIVERY', 'DELIVERED'), 'FEEDBACK_ARTIFACT_NOT_RETAINED')
            result = found['result']
            receipt = found['delivery']
            if receipt is None:
                require(found['delivery_state'] in ('ABSENCE_VERIFIED', 'ABSENCE_OBSERVED'),
                    'FEEDBACK_ABSENCE_NOT_REVIEWED')
                self.release()
                try:
                    receipt = deliver(source_commit=self.binding['source_commit'],
                        cycle=found['bundle_sha256'], exported=result['feedback'])
                except FeedbackDeliveryPending as exc:
                    from tools.demo_feedback_publish import pending_receipt
                    expected = pending_receipt(source_commit=self.binding['source_commit'],
                        cycle=found['bundle_sha256'], artifact_sha256=result['feedback']['artifact_sha256'],
                        feedback_sha256=result['feedback']['feedback_sha256'])
                    require(exc.receipt == expected, 'FEEDBACK_PENDING_BINDING')
                    current = self._same_original(found, current_runtime)
                    if current['delivery'] is not None:
                        return dict(result, research_feedback=current['delivery'])
                    return dict(result, feedback_delivery_pending=deepcopy(exc.receipt))
                delivery_check(result, receipt)
                current = self._same_original(found, current_runtime)
                if current['delivery'] is None:
                    self.retention.attach(result, 'delivery', receipt)
                else:
                    require(current['delivery'] == receipt, 'FEEDBACK_DELIVERY_CHANGED')
                current = self._same_original(found, current_runtime)
                require(current['delivery'] == receipt, 'FEEDBACK_DELIVERY_NOT_RETAINED')
            return dict(result, research_feedback=receipt)
        finally:
            self.release()

    def inspect_reobservation(self, *, current_runtime, now_ms):
        require(not self.closed and self.retention is not None, 'FEEDBACK_OWNER_NOT_BOUND')
        if self.retention.enabled:
            return self.retention.inspect_reobservation(current_runtime=current_runtime, now_ms=now_ms)
        from tools.gold_feedback_legacy import inspect_reobservation
        b = self.binding
        return inspect_reobservation(root=Path(b['root']), executor_root=Path(b['executor_root']),
            base_runtime=b['base_runtime'], current_runtime=current_runtime,
            source_commit=b['source_commit'], account=b['account'],
            executor_uid=b['registry'].get('executor_uid'), now_ms=now_ms)

    def preflight_reobservation(self, *, current_runtime, now_ms, request):
        require(not self.closed and self.retention is not None, 'FEEDBACK_OWNER_NOT_BOUND')
        if self.retention.enabled:
            return self.retention.preflight_reobservation(current_runtime=current_runtime,
                now_ms=now_ms, request=request)
        from tools.gold_feedback_legacy import preflight_reobservation
        b = self.binding
        return preflight_reobservation(root=Path(b['root']), executor_root=Path(b['executor_root']),
            base_runtime=b['base_runtime'], current_runtime=current_runtime,
            source_commit=b['source_commit'], account=b['account'],
            executor_uid=b['registry'].get('executor_uid'), now_ms=now_ms, request=request)

    def _same_original(self, found, current_runtime):
        current = self.discover(current_runtime=current_runtime)
        require(current is not None and all(current.get(k) == found[k] for k in
            ('result_sha256', 'result_bytes', 'claim', 'bundle_sha256', 'relation', 'rotation_sha256')),
            'FEEDBACK_ORIGINAL_CHANGED')
        return current

    def release(self):
        if self.retention is not None:
            self.retention.release()

    def close(self):
        if self.retention is not None:
            self.retention.close()
        self.closed = True

    def __enter__(self):
        require(not self.closed, 'FEEDBACK_OWNER_CLOSED')
        return self

    def __exit__(self, *args):
        self.close()


def delivery_check(result, receipt):
    require(type(receipt) is dict and receipt.get('status') == 'RESEARCH_FEEDBACK_DELIVERED'
        and receipt.get('cycle_sha256') == result['bundle_sha256']
        and receipt.get('feedback_sha256') == result['feedback']['feedback_sha256']
        and all(receipt.get(k) is False for k in ('source_verified', 'submission_enabled', 'economic_authority')),
        'FEEDBACK_DELIVERY_BINDING')


def original_result(result):
    """Remove only controller handoff fields, preserving original worker bytes."""
    return {k: v for k, v in result.items() if k not in (
        'research_feedback', 'feedback_delivery_pending', 'feedback_recovery_control',
        'pilot_closure', 'pilot_closure_error')}


def validate_pilot_closure(result):
    original = original_result(result)
    delivery_check(original, result.get('research_feedback'))
    closure = result.get('pilot_closure')
    require(type(closure) is dict, 'FEEDBACK_PILOT_CLOSURE_REQUIRED')
    from tools import gold_pilot_closure
    return gold_pilot_closure.validate_result(closure, original)


def recovery_control(result, control):
    """Preserve the old decision and distinguish present closure obligations."""
    if (control is None or control['status'] == 'ACTIVE'
            or result['decision'].get('pilot_control') == control
            or 'research_feedback' not in result):
        return result
    return dict(result, feedback_recovery_control=dict(schema='GOLD_FEEDBACK_RECOVERY_CONTROL_V1',
        original_result_sha256=_digest(original_result(result)),
        current_control=deepcopy(control), closure_required=True, submission_enabled=False))


def validate_recovery_control(result):
    value = result.get('feedback_recovery_control')
    require(type(value) is dict and set(value) == {
        'schema', 'original_result_sha256', 'current_control', 'closure_required', 'submission_enabled'}
        and value['schema'] == 'GOLD_FEEDBACK_RECOVERY_CONTROL_V1'
        and value['submission_enabled'] is False and value['closure_required'] is True,
        'FEEDBACK_RECOVERY_CONTROL')
    original = original_result(result)
    require(value['original_result_sha256'] == _digest(original), 'FEEDBACK_RECOVERY_ORIGINAL')
    control = value['current_control']
    require(type(control) is dict and control.get('status') in ('EXPIRED', 'STOPPED')
        and control.get('configuration_sha256') == result['decision']['pilot_control']['configuration_sha256']
        and control.get('submission_enabled') is False, 'FEEDBACK_RECOVERY_CURRENT_CONTROL')
    delivery_check(original, result.get('research_feedback'))


def idle_window(*, executor_root, source_commit, account, executor_uid, now_ms):
    """Read a completed original window; this does not prove no execution.

    An absent request or not-yet-enrolled request has no terminal window. A
    partial or changed completed enrollment is an error, never an idle proof.
    Only the executor may compare its original full state and observe the stop.
    """
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from tools.gold_bar_pilot_capacity import _read
    from tools.demo_resident_recovery import _check_path
    root = Path(executor_root)
    area, path = root/'gold-pilot', root/'gold-pilot-request.json'
    if not path.exists() and not path.is_symlink():
        return None
    _check_path(root, executor_uid, directory=True)
    originals = {}
    def read(target, maximum=32768):
        raw = _read(target, executor_uid, maximum)
        originals[target] = raw
        return decode(raw)
    request = read(path)
    enrollment._request_check(request, source_commit=source_commit, account=account)
    if not area.exists() and not area.is_symlink():
        require(_read(path, executor_uid) == originals[path], 'FEEDBACK_PILOT_ORIGINAL_CHANGED')
        return None
    _check_path(area, executor_uid, directory=True)
    config = read(area/'configuration.json')
    receipt = read(area/'enrollment.json')
    intent_path = area/'enrollment-intent.json'
    intent = read(intent_path, enrollment.INTENT_MAXIMUM) if intent_path.exists() or intent_path.is_symlink() else None
    require(enrollment.completed_configuration_records(configuration=config, receipt=receipt,
        intent=intent, request=request) == config, 'FEEDBACK_PILOT_ENROLLMENT_CHANGED')
    require(type(now_ms) is int and config['started_at_ms'] <= now_ms <= 2**63-1, 'FEEDBACK_PILOT_CLOCK')
    require(all(_read(p, executor_uid, max(32768, len(raw))) == raw for p, raw in originals.items()),
        'FEEDBACK_PILOT_ORIGINAL_CHANGED')
    if now_ms < config['ends_at_ms']:
        return None
    return dict(schema='GOLD_PILOT_ORIGINAL_WINDOW_V1', source_commit=source_commit,
        account_ref_sha256=account, request_sha256=_digest(request), configuration_sha256=_digest(config),
        started_at_ms=config['started_at_ms'], ends_at_ms=config['ends_at_ms'], submission_enabled=False)


def current_control(*, executor_root, base_runtime, source_commit, account, executor_uid, result, now_ms):
    """Read current original control. Expiry alone never fabricates a stop.

    This authorizes no order and performs no account observation. The executor
    must perform its original observation to close an expired, unstopped pilot.
    """
    from live.execution import demo_gold_pilot as pilot, demo_gold_pilot_enrollment as enrollment
    from tools.gold_bar_pilot_capacity import _read
    from tools.demo_resident_recovery import _check_path
    from tools.gold_strategy_controller import validate_original_pilot_window
    previous = result['decision'].get('pilot_control')
    root = Path(executor_root)
    area = root/'gold-pilot'
    if previous is None:
        require(not area.exists() and not area.is_symlink()
            and 'original_pilot_window' not in result, 'FEEDBACK_PILOT_CONTROL_MISSING')
        return None
    _check_path(root, executor_uid, directory=True)
    _check_path(area, executor_uid, directory=True)
    originals = {}
    def read(path, maximum=32768):
        raw = _read(path, executor_uid, maximum)
        originals[path] = raw
        return decode(raw)
    config = read(area/'configuration.json')
    pilot.config_check(config)
    require(config['account_ref_sha256'] == account and base_runtime['source_commit'] == source_commit
        and previous.get('configuration_sha256') == _digest(config)
        and previous.get('submission_enabled') is False and previous.get('status') in ('ACTIVE', 'STOPPED'),
        'FEEDBACK_PILOT_CONTROL_BINDING')
    require(type(now_ms) is int and config['started_at_ms'] <= now_ms <= 2**63-1,
        'FEEDBACK_PILOT_CLOCK')
    request_path = root/'gold-pilot-request.json'
    window = validate_original_pilot_window(result, source_commit=source_commit, account=account)
    if request_path.exists() or request_path.is_symlink():
        request = read(request_path)
        enrollment._request_check(request, source_commit=source_commit, account=account)
        intent_path = area/'enrollment-intent.json'
        intent = read(intent_path, enrollment.INTENT_MAXIMUM) if intent_path.exists() or intent_path.is_symlink() else None
        require(enrollment.completed_configuration_records(configuration=config,
            receipt=read(area/'enrollment.json'), intent=intent, request=request) == config,
            'FEEDBACK_PILOT_ENROLLMENT_CHANGED')
        require(window is not None and window['request_sha256'] == _digest(request)
            and window['configuration_sha256'] == _digest(config)
            and all(window[k] == config[k] for k in ('started_at_ms', 'ends_at_ms')),
            'FEEDBACK_PILOT_WINDOW_CHANGED')
    else:
        require(window is None and not (area/'enrollment-intent.json').exists()
            and not (area/'enrollment-intent.json').is_symlink(), 'FEEDBACK_PILOT_REQUEST_MISSING')
    path = area/'stopped.json'
    if path.exists() or path.is_symlink():
        control = pilot.validate_stop(read(path), config=config, now_ms=now_ms)
        if previous['status'] == 'STOPPED':
            require(previous == control, 'FEEDBACK_PILOT_STOP_CHANGED')
    else:
        require(previous['status'] != 'STOPPED', 'FEEDBACK_PILOT_STOP_MISSING')
        control = dict(status='ACTIVE' if now_ms < config['ends_at_ms'] else 'EXPIRED',
            configuration_sha256=_digest(config), started_at_ms=config['started_at_ms'],
            ends_at_ms=config['ends_at_ms'], submission_enabled=False)
    require(all(_read(p, executor_uid, max(32768, len(raw))) == raw for p, raw in originals.items()),
        'FEEDBACK_PILOT_ORIGINAL_CHANGED')
    # Recheck an absent stop too. Appearance requires a new read, never an old
    # ACTIVE result used to advance another episode.
    require(path in originals or not path.exists() and not path.is_symlink(), 'FEEDBACK_PILOT_STOP_CHANGED')
    return control
