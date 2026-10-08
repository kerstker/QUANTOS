"""Exact reviewed strategy-decision bindings; no approval creation."""
import hashlib
import json

from services.validation.demo_submission import (
    _fields, _digest, _integer, _canonical, validate_orders, GOLD_BATCH_SCHEMA,
    DemoSubmissionRejected,
)

DELEGATED_PROPOSAL_SCHEMA = 'DEMO_GOLD_DELEGATED_PRE_SUBMISSION_PROPOSAL_V1'
DELEGATED_PROOF_SCHEMA = 'DEMO_GOLD_DELEGATED_DECISION_PROOF_V1'
MAX_DELEGATED_PROOF_BYTES = 12_000_000


def delegated_proof_digest(value):
    """Bound transport bytes only; signature, custody and replay are separate."""
    if type(value) is not dict or value.get('schema') != DELEGATED_PROOF_SCHEMA:
        raise DemoSubmissionRejected('DELEGATED_PROOF_WIRE_SCHEMA')
    for name in ('enable_sha256', 'delegation_sha256'):
        _digest(value.get(name))
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(',', ':')).encode()
    except (TypeError, ValueError):
        raise DemoSubmissionRejected('DELEGATED_PROOF_WIRE_JSON') from None
    if not 0 < len(raw) <= MAX_DELEGATED_PROOF_BYTES:
        raise DemoSubmissionRejected('DELEGATED_PROOF_WIRE_BUDGET')
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def validate(value, *, orders, now_ms):
    _fields(value, 'schema strategy_definition_sha256 decision_sha256 orders_sha256 not_before_ms valid_until_ms')
    if value['schema'] != 'DEMO_GOLD_STRATEGY_CONSTRAINTS_V1':
        raise DemoSubmissionRejected('STRATEGY_CONSTRAINTS_SCHEMA')
    for name in ('strategy_definition_sha256', 'decision_sha256', 'orders_sha256'):
        _digest(value[name])
    start, end, now = (_integer(v) for v in (value['not_before_ms'], value['valid_until_ms'], now_ms))
    if not start <= now < end or not 0 < end-start <= 60000:
        raise DemoSubmissionRejected('STRATEGY_CONSTRAINTS_EXPIRED_OR_FUTURE')
    validate_orders(orders, schema=GOLD_BATCH_SCHEMA)
    if digest(orders) != value['orders_sha256']:
        raise DemoSubmissionRejected('STRATEGY_CONSTRAINTS_ORDERS')
    return value


def check_proposal_binding(proposal):
    """Verify immutable transport bindings without renewing expired authority."""
    context = proposal['context']
    present = 'execution_constraints' in proposal
    delegated = proposal.get('schema') == DELEGATED_PROPOSAL_SCHEMA
    if (delegated != ('delegated_admission' in proposal)
            or delegated != ('delegated_admission_sha256' in context)
            or delegated and not present):
        raise DemoSubmissionRejected('DELEGATED_PROOF_MISSING_OR_DOWNGRADED')
    if delegated and delegated_proof_digest(proposal['delegated_admission']) != context['delegated_admission_sha256']:
        raise DemoSubmissionRejected('DELEGATED_PROOF_CONTEXT_BINDING')
    if present != ('execution_constraints_sha256' in context):
        raise DemoSubmissionRejected('STRATEGY_CONSTRAINTS_MISSING')
    if present:
        constraint = proposal['execution_constraints']
        value = validate(constraint, orders=proposal['orders'], now_ms=constraint['not_before_ms'])
        if digest(value) != context['execution_constraints_sha256']:
            raise DemoSubmissionRejected('STRATEGY_CONSTRAINTS_BINDING')
        return value
    return None


def check_proposal(proposal, *, now_ms):
    value = check_proposal_binding(proposal)
    if value is not None:
        return validate(value, orders=proposal['orders'], now_ms=now_ms)
    return None
