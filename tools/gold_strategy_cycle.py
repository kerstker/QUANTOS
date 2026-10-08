"""Select the next strategy action from original execution state.

One read-only tick, not an execution permit or a second lifecycle store. The
caller must retain a returned decision for independent approval, then hand the
exact approved draft to gold_strategy_request. Unknown sends remain recovery
work; a completed signal is never reopened merely because the account is flat.
"""
from decimal import Decimal

from live.execution import demo_position_context as position
from live.execution import demo_gold_pilot
from live.execution import demo_market_partition_runtime as partition
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state, _require_origin, DemoPreparationRejected
from services.research.demo_execution_origin import GOLD_ACCOUNT_TERMS_SCHEMA
from tools import gold_strategy_entry, gold_strategy_exit
from tools.gold_forward_review import review
from tools.gold_strategy_request import constraints_from_draft


def prepare(observation_directory, *, strategy_trial, origin, dispatch, recovery, oms, now_ms, prepared_strategy=None):
    if (type(now_ms) is not int or not 0 <= now_ms <= 2**63-1
            or type(origin) is not dict or origin.get('schema') != GOLD_ACCOUNT_TERMS_SCHEMA
            or origin.get('symbol') != 'XAUUSDT'):
        raise ValueError('GOLD_STRATEGY_CYCLE_INPUT')
    result = dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1', observed_at_ms=now_ms,
        status='RECOVERY_REQUIRED', action=None, draft=None, execution_constraints=None,
        submission_enabled=False, economic_authority=False, cash_reconciled=False)
    # Inspect the same reservations/recovery/OMS barrier used before original
    # risk preparation. A flat account snapshot alone cannot prove no send.
    try:
        state = _state(dispatch, recovery, oms)
    except DemoPreparationRejected as exc:
        result['reason'] = str(exc)
        return result
    prepared = {'prepared_strategy': prepared_strategy} if prepared_strategy is not None else {}
    checked = review(observation_directory, strategy_trial=strategy_trial, **prepared)
    result.update(strategy_definition_sha256=checked['strategy_definition_sha256'],
                  review_sha256=_digest(checked), execution_state_sha256=_digest(state))
    # A settled legacy prefix is real history without a Gold context. Resolve
    # that distinction through the same protected cut used before submission.
    context, _ = partition.resolve_context(state=state, origin=origin, as_of_ms=now_ms)
    if context is not None:
        account=position.validate_origin(origin, context, as_of_ms=now_ms)
        result['position_context_sha256'] = _digest(context)
    else:
        account={'equity':_require_origin(origin,now_ms=now_ms)['wallet']}
    account={**account,'account_ref_sha256':origin['account_ref_sha256']}
    result['position_flat'] = context is None or Decimal(context['quantity']) == 0
    result['execution_history_empty'] = not state['reservations']
    pilot=demo_gold_pilot.observe(account,now_ms=now_ms,origin_sha256=_digest(origin),state_root=dispatch.path.parent)
    if pilot is not None:result['pilot_control']=pilot
    if not checked['signals']:
        result.update(status='PILOT_STOPPED' if pilot and pilot['status']=='STOPPED' else 'NO_ENTRY_SIGNAL',
                      reason=pilot['reason'] if pilot and pilot['status']=='STOPPED' else checked['observation_status'])
        return result
    if checked['observation_status'] != 'COMPLETE_OBSERVATION' or len(checked['signals']) != 1:
        raise ValueError('GOLD_STRATEGY_CYCLE_SINGLE_SIGNAL')
    client = gold_strategy_entry.entry_client_order_id(checked, origin['account_ref_sha256'])
    owned = [row for row in state['reservations']
             if any(order['newClientOrderId'] == client for order in row['payload']['orders'])]
    if len(owned) > 1:
        raise ValueError('GOLD_STRATEGY_CYCLE_DUPLICATE_ENTRY')
    if owned:
        if context is not None and Decimal(context['quantity']) == 0:
            # Includes an expired IOC with zero fills. A new signal requires a
            # new observation identity; never retry this decision as an entry.
            result.update(status='STRATEGY_EPISODE_SETTLED', reason='ORIGINAL_ENTRY_TERMINAL_AND_FLAT')
            return result
        draft = gold_strategy_exit.prepare(observation_directory, strategy_trial=strategy_trial,
            origin=origin, dispatch=dispatch, recovery=recovery, oms=oms, now_ms=now_ms, **prepared)
        if draft['status'] == 'HOLDING':
            result.update(status='HOLDING', reason=draft['reason'])
            return result
        action = 'REDUCE_LONG'
    else:
        if pilot and pilot['status']=='STOPPED':
            result.update(status='PILOT_STOPPED',reason=pilot['reason'])
            return result
        if context is not None and Decimal(context['quantity']) != 0:
            result.update(status='OTHER_POSITION_REQUIRES_RECONCILIATION')
            return result
        draft = gold_strategy_entry.prepare(observation_directory, strategy_trial=strategy_trial,
            origin=origin, now_ms=now_ms, **prepared)
        action = 'OPEN_LONG'
    constraints = constraints_from_draft(draft, now_ms=now_ms)
    result.update(status='DECISION_REQUIRES_APPROVAL', action=action, draft=draft,
                  execution_constraints=constraints)
    return result
