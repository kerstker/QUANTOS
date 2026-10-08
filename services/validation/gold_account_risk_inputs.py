"""Produce risk inputs from one bound Gold net account, without new cash flows.

The original consumer authenticates the simulation, dataset and receipt bindings
before calling this module. Account returns are not attributed alpha returns.
Available inputs do not establish source authority, policy approval or full risk.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from services.validation.gold_net_attribution import NetAttribution
from services.validation.gold_market_states import MarketStateInputs
from services.validation.gold_capacity_inputs import CapacityInputs
from services.validation.gold_model_risk_inputs import GoldModelRiskInputs
from quantos.contracts.gold_chunked_source import MAX_PARTS
from quantos.contracts.research_statistics import DAY_MS, fixed_frequency_equity
from quantos.streaming_identity import StreamArray, exact_stream_identity_hash as exact_identity_hash

SCHEMA = 'QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V1'
POSITION_POLICY = 'ACTUAL_LEDGER_POSITION_MARKS_V1'
MISSING_GROUPS = ('weights', 'alpha_returns', 'regime_labels', 'alpha_metadata',
                  'capacity_and_aum', 'risk_config')


def _require(condition, reason):
    if not condition:
        raise ValueError('GOLD_ACCOUNT_RISK_' + reason)


def _number(value):
    _require(type(value) is str and 0 < len(value) <= 256, 'DECIMAL')
    number = Decimal(value)
    _require(number.is_finite() and (not number or -96 <= number.adjusted() <= 96), 'DECIMAL')
    return number


def _time(value):
    _require(type(value) is int and 0 <= value < 2**63, 'TIME')
    return value


def currency_from_dataset(dataset):
    """Use original public manifests; neither a symbol suffix nor implicit FX."""
    manifest = dataset['quote_manifest']
    parts = manifest.get('segments', [manifest])
    _require(isinstance(parts, Sequence) and not isinstance(parts, (str, bytes))
             and 0 < len(parts) <= MAX_PARTS, 'UNIT_MANIFEST')
    for part in parts:
        _require(isinstance(part, Mapping) and list(part['instruments']) == ['XAUUSDT']
                 and dict(part['units']) == dict(price='USDT', quantity='BASE', volume='QUOTE_USDT'),
                 'UNIT_MANIFEST')
    return 'USDT'


@dataclass(frozen=True)
class GoldAccountRiskInputs:
    """Aligned immutable net-account series for the existing risk engines.

    The equity curve/timestamp at each index is the END of that return interval;
    the separate initial equity is its anchor. Cash stays in its original unit.
    The small projection, rather than long vectors, enters the signed facts.
    """

    returns: tuple[Decimal, ...] | None
    equity_curve: tuple[Decimal, ...] | None
    timestamps: tuple[int, ...] | None
    initial_equity: Decimal | None
    opening_equity: Decimal
    currency: str
    periods_per_year: Decimal
    projection: dict
    position_history: StreamArray | None = field(default=None, compare=False, repr=False)
    net_attribution: NetAttribution | None = field(default=None, compare=False, repr=False)
    market_states: MarketStateInputs | None = field(default=None, compare=False, repr=False)
    capacity_inputs: CapacityInputs | None = field(default=None, compare=False, repr=False)
    model_inputs: GoldModelRiskInputs | None = field(default=None, compare=False, repr=False)


@ledger_decimal_context()
def _position_record(row, known, currency):
    positions = row.get('positions')
    _require(isinstance(positions, Mapping) and set(positions) <= {'XAUUSDT'}, 'POSITION_SHAPE')
    gross = Decimal(0)
    weights = {}
    equity = _number(row['equity'])
    for name, value in positions.items():
        _require(isinstance(value, Mapping) and set(value) == {
            'instrument_id', 'quantity', 'mark_price', 'contract_multiplier', 'margin_asset',
            'contract_rule_id', 'notional', 'margin_mode'}, 'POSITION_FIELDS')
        _require(value['instrument_id'] == name and value['margin_asset'] == currency
            and value['margin_mode'] in ('CROSS', 'ISOLATED')
            and type(value['contract_rule_id']) is str and 0 < len(value['contract_rule_id']) <= 256,
            'POSITION_IDENTITY')
        try:
            quantity, mark, multiplier, notional = (_number(value[k]) for k in
                ('quantity', 'mark_price', 'contract_multiplier', 'notional'))
        except ValueError as error:
            raise ValueError('GOLD_ACCOUNT_RISK_POSITION_NUMBER') from error
        _require(quantity != 0 and mark > 0 and multiplier > 0 and notional > 0
            and notional == abs(quantity) * mark * multiplier, 'POSITION_NOTIONAL')
        gross += notional
        if equity > 0:
            weights[name] = canonical_decimal(notional / equity * (1 if quantity > 0 else -1))
    return dict(timestamp=row['timestamp'], available_at_ms=known['timestamp'],
        sequence=row['sequence'], phase=row['phase'], equity=row['equity'],
        positions={name: dict(value) for name, value in positions.items()},
        gross_notional=canonical_decimal(gross),
        gross_weight=canonical_decimal(gross / equity) if equity > 0 else None,
        signed_weights=weights if equity > 0 else None)


def _position_inputs(simulation, history, observations, *, end, currency, covered, nonpositive):
    policy = simulation.get('position_history_policy')
    if policy is None:
        _require(all('positions' not in row or row['positions'] is None for row in history),
                 'POSITION_POLICY_MISSING')
        return dict(status='UNAVAILABLE_NOT_RECORDED'), None
    _require(policy == POSITION_POLICY, 'POSITION_POLICY')

    def records():
        for row, known in zip(history, observations(), strict=True):
            yield _position_record(row, known, currency)

    latest = last = peak_notional = peak_weight = None
    count = 0
    for value in records():
        last = value
        if value['available_at_ms'] > end:
            continue
        count += 1
        latest = value
        if peak_notional is None or _number(value['gross_notional']) > _number(peak_notional['value']):
            peak_notional = dict(value=value['gross_notional'], timestamp=value['timestamp'],
                available_at_ms=value['available_at_ms'], sequence=value['sequence'])
        if value['gross_weight'] is not None and (peak_weight is None
                or _number(value['gross_weight']) > _number(peak_weight['value'])):
            peak_weight = dict(value=value['gross_weight'], timestamp=value['timestamp'],
                available_at_ms=value['available_at_ms'], sequence=value['sequence'])
    ending = simulation['ending_state']['positions']
    _require(last is not None and isinstance(ending, Mapping)
             and set(last['positions']) == set(ending), 'POSITION_TERMINAL')
    for name, position in last['positions'].items():
        _require(all(position[k] == ending[name][k] for k in
            ('instrument_id', 'quantity', 'mark_price', 'margin_mode')), 'POSITION_TERMINAL')
    causal = StreamArray(lambda: (value for value in records() if value['available_at_ms'] <= end))
    projection = dict(schema='QUANTOS_GOLD_ACTUAL_ACCOUNT_POSITIONS_V1', policy=policy,
        status='AVAILABLE' if covered else 'UNAVAILABLE_SAMPLE_COVERAGE',
        observation_count=count, history_sha256=exact_identity_hash(causal),
        as_of_ms=end, latest=latest, peak_gross_notional=peak_notional, peak_gross_weight=peak_weight,
        weight_kind='SIGNED_INSTRUMENT_MARK_NOTIONAL_OVER_ACCOUNT_EQUITY',
        weights_status='UNAVAILABLE_NONPOSITIVE_EQUITY' if nonpositive else 'AVAILABLE',
        alpha_allocation_bound=False, independent_valuation_verified=False)
    return projection, causal


@ledger_decimal_context()
def build_account_risk_inputs(simulation, *, sample, settlements, account_currency,
                              target_currency=None, streamed=False,
                              maximum_points=100_000, maximum_events=100_000, grid_budget=None,
                              research_risk_policy=None, portfolio=None, market_state_policy=None,
                              market_bars=None, market_history_policy=None, capacity_policy=None,
                              capacity_history=None, capacity_history_policy=None):
    """Consume original equity on the receipt-available clock, preserving losses.

    Malformed originals raise. Nonpositive equity or an uncovered window produces
    unavailable return inputs, never cleaned returns or extended flat history.
    Drawdown consumes atomic observations, not the fixed-frequency samples.
    """
    from services.validation.gold_native_statistics import (
        receipt_available_history, receipt_available_history_stream,
    )

    _require(type(streamed) is bool and all(type(v) is int and 0 < v <= (
        100_000_000 if streamed else 100_000) for v in (maximum_points, maximum_events)), 'BUDGET')
    _require(account_currency == 'USDT' and (target_currency is None or target_currency == account_currency), 'UNIT')
    _require(isinstance(sample, Mapping) and set(sample) == {'start_ms', 'end_ms', 'frequency_ms'}, 'SAMPLE')
    start, end, frequency = (_time(sample[k]) for k in ('start_ms', 'end_ms', 'frequency_ms'))
    _require(frequency > 0 and end >= start, 'SAMPLE')
    # Validate the grid budget even for an unavailable window.
    from quantos.contracts.gold_long_statistics_budget import validate as validate_grid_budget
    intervals = 100_000 if grid_budget is None else validate_grid_budget(grid_budget)['maximum_points'] - 1
    _require((end - start) // frequency <= intervals, 'GRID_BUDGET')
    history = simulation['equity_history']
    _require(isinstance(history, Sequence) and not isinstance(history, (str, bytes))
             and 0 < len(history) <= maximum_points, 'HISTORY_BUDGET')
    _require(len(simulation['ledger_events']) <= maximum_events, 'HISTORY_BUDGET')
    available = (receipt_available_history_stream(simulation, settlements,
        maximum_points=maximum_points, maximum_events=maximum_events) if streamed else
        receipt_available_history(simulation, settlements))

    def observations():
        return available.items() if streamed else iter(available)

    first = last = None
    peak = worst = Decimal(0)
    trough = None
    last_known = None
    nonpositive_observation = False
    for index, (row, known) in enumerate(zip(history, observations(), strict=True)):
        _require(isinstance(row, Mapping) and type(row['sequence']) is int
                 and row['sequence'] == index and row['phase'] in ('INITIAL', 'FUNDING', 'MARK', 'MARKET', 'FILL'),
                 'HISTORY_ORDER')
        equity = _number(row['equity'])
        if first is None:
            _require(row['phase'] == 'INITIAL' and equity > 0, 'INITIAL_HISTORY')
            first = equity
        else:
            _require(row['phase'] != 'INITIAL', 'INITIAL_HISTORY')
        last = equity
        last_known = known['timestamp']
        if last_known <= end:
            nonpositive_observation |= equity <= 0
            peak = max(peak, equity)
            drawdown = (peak - equity) / peak
            if drawdown > worst:
                worst = drawdown
                trough = dict(timestamp=row['timestamp'], available_at_ms=last_known,
                    sequence=index, phase=row['phase'], equity=row['equity'],
                    high_watermark=canonical_decimal(peak))

    identity = simulation['reconciliation']
    state = simulation['ending_state']
    _require(dict(identity) == dict(state['cashflow_identity'])
             and first == _number(identity['initial_equity'])
             and last == _number(identity['ending_equity']) == _number(state['account_equity'])
             and _number(identity['residual']) == 0
             and last - first == _number(identity['attributed_change']), 'CASH_IDENTITY')
    covered = history[0]['timestamp'] <= start <= end <= history[-1]['timestamp']
    grid = fixed_frequency_equity(observations(), **sample, budget=grid_budget) if covered else None
    points = [] if grid is None else grid['points']
    nonpositive = nonpositive_observation or any(_number(point['equity']) <= 0 for point in points)
    initial = _number(points[0]['equity']) if points else None
    curve = tuple(_number(p['equity']) for p in points[1:]) if points else None
    axis = tuple(p['timestamp'] for p in points[1:]) if points else None
    returns = (tuple(_number(p['net_return']) for p in points[1:])
               if points and not nonpositive and len(points) > 1 else None)
    unavailable = ('UNAVAILABLE_SAMPLE_COVERAGE' if grid is None else
                   'UNAVAILABLE_NONPOSITIVE_EQUITY' if nonpositive else 'UNAVAILABLE_EMPTY_RETURNS')

    def series_group(values):
        if values is None or not values or returns is None:
            return dict(status=unavailable, count=0, value_sha256=None)
        # Preserve exact Decimal strings; no float cleaning or implicit units.
        encoded = tuple(canonical_decimal(v) if isinstance(v, Decimal) else v for v in values)
        return dict(status='AVAILABLE', count=len(values), value_sha256=exact_identity_hash(encoded))

    groups = dict(portfolio_returns=series_group(returns), equity_curve=series_group(curve),
                  timestamps=series_group(axis))
    groups['initial_equity'] = (dict(status='AVAILABLE', count=1,
        value=canonical_decimal(initial), value_sha256=exact_identity_hash(canonical_decimal(initial)))
        if returns is not None else dict(status=unavailable, count=0, value_sha256=None))
    for name in MISSING_GROUPS:
        groups[name] = dict(status='MISSING', count=0, value_sha256=None)
    bound_policy = None
    if research_risk_policy is not None:
        from quantos.contracts.gold_research_risk_policy import validate, policy_sha256
        bound_policy = validate(research_risk_policy, sample=dict(sample))
        groups['risk_config'] = dict(status='AVAILABLE', count=len(bound_policy['config']),
            value_sha256=policy_sha256(bound_policy))
    position_projection, position_history = _position_inputs(simulation, history, observations,
        end=end, currency=account_currency, covered=covered, nonpositive=nonpositive)
    groups['account_positions'] = dict(status=position_projection['status'],
        count=position_projection.get('observation_count', 0),
        value_sha256=position_projection.get('history_sha256'))
    from services.validation.gold_net_attribution import build_net_attribution
    attribution = build_net_attribution(portfolio, simulation, observations=observations, points=points,
        end=end, covered=covered, nonpositive=nonpositive, maximum_points=maximum_points, maximum_events=maximum_events)
    if attribution is not None:
        for name, data in (('weights', attribution.weights), ('alpha_returns', attribution.contribution_returns)):
            status = attribution.projection['weights_status' if name == 'weights' else 'status']
            groups[name] = dict(status=status, count=0 if data is None else len(data),
                value_sha256=None if data is None else (attribution.projection['history_sha256']
                    if name == 'weights' else attribution.projection['contribution_returns_sha256']))
    market_states = None
    if market_state_policy is not None:
        from services.validation.gold_market_states import build_market_state_inputs
        market_states = build_market_state_inputs(market_state_policy, market_bars=market_bars,
            market_history_policy=market_history_policy, sample=dict(sample),
            account_returns_available=returns is not None, maximum_bars=maximum_points,
            maximum_intervals=intervals)
        groups['regime_labels'] = dict(status=market_states.projection['status'],
            count=0 if market_states.labels is None else len(market_states.labels),
            value_sha256=market_states.projection['label_values_sha256'])
    capacity_inputs = None
    if capacity_policy is not None:
        _require(account_currency == 'USDT', 'CAPACITY_UNIT')
        from services.validation.gold_capacity_inputs import build_capacity_inputs
        capacity_inputs = build_capacity_inputs(capacity_policy,
            capacity_history=capacity_history, capacity_history_policy=capacity_history_policy,
            sample=dict(sample), account_observations=StreamArray(observations),
            allocation_observations=None if attribution is None else attribution.history,
            account_returns_available=returns is not None, maximum_snapshots=maximum_events,
            maximum_intervals=intervals, maximum_points=maximum_points)
        groups['capacity_and_aum'] = dict(status=capacity_inputs.projection['status'],
            count=capacity_inputs.projection['observation_count'],
            value_sha256=capacity_inputs.projection['engine_inputs_sha256'])
    missing = sorted(k for k, value in groups.items() if value['status'] != 'AVAILABLE')
    periods = Decimal(365 * DAY_MS) / frequency
    projection = dict(schema='QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V6' if capacity_inputs is not None else
        'QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V5' if market_states is not None else
        'QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V4' if attribution is not None else
        'QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V3' if bound_policy is not None else
        'QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V2' if position_history is not None else SCHEMA,
        status='PARTIAL_INPUTS_AVAILABLE',
        native_result_hash=simulation['result_hash'], sample=dict(sample), currency=account_currency,
        annualization=dict(calendar='ELAPSED_UTC_MILLISECONDS_365_DAY_YEAR',
                           periods_per_year=canonical_decimal(periods)),
        available_history_sha256=exact_identity_hash(StreamArray(observations)),
        funding_bindings_sha256=exact_identity_hash(settlements),
        original_cash_identity_sha256=exact_identity_hash(identity),
        opening_equity=canonical_decimal(first), source_groups=groups, missing_groups=missing,
        position_inputs=position_projection,
        causal_drawdown=dict(method='RECEIPT_AVAILABLE_ATOMIC_EQUITY_V1',
                            as_of_ms=end, max_drawdown=canonical_decimal(worst), trough=trough),
        original_points=len(history), last_available_at_ms=last_known,
        full_run_covered=bool(covered and last_known <= end and not state['positions']
                             and not state['pending_initial_margin_execution_prices']),
        full_research_risk_ready=False,
        limitations=['SAME_ENGINE_ACCOUNT_INPUTS_NOT_INDEPENDENT_MATHEMATICS',
                     'ACCOUNT_INPUTS_ARE_NOT_ALPHA_ATTRIBUTION', 'NO_IMPLICIT_FX_CONVERSION',
                     'RISK_POLICY_AND_EXTERNAL_INPUTS_REQUIRED', 'NO_SOURCE_OR_TIME_AUTHORITY'],
        economic_authority=False, submission_enabled=False)
    if attribution is not None:
        projection['net_attribution'] = attribution.projection
        projection['limitations'].remove('ACCOUNT_INPUTS_ARE_NOT_ALPHA_ATTRIBUTION')
        projection['limitations'].extend(attribution.projection['limitations'])
    if bound_policy is not None:
        projection['risk_policy'] = dict(policy_sha256=policy_sha256(bound_policy), policy=bound_policy,
            preregistration_time_authority=False, source='ORIGINAL_FORWARD_DECLARATION',
            engine_replay_completed=False)
    if market_states is not None:
        projection['market_states'] = market_states.projection
        projection['limitations'].extend(market_states.projection['limitations'])
    if capacity_inputs is not None:
        projection['capacity_inputs'] = capacity_inputs.projection
        projection['limitations'].extend(capacity_inputs.projection['limitations'])
    return GoldAccountRiskInputs(returns, curve, axis, initial, first, account_currency, periods,
                                projection, position_history, attribution, market_states, capacity_inputs)
