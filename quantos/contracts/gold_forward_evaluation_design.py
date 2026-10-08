"""Prospective Gold declarations, bound later to original inputs and trials.

The declaration cannot know the future input hash or authenticate its own time.
It preserves a comparison window, never independent OOS or trading authority.
"""
import base64
import copy
from decimal import Decimal, InvalidOperation

from quantos.contracts import registered_research_run as run
from quantos.contracts.decimal_arithmetic import canonical_decimal
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from quantos.contracts.research_statistical_review import MAX_ATTEMPTS, PROFILES
from quantos.contracts import gold_long_statistics_budget as long_budget

SCHEMA = 'QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V1'
REVIEW_SCHEMA = 'QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V2'
SCOPED_REVIEW_SCHEMA = 'QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V3'
LONG_REVIEW_SCHEMA = 'QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V4'
RISK_REVIEW_SCHEMA = 'QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V5'
LONG_REVIEW_SCHEMAS = (LONG_REVIEW_SCHEMA, RISK_REVIEW_SCHEMA)
SCOPED_REVIEW_SCHEMAS = (SCOPED_REVIEW_SCHEMA, *LONG_REVIEW_SCHEMAS)
FULL_REVIEW_SCHEMAS = (REVIEW_SCHEMA, *SCOPED_REVIEW_SCHEMAS)
INTERVAL = 900_000
NAMES = ('channel', 'trend', 'reversion')
FIELDS = frozenset('schema program_id attempt declared_at_ms source_identity_sha256 configuration '
    'window cohort selection budget metrics statistical_policy benchmark_policy risk_policy '
    'termination oos_status economic_authority submission_enabled'.split())
STATISTICS = dict(profile='ROBUST_RESEARCH_SIGNIFICANCE_V1', dsr_method='ORIGINAL_DSR_V1',
    minimum_dsr_probability=canonical_decimal(PROFILES['ROBUST_RESEARCH_SIGNIFICANCE_V1'][0]),
    maximum_pbo_probability=canonical_decimal(PROFILES['ROBUST_RESEARCH_SIGNIFICANCE_V1'][1]),
    cpcv_method='ORIGINAL_CPCV_V1', cpcv_groups=6, cpcv_test_groups=2,
    pbo_method='ORIGINAL_CSCV_V1', pbo_blocks=8, trial_count='ALL_CANDIDATE_ATTEMPTS',
    status='ADAPTER_NOT_IMPLEMENTED')
FULL_STATISTICS = dict(
    profile='ROBUST_RESEARCH_SIGNIFICANCE_V1',
    sample_method='NATIVE_GOLD_RECEIPT_AVAILABLE_EQUITY_GRID_V1',
    trial_budget=dict(max_attempts=MAX_ATTEMPTS),
    comparison_policy=dict(population='COMPLETE_PROJECT_ATTEMPTS', source_dataset_grid='EXACT',
        missing_attempt='INSUFFICIENT', benchmark='ORIGINAL_EMBEDDED_CASH_ACCOUNT',
        selection_tie_break='ATTEMPT_ID_ASCENDING', information_horizon='FULL_DECLARED_OBSERVATION_EPISODE'),
    split_policy=dict(scope='TRAIN_VALIDATION_ONLY', maximum_information_horizon_ms=900_000,
        embargo_ms=900_000),
    dsr_policy=dict(method='ORIGINAL_DSR_V1', trial_count='ALL_CANDIDATE_ATTEMPTS',
        moments='EMPIRICAL_POPULATION_CENTRAL_MOMENTS_V1',
        variance='ORIGINAL_POPULATION_VARIANCE_FLOOR_V1',
        minimum_probability=canonical_decimal(PROFILES['ROBUST_RESEARCH_SIGNIFICANCE_V1'][0])),
    cpcv_policy=dict(method='ORIGINAL_CPCV_V1', n_groups=6, k_test=2,
        minimum_positive_fraction='0.5', minimum_p05_sharpe='0'),
    pbo_policy=dict(method='ORIGINAL_CSCV_V1', block_count=8,
        maximum_probability=canonical_decimal(PROFILES['ROBUST_RESEARCH_SIGNIFICANCE_V1'][1])),
    economic_checks=dict(minimum_net_return='0', minimum_benchmark_excess='0',
        maximum_drawdown='0.05', minimum_observations=30, minimum_duration_ms=30*86_400_000),
    oos_policy=dict(status='NOT_EVALUATED_BY_THIS_STATISTICAL_REPORT'),
    elimination_policy='RETAIN_ALL_ATTEMPTS_NO_FABRICATED_RETURNS',
    termination_policy='MISSING_INSUFFICIENT_THRESHOLD_FAIL')
