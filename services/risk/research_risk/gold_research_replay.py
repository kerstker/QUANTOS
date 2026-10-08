"""Detached Gold net-account review with the original research risk engines.

The owning research read set composes this port. Validation never imports risk
backwards. This is diagnostic engine execution, not independent mathematics,
OOS, provenance, model maturity, candidate approval or order permission.
Shared-account contributions are not independent alpha books. Missing model
facts remain unavailable until an original trial/lifecycle binding exists.
"""

from dataclasses import asdict
from decimal import Decimal
import math

from quantos.contracts.alpha import AlphaCluster
from quantos.contracts.gold_research_risk_policy import validate, policy_sha256, INTEGER_FIELDS
from quantos.streaming_identity import exact_stream_identity_hash as digest
from services.validation.gold_account_risk_inputs import GoldAccountRiskInputs
from .domain.models import RiskConfig
from .domain.risk_modules import (
    CapacityRiskEngine, ConcentrationRiskEngine, CorrelationRiskEngine, DrawdownRiskEngine,
    LiquidityRiskEngine, MarketRiskEngine, RegimeRiskEngine, StressTestEngine,
)

SCHEMA = 'QUANTOS_GOLD_NINE_ENGINE_REPLAY_V1'
ENGINES = ('capacity', 'concentration', 'correlation', 'drawdown', 'liquidity', 'market',
           'model_alpha', 'regime', 'stress')


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_NINE_ENGINE_' + reason)


def number(value):
    require(not isinstance(value, bool) and isinstance(value, (int, float, Decimal)), 'NUMBER')
    converted = float(value)
    require(math.isfinite(converted) and (converted != 0 or value == 0), 'FINITE_NUMBER')
    return converted


def missing(reason):
    return dict(status='INSUFFICIENT', invocations=0, original_report=None, reasons=[reason])


def recorded(report):
    value = asdict(report)
    # A nonfinite calculation must never enter signed JSON as a passed report.
    require(all(not isinstance(v, float) or math.isfinite(v) for v in value.values()), 'ENGINE_NONFINITE')
    return dict(status='PASS' if report.passed else 'FAIL', invocations=1,
                original_report=value, reasons=[])


def combine(current, report, *, score):
    if current['invocations'] == 0:
        return report
    count = current['invocations'] + report['invocations']
    failed = current['status'] == 'FAIL' or report['status'] == 'FAIL'
    winner = report if score(report) > score(current) else current
    result = dict(winner, invocations=count, status='FAIL' if failed else 'PASS')
    return result


def review_gold_account(inputs, *, maximum_atomic_points=100_000_000, maximum_intervals=100_000_000):
    require(isinstance(inputs, GoldAccountRiskInputs), 'INPUT_PORT')
    require(all(type(v) is int and 0 < v <= 100_000_000
                for v in (maximum_atomic_points, maximum_intervals)), 'BUDGET')
    require(inputs.currency == 'USDT', 'EXPLICIT_UNIT')
    gates = {name: missing('REGISTERED_FULL_RISK_POLICY_MISSING') for name in ENGINES}
    bound = inputs.projection.get('risk_policy')
    policy = None
    if bound is not None:
        policy = validate(bound['policy'], sample=inputs.projection['sample'])
        require(policy_sha256(policy) == bound['policy_sha256'], 'POLICY_BINDING')
        config = RiskConfig(**{k: v if k in INTEGER_FIELDS else number(Decimal(v))
                               for k, v in policy['config'].items()})
        require(number(inputs.periods_per_year) == config.periods_per_year, 'ANNUALIZATION')
        gates = {name: missing('ORIGINAL_INPUTS_UNAVAILABLE') for name in ENGINES}
        _evaluate(inputs, policy, config, gates, maximum_atomic_points, maximum_intervals)
        if not inputs.projection['full_run_covered']:
            for gate in gates.values():
                if gate['status'] == 'PASS':
                    gate['status'] = 'INSUFFICIENT'
                    gate['reasons'].append('FULL_RUN_SAMPLE_COVERAGE_INCOMPLETE')
    failed = [name for name, value in gates.items() if value['status'] == 'FAIL']
    unavailable = [name for name, value in gates.items() if value['status'] == 'INSUFFICIENT']
    executed = sum(value['invocations'] > 0 for value in gates.values())
    value = dict(schema=SCHEMA, status='REJECTED' if failed else 'INSUFFICIENT' if unavailable else 'PASS',
        native_result_hash=inputs.projection['native_result_hash'],
        account_inputs_sha256=digest(inputs.projection),
        policy_sha256=None if policy is None else policy_sha256(policy),
        currency=inputs.currency, sample=dict(inputs.projection['sample']), engines=gates,
        engines_configured=9, engines_executed=executed,
        engines_passed=sum(v['status'] == 'PASS' for v in gates.values()),
        missing_engines=unavailable, failed_engines=failed,
        engine_replay_completed=executed == 9,
        full_research_risk_9_of_9=not failed and not unavailable and executed == 9,
        mathematical_independence=False,
        limitations=['SAME_ORIGINAL_RISK_ENGINES_NOT_INDEPENDENT_MATHEMATICS',
                     'SOURCE_POLICY_TIME_AND_LOCKED_OOS_REQUIRE_INDEPENDENT_VERIFICATION',
                     'MODEL_FACTS_REQUIRE_ORIGINAL_TRIAL_AND_LIFECYCLE_BINDING',
                     'RISK_REPLAY_IS_NOT_CANDIDATE_QUALIFICATION'],
        economic_authority=False, promotion_approved=False, submission_enabled=False)
    value['replay_sha256'] = digest(value)
    return value


