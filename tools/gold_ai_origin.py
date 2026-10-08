"""Bind an AI choice to its base input and exact gold cost experiment."""
import hashlib
import json

from quantos.local_artifacts import with_result_hash
from services.research.gold_factory_candidates import CATALOG, candidates, apply_candidate
from tools.gold_cost_inputs import load_inputs, dump_inputs


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def verify_origin(origin, selected_raw):
    memory=origin.get('research_memory') if isinstance(origin,dict) else None
    extras={'research_memory'} if memory is not None else set()
    if (not isinstance(origin, dict) or set(origin)-extras != {
            'schema', 'base_input', 'proposal_plan', 'proposal', 'selected_input_sha256'}
            or origin['schema'] != ('GOLD_AI_COST_ORIGIN_V2' if memory is not None else 'GOLD_AI_COST_ORIGIN_V1')):
        raise ValueError('INVALID_GOLD_AI_ORIGIN')
    if memory is not None:
        from services.research.gold_research_memory import validate_memory
        validate_memory(memory)
    plan, proposal = origin['proposal_plan'], origin['proposal']
    for item in (plan, proposal):
        if not isinstance(item, dict) or with_result_hash(
                {k: v for k, v in item.items() if k != 'result_hash'}) != item:
            raise ValueError('GOLD_AI_RECEIPT_HASH')
    from services.research.gold_factory_campaign import remaining_candidates
    remaining = remaining_candidates(plan.get('remaining'))
    if (plan.get('protocol') != 'SINGLE_V1' or plan.get('roles') != ['PROPOSE']
            or type(proposal.get('candidate_id')) is not int or proposal['candidate_id'] not in remaining
            or plan.get('history') != [] or plan.get('maximum_calls') != 1
            or proposal.get('plan_hash') != plan['result_hash']
            or proposal.get('protocol') != 'SINGLE_V1'
            or proposal.get('economic_eligible') is not False
            or len(proposal.get('roles', [])) != 1):
        raise ValueError('GOLD_AI_PROPOSAL_BINDING')
    role = proposal['roles'][0]
    catalog_hash = hashlib.sha256(encode(candidates())).hexdigest()
    from services.research.ai_factory_store import digest
    if (role.get('role') != 'PROPOSE' or role.get('candidate_catalog') != CATALOG
            or role.get('candidate_catalog_sha256') != catalog_hash
            or role.get('candidate_id') != proposal.get('candidate_id')
            or role.get('research_memory_hash') != (memory['result_hash'] if memory else None)
            or plan.get('options_hash') != digest({'candidate_catalog': CATALOG,**({'research_memory':memory} if memory else {})})):
        raise ValueError('GOLD_AI_CATALOG_BINDING')
    base = load_inputs(encode(origin['base_input']))
    base['document'] = apply_candidate(base['document'], proposal['candidate_id'])
    if (dump_inputs(base) != selected_raw
            or hashlib.sha256(selected_raw).hexdigest() != origin['selected_input_sha256']):
        raise ValueError('GOLD_AI_SELECTED_INPUT_BINDING')
    return proposal['candidate_id']
