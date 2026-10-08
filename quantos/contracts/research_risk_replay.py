"""Bounded diagnostic replay contract. No research qualification or trading rights."""
import copy
import re
from decimal import Decimal

from quantos.contracts.registered_research_run import canonical, digest
from quantos.contracts.decimal_arithmetic import canonical_decimal

SCHEMA = 'QUANTOS_TECHNICAL_RISK_REPLAY_V1'
ADAPTER = 'TECHNICAL_LINEAR_PERPETUAL_V2'
ENGINES = frozenset(('market', 'liquidity', 'concentration', 'correlation',
                    'drawdown', 'regime', 'stress', 'capacity', 'model_alpha'))
LIMITATIONS = [
    'SAME_REVIEWED_ENGINE_NOT_INDEPENDENT_MATHEMATICS',
    'SUCCESSFUL_NATIVE_RECORD_ONLY_FAILED_ATTEMPTS_REMAIN_IN_PROJECT_POPULATION',
    'SOURCE_COMPLETENESS_REQUIRES_INDEPENDENT_VERIFICATION',
    'REGISTERED_RISK_PLAN_UNAVAILABLE',
    'POSITION_ATTRIBUTION_UNAVAILABLE',
    'REGIME_PROVENANCE_UNAVAILABLE',
    'MODEL_METADATA_PROVENANCE_UNAVAILABLE',
    'DEPLOYMENT_EQUIVALENCE_NOT_ESTABLISHED',
]


def require(condition, reason):
    if not condition:
        raise ValueError('RESEARCH_RISK_REPLAY_' + reason)


def _fields(value, fields):
    require(type(value) is dict and set(value) == set(fields.split()), 'FIELDS')


def _hash(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'HASH')


def _time(value):
    require(type(value) is int and 0 <= value < 2**63, 'TIME')


def _reasons(value):
    require(type(value) is list and all(type(v) is str and v for v in value)
            and len(set(value)) == len(value), 'REASONS')


