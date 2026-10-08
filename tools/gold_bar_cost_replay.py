"""Compare fixed bar hypotheses on subsequent real public quote observations.

Historical candles warm indicators only. This never invents historical bid/ask
or backdates candle receipts; it is not a historical OOS performance evaluation.
"""
import hashlib
from decimal import Decimal

from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_public_replay import prepare_public_replay
from services.execution.perpetual_simulation import (
    SimulationCostModel, run_linear_perpetual_simulation,
)
from services.research.gold_bar_candidates import CANDIDATES, evaluate
from services.research.gold_bar_dataset import load_bars
from services.research.gold_bar_v2_strategy import GoldBarV2Strategy
from services.research.gold_loss_control import GoldResearchLossControl
from services.research.gold_tape_execution import execute


def compare_public(*, raw_bars, bar_receipt, capture, release_identity,
                   quantity, initial_collateral, fee_rate, frame_limit=None,
                   observed_funding_records=None, settlement_window_ms=None, history=None):
    if (hashlib.sha256(raw_bars).hexdigest() != bar_receipt['response_sha256']
            or capture.get('schema_version') not in ('GOLD_PUBLIC_REPLAY_CAPTURE_V1', 'GOLD_PUBLIC_CONTINUOUS_CAPTURE_V1')
            or capture.get('symbol') != 'XAUUSDT'
            or type(fee_rate) is not Decimal or not fee_rate.is_finite() or fee_rate <= 0):
        raise ValueError('GOLD_BAR_COMPARISON_INPUT')
    bars = load_bars(raw_bars, requested_at_ms=bar_receipt['requested_at_ms'],
                     received_at_ms=bar_receipt['received_at_ms'])
    prepared = prepare_public_replay(capture, observed_funding_records=observed_funding_records,
                                     settlement_window_ms=settlement_window_ms)
    if frame_limit is not None:
        if type(frame_limit) is not int or not 1 <= frame_limit <= len(prepared['frames']):
            raise ValueError('INVALID_BAR_FRAME_PREFIX')
        prepared['frames'] = prepared['frames'][:frame_limit]
    if bars[-1].available_ms > prepared['frames'][0].timestamp:
        raise ValueError('BAR_HISTORY_UNAVAILABLE_AT_OBSERVATION')
    identity = dict(bar_source_sha256=bar_receipt['response_sha256'],
                              bar_receipt=bar_receipt, quote_manifest=prepared['manifest'],
                              evaluated_frames=len(prepared['frames']))
    return _compare_bars(bars=bars, prepared=prepared, dataset_identity=identity,
        release_identity=release_identity, quantity=quantity,
        initial_collateral=initial_collateral, fee_rate=fee_rate, history=history)


def _compare_bars(*, bars, prepared, dataset_identity, release_identity,
                  quantity, initial_collateral, fee_rate, entry_not_before_ms=None,
                  incremental_bars=False, history=None):
    if type(fee_rate) is not Decimal or not fee_rate.is_finite() or fee_rate <= 0:
        raise ValueError('GOLD_BAR_COMPARISON_COST')
    common = dict(release_identity=release_identity, dataset_identity=dataset_identity,
        initial_collateral=initial_collateral, costs=SimulationCostModel(fee_rate=fee_rate),
        **{k: prepared[k] for k in ('contract_rules', 'funding_policies', 'order_rule_profiles', 'frames')})
    for key in ('environment_updates', 'replay_budget'):
        if key in prepared:
            common[key] = prepared[key]
    baseline_config = dict(common, strategy=lambda ctx: (),
        strategy_identity=dict(version='GOLD_BAR_CASH_BASELINE_V1'))
    baseline = (run_linear_perpetual_simulation(**baseline_config) if history is None
                else history.run(baseline_config))
    trials = []
    initial_bars = bars
    if incremental_bars:
        if any(a.available_ms > b.available_ms for a, b in zip(bars, bars[1:])):
            raise ValueError('BAR_RECEIPT_ORDER_REQUIRED_FOR_INCREMENTAL_REPLAY')
        initial_bars = tuple(b for b in bars if b.available_ms <= prepared['frames'][0].timestamp)
    for candidate in CANDIDATES:
        evaluate(bars, candidate=candidate, as_of_ms=0)
        strategy = GoldBarV2Strategy(initial_bars, candidate=candidate, quantity=quantity,
                                     entry_timeout_ms=5000, max_quote_age_ms=30000,
                                     entry_not_before_ms=entry_not_before_ms)
        entry_identity = ({} if entry_not_before_ms is None else
                          {'entry_not_before_ms': entry_not_before_ms})
        control = GoldResearchLossControl(strategy, initial_equity=initial_collateral)
        configuration = dict(common, strategy=control,
            risk_policy=control.risk, risk_policy_identity=control.identity,
            strategy_identity=dict(version='GOLD_BAR_PUBLIC_OBSERVATION_V1',
                candidate=candidate, quantity=str(quantity), entry_timeout_ms=5000,
                max_quote_age_ms=30000, **entry_identity))
        if incremental_bars:
            engine = execute(configuration, strategy, bars, initial_bars, history=history)
        else:
            engine = (run_linear_perpetual_simulation(**configuration) if history is None
                      else history.run(configuration, strategy=strategy))
        pending = any(o['status'] in ('OPEN', 'PARTIALLY_FILLED') for o in engine['orders'])
        outcome = ('OPEN_AT_END' if engine['ending_state']['positions'] or pending else
                   'INPUT_UNAVAILABLE' if not any(o['status'] == 'READY' for o in strategy.observations) else
                   'NO_SIGNAL' if not strategy.events else
                   'NO_FILL' if not any(o['filled_at'] is not None for o in engine['orders']) else
                   'FLAT_TERMINAL')
        trials.append(dict(candidate=candidate, outcome=outcome,
                           signal_observations=strategy.observations,
                           strategy_events=strategy.events, simulation=engine,
                           loss_control=control.report(engine)))
    result = dict(schema='GOLD_BAR_PUBLIC_COMPARISON_V1', trials=trials, cash_baseline=baseline,
        release_identity=release_identity, dataset_identity=common['dataset_identity'],
        fee_policy='EXPLICIT_UNCALIBRATED_SIMULATION_RATE', fee_rate=str(fee_rate),
        winner=None, economic_eligible=False, authoritative=False, submission_enabled=False,
        historical_oos_evaluated=False, limitations=[
            'BOUNDED_FORWARD_QUOTE_WINDOW_NOT_HISTORICAL_BACKTEST',
            'HISTORICAL_CANDLES_ARE_WARMUP_ONLY', 'NO_AUTOMATIC_STRATEGY_SELECTION',
            ('FIVE_PERCENT_STOP_RETAINS_ORIGINAL_ACCOUNT_ACROSS_DECLARED_SEGMENTS'
             if 'environment_updates' in prepared else
             'FIVE_PERCENT_STOP_IS_PER_SIMULATED_TAPE_NOT_CROSS_WINDOW_ACCOUNT'),
            'LOSS_STOP_EXIT_COSTS_AND_GAPS_CAN_EXCEED_TRIGGER_THRESHOLD',
            *prepared['manifest']['limitations']])
    from quantos.streaming_identity import exact_stream_identity_hash
    hash_result = exact_identity_hash if history is None else exact_stream_identity_hash
    result['result_hash'] = hash_result(result, name='gold_bar_public_comparison')
    return result
