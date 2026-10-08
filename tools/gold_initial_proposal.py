"""Freeze a completed bounded gold hypothesis before a new research window."""
import hashlib
import time
from pathlib import Path

from services.research.ai_factory_store import digest
from services.research.gold_bar_candidates import CATALOG
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.ai_factory_collaboration import verify_collaboration
from tools.gold_cost_review import _read


def freeze_proposal(origin, destination, *, memory=None):
    root, target = Path(origin).absolute(), Path(destination).absolute()
    if root.resolve() != root or target.resolve() != target:
        raise ValueError('INITIAL_PROPOSAL_UNSAFE_PATH')
    names = ('plan.json', 'result.json', 'roles/0.started.json', 'roles/0.json')
    raw = {name: _read(root, name) for name in names}
    if sum(map(len, raw.values())) > 262144:
        raise ValueError('INITIAL_PROPOSAL_BYTE_BUDGET')
    options = {'candidate_catalog': CATALOG}
    memory_path = None
    if memory is not None:
        from services.research.gold_portfolio_memory import validate_memory, continue_memory
        memory_path = Path(memory).absolute()
        if memory_path.resolve() != memory_path:
            raise ValueError('INITIAL_MEMORY_UNSAFE_PATH')
        memory_raw = _read(memory_path.parent, memory_path.name)
        document = validate_memory(decode(memory_raw))
        continue_memory(document, [], next_attempt=document['next_attempt'],
                        as_of_ms=time.time_ns() // 1000000)
        options['research_memory'] = document
    plan = decode(raw['plan.json'])
    if (plan.get('protocol') != 'SINGLE_V1' or plan.get('roles') != ['PROPOSE']
            or plan.get('remaining') != [0, 1, 2] or plan.get('history') != []
            or plan.get('economic_eligible') is not False
            or plan.get('options_hash') != digest(options)):
        raise ValueError('INITIAL_PROPOSAL_CATALOG_OR_SCOPE')
    original = verify_collaboration(root)
    if original is None or type(original['candidate_id']) is not int or original['candidate_id'] not in (0, 1, 2):
        raise ValueError('INITIAL_PROPOSAL_INCOMPLETE')
    if any(_read(root, name) != body for name, body in raw.items()):
        raise ValueError('INITIAL_PROPOSAL_CHANGED')
    if memory_path is not None:
        if (_read(memory_path.parent, memory_path.name) != memory_raw
                or original['roles'][0].get('research_memory_hash') != document['result_hash']):
            raise ValueError('INITIAL_MEMORY_CHANGED_OR_UNBOUND')
        raw['research-memory.json'] = memory_raw
        if sum(map(len, raw.values())) > 262144:
            raise ValueError('INITIAL_PROPOSAL_BYTE_BUDGET')
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    (target/'roles').mkdir(mode=0o700)
    for name, body in raw.items():
        with (target/name).open('xb') as handle:
            handle.write(body)
    if verify_collaboration(target) != original:
        raise ValueError('INITIAL_PROPOSAL_COPY_MISMATCH')
    return original, {name: hashlib.sha256(body).hexdigest() for name, body in raw.items()}