RISK = dict(stop_policy='GOLD_RESEARCH_INITIAL_EQUITY_LOSS_V1', max_loss_fraction='0.05',
    scope='ONE_SIMULATED_ACCOUNT_PER_TAPE', independent_replay='REQUIRED_NOT_IMPLEMENTED',
    account_cost_calibration='REQUIRED_NOT_VERIFIED')
BENCHMARK = dict(id='GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1', same_source_dataset_grid=True,
    method='CANONICAL_LEDGER_WITHOUT_ORDER_INTENTS', status='NOT_EXECUTED')
TERMINATION = dict(on_invalid_inputs='REJECT', on_missing_evidence='INSUFFICIENT',
    retain_failed_and_eliminated=True, on_loss_limit='STOP_WITHOUT_NEXT_WINDOW')


def full_statistics(window_bars, *, scoped=False, long_window=False):
    value = copy.deepcopy(FULL_STATISTICS)
    # Positions can span every declared bar. A one-bar embargo is not a bound
    # on those outcomes; include the declared final observation tail as well.
    horizon = window_bars*INTERVAL+40_000
    value['split_policy'].update(maximum_information_horizon_ms=horizon, embargo_ms=horizon)
    if scoped:
        value['comparison_policy'].update(
            candidate_matrix='ALL_ATTEMPTS_BOUND_TO_THIS_DECLARATION',
            other_declarations='RETAIN_INCLUDE_IN_GLOBAL_TRIAL_COUNT',
            global_dsr_variance='FULL_PROJECT_COMPARABLE_OR_INSUFFICIENT')
    if long_window:
        value['computation_budget'] = long_budget.declaration()
    return value


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_FORWARD_DESIGN_'+reason)


def configuration(value):
    require(type(value) is dict and {'quantity','initial_collateral','fee_rate'} <= set(value)
        and set(value) <= {'quantity','initial_collateral','fee_rate','portfolio_plan','settlement_window_ms'},
        'CONFIGURATION')
    for name in ('quantity','initial_collateral','fee_rate'):
        require(type(value[name]) is str and len(value[name]) <= 100, 'CONFIGURATION')
        try:
            number = Decimal(value[name])
        except InvalidOperation:
            raise ValueError('GOLD_FORWARD_DESIGN_CONFIGURATION') from None
        require(number.is_finite() and canonical_decimal(number) == value[name]
            and (number >= 0 if name == 'fee_rate' else number > 0), 'CONFIGURATION')
    if 'portfolio_plan' in value:
        portfolio = value['portfolio_plan']
        require(type(portfolio) is dict and set(portfolio) == {'quantities','max_notional'}
            and type(portfolio['quantities']) is dict and set(portfolio['quantities']) == set(NAMES),
            'PORTFOLIO')
        for raw in (*portfolio['quantities'].values(), portfolio['max_notional']):
            require(type(raw) is str and len(raw) <= 100, 'PORTFOLIO')
            try:
                number = Decimal(raw)
            except InvalidOperation:
                raise ValueError('GOLD_FORWARD_DESIGN_PORTFOLIO') from None
            require(number.is_finite() and number > 0 and canonical_decimal(number) == raw, 'PORTFOLIO')
    if 'settlement_window_ms' in value:
        require(type(value['settlement_window_ms']) is int and 1 <= value['settlement_window_ms'] <= 1000,
            'FUNDING_WINDOW')
    return value


def _cohort(source, config):
    names = (*NAMES, 'net_portfolio') if 'portfolio_plan' in config else NAMES
    return [dict(candidate_id=name, strategy_definition_sha256=run.digest(dict(
        schema='QUANTOS_GOLD_FORWARD_STRATEGY_DECLARATION_V1', catalog='GOLD_BAR_CANDIDATES_V1',
        source_identity_sha256=source, configuration=config, candidate_id=name))) for name in names]


