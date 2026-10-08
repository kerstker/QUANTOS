"""Review bound Gold execution gates and loss records, without qualification.

The project reader binds the complete original result before calling this pure
consumer. Recorded predicates and actual ledger fill coverage are checked here;
accounting, input provenance, missing callbacks and nine-engine research risk
remain outside this diagnostic adapter.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from itertools import zip_longest

from quantos.contracts.decimal_arithmetic import ledger_decimal_context
from quantos.contracts import gold_loss_observation as observation

SCHEMA = 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V2'
LIMITATIONS = (
    'NINE_ENGINE_RISK_REPLAY_NOT_IMPLEMENTED',
    'ORIGINAL_RISK_VALUATION_INPUTS_NOT_INDEPENDENTLY_REPLAYED',
    'LOSS_CALLBACK_OBSERVATION_COVERAGE_UNPROVEN',
    'ACCOUNT_COSTS_NOT_CALIBRATED',
    'DEPLOYMENT_EQUIVALENCE_UNESTABLISHED',
)


def _require(condition, reason):
    if not condition:
        raise ValueError('GOLD_EXECUTION_RISK_' + reason)


def _number(value):
    _require(type(value) is str and 0 < len(value) <= 256, 'DECIMAL')
    number = Decimal(value)
    _require(number.is_finite() and (not number or -96 <= number.adjusted() <= 96), 'DECIMAL')
    return number


def _time(value):
    _require(type(value) is int and 0 <= value < 2**63, 'TIME')
    return value


def _rows(value, maximum):
    _require(isinstance(value, Sequence) and not isinstance(value, (str, bytes))
             and len(value) <= maximum, 'HISTORY_BUDGET')
    return value


def _fill_checks(simulation, maximum):
    events = _rows(simulation['risk_events'], maximum)
    approved = rejected = 0
    previous = -1
    for row in events:
        _require(isinstance(row, Mapping) and row['phase'] == 'PROJECTED_FILL', 'FILL_PHASE')
        timestamp = _time(row['timestamp'])
        _require(timestamp >= previous, 'FILL_ORDER')
        previous = timestamp
        _require(type(row['approved']) is bool and type(row['reducing']) is bool, 'FILL_BOOLEAN')
        _require(type(row['order_id']) is str and 0 < len(row['order_id']) <= 256
                 and _number(row['filled_quantity']) != 0, 'FILL_IDENTITY')
        equity = _number(row['risk_equity'])
        _require(equity == min(_number(row['marked_equity']), _number(row['executable_equity'])), 'RISK_EQUITY')
        notionals = row['instrument_notionals']
        _require(isinstance(notionals, Mapping) and set(notionals) <= {'XAUUSDT'}, 'MARKET')
        values = [_number(v) for v in notionals.values()]
        gross = sum(values, Decimal(0))
        _require(all(v >= 0 for v in values) and gross == _number(row['gross_notional']), 'GROSS')
        limits = row['limits']
        gross_limit, single_limit, drawdown_limit = (
            _number(limits[k]) for k in ('max_gross_leverage', 'max_single_weight', 'max_drawdown')
        )
        drawdown = _number(row['drawdown'])
        _require(gross_limit > 0 and single_limit > 0 and 0 <= drawdown_limit <= 1 and drawdown >= 0, 'LIMITS')
        checks = dict(
            positive_equity=equity > 0, drawdown=drawdown <= drawdown_limit,
            single_name=all(v <= equity * single_limit for v in values),
            gross_exposure=gross <= equity * gross_limit,
        )
        _require(isinstance(row['checks'], Mapping) and set(row['checks']) == set(checks)
                 and all(type(row['checks'][k]) is bool and row['checks'][k] == v for k, v in checks.items()),
                 'FILL_PREDICATES')
        if row['approved']:
            _require(row['reason'] is None, 'APPROVED_REASON')
            if not row['reducing']:
                _require(all(checks.values()), 'APPROVED_LIMITS')
                if simulation['order_rule_profiles'] is not None:
                    _require(_number(row['reservation']['available']) >= 0, 'APPROVED_RESERVATION')
            approved += 1
        else:
            _require(type(row['reason']) is str and 0 < len(row['reason']) <= 256, 'REJECTED_REASON')
            rejected += 1

    # Join every accepted tentative transition to its original canonical fill.
    # Rejected attempts are diagnostic records; no new cash flow is calculated.
    ledger = _rows(simulation['ledger_events'], maximum)
    actual = (row for row in ledger if row.get('event_type') == 'TRADE'
              and _number(row['filled_quantity']) != 0)
    accepted = (row for row in events if row['approved'])
    for gate, fill in zip_longest(accepted, actual):
        _require(gate is not None and fill is not None, 'FILL_COVERAGE')
        _require(fill['instrument_id'] == 'XAUUSDT' and fill['quantity_basis'] == 'ADMITTED_ORDER_FILL'
                 and fill['order_id'] == gate['order_id'] and fill['timestamp'] == gate['timestamp']
                 and _number(fill['filled_quantity']) == _number(gate['filled_quantity']), 'FILL_BINDING')
    return dict(status='CONSISTENT', count=len(events), approved_count=approved, rejected_count=rejected,
                actual_fill_coverage='COMPLETE')


def _loss_check(simulation, loss, maximum, registered_policy):
    _require(isinstance(loss, Mapping) and loss['schema'] in (
        'GOLD_RESEARCH_LOSS_CONTROL_V1', 'GOLD_RESEARCH_LOSS_CONTROL_V2')
             and loss['economic_eligible'] is False and loss['submission_enabled'] is False, 'LOSS_REPORT')
    witnessed = loss['schema'] == 'GOLD_RESEARCH_LOSS_CONTROL_V2'
    _require(('history_observation' in loss) == witnessed, 'LOSS_OBSERVATION_VERSION')
    observed = observation.validate_observation(loss['history_observation'], maximum_points=maximum) if witnessed else None
    policy = loss['policy']
    initial = _number(policy['initial_equity'])
    _require(policy['version'] == 'GOLD_RESEARCH_INITIAL_EQUITY_LOSS_V1'
             and policy['max_loss_fraction'] == '0.05'
             and policy['scope'] == 'ONE_SIMULATED_ACCOUNT_PER_TAPE'
             and policy['trigger_basis'] == 'MARKED_EQUITY_AFTER_ACTUAL_LEDGER_EVENTS'
             and type(policy['max_quote_age_ms']) is int and 1 <= policy['max_quote_age_ms'] <= 30000
             and initial > 0, 'LOSS_POLICY')
    _require(type(loss['exit_attempts']) is int and loss['exit_attempts'] >= 0, 'LOSS_ATTEMPTS')
    threshold = initial * Decimal('0.95')
    history = _rows(simulation['equity_history'], maximum)
    _require(len(history) > 0, 'EMPTY_HISTORY')
    if witnessed:
        _require(observed['observed_points'] <= len(history), 'LOSS_OBSERVATION_LENGTH')
    state = observation.new_digest()
    observed_breach = None
    first = last = breach = None
    previous = -1
    for sequence, row in enumerate(history):
        timestamp = _time(row['timestamp'])
        _require(type(row['sequence']) is int and row['sequence'] == sequence
                 and timestamp >= previous, 'HISTORY_ORDER')
        previous = timestamp
        equity = _number(row['equity'])
        if witnessed and sequence < observed['observed_points']:
            observation.update_digest(state, timestamp=timestamp, sequence=sequence, phase=row['phase'], equity=equity)
            if observed_breach is None and equity <= threshold:
                observed_breach = dict(timestamp=timestamp, sequence=sequence, equity=row['equity'],
                                       reason='CUMULATIVE_LOSS_LIMIT')
        if first is None:
            first = equity
            _require(row['phase'] == 'INITIAL', 'INITIAL_HISTORY')
        else:
            _require(row['phase'] != 'INITIAL', 'INITIAL_HISTORY')
        last = equity
        if breach is None and equity <= threshold:
            breach = dict(timestamp=timestamp, sequence=sequence, equity=row['equity'], reason='CUMULATIVE_LOSS_LIMIT')
    identity = simulation['ending_state']['cashflow_identity']
    _require(dict(identity) == dict(simulation['reconciliation'])
             and first == initial == _number(identity['initial_equity'])
             and last == _number(identity['ending_equity']) == _number(simulation['ending_state']['account_equity'])
             and _number(identity['residual']) == 0
             and last - initial == _number(identity['attributed_change']), 'RECONCILIATION')
    trigger = loss['trigger']
    if witnessed:
        _require(state.hexdigest() == observed['observed_prefix_sha256'], 'LOSS_OBSERVATION_HASH')
        _require(trigger == observed_breach, 'LOSS_OBSERVED_TRIGGER')
    if trigger is not None:
        _require(isinstance(trigger, Mapping) and type(trigger['timestamp']) is int
                 and type(trigger['sequence']) is int and dict(trigger) == breach, 'LOSS_TRIGGER')
    orders = _rows(simulation['orders'], maximum)
    flat = not simulation['ending_state']['positions'] and not any(
        row['status'] in ('OPEN', 'PARTIALLY_FILLED') for row in orders
    )
    expected = 'ACTIVE' if trigger is None else 'STOPPED_FLAT' if flat else 'STOPPING_UNRESOLVED'
    _require(loss['status'] == expected, 'LOSS_STATUS')
    preregistered = False
    if registered_policy is not None:
        _require(isinstance(registered_policy, Mapping)
                 and registered_policy['stop_policy'] == policy['version']
                 and registered_policy['max_loss_fraction'] == policy['max_loss_fraction']
                 and registered_policy['scope'] == policy['scope'], 'REGISTERED_LOSS_POLICY')
        preregistered = True
    coverage = dict(status='UNAVAILABLE_LEGACY')
    if witnessed:
        count = observed['observed_points']
        coverage = dict(status='COMPLETE_RECORDED_PREFIX' if count == len(history) else 'INCOMPLETE_RECORDED_PREFIX',
            observed_points=count, original_points=len(history), unobserved_points=len(history) - count,
            first_unobserved_sequence=None if count == len(history) else count,
            observed_prefix_sha256=observed['observed_prefix_sha256'])
    return dict(status=expected, policy_preregistered=preregistered, original_trigger=None if trigger is None else dict(trigger),
                first_threshold_breach=breach, ending_equity=identity['ending_equity'], exit_attempts=loss['exit_attempts'],
                history_observation=coverage)


@ledger_decimal_context()
def review_execution_risk(simulation, loss_control, *, registered_policy=None, streamed=False,
                          maximum_points=100_000, maximum_events=100_000):
    """Consume already bound, repeatable originals using bounded memory.

    Streamed readers must explicitly request their larger bounded capacity.
    Consistent gates and lawful rejections grant no economic or order authority.
    """
    _require(type(streamed) is bool and all(type(v) is int and 0 < v <= (
        100_000_000 if streamed else 100_000) for v in (maximum_points, maximum_events)), 'BUDGET')
    _require(isinstance(simulation, Mapping) and simulation['authoritative'] is False
             and simulation['evidence_status'] == 'TECHNICAL_VALIDATION_ONLY'
             and simulation['full_research_risk_9_of_9'] is False
             and simulation['risk_history_source'] == 'LEDGER_EVENT_REPLAY', 'SIMULATION')
    gates = _fill_checks(simulation, maximum_events)
    loss = _loss_check(simulation, loss_control, maximum_points, registered_policy)
    reasons = set(LIMITATIONS)
    if not loss['policy_preregistered']:
        reasons.add('LOSS_POLICY_NOT_PREREGISTERED')
    if loss['status'] == 'STOPPING_UNRESOLVED':
        reasons.add('STOP_EXIT_NOT_RESOLVED')
    return dict(schema=SCHEMA, status='INSUFFICIENT', native_result_hash=simulation['result_hash'],
                projected_fill_checks=gates, loss_control=loss, reconciliation_residual='0',
                mathematical_independence=False, full_research_risk_9_of_9=False,
                limitations=sorted(reasons), economic_authority=False, submission_enabled=False)
