"""Derive a reduction draft from the original owned position and gold rules.

Caller supplies the original stores; context is rebuilt, never caller asserted.
No order persistence, signature, network, or execution permission occurs here.
"""
from copy import deepcopy
from decimal import Decimal

from live.execution import demo_position_context as position
from live.execution import demo_gold_pilot
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state, _venue_rules, _venue_account_cap
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.research.demo_execution_origin import GOLD_ACCOUNT_TERMS_SCHEMA
from services.research.gold_signal import ExitPlan, exit_condition
from services.validation.demo_submission import GOLD_BATCH_SCHEMA, validate_orders
from tools.gold_forward_review import review
from tools.gold_strategy_entry import entry_client_order_id


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_STRATEGY_EXIT_'+reason)


@ledger_decimal_context()
def prepare(observation_directory, *, strategy_trial, origin, dispatch, recovery, oms, now_ms, prepared_strategy=None):
    require(type(now_ms) is int and 0 <= now_ms <= 2**63-1, 'TIME')
    checked = review(observation_directory, strategy_trial=strategy_trial,
                     **({'prepared_strategy': prepared_strategy} if prepared_strategy is not None else {}))
    require(checked['observation_status'] == 'COMPLETE_OBSERVATION'
            and len(checked['signals']) == 1, 'ORIGINAL_SIGNAL_REQUIRED')
    signal = checked['signals'][0]
    plan = signal['exit_and_size_plan']
    snapshot = deepcopy(origin)
    require(snapshot['schema'] == GOLD_ACCOUNT_TERMS_SCHEMA and snapshot['symbol'] == 'XAUUSDT', 'DEMO_ORIGIN')
    state = _state(dispatch, recovery, oms)
    context = position.load_context(state, symbol='XAUUSDT',
                                    account_ref=snapshot['account_ref_sha256'], as_of_ms=now_ms)
    account = position.validate_origin(snapshot, context, as_of_ms=now_ms)
    result = {'schema': 'GOLD_DEMO_STRATEGY_EXIT_DRAFT_V1', 'environment': 'BINANCE_USDM_DEMO',
              'observed_at_ms': now_ms, 'origin_sha256': _digest(snapshot),
              'position_context_sha256': _digest(context), 'review_sha256': _digest(checked),
              'strategy_definition_sha256': checked['strategy_definition_sha256'],
              'research_identity': checked['research_identity'], 'orders': [],
              'submission_enabled': False, 'economic_authority': False, 'risk_status': 'NOT_RUN',
              'authorization_status': 'STRATEGY_ENROLLMENT_REQUIRED'}
    quantity = Decimal(context['quantity'])
    if quantity == 0:
        result.update(status='FLAT', reason='NO_REMAINING_POSITION')
        return result
    require(0 < quantity <= Decimal(plan['quantity']), 'UNEXPECTED_POSITION')
    episode = context['position_episode']
    require(type(episode) is dict and episode.get('status') == 'REPORTED_FILL_START', 'START_UNKNOWN')
    entry_client = entry_client_order_id(checked, snapshot['account_ref_sha256'])
    require(episode['client_order_id'] == entry_client, 'OTHER_STRATEGY_EPISODE')
    reservations = [row for row in state['reservations'] if row['record_hash'] == episode['reservation_sha256']]
    require(len(reservations) == 1, 'ENTRY_RESERVATION')
    orders = reservations[0]['payload']['orders']
    require(len(orders) == 1 and orders[0]['newClientOrderId'] == entry_client
            and orders[0]['symbol'] == 'XAUUSDT' and orders[0]['side'] == 'BUY'
            and orders[0]['reduceOnly'] is False
            and Decimal(orders[0]['quantity']) == Decimal(plan['quantity']), 'ENTRY_BINDING')
    reads = {row['kind']: row for row in snapshot['reads']}
    book_read = reads['book']; book = book_read['data']
    clocks = [reads[k] for k in ('clock_before', 'clock_after')]
    # Conservative union of observed venue/local clock offset intervals. Do not
    # compare a venue fill timestamp directly to local process time.
    offset_lower = min(row['data']['serverTime']-row['received_at_ms'] for row in clocks)
    offset_upper = max(row['data']['serverTime']-row['requested_at_ms'] for row in clocks)
    quote_lower, quote_upper = book['time']-offset_upper, book['time']-offset_lower
    require(0 <= quote_lower <= quote_upper <= book_read['received_at_ms'] <= now_ms
            and now_ms-quote_lower < plan['max_quote_age_ms'], 'QUOTE_TIME_UNKNOWN_OR_STALE')
    opened = episode['opened_venue_at_ms']
    rule = ExitPlan(opened, Decimal(plan['stop']), Decimal(plan['target']), opened+plan['holding_ms'])
    # Both timestamps are venue timestamps. Timeout is evaluated only as far
    # as the fresh observed book, not an invented venue "now" after receipt.
    reason = exit_condition(rule, bid=Decimal(book['bidPrice']), at_ms=book['time'])
    # A persistent account-level pilot stop is an additional exit trigger.
    # Ownership, fresh quote, venue rules and all downstream approval/risk gates
    # still apply. This produces only an independently reviewable reduction.
    if dispatch is not None:
        pilot = demo_gold_pilot.observe(
            {**account, 'account_ref_sha256': snapshot['account_ref_sha256']},
            now_ms=now_ms, origin_sha256=_digest(snapshot), state_root=dispatch.path.parent)
        if pilot is not None:
            result['pilot_control'] = pilot
            if pilot['status'] == 'STOPPED':
                reason = 'PILOT_'+pilot['reason']
    result.update(reason=reason, position_episode=episode, exit_and_size_plan=plan,
                  valid_until_ms=quote_lower+plan['max_quote_age_ms'])
    if reason == 'NO_EXIT_TRIGGER':
        result['status'] = 'HOLDING'
        return result
    exit_id = _digest({'entry_client_order_id': entry_client,
                       'ledger_state_hash': context['ledger_state_hash'], 'action': 'REDUCE_LONG'})
    order = dict(symbol='XAUUSDT', side='SELL', type='LIMIT', timeInForce='IOC',
                 quantity=canonical_decimal(quantity), price=canonical_decimal(Decimal(book['bidPrice'])),
                 reduceOnly=True, positionSide='BOTH', newClientOrderId='QD'+exit_id[:32], newOrderRespType='ACK')
    validate_orders([order], schema=GOLD_BATCH_SCHEMA)
    _venue_rules([order], snapshot)
    _venue_account_cap([order], snapshot, context)
    result.update(status='STRATEGY_EXIT_DRAFT', orders=[order],
                  required_before_execution=['STRATEGY_SPECIFIC_ENROLLMENT', 'INDEPENDENT_RISK_AND_SIGNATURE',
                      'FRESH_ORIGINAL_POSITION_RECHECK', 'ORIGINAL_DISPATCH_DEDUPLICATION'])
    result['result_hash'] = _digest(result)
    return result
