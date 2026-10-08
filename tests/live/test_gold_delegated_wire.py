"""Transport binding tests; these synthetic payloads grant no permission.

Admission, fixed public custody and original bar replay have separate native
tests. Here an otherwise valid original request must not drop or substitute
the future-signal proof while traversing its existing data formats.
"""
from copy import deepcopy
import json

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution import demo_request_inputs, demo_session_inputs
from services.validation import demo_submission, demo_strategy_constraints
from tests.live.test_gold_strategy_constraints import case as case, constrained, NOW
from tools.demo_task import same_declarations


PROPOSAL = 'DEMO_GOLD_DELEGATED_PRE_SUBMISSION_PROPOSAL_V1'
SESSION = 'DEMO_GOLD_DELEGATED_SESSION_INPUT_V1'
TASK = 'DEMO_GOLD_DELEGATED_REQUEST_TASK_V1'


def wire_proof():
    return dict(schema='DEMO_GOLD_DELEGATED_DECISION_PROOF_V1',
        enable_sha256='a'*64, delegation_sha256='b'*64,
        isolated_transport_payload={'value': 'unchanged original bytes'})


def bound_proposal(case):
    args = constrained(case)
    proof = wire_proof()
    context = {key: 'a'*64 for key in demo_submission.CONTEXT_HASHES}
    context.update(execution_constraints_sha256=_digest(args['execution_constraints']),
        delegated_admission_sha256=_digest(proof))
    return dict(schema=PROPOSAL, orders=args['orders'], context=context,
        execution_constraints=args['execution_constraints'], delegated_admission=proof)


def test_delegated_context_pins_proof_and_exact_constraints(case):
    proposal = bound_proposal(case)
    assert demo_submission.validate_context(proposal['context']) == proposal['context']
    assert demo_strategy_constraints.check_proposal(proposal, now_ms=NOW+10) == proposal['execution_constraints']


@pytest.mark.parametrize('damage', ['legacy_schema', 'missing_proof', 'missing_hash',
    'proof_swapped', 'missing_constraints', 'wrong_proof_schema'])
def test_delegated_proof_cannot_be_dropped_or_swapped(case, damage):
    proposal = bound_proposal(case)
    if damage == 'legacy_schema': proposal['schema'] = 'DEMO_PRE_SUBMISSION_PROPOSAL_V1'
    elif damage == 'missing_proof': proposal.pop('delegated_admission')
    elif damage == 'missing_hash': proposal['context'].pop('delegated_admission_sha256')
    elif damage == 'proof_swapped': proposal['delegated_admission']['enable_sha256'] = 'c'*64
    elif damage == 'missing_constraints':
        proposal.pop('execution_constraints'); proposal['context'].pop('execution_constraints_sha256')
    else:
        proposal['delegated_admission']['schema'] = 'UNAPPROVED_DATA'
        proposal['context']['delegated_admission_sha256'] = _digest(proposal['delegated_admission'])
    with pytest.raises(ValueError):
        demo_strategy_constraints.check_proposal(proposal, now_ms=NOW+10)


def test_context_without_exact_constraints_cannot_bind_delegated_proof(case):
    context = bound_proposal(case)['context']
    context.pop('execution_constraints_sha256')
    with pytest.raises(ValueError): demo_submission.validate_context(context)


def test_original_decoder_preserves_proof_without_issuing_authority(case):
    args = constrained(case)
    request = demo_request_inputs.build(**args)['request']
    request.update(schema=SESSION, delegated_admission=wire_proof())
    parsed = demo_session_inputs.decode(json.dumps(request).encode(), now_ms=NOW+10)
    assert parsed['delegated_admission'] == request['delegated_admission']
    assert parsed['execution_constraints'] == request['execution_constraints']
    assert args['dispatch'].snapshot() == []


@pytest.mark.parametrize('damage', [None, 'drop', 'swap', 'downgrade'])
def test_task_handoff_keeps_identical_delegated_proof(case, damage):
    args = constrained(case)
    request = demo_request_inputs.build(**args)['request']
    request.update(schema=SESSION, delegated_admission=wire_proof())
    task = dict(schema=TASK, orders=args['orders'], profile=args['profile'],
        kill_switch=request['risk']['kill_switch'], execution_constraints=args['execution_constraints'],
        delegated_admission=deepcopy(request['delegated_admission']))
    if damage == 'drop': request.pop('delegated_admission')
    elif damage == 'swap': request['delegated_admission']['enable_sha256'] = 'c'*64
    elif damage == 'downgrade': request['schema'] = 'DEMO_GOLD_STRATEGY_SESSION_INPUT_V1'
    if damage is None: same_declarations(request, task)
    else:
        with pytest.raises(ValueError): same_declarations(request, task)


@pytest.mark.parametrize('role', ['executor', 'issuer'])
def test_public_enable_is_visible_without_opening_other_roles_private_roots(role):
    from tools.demo_supervisor import child_command
    command = child_command(role, source_commit='c'*40,
        registry={'executor_uid': 2001, 'issuer_uid': 2002}, purpose='submission',
        unit='quantos-demo-'+role+'-isolated1234')
    properties = dict(command[index+1].split('=', 1)
        for index, value in enumerate(command[:-1]) if value == '-p')
    assert '-/etc/quantos/demo-gold-delegation' in properties['ReadOnlyPaths'].split()
    private = ('/etc/quantos/demo-submission /var/lib/quantos-demo-executor' if role == 'issuer'
        else '/etc/quantos/demo-issuer /var/lib/quantos-demo-issuer')
    assert properties['InaccessiblePaths'] == private
    assert properties['ProtectSystem'] == 'strict'
    if role == 'issuer': assert properties['PrivateNetwork'] == 'yes'
