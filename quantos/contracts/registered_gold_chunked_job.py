"""Technical queue mirror; registration does not approve research or execution."""
from decimal import Decimal

from quantos.contracts import registered_research_run as native
from quantos.contracts.registered_research_job import ACCOUNT_FIELDS, validate_params as validate_params
from quantos.contracts.decimal_arithmetic import ledger_decimal_context

SCHEMA = 'QUANTOS_REGISTERED_GOLD_CHUNKED_JOB_REPORT_V1'
KINDS = ('registered_gold_chunked', 'registered_gold_chunked_recover')
FIELDS = set('schema status plan plan_sha256 result_hash accounts research_status '
             'submission_enabled economic_authority promotion_approved'.split())
STATUS = dict(oos='NOT_INDEPENDENTLY_LOCKED', statistical_review='CHUNKED_EVIDENCE_ADAPTER_PENDING',
    risk_replay='NOT_INDEPENDENTLY_REPLAYED', independent_review='INDEPENDENT_REVIEW_REQUIRED',
    candidate_admission='NOT_APPROVED')


@ledger_decimal_context()
def validate_report(value):
    native.require(type(value) is dict and set(value) == FIELDS, 'GOLD_CHUNKED_JOB_REPORT')
    native.canonical(value, maximum=131_072)
    plan = native.validate_plan(value['plan'])
    native.require(plan['schema'] in native.CHUNKED_GOLD_SCHEMAS
        and plan['adapter'] == native.GOLD and value['plan_sha256'] == native.digest(plan)
        and native._hash(value['result_hash']) and value['schema'] == SCHEMA
        and value['status'] == 'TECHNICAL_VALIDATION_ONLY'
        and all(value[k] is False for k in ('submission_enabled', 'economic_authority', 'promotion_approved')),
        'GOLD_CHUNKED_JOB_AUTHORITY')
    accounts = value['accounts']
    native.require(type(accounts) is dict and set(accounts) == {t['candidate_id'] for t in plan['trials']},
        'GOLD_CHUNKED_JOB_COHORT')
    for account in accounts.values():
        native.require(type(account) is dict and set(account) == ACCOUNT_FIELDS, 'GOLD_CHUNKED_JOB_ACCOUNT')
        for scalar in account.values():
            native.require(type(scalar) is str and 0 < len(scalar) <= 120
                and Decimal(scalar).is_finite(), 'GOLD_CHUNKED_JOB_DECIMAL')
        native.require(account['residual'] == '0' and
            Decimal(account['ending_equity']) - Decimal(account['initial_equity']) ==
            Decimal(account['attributed_change']), 'GOLD_CHUNKED_JOB_CASH')
    native.require(value['research_status'] == STATUS, 'GOLD_CHUNKED_JOB_STATUS')
    return value