def validate(value):
    require(type(value) is dict and set(value) == FIELDS, 'FIELDS')
    run.canonical(value, maximum=65_536)
    require(value['schema'] in (SCHEMA, *FULL_REVIEW_SCHEMAS) and run._hash(value['source_identity_sha256']), 'SCHEMA')
    for name in ('program_id','attempt'):
        require(type(value[name]) is int and 1 <= value[name] <= 1000, 'IDENTITY')
    require(type(value['declared_at_ms']) is int and 0 <= value['declared_at_ms'] < 2**63, 'TIME')
    config = configuration(value['configuration'])
    window = value['window']
    require(type(window) is dict and set(window) == {'market','interval_ms','observation_start_ms','closed_bars'}
        and window['market'] == 'XAUUSDT' and window['interval_ms'] == INTERVAL
        and type(window['closed_bars']) is list and 1 <= len(window['closed_bars']) <= (
            long_budget.MAX_CLOSED_BARS if value['schema'] in LONG_REVIEW_SCHEMAS else 4)
        and all(type(t) is int and INTERVAL <= t < 2**63 and t % INTERVAL == 0
                for t in window['closed_bars']), 'WINDOW')
    closes = window['closed_bars']
    require(closes == [closes[0]+i*INTERVAL for i in range(len(closes))]
        and type(window['observation_start_ms']) is int
        and window['observation_start_ms'] == closes[0]-INTERVAL
        and value['declared_at_ms'] < window['observation_start_ms'], 'LATE_DECLARATION')
    require(value['cohort'] == _cohort(value['source_identity_sha256'],config), 'COHORT')
    require(run.canonical(value['budget']) == run.canonical(dict(max_invocations=1,
        max_trials=len(value['cohort']))), 'BUDGET')
    selection = value['selection']
    require(type(selection) is dict and set(selection) == {'method','candidate_id','proposal_sha256'},
        'SELECTION')
    if selection['method'] == 'COMPARE_ALL_WITHOUT_SELECTION':
        require(selection['candidate_id'] is None and selection['proposal_sha256'] is None, 'SELECTION')
    else:
        require(selection['method'] == 'PREDECLARED_CANDIDATE'
            and selection['candidate_id'] in {row['candidate_id'] for row in value['cohort']}
            and run._hash(selection['proposal_sha256']), 'SELECTION')
    require(value['metrics'] == dict(schema='PERPETUAL_FIXED_FREQUENCY_EQUITY_V1',
        calendar='ELAPSED_UTC_MILLISECONDS_365_DAY_YEAR', frequency_ms=10_000,
        start_ms=closes[0], end_ms=closes[-1]+10_000), 'METRICS')
    expected_risk = copy.deepcopy(RISK)
    if value['schema'] == RISK_REVIEW_SCHEMA:
        require(type(value['risk_policy']) is dict, 'RESEARCH_RISK_POLICY')
        from quantos.contracts.gold_research_risk_policy import validate as risk_policy
        expected_risk['research_risk_policy'] = risk_policy(
            value['risk_policy'].get('research_risk_policy'),
            sample={k: value['metrics'][k] for k in ('start_ms', 'end_ms', 'frequency_ms')},
            declared_at_ms=value['declared_at_ms'])
    require(all(run.canonical(value[key]) == run.canonical(expected) for key, expected in (
        ('statistical_policy', full_statistics(len(closes), scoped=value['schema'] in SCOPED_REVIEW_SCHEMAS,
                                              long_window=value['schema'] in LONG_REVIEW_SCHEMAS)
            if value['schema'] in FULL_REVIEW_SCHEMAS else STATISTICS), ('risk_policy', expected_risk),
        ('benchmark_policy', BENCHMARK), ('termination', TERMINATION))), 'POLICY')
    require(value['oos_status'] == 'NOT_INDEPENDENTLY_LOCKED'
        and value['economic_authority'] is False and value['submission_enabled'] is False, 'AUTHORITY')
    return value