def _decimal(value):
    require(type(value) is str and len(value) <= 512
            and re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', value), 'DECIMAL')
    number = Decimal(value)
    require(canonical_decimal(number) == value, 'DECIMAL')
    return number


def validate_report(value):
    """Validate a diagnostic envelope, never authenticate its caller or attest facts."""
    canonical(value)
    _fields(value, 'schema adapter input_sha256 original_result_sha256 run_id '
            'manifest_sha256 status reproducibility decisions execution_checks '
            'limitations submission_enabled economic_authority')
    require(value['schema'] == SCHEMA and value['adapter'] == ADAPTER, 'SCHEMA')
    for key in ('input_sha256', 'original_result_sha256', 'run_id', 'manifest_sha256'):
        _hash(value[key])
    require(value['submission_enabled'] is False and value['economic_authority'] is False,
            'AUTHORITY')
    require(value['status'] in ('INSUFFICIENT', 'REJECTED'), 'STATUS')
    require(value['limitations'] == LIMITATIONS, 'LIMITATIONS')
    reproduction = value['reproducibility']
    _fields(reproduction, 'original_result_match ledger_match mathematical_independence original_attestations')
    require(reproduction['original_result_match'] is True and reproduction['ledger_match'] is True
            and reproduction['mathematical_independence'] is False, 'REPRODUCIBILITY')
    attestations = reproduction['original_attestations']
    _fields(attestations, 'causal_available_time strict_next_event_execution decimal_contracts '
            'settlement_event_funding historical_venue_rules margin_liquidation_state_machine '
            'complete_cashflow_identity deployment_equivalent_risk capacity_is_load_bearing '
            'full_research_risk_9_of_9 risk_history_ledger_replayed')
    require(all(type(v) is bool for v in attestations.values())
            and attestations['deployment_equivalent_risk'] is False
            and attestations['risk_history_ledger_replayed'] is False, 'ORIGINAL_CLAIMS')
    require(type(value['decisions']) is list and 0 < len(value['decisions']) <= 100_000, 'DECISIONS')
    ids = set()
    rejected = False
    for decision in value['decisions']:
        _fields(decision, 'decision_id timestamp risk_input_sha256 config_sha256 target_weights '
                'key_binding coverage engine_checks reasons')
        require(type(decision['decision_id']) is str and decision['decision_id']
                and decision['decision_id'] not in ids, 'DECISION_ID')
        ids.add(decision['decision_id'])
        _time(decision['timestamp'])
        _hash(decision['risk_input_sha256']); _hash(decision['config_sha256'])
        require(type(decision['target_weights']) is dict and decision['target_weights']
                and all(type(k) is str and type(v) is str for k, v in decision['target_weights'].items()), 'WEIGHTS')
        require(decision['key_binding'] in ('MATCH', 'UNMAPPED'), 'KEY_BINDING')
        rejected |= decision['key_binding'] == 'UNMAPPED'
        _reasons(decision['reasons'])
        coverage = decision['coverage']
        _fields(coverage, 'status source_cut_ms initial_equity claimed_initial_equity points')
        require(coverage['status'] in ('MATCH', 'MISMATCH', 'INSUFFICIENT'), 'COVERAGE')
        rejected |= coverage['status'] == 'MISMATCH'
        _time(coverage['source_cut_ms'])
        initial = _decimal(coverage['initial_equity'])
        claimed_initial = _decimal(coverage['claimed_initial_equity'])
        require(type(coverage['points']) is list and coverage['points'], 'POINTS')
        previous = -1
        mismatch = uncovered = False
        for point in coverage['points']:
            _fields(point, 'timestamp status claimed_equity actual_equity state_sha256 input_prefix_sha256 input_events')
            _time(point['timestamp'])
            require(previous < point['timestamp'] <= decision['timestamp'], 'AXIS')
            previous = point['timestamp']
            require(type(point['claimed_equity']) is str and point['status'] in ('MATCH', 'MISMATCH', 'UNCOVERED'), 'POINT')
            claimed = _decimal(point['claimed_equity'])
            if point['status'] == 'UNCOVERED':
                uncovered = True
                require(point['timestamp'] < coverage['source_cut_ms'], 'UNCOVERED_TIME')
                require(all(point[k] is None for k in ('actual_equity', 'state_sha256', 'input_prefix_sha256', 'input_events')),
                        'UNCOVERED')
            else:
                require(point['timestamp'] >= coverage['source_cut_ms'], 'COVERED_TIME')
                require(type(point['actual_equity']) is str and type(point['input_events']) is int
                        and point['input_events'] >= 0, 'COVERED')
                _hash(point['state_sha256']); _hash(point['input_prefix_sha256'])
                is_match = claimed == _decimal(point['actual_equity'])
                require(point['status'] == ('MATCH' if is_match else 'MISMATCH'), 'POINT_MATCH')
                mismatch |= not is_match
        mismatch |= not uncovered and initial != claimed_initial
        require(coverage['status'] == ('MISMATCH' if mismatch else ('INSUFFICIENT' if uncovered else 'MATCH')),
                'COVERAGE_AGGREGATE')
        checks = decision['engine_checks']
        require(type(checks) is dict and set(checks) == ENGINES, 'ENGINES')
        for check in checks.values():
            _fields(check, 'status reasons reproduction')
            require(check['status'] in ('REPLAYED_MATCH', 'INSUFFICIENT', 'REJECTED'), 'ENGINE_STATUS')
            _reasons(check['reasons'])
            require(type(check['reproduction']) is dict, 'ENGINE_REPRODUCTION')
    require(value['status'] == ('REJECTED' if rejected else 'INSUFFICIENT'), 'AGGREGATE')
    require(type(value['execution_checks']) is list and len(value['execution_checks']) == len(ids), 'EXECUTIONS')
    for execution in value['execution_checks']:
        _fields(execution, 'execution_id timestamp risk_equity target_weights research_risk')
        require(type(execution['execution_id']) is str and execution['execution_id'], 'EXECUTION_ID')
        _time(execution['timestamp'])
        require(type(execution['risk_equity']) is str and type(execution['target_weights']) is dict
                and type(execution['research_risk']) is dict, 'EXECUTION')
    return copy.deepcopy(value)


def report_sha256(value):
    return digest(validate_report(value))
