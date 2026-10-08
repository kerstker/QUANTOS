"""Owned long/short bar-strategy reduction drafts, never a dispatch permit.

Exit intent survives process replacement in original executor-owned storage.
Original stores and venue gates are mandatory; no caller-supplied position basis.
"""
from copy import deepcopy
from decimal import Decimal

from live.execution import demo_position_context as position, demo_gold_pilot, demo_bar_exit_trigger
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state, _venue_rules, _venue_account_cap
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.research.demo_execution_origin import GOLD_ACCOUNT_TERMS_SCHEMA
from services.research.gold_bar_candidates import INTERVAL_MS, evaluate
from services.validation.demo_submission import validate_orders, GOLD_BATCH_SCHEMA
from tools.gold_bar_demo_entry import BarDemoEntry


def require(value, reason):
    if not value:
        raise ValueError('GOLD_BAR_DEMO_EXIT_' + reason)


@ledger_decimal_context()
def prepare(*, engine, entry_decision_ms, origin, dispatch, recovery, oms, now_ms):
    require(type(engine) is BarDemoEntry and type(now_ms) is int and 0 <= now_ms <= 2**63-1, 'INPUT')
    definition = engine.verified_definition()
    signal = engine.entry_signal(entry_decision_ms)
    require(entry_decision_ms < now_ms, 'FUTURE_ENTRY')
    snapshot = deepcopy(origin)
    require(type(snapshot) is dict and snapshot.get('schema') == GOLD_ACCOUNT_TERMS_SCHEMA
            and snapshot.get('symbol') == 'XAUUSDT', 'ORIGIN')
    state = _state(dispatch, recovery, oms)
    context = position.load_context(state, symbol='XAUUSDT',
        account_ref=snapshot['account_ref_sha256'], as_of_ms=now_ms)
    account = position.validate_origin(snapshot, context, as_of_ms=now_ms)
    result = dict(schema='GOLD_BAR_DEMO_EXIT_DRAFT_V1', environment='BINANCE_USDM_DEMO',
        definition_hash=definition['definition_hash'], trial_id=definition['trial_id'],
        origin_sha256=_digest(snapshot), position_context_sha256=_digest(context), observed_at_ms=now_ms,
        orders=[], submission_enabled=False, economic_authority=False, risk_status='NOT_RUN',
        authorization_status='BAR_STRATEGY_ENROLLMENT_REQUIRED')
    quantity = Decimal(context['quantity'])
    require(quantity.is_finite(), 'QUANTITY')
    if not quantity:
        return dict(result, status='FLAT')
    side = signal['entry_side']
    require((quantity > 0) == (side == 'BUY') and abs(quantity) <= Decimal(definition['size_and_timing']['quantity']), 'QUANTITY')
    client = 'QD' + _digest(dict(account_ref_sha256=snapshot['account_ref_sha256'],
        definition_hash=definition['definition_hash'], decision_id=signal['decision_id'], action='OPEN_'+side))[:32]
    episode = context['position_episode']
    require(type(episode) is dict and episode.get('status') == 'REPORTED_FILL_START'
            and episode.get('client_order_id') == client, 'OWNERSHIP')
    reservations = [r for r in state['reservations'] if r['record_hash'] == episode['reservation_sha256']]
    require(len(reservations) == 1 and len(reservations[0]['payload']['orders']) == 1, 'RESERVATION')
    entry = reservations[0]['payload']['orders'][0]
    require(entry['newClientOrderId'] == client and entry['symbol'] == 'XAUUSDT'
            and entry['side'] == side and entry['reduceOnly'] is False
            and Decimal(entry['quantity']) == Decimal(definition['size_and_timing']['quantity']), 'ENTRY_BINDING')
    average = Decimal(context['average_entry_price'])
    distance = Decimal(signal['stop_distance'])
    require(average.is_finite() and distance.is_finite() and 0 < distance < average, 'BASIS')
    reads = {r['kind']: r for r in snapshot['reads']}
    book_read = reads['book']
    book = book_read['data']
    clocks = [reads[k] for k in ('clock_before', 'clock_after')]
    lower = min(r['data']['serverTime']-r['received_at_ms'] for r in clocks)
    upper = max(r['data']['serverTime']-r['requested_at_ms'] for r in clocks)
    quote_lower, quote_upper = book['time']-upper, book['time']-lower
    require(0 <= quote_lower <= quote_upper <= book_read['received_at_ms'] <= now_ms
            and now_ms-quote_lower < definition['size_and_timing']['max_quote_age_ms'], 'QUOTE_CLOCK')
    opened = episode['opened_venue_at_ms']
    require(type(opened) is int and 0 <= opened <= book['time'], 'OPEN_TIME')
    price = Decimal(book['bidPrice' if quantity > 0 else 'askPrice'])
    stop_price = average-distance if quantity > 0 else average+distance
    stop = price <= stop_price if quantity > 0 else price >= stop_price
    timed = book['time'] >= opened+signal['max_holding_bars']*INTERVAL_MS
    current = None if engine._cursor.blocked else evaluate(engine._cursor.bars,
        candidate=definition['candidate'], as_of_ms=now_ms)
    reverse = (current is not None and current['status'] == 'READY'
               and any(b.available_ms > entry_decision_ms and b.available_ms <= now_ms for b in engine._cursor.bars)
               and current['exit_long' if quantity > 0 else 'exit_short'])
    pilot = demo_gold_pilot.observe(dict(account, account_ref_sha256=snapshot['account_ref_sha256']),
        now_ms=now_ms, origin_sha256=_digest(snapshot))
    require(pilot is not None, 'PILOT_REQUIRED')
    reason = ('PILOT_'+pilot['reason'] if pilot['status'] == 'STOPPED' else
              'STOP' if stop else 'TIMEOUT' if timed else 'SIGNAL_EXIT' if reverse else None)
    binding = dict(account_ref_sha256=snapshot['account_ref_sha256'],
        definition_hash=definition['definition_hash'], entry_client_order_id=client,
        reservation_sha256=episode['reservation_sha256'], opened_venue_at_ms=opened)
    trigger = demo_bar_exit_trigger.load(binding, now_ms=now_ms)
    if trigger is None and reason:
        trigger = demo_bar_exit_trigger.retain(binding, reason=reason, now_ms=now_ms,
            origin_sha256=_digest(snapshot), position_context_sha256=_digest(context))
    reason = trigger['reason'] if trigger else None
    if trigger:
        result['exit_trigger_sha256'] = trigger['record_hash']
    result.update(pilot_control=pilot, average_entry_price=canonical_decimal(average),
        stop_price=canonical_decimal(stop_price), position_episode=episode,
        valid_until_ms=quote_lower+definition['size_and_timing']['max_quote_age_ms'])
    if not reason:
        return dict(result, status='HOLDING', reason='NO_EXIT_TRIGGER')
    order = dict(symbol='XAUUSDT', side='SELL' if quantity > 0 else 'BUY', type='LIMIT', timeInForce='IOC',
        quantity=canonical_decimal(abs(quantity)), price=canonical_decimal(price), reduceOnly=True,
        positionSide='BOTH', newClientOrderId='QD'+_digest(dict(entry_client_order_id=client,
        ledger_state_hash=context['ledger_state_hash'], action='REDUCE_'+side))[:32], newOrderRespType='ACK')
    validate_orders([order], schema=GOLD_BATCH_SCHEMA)
    _venue_rules([order], snapshot)
    _venue_account_cap([order], snapshot, context)
    result.update(status='BAR_EXIT_DRAFT', reason=reason, orders=[order],
        required_before_execution=['BAR_STRATEGY_ENROLLMENT', 'INDEPENDENT_RISK_AND_SIGNATURE',
            'FRESH_ORIGINAL_POSITION_RECHECK', 'ORIGINAL_DISPATCH_DEDUPLICATION',
            'CONTROLLER_RESTORE_OF_ORIGINAL_BAR_AND_EXECUTION_HISTORY'])
    return dict(result, result_hash=_digest(result))