def declare(*, program_id, attempt, source_identity, configuration, first_close_ms, window_bars,
            declared_at_ms, selected_candidate=None, proposal_sha256=None, legacy=False,
            comparison_scope='DECLARATION', long_window=False, research_risk_policy=None):
    require(type(legacy) is bool, 'LEGACY_MODE')
    require(type(long_window) is bool and (not long_window or not legacy), 'LONG_MODE')
    require(comparison_scope in ('DECLARATION', 'COMPLETE_PROJECT'), 'COMPARISON_SCOPE')
    require(not long_window or comparison_scope == 'DECLARATION', 'LONG_COMPARISON_SCOPE')
    require(type(window_bars) is int and 1 <= window_bars <= (long_budget.MAX_CLOSED_BARS if long_window else 4)
            and type(first_close_ms) is int, 'WINDOW')
    require(research_risk_policy is None or (long_window and not legacy), 'RESEARCH_RISK_MODE')
    config = copy.deepcopy(configuration)
    source = run.digest(source_identity)
    cohort = _cohort(source, config)
    schema = RISK_REVIEW_SCHEMA if research_risk_policy is not None else LONG_REVIEW_SCHEMA if long_window else (
        SCHEMA if legacy else (SCOPED_REVIEW_SCHEMA if comparison_scope == 'DECLARATION' else REVIEW_SCHEMA))
    registered_risk = copy.deepcopy(RISK)
    if research_risk_policy is not None:
        registered_risk['research_risk_policy'] = copy.deepcopy(research_risk_policy)
    return validate(dict(schema=schema, program_id=program_id, attempt=attempt,
        declared_at_ms=declared_at_ms, source_identity_sha256=source, configuration=config,
        window=dict(market='XAUUSDT',interval_ms=INTERVAL,observation_start_ms=first_close_ms-INTERVAL,
            closed_bars=[first_close_ms+i*INTERVAL for i in range(window_bars)]),
        cohort=cohort, selection=dict(method='COMPARE_ALL_WITHOUT_SELECTION' if selected_candidate is None
            else 'PREDECLARED_CANDIDATE', candidate_id=selected_candidate, proposal_sha256=proposal_sha256),
        budget=dict(max_invocations=1,max_trials=len(cohort)),
        metrics=dict(schema='PERPETUAL_FIXED_FREQUENCY_EQUITY_V1',
            calendar='ELAPSED_UTC_MILLISECONDS_365_DAY_YEAR',frequency_ms=10_000,
            start_ms=first_close_ms,end_ms=first_close_ms+(window_bars-1)*INTERVAL+10_000),
        statistical_policy=copy.deepcopy(STATISTICS) if legacy else full_statistics(window_bars,
            scoped=schema in SCOPED_REVIEW_SCHEMAS, long_window=long_window), benchmark_policy=copy.deepcopy(BENCHMARK),
        risk_policy=registered_risk, termination=copy.deepcopy(TERMINATION),
        oos_status='NOT_INDEPENDENTLY_LOCKED', economic_authority=False, submission_enabled=False))


def bind_plan(value, plan):
    """Bind the prospective recipe to the original native plan identity."""
    validate(value)
    require(plan['adapter'] == run.GOLD
        and value['source_identity_sha256'] == run.digest(plan['source_identity'])
        and value['declared_at_ms'] < plan['search']['recorded_at_ms']
        and [row['candidate_id'] for row in value['cohort']] == [row['candidate_id'] for row in plan['trials']],
        'PLAN_BINDING')
    if value['schema'] in FULL_REVIEW_SCHEMAS:
        require(all(t['validation_policy_digest'] == run.digest(value) for t in plan['trials']),
            'NATIVE_POLICY_BINDING')
    return value


def bind_input(value, plan, document):
    """Verify actual retained input, without assigning authority to its clock."""
    bind_plan(value, plan)
    require(type(document) is dict and document.get('release_identity') == plan['source_identity'], 'INPUT')
    actual = {key:document[key] for key in ('quantity', 'initial_collateral', 'fee_rate',
        'portfolio_plan', 'settlement_window_ms') if key in document}
    require(actual == value['configuration'], 'INPUT_CONFIGURATION')
    from quantos.contracts.gold_native_source import INPUT_SCHEMAS, input_document
    if document.get('input_schema') in INPUT_SCHEMAS:
        input_document(document)
        observed = []
        for close, received in document['forward_input']['closed_observations']:
            require(close <= received <= plan['search']['recorded_at_ms'], 'JOURNAL_WINDOW')
            if close not in observed:
                observed.append(close)
        require(observed == value['window']['closed_bars'], 'INPUT_WINDOW')
        return value
    files = document.get('journal_files')
    if 'input_schema' in document or 'continuation_segments' in document:
        from quantos.contracts.gold_segmented_input import final_journal
        files = final_journal(document)
    require(type(files) is dict and type(files.get('messages.jsonl')) is str, 'JOURNAL')
    try:
        raw = base64.b64decode(files['messages.jsonl'], validate=True)
        lines = raw.splitlines()
        require(len(lines) <= 100_000, 'JOURNAL_BOUND')
        observed = []
        for line in lines:
            entry = decode(line)
            message = decode(entry['raw'])
            candle = message['data']['k']
            if candle['x'] is True:
                close = candle['T']+1
                require(candle['s'] == 'XAUUSDT' and candle['i'] == '15m'
                    and candle['t'] == close-INTERVAL and type(entry['received_at_ms']) is int
                    and close <= entry['received_at_ms'] <= plan['search']['recorded_at_ms'], 'JOURNAL_WINDOW')
                if close not in observed:
                    observed.append(close)
        require(observed == value['window']['closed_bars'], 'INPUT_WINDOW')
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        if str(exc).startswith('GOLD_FORWARD_DESIGN_'):
            raise
        raise ValueError('GOLD_FORWARD_DESIGN_JOURNAL') from exc
    return value
