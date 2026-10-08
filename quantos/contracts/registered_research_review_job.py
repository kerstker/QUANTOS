"""Bounded request identities and a non-authorizing project-review mirror."""
from quantos.contracts import registered_research_run as native

SCHEMA = 'QUANTOS_REGISTERED_RESEARCH_REVIEW_JOB_V1'
PARAMS = {'registered_review_request': 'native_invocation_id',
    'registered_review': 'review_request_id', 'registered_review_recover': 'review_request_id'}
FIELDS = frozenset(('schema', 'status', 'phase', 'review_request_id', 'native_invocation_id',
    'attempt_count', 'statistical_decision', 'authentication', 'qualification', 'review_id',
    'reasons', 'oos_status', 'risk_status', 'checked_at_ms', 'economic_authority',
    'promotion_approved', 'submission_enabled'))


def validate_params(kind, value):
    key = PARAMS[kind]
    native.require(type(value) is dict and set(value) == {key} and native._hash(value[key]), 'REVIEW_JOB_PARAMS')
    return dict(value)


def validate_report(value):
    native.require(type(value) is dict and set(value) == FIELDS, 'REVIEW_JOB_REPORT_FIELDS')
    native.canonical(value, maximum=65_536)
    native.require(value['schema'] == SCHEMA and value['status'] == 'TECHNICAL_VALIDATION_ONLY'
        and value['phase'] in ('REQUEST_PREPARED', 'REVIEW_RECORDED')
        and all(value[k] is False for k in ('economic_authority', 'promotion_approved', 'submission_enabled')),
        'REVIEW_JOB_AUTHORITY')
    for key in ('review_request_id', 'native_invocation_id'):
        native.require(native._hash(value[key]), 'REVIEW_JOB_IDENTITY')
    native.require(type(value['attempt_count']) is int and 0 <= value['attempt_count'] <= 1000
        and value['statistical_decision'] in ('PASS', 'FAIL', 'INSUFFICIENT')
        and value['oos_status'] in ('UNKNOWN', 'INSUFFICIENT')
        and value['risk_status'] in ('REJECTED', 'INSUFFICIENT', None), 'REVIEW_JOB_STATUS')
    native.require(type(value['reasons']) is list and len(value['reasons']) <= 200
        and all(native._text(reason) for reason in value['reasons'])
        and type(value['checked_at_ms']) is int and 0 <= value['checked_at_ms'] <= 2**63-1, 'REVIEW_JOB_REASONS')
    if value['phase'] == 'REQUEST_PREPARED':
        native.require(value['authentication'] == 'UNSIGNED_REQUEST'
            and value['qualification'] == 'NOT_REVIEWED' and value['review_id'] is None, 'REVIEW_JOB_UNSIGNED')
    else:
        native.require(value['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
            and value['qualification'] in ('REJECTED', 'INSUFFICIENT')
            and native._hash(value['review_id']), 'REVIEW_JOB_RECORDED')
    return value
