"""Bind original task/request/proposal inputs to independently enabled proof.

Pure resolution consumes a caller's already-read public trust snapshot. Role
entrypoints separately reload the fixed root-owned enable document; a supplied
proof, effective policy or boolean never replaces that custody boundary.
"""
from live.execution.demo_batch_risk import _fields
from live.execution.demo_issuer_policy import require
from services.validation.demo_strategy_constraints import delegated_proof_digest

TASK_SCHEMA = 'DEMO_GOLD_DELEGATED_REQUEST_TASK_V1'
SESSION_SCHEMA = 'DEMO_GOLD_DELEGATED_SESSION_INPUT_V1'


def resolve(proof, *, policy, orders, constraints, context, now_ms):
    from live.execution.demo_gold_delegated_admission import admit

    _fields(context, 'registry enable')
    identity = delegated_proof_digest(proof)
    enabled = context['enable']
    checked = admit(delegation=enabled['delegation'], enable=enabled,
        base_policy=policy, registry=context['registry'], proof=proof, now_ms=now_ms)
    require(checked['proof_sha256'] == identity and checked['constraints'] == constraints
        and checked['orders'] == orders, 'DELEGATED_INPUTS_CHANGED')
    return checked


def installed_context(*, policy, registry, now_ms, role):
    from live.execution.demo_gold_delegation_enable import load_enabled

    enabled = load_enabled(registry=registry, source_commit=policy['executor_release_commit'],
        account_ref_sha256=policy['account_ref_sha256'], now_ms=now_ms, role=role)
    return {'registry': registry, 'enable': enabled}


def task_context(task, *, policy, registry, now_ms, role):
    if type(task) is dict and task.get('schema') == TASK_SCHEMA:
        return {'delegation_context': installed_context(policy=policy, registry=registry,
            now_ms=now_ms, role=role)}
    return {}


def executor_review(proposal, *, now_ms):
    """Recheck current independent activation before intents and every send gate."""
    from live.execution.demo_gold_delegation_enable import load_enabled
    from live.execution.demo_authority import load_registry_for_executor
    from live.execution.demo_gold_execution_delegation import delegation_base
    from live.execution.demo_issuer_policy import check_delegated_snapshot
    from services.validation.demo_strategy_constraints import check_proposal

    check_proposal(proposal, now_ms=now_ms)
    check_delegated_snapshot(proposal)
    proof = proposal['delegated_admission']
    registry = load_registry_for_executor()
    enabled = load_enabled(registry=registry, source_commit=proof['source_commit'],
        account_ref_sha256=proposal['context']['account_ref_sha256'], now_ms=now_ms, role='executor')
    return resolve(proof, policy=delegation_base(enabled['delegation']), orders=proposal['orders'],
        constraints=proposal['execution_constraints'], context={'registry': registry, 'enable': enabled}, now_ms=now_ms)
