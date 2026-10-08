"""Offline packet/signal/plan bridge to the sole V2 perpetual simulator.

Callers supply explicit historical rules, funding, executable quotes and costs.
Public trade packets are never converted into fictitious executable quotes.
This function neither registers economic eligibility nor prepares live orders.
"""
import hashlib
import json
from decimal import Decimal

from services.data.gold_execution_tape import new_gold_trade_cursor
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_simulation import run_linear_perpetual_simulation
from services.research.gold_replay import replay
from services.research.gold_v2_strategy import GoldV2Strategy


def _json_decimal(value):
    if type(value) is Decimal and value.is_finite():
        return str(value)
    raise ValueError('GOLD_COST_RESULT_NOT_SERIALIZABLE')


def replay_costs(*, document, plan, release_identity, market_identity,
                 contract_rules, funding_policies, frames, initial_collateral,
                 costs, order_rule_profiles=None, limits=None):
    """Evaluate one zone and its first confirmed signal, including a cash baseline.

    All signals are recomputed from retained packets. No selected signal, fill,
    cash-flow, or performance number is accepted from a caller. An unfinished
    position remains explicitly open; the last quote never forces liquidation.
    """
    raw = json.dumps(document, sort_keys=True, separators=(',', ':'),
                     allow_nan=False).encode()
    # Own the input used in both recomputation and run identity.
    document = json.loads(raw)
    signal_report = replay(document, cursor_factory=new_gold_trade_cursor)
    if signal_report['status'] != 'RESEARCH_SIGNALS_ONLY':
        raise ValueError('SIGNAL_INPUT_INCOMPLETE')
    signals = [r['decision'] for r in signal_report['records']
               if r['decision']['intent'] == 'LONG_RESEARCH_SIGNAL']
    if len(signals) > 1:
        raise ValueError('MULTIPLE_ZONE_SIGNALS_UNSUPPORTED')
    rules, policies, frames = tuple(contract_rules), tuple(funding_policies), tuple(frames)
    profiles = None if order_rule_profiles is None else tuple(order_rule_profiles)
    if (not rules or not policies or not frames or len(frames) > 20000
            or any(r.spec.instrument_id != 'XAUUSDT' for r in rules)
            or any(p.instrument_id != 'XAUUSDT' for p in policies)
            or any(item.instrument_id != 'XAUUSDT' for f in frames
                   for items in (f.quotes, f.market_events, f.capacity_snapshots) for item in items)
            or (profiles is not None and any(p.instrument_id != 'XAUUSDT' for p in profiles))):
        raise ValueError('GOLD_MARKET_SCOPE')
    source_hash = hashlib.sha256(raw).hexdigest()
    if signals:
        strategy = GoldV2Strategy.from_signal(signals[0], **plan)
        if strategy.signal_time not in {f.timestamp for f in frames}:
            raise ValueError('SIGNAL_FRAME_MISSING')
        callback = strategy
    else:
        # Validate and bind the unchanged proposed plan even for a losing/no-signal trial.
        strategy = GoldV2Strategy(signal_time_ms=document['decision_times_ms'][0],
                                  signal_id=source_hash, **plan)
        def callback(ctx):
            return ()
    engine = run_linear_perpetual_simulation(
        release_identity=release_identity,
        dataset_identity={'signal_input_sha256': source_hash, 'market': market_identity},
        strategy_identity={'version': 'GOLD_ZONE_COST_REPLAY_V1', 'plan': strategy.identity,
                           'signal_present': bool(signals)},
        contract_rules=rules, funding_policies=policies, frames=frames,
        initial_collateral=initial_collateral, costs=costs, strategy=callback,
        order_rule_profiles=profiles, limits=limits)
    open_orders = any(o['status'] in ('OPEN', 'PARTIALLY_FILLED') for o in engine['orders'])
    outcome = ('OPEN_AT_END' if engine['ending_state']['positions'] or open_orders
               else 'FLAT_TERMINAL' if signals else 'NO_SIGNAL')
    result = {'schema': 'GOLD_COST_REPLAY_V1', 'authoritative': False,
              'evidence_status': 'TECHNICAL_VALIDATION_ONLY', 'outcome': outcome,
              'signal_input_sha256': source_hash, 'signal_replay': signal_report,
              'plan': strategy.identity, 'strategy_events': strategy.events,
              'simulation': engine,
              'limitations': ['RETROSPECTIVE_SINGLE_ZONE', 'INPUT_AUTHENTICITY_UNPROVEN',
                              'VENUE_RULES_AND_COSTS_REQUIRE_INDEPENDENT_VALIDATION',
                              'NO_ECONOMIC_ADMISSION']}
    # Normalize exact window amounts before hashing the report that is persisted.
    # A generic default=str would silently admit unsupported objects.
    result = json.loads(json.dumps(result, default=_json_decimal, allow_nan=False))
    result['result_hash'] = exact_identity_hash(result, name='gold_cost_replay')
    return result
