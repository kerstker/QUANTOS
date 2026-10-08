"""One bar-strategy episode decision through original recovery and risk paths.

The protected worker must serialize ticks with its existing preparation lease.
This module has no sender, signer, policy installer or alternative order store.
"""
from decimal import Decimal

from live.execution import demo_position_context as position, demo_gold_pilot
from live.execution import demo_market_partition_runtime as partition
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state, _require_origin, DemoPreparationRejected
from services.research.demo_execution_origin import GOLD_ACCOUNT_TERMS_SCHEMA
from tools.gold_bar_demo_entry import BarDemoEntry
from tools import gold_bar_demo_exit
from tools.gold_strategy_request import constraints_from_draft


def prepare(*, engine, entry_decision_ms, origin, dispatch, recovery, oms, now_ms):
    if (type(engine) is not BarDemoEntry or type(now_ms) is not int or not 0 <= now_ms <= 2**63-1
            or type(entry_decision_ms) is not int or not 0 <= entry_decision_ms <= now_ms
            or type(origin) is not dict or origin.get('schema') != GOLD_ACCOUNT_TERMS_SCHEMA
            or origin.get('symbol') != 'XAUUSDT'):
        raise ValueError('GOLD_BAR_CYCLE_INPUT')
    result = dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', strategy_family='GOLD_BAR_CANDIDATES_V1',
        status='RECOVERY_REQUIRED', observed_at_ms=now_ms, action=None, draft=None,
        execution_constraints=None, submission_enabled=False, economic_authority=False, cash_reconciled=False)
    try:
        state = _state(dispatch, recovery, oms)
    except DemoPreparationRejected as exc:
        return dict(result, reason=str(exc))
    definition = engine.verified_definition()
    signal = engine.signal_at(entry_decision_ms)
    result.update(strategy_definition_sha256=definition['definition_hash'],
                  bar_signal_sha256=_digest(signal), execution_state_sha256=_digest(state))
    # A settled legacy prefix is real history without a Gold context. Resolve
    # that distinction through the same protected cut used before submission.
    context, _ = partition.resolve_context(state=state, origin=origin, as_of_ms=now_ms)
    if context is not None:
        account = position.validate_origin(origin, context, as_of_ms=now_ms)
        result['position_context_sha256'] = _digest(context)
    else:
        account = {'equity': _require_origin(origin, now_ms=now_ms)['wallet']}
    flat = context is None or Decimal(context['quantity']) == 0
    result.update(position_flat=flat, execution_history_empty=not state['reservations'])
    pilot = demo_gold_pilot.observe(dict(account, account_ref_sha256=origin['account_ref_sha256']),
        now_ms=now_ms, origin_sha256=_digest(origin))
    if pilot is None:
        return dict(result, status='PILOT_CONFIGURATION_REQUIRED')
    result['pilot_control'] = pilot
    side = signal['entry_side'] if signal['status'] == 'READY' else None
    client = ('QD'+_digest(dict(account_ref_sha256=origin['account_ref_sha256'],
        definition_hash=definition['definition_hash'], decision_id=signal['decision_id'], action='OPEN_'+side))[:32]
        if side else None)
    owned = [r for r in state['reservations'] if any(o['newClientOrderId'] == client for o in r['payload']['orders'])]
    if len(owned) > 1:
        raise ValueError('GOLD_BAR_CYCLE_DUPLICATE_ENTRY')
    if owned:
        if flat:
            return dict(result, status='STRATEGY_EPISODE_SETTLED', reason='ORIGINAL_ENTRY_TERMINAL_AND_FLAT')
        draft = gold_bar_demo_exit.prepare(engine=engine, entry_decision_ms=entry_decision_ms,
            origin=origin, dispatch=dispatch, recovery=recovery, oms=oms, now_ms=now_ms)
        if draft['status'] == 'HOLDING':
            return dict(result, status='HOLDING', reason=draft['reason'])
        if draft['status'] != 'BAR_EXIT_DRAFT':
            return dict(result, status='RECOVERY_REQUIRED', reason='POSITION_CHANGED_DURING_CYCLE')
        action = 'REDUCE_LONG' if side == 'BUY' else 'REDUCE_SHORT'
    else:
        if not flat:
            return dict(result, status='OTHER_POSITION_REQUIRES_RECONCILIATION')
        if pilot['status'] == 'STOPPED':
            return dict(result, status='PILOT_STOPPED', reason=pilot['reason'])
        if not side:
            return dict(result, status='NO_ENTRY_SIGNAL')
        if engine._last is None or engine._last['received_at_ms'] != entry_decision_ms:
            raise ValueError('GOLD_BAR_CYCLE_ENTRY_OBSERVATION_MOVED')
        draft = engine.prepare(origin=origin, now_ms=now_ms)
        if draft['status'] == 'PILOT_STOPPED':
            return dict(result, status='PILOT_STOPPED', reason=draft['reason'], pilot_control=draft['pilot_control'])
        action = 'OPEN_LONG' if side == 'BUY' else 'OPEN_SHORT'
    constraints = constraints_from_draft(draft, now_ms=now_ms)
    return dict(result, status='DECISION_REQUIRES_APPROVAL', action=action, draft=draft,
                execution_constraints=constraints)
