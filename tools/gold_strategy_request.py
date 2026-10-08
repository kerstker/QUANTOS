"""Bridge a reviewed gold entry/exit draft into the original risk request.

An independently installed policy must pin these exact constraints. This module
does not construct that policy, enroll an operator, sign, or submit anything.
"""
from live.execution.demo_batch_risk import _digest
from live.execution.demo_gold_pilot import require_opening_allowed
import json

from live.execution.demo_request_inputs import build, task_check
from services.validation.demo_strategy_constraints import validate


def constraints_from_draft(draft, *, now_ms):
    schemas = {'GOLD_DEMO_STRATEGY_ENTRY_DRAFT_V1': 'STRATEGY_ENTRY_DRAFT',
               'GOLD_DEMO_STRATEGY_EXIT_DRAFT_V1': 'STRATEGY_EXIT_DRAFT',
               'GOLD_BAR_DEMO_ENTRY_DRAFT_V1': 'BAR_ENTRY_DRAFT',
               'GOLD_BAR_DEMO_EXIT_DRAFT_V1': 'BAR_EXIT_DRAFT'}
    if (type(draft) is not dict or draft.get('schema') not in schemas
            or draft.get('status') != schemas[draft['schema']]
            or draft.get('environment') != 'BINANCE_USDM_DEMO'
            or draft.get('submission_enabled') is not False
            or draft.get('economic_authority') is not False
            or draft.get('risk_status') != 'NOT_RUN'
            or draft.get('result_hash') != _digest({k:v for k,v in draft.items() if k != 'result_hash'})):
        raise ValueError('GOLD_STRATEGY_REQUEST_DRAFT')
    bar = draft['schema'].startswith('GOLD_BAR_')
    if bar:
        opening = draft['schema'] == 'GOLD_BAR_DEMO_ENTRY_DRAFT_V1'
        pilot = draft.get('pilot_control')
        if (type(pilot) is not dict or not isinstance(draft.get('orders'), list)
                or len(draft['orders']) != 1
                or draft['orders'][0].get('reduceOnly') is not (not opening)):
            raise ValueError('GOLD_BAR_REQUEST_PILOT_OR_ACTION')
        require_opening_allowed(draft['orders'], pilot)
        if opening and (type(draft.get('signal_valid_until_ms')) is not int
                        or type(draft.get('valid_until_ms')) is not int
                        or draft['valid_until_ms'] > draft['signal_valid_until_ms']):
            raise ValueError('GOLD_BAR_REQUEST_ENTRY_DEADLINE')
    value = dict(schema='DEMO_GOLD_STRATEGY_CONSTRAINTS_V1',
        strategy_definition_sha256=draft['definition_hash' if bar else 'strategy_definition_sha256'],
        decision_sha256=draft['result_hash'], orders_sha256=_digest(draft['orders']),
        not_before_ms=draft['observed_at_ms'], valid_until_ms=draft['valid_until_ms'])
    return validate(value, orders=draft['orders'], now_ms=now_ms)


def prepare(draft, *, origin, now_ms, **inputs):
    constraints = constraints_from_draft(draft, now_ms=now_ms)
    if _digest(origin) != draft['origin_sha256']:
        raise ValueError('GOLD_STRATEGY_REQUEST_ORIGIN_CHANGED')
    return build(orders=draft['orders'], execution_constraints=constraints,
                 origin=origin, now_ms=now_ms, **inputs)


def task_from_draft(draft, *, profile, issuer_policy, kill_state, source_commit, now_ms,
                    delegated_admission=None,delegation_context=None):
    """Create data for the existing claimed-task runner, never install or run it."""
    constraints = constraints_from_draft(draft, now_ms=now_ms)
    task = dict(schema='DEMO_GOLD_STRATEGY_REQUEST_TASK_V1', source_commit=source_commit,
        valid_from_ms=max(issuer_policy['valid_from_ms'], constraints['not_before_ms']),
        valid_until_ms=min(issuer_policy['valid_until_ms'], constraints['valid_until_ms']),
        issuer_policy_sha256=_digest(issuer_policy), profile=profile, orders=draft['orders'],
        kill_switch=kill_state.model_dump(mode='json'), execution_constraints=constraints)
    if delegated_admission is not None:
        from live.execution.demo_delegated_inputs import resolve
        admitted=resolve(delegated_admission,policy=issuer_policy,orders=task['orders'],constraints=constraints,
            context=delegation_context,now_ms=now_ms)
        task['valid_until_ms']=min(task['valid_until_ms'],admitted['valid_until_ms'])
        task.update(schema='DEMO_GOLD_DELEGATED_REQUEST_TASK_V1',delegated_admission=delegated_admission)
    frozen = json.loads(json.dumps(task, allow_nan=False))
    return task_check(frozen, policy=issuer_policy, source_commit=source_commit, now_ms=now_ms,
        **({'delegation_context':delegation_context} if delegated_admission is not None else {}))
