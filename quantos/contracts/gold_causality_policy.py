"""Prospective native Gold signal/receipt audit; no OOS or order authority."""
import copy
from quantos.contracts.registered_research_run import canonical, digest
from quantos.contracts.gold_market_state_policy import validate as validate_axis

SCHEMA = 'QUANTOS_GOLD_CAUSALITY_POLICY_V1'
REVIEW_SCHEMA = 'QUANTOS_GOLD_CANDIDATE_CAUSALITY_REVIEW_V1'
SCOPE = 'ACTUAL_NATIVE_SIGNAL_AND_RECEIPT_CAUSALITY_NOT_OOS_OR_SEARCH_SELECTION'
METHOD = dict(scope='ALL_ACTUAL_NATIVE_CANDIDATE_SIGNAL_DECISIONS',
    candidate_catalog='GOLD_BAR_CANDIDATES_V1', net_components='ALL_THREE_ORIGINAL_COMPONENTS',
    reference='STRICT_RECEIPT_AVAILABLE_PREFIX',
    probes='TWO_EXPLICIT_UNAVAILABLE_FUTURE_BARS_NEVER_MARKET_EVIDENCE',
    original_runtime_paths='CAUSAL_INCREMENTAL_AND_NONMONOTONE_FULL_HISTORY',
    comparison='EXACT_ORIGINAL_SIGNAL_OBJECT', unassessable='INSUFFICIENT_RETAIN_KNOWN_FAILURE',
    selected_observations='RECOMPUTE_WHERE_RECORDED', no_oos_or_selection_authority=True)
BUDGET = dict(maximum_decisions=100_000_000, maximum_bars=131_072,
              maximum_fallback_bar_reads=20_000_000)


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_CAUSALITY_POLICY_' + reason)


def validate(value, *, sample=None, declared_at_ms=None):
    require(type(value) is dict and set(value) == {'schema','method','budget','sample','declared_at_ms',
        'independent_verification','economic_authority','submission_enabled'}, 'FIELDS')
    canonical(value, maximum=65_536)
    require(value['schema'] == SCHEMA and canonical(value['method']) == canonical(METHOD), 'METHOD')
    # Share the original exact prospective axis/time validation, not new timing defaults.
    from quantos.contracts.gold_market_state_policy import SCHEMA as AXIS_SCHEMA, POLICY as AXIS_METHOD
    validate_axis({k:v for k,v in value.items() if k != 'budget'} | dict(schema=AXIS_SCHEMA,method=AXIS_METHOD),
        sample=sample, declared_at_ms=declared_at_ms)
    budget = value['budget']
    require(type(budget) is dict and set(budget) == set(BUDGET)
        and all(type(budget[k]) is int and 0 < budget[k] <= BUDGET[k] for k in BUDGET), 'BUDGET')
    return value


def declare(*, sample, declared_at_ms, budget=None):
    return validate(dict(schema=SCHEMA, method=copy.deepcopy(METHOD),
        budget=copy.deepcopy(BUDGET if budget is None else budget), sample=copy.deepcopy(sample),
        declared_at_ms=declared_at_ms, independent_verification=False,
        economic_authority=False, submission_enabled=False))


def policy_sha256(value):
    return digest(validate(value))