def _evaluate(inputs, policy, config, gates, maximum_points, maximum_intervals):
    sample = policy['sample']
    expected = (sample['end_ms'] - sample['start_ms']) // sample['frequency_ms']
    require(expected <= maximum_intervals, 'INTERVAL_BUDGET')
    vectors = (inputs.returns, inputs.equity_curve, inputs.timestamps, inputs.initial_equity)
    require(all(v is None for v in vectors) or all(v is not None for v in vectors), 'ALIGNED_SERIES')
    returns = curve = None
    if inputs.returns is not None:
        require(len(inputs.returns) == len(inputs.equity_curve) == len(inputs.timestamps) == expected,
                'ALIGNED_SERIES')
        require(all(type(t) is int and t == sample['start_ms'] + (i + 1) * sample['frequency_ms']
                    for i, t in enumerate(inputs.timestamps)), 'RETURN_AXIS')
        returns = [number(v) for v in inputs.returns]
        curve = [number(inputs.initial_equity), *(number(v) for v in inputs.equity_curve)]
        require(all(v > 0 for v in curve), 'POSITIVE_EQUITY')
        if len(returns) >= 2:
            gates['market'] = recorded(MarketRiskEngine().evaluate(returns, config))
        else:
            gates['market'] = missing('AT_LEAST_TWO_RETURN_OBSERVATIONS_REQUIRED')
        gates['stress'] = recorded(StressTestEngine().evaluate(returns, config, timestamps=inputs.timestamps))
        if inputs.market_states is not None and inputs.market_states.labels is not None:
            require(len(inputs.market_states.labels) == len(returns), 'REGIME_AXIS')
            gates['regime'] = recorded(RegimeRiskEngine().evaluate(returns, list(inputs.market_states.labels), config))

    if inputs.position_history is not None and curve is not None:
        def atomic():
            for i, row in enumerate(inputs.position_history.items()):
                require(i < maximum_points, 'ATOMIC_BUDGET')
                if row['available_at_ms'] <= sample['end_ms']:
                    value = number(Decimal(row['equity']))
                    require(value > 0, 'ATOMIC_EQUITY')
                    yield value
        atomic_report = DrawdownRiskEngine().evaluate_stream(atomic(), config)
        duration_report = DrawdownRiskEngine().evaluate(curve, config)
        gate = recorded(atomic_report)
        # The policy measures duration in declared return intervals, never
        # in variable-density mark/funding/fill events. Both are original math.
        passed = atomic_report.max_drawdown <= config.max_drawdown and duration_report.passed
        gate.update(status='PASS' if passed else 'FAIL', invocations=2,
                    original_duration_report=asdict(duration_report), duration_unit='DECLARED_RETURN_INTERVALS')
        gates['drawdown'] = gate

    attribution = inputs.net_attribution
    if attribution is not None:
        if attribution.projection['weights_status'] == 'AVAILABLE':
            cluster = AlphaCluster(cluster_id='XAUUSDT_SHARED_EXPOSURE',
                member_ids=tuple(attribution.weights), representative_id=next(iter(attribution.weights)))
            exposure_count = 0
            for i, row in enumerate(attribution.history.items()):
                require(i < maximum_points, 'ALLOCATION_BUDGET')
                require(row['available_at_ms'] <= sample['end_ms'], 'ALLOCATION_TIME')
                require(row['signed_weights'] is not None, 'ALLOCATION_WEIGHTS')
                weights = {k: number(Decimal(v)) for k, v in row['signed_weights'].items()}
                report = ConcentrationRiskEngine().evaluate(weights, [cluster], config)
                active = any(v != 0 for v in weights.values())
                exposure_count += active
                gate = recorded(report)
                # Equity-relative weights do not make one instrument many
                # independent bets merely because gross exposure is small.
                if active and config.min_effective_number_of_bets > 1:
                    gate['status'] = 'FAIL'
                gate.update(shared_instrument_effective_bets_upper_bound=1 if active else 0,
                    original_receipt=dict(available_at_ms=row['available_at_ms'], sequence=row['sequence']))
                gates['concentration'] = combine(gates['concentration'], gate,
                    score=lambda x: (x['status'] == 'FAIL', x['original_report']['max_cluster']))
            gates['concentration']['exposure_observation_count'] = exposure_count
        ids = attribution.projection['active_component_ids']
        gates['correlation'].update(component_ids=list(ids), independent_strategy_pnl=False)
        if attribution.contribution_returns is not None and returns is not None:
            matrix = {k: [number(v) for v in attribution.contribution_returns[k]] for k in ids}
            require(all(len(v) == len(returns) for v in matrix.values()), 'CONTRIBUTION_AXIS')
            if len(matrix) >= 2 and len(returns) >= 2 and all(len(set(v)) > 1 for v in matrix.values()):
                gates['correlation'] = dict(recorded(CorrelationRiskEngine().evaluate(matrix, config)),
                    component_ids=list(ids), independent_strategy_pnl=False)
            else:
                gates['correlation']['reasons'] = ['INSUFFICIENT_ACTIVE_NONCONSTANT_CONTRIBUTION_SERIES']
    else:
        gates['correlation']['component_ids'] = []

    capacity = inputs.capacity_inputs
    if capacity is not None and capacity.atomic_observations is not None:
        incomplete = False
        closed = 0
        for i, row in enumerate(capacity.atomic_observations.items()):
            require(i < maximum_points and row['currency'] == 'USDT'
                and row['pool_instrument'] == 'XAUUSDT' and row['decision_at_ms'] <= sample['end_ms'],
                'ATOMIC_CAPACITY_BINDING')
            weights = None if row['signed_weights'] is None else {
                k: number(Decimal(v)) for k, v in row['signed_weights'].items()}
            if weights is not None and not any(weights.values()):
                closed += 1
                continue
            if row['status'] != 'AVAILABLE' or weights is None:
                incomplete = True
                continue
            book = {k: v for k, v in weights.items() if v != 0}
            amounts = {k: number(Decimal(v)) for k, v in row['alpha_daily_capacity'].items()}
            # Currency is explicitly USDT. Legacy engine argument spelling
            # "capacity_cny" is a generic amount; this port performs no FX.
            aum, adv = (number(Decimal(row[k])) for k in ('account_equity', 'original_daily_adv'))
            for name, report, metric in (
                ('capacity', CapacityRiskEngine().evaluate(book, amounts, aum, config), 'capacity_usage'),
                ('liquidity', LiquidityRiskEngine().evaluate(book, amounts, adv, aum, config), 'participation_rate'),
            ):
                gate = recorded(report)
                gate['original_receipt'] = dict(available_at_ms=row['decision_at_ms'],
                    sequence=row['account_sequence'], capacity_snapshot_sha256=row['capacity_snapshot_sha256'])
                gates[name] = combine(gates[name], gate,
                    score=lambda x, m=metric: (x['status'] == 'FAIL', x['original_report'][m]))
        for name in ('capacity', 'liquidity'):
            gates[name]['closed_book_observations'] = closed
            if incomplete:
                gates[name]['reasons'].append('ATOMIC_CAPACITY_OR_ACCOUNT_COVERAGE_INCOMPLETE')
                if gates[name]['status'] != 'FAIL':
                    gates[name]['status'] = 'INSUFFICIENT'
    gates['model_alpha'] = missing('BOUND_ORIGINAL_TRIAL_MODEL_FACTS_MISSING')
