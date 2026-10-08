"""Non-authorizing console mirror of one original registered technical run."""
from decimal import Decimal

from quantos.contracts import registered_research_run as native
from quantos.contracts.research_evaluation_plan import core_status

SCHEMA = 'QUANTOS_REGISTERED_RESEARCH_JOB_V1'
KINDS = frozenset(('registered_research', 'registered_research_recover'))
FIELDS = frozenset(('schema', 'status', 'plan', 'plan_sha256', 'native_trial_id',
    'result_hash', 'account', 'research_status', 'submission_enabled',
    'economic_authority', 'promotion_approved'))
ACCOUNT_FIELDS = frozenset(('initial_equity', 'ending_equity', 'price_pnl', 'fee',
    'spread', 'impact', 'funding', 'liquidation_fee', 'insurance_adjustment',
    'adl_adjustment', 'attributed_change', 'residual'))


def validate_params(params):
    native.require(type(params) is dict and set(params) == {'native_invocation_id'}
        and native._hash(params['native_invocation_id']), 'JOB_PARAMS')
    return dict(params)


def validate_report(value):
    native.require(type(value) is dict and set(value) == FIELDS, 'JOB_REPORT_FIELDS')
    native.canonical(value, maximum=131_072)
    native.require(value['schema'] == SCHEMA and value['status'] == 'TECHNICAL_VALIDATION_ONLY'
        and all(value[field] is False for field in
            ('submission_enabled', 'economic_authority', 'promotion_approved')), 'JOB_AUTHORITY')
    plan = native.validate_plan(value['plan'])
    native.require(plan['schema'] == native.RISK_EVALUATED_SCHEMA and plan['adapter'] == native.TECHNICAL
        and value['plan_sha256'] == native.digest(plan)
        and value['native_trial_id'] == plan['trials'][0]['native_trial_id']
        and native._hash(value['result_hash']), 'JOB_BINDING')
    account = value['account']
    native.require(type(account) is dict and set(account) == ACCOUNT_FIELDS, 'JOB_ACCOUNT')
    for field in ACCOUNT_FIELDS:
        item = account[field]
        native.require(type(item) is str and 0 < len(item) <= 120, 'JOB_DECIMAL')
        try:
            finite = Decimal(item).is_finite()
        except ArithmeticError as exc:
            raise ValueError('REGISTERED_RESEARCH_JOB_DECIMAL') from exc
        native.require(finite, 'JOB_DECIMAL')
    native.require(account['residual'] == '0', 'JOB_RECONCILIATION')
    status = value['research_status']
    native.require(type(status) is dict and set(status) == set(
        ('oos', 'statistical_review', 'risk_replay', 'independent_review', 'candidate_admission')),
        'JOB_RESEARCH_STATUS')
    native.require(status['oos'] == core_status(plan['evaluation_core'])['oos_status']
        and status['statistical_review'] == 'NOT_REVIEWED'
        and status['risk_replay'] in ('INSUFFICIENT', 'REJECTED')
        and status['independent_review'] == 'INDEPENDENT_REVIEW_REQUIRED'
        and status['candidate_admission'] == 'NOT_APPROVED', 'JOB_RESEARCH_STATUS')
    return value
