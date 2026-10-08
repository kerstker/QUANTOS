"""Prepare a bounded gold strategy entry draft from retained observations.

No network, credentials, approvals or durable order intents. The draft is not a
Demo session input: strategy enrollment, risk and signed deadline enforcement
must be integrated before an execution consumer may accept it.
"""
import argparse
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import time

from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _venue_rules, _venue_account_cap
from services.execution.perpetual_ledger_v2 import ledger_decimal_context, canonical_decimal
from services.research.demo_execution_origin import assess, load, GOLD_ACCOUNT_TERMS_SCHEMA
from services.validation.demo_submission import validate_orders, GOLD_BATCH_SCHEMA
from tools.gold_forward_review import review


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_STRATEGY_ENTRY_'+reason)


def entry_client_order_id(checked, account_ref):
    return 'QD'+_digest({'account_ref_sha256': account_ref,
        'strategy_definition_sha256': checked['strategy_definition_sha256'],
        'decision_id': checked['signals'][0]['decision']['decision_id'], 'action': 'OPEN_LONG'})[:32]


@ledger_decimal_context()
def prepare(observation_directory, *, strategy_trial, origin, now_ms, prepared_strategy=None):
    require(type(now_ms) is int and 0 <= now_ms <= 2**63-1, 'TIME')
    checked = review(observation_directory, strategy_trial=strategy_trial,
                     **({'prepared_strategy': prepared_strategy} if prepared_strategy is not None else {}))
    result = {'schema': 'GOLD_DEMO_STRATEGY_ENTRY_DRAFT_V1',
              'environment': 'BINANCE_USDM_DEMO', 'observed_at_ms': now_ms,
              'review_sha256': _digest(checked),
              'strategy_definition_sha256': checked['strategy_definition_sha256'],
              'research_identity': checked['research_identity'],
              'orders': [], 'submission_enabled': False, 'economic_authority': False,
              'risk_status': 'NOT_RUN', 'authorization_status': 'STRATEGY_ENROLLMENT_REQUIRED'}
    if not checked['signals']:
        result['status'] = 'NO_ENTRY_SIGNAL'
        return result
    require(checked['observation_status'] == 'COMPLETE_OBSERVATION'
            and len(checked['signals']) == 1, 'SINGLE_COMPLETE_SIGNAL_REQUIRED')
    signal = checked['signals'][0]
    decision, plan = signal['decision'], signal['exit_and_size_plan']
    deadline = decision['decision_ms']+plan['entry_timeout_ms']
    require(decision['decision_ms'] < now_ms and signal['available_at_ms'] <= now_ms < deadline,
            'SIGNAL_EXPIRED_OR_FUTURE')
    snapshot = deepcopy(origin)
    require(type(snapshot) is dict and snapshot.get('schema') == GOLD_ACCOUNT_TERMS_SCHEMA
            and snapshot.get('symbol') == 'XAUUSDT', 'DEMO_ORIGIN_REQUIRED')
    assessment = assess(snapshot, as_of_ms=now_ms)
    require(assessment['status'] == 'OBSERVED_FLAT_QUOTE', 'ACCOUNT_NOT_FLAT_OR_UNRESOLVED')
    reads = {row['kind']: row for row in snapshot['reads']}
    book_read = reads['book']; book = book_read['data']
    clocks = [reads[k] for k in ('clock_before', 'clock_after')]
    # Match the exit draft's observed clock envelope. Venue epoch values are
    # not local signal/expiry times; keep the whole interval, not a midpoint.
    offset_lower = min(row['data']['serverTime']-row['received_at_ms'] for row in clocks)
    offset_upper = max(row['data']['serverTime']-row['requested_at_ms'] for row in clocks)
    quote_lower, quote_upper = book['time']-offset_upper, book['time']-offset_lower
    require(signal['available_at_ms'] <= quote_lower <= quote_upper
            <= book_read['received_at_ms'] <= now_ms
            and now_ms-quote_lower < plan['max_quote_age_ms'], 'QUOTE_NOT_CURRENT_FOR_SIGNAL')
    quantity, ask = Decimal(plan['quantity']), Decimal(book['askPrice'])
    require(Decimal(plan['stop']) < ask < Decimal(plan['target']), 'ENTRY_OUTSIDE_EXIT_PLAN')
    # One stable identity per account/definition/decision, including when a new
    # quote yields a revised draft. Original dispatch must own deduplication.
    order = {'symbol': 'XAUUSDT', 'side': 'BUY', 'type': 'LIMIT', 'timeInForce': 'IOC',
             'quantity': canonical_decimal(quantity), 'price': canonical_decimal(ask),
             'reduceOnly': False, 'positionSide': 'BOTH',
             'newClientOrderId': entry_client_order_id(checked, snapshot['account_ref_sha256']), 'newOrderRespType': 'ACK'}
    # Do not increase a strategy's size to meet venue minima or silently round
    # its quantity. An incompatible strategy must be returned for research.
    validate_orders([order], schema=GOLD_BATCH_SCHEMA)
    _venue_rules([order], snapshot)
    _venue_account_cap([order], snapshot)
    result.update(status='STRATEGY_ENTRY_DRAFT', orders=[order], decision=decision,
                  signal_available_at_ms=signal['available_at_ms'], signal_valid_until_ms=deadline,
                  valid_until_ms=min(deadline, quote_lower+plan['max_quote_age_ms']),
                  exit_and_size_plan=plan, origin_sha256=_digest(snapshot),
                  account_ref_sha256=snapshot['account_ref_sha256'],
                  price_policy='LIMIT_AT_OBSERVED_ASK_NO_SIZE_ADJUSTMENT',
                  required_before_execution=['STRATEGY_SPECIFIC_ENROLLMENT',
                      'INDEPENDENT_ADMISSION_AND_RISK', 'SIGNED_ENTRY_DEADLINE',
                      'ORIGINAL_DISPATCH_DEDUPLICATION', 'OMS_BOUND_EXIT_CONTROLLER'])
    result['result_hash'] = _digest(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation-dir', type=Path, required=True)
    parser.add_argument('--strategy-trial', type=Path, required=True)
    parser.add_argument('--origin', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.observation_dir, strategy_trial=args.strategy_trial,
                         origin=load(args.origin), now_ms=time.time_ns()//1000000)
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'status': 'REJECTED', 'submission_enabled': False}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
