"""One reserved AI hypothesis -> existing gold ledger -> independent review request."""
import hashlib
import json
import threading
import time

from quantos.runtime_paths import runtime_state_root
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.gold_factory_candidates import CATALOG, apply_candidate, candidates
from tools.ai_factory_collaboration import bounded_proposal, verify_collaboration
from tools.ai_factory_provider import propose
from tools.gold_cost_inputs import load_inputs, dump_inputs
from tools.gold_cost_research import archive_experiment, read_completed_experiment, _write
from tools.gold_cost_review import _read
from tools.gold_ai_origin import verify_origin
from tools.perpetual_replay_research import source_identity
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock

_LOCK = threading.Lock()


def _scope(params, root=None):
    if (type(params) is not dict or set(params) != {'attempt'}
            or type(params['attempt']) is not int or not 1 <= params['attempt'] <= 1000):
        raise ValueError('INVALID_GOLD_AI_ATTEMPT')
    root = root if root is not None else runtime_state_root()/'data/cache/gold_ai_research'
    dest = root/'results'/str(params['attempt'])
    if dest.resolve() != dest.absolute():
        raise ValueError('GOLD_AI_UNSAFE_OUTPUT')
    return dest, dest.parent/f'.attempt-{params["attempt"]}.lock'


def _request_remaining(request):
    from services.research.gold_factory_campaign import remaining_candidates
    if (not isinstance(request, dict) or set(request)-{'remaining_candidates','research_memory'} != {'objective'}
            or not isinstance(request['objective'], str)
            or not 1 <= len(request['objective'].strip()) <= 2000):
        raise ValueError('INVALID_GOLD_AI_REQUEST')
    if 'research_memory' in request:
        from services.research.gold_research_memory import validate_memory
        validate_memory(request['research_memory'])
    return remaining_candidates(request.get('remaining_candidates', list(range(len(candidates())))))


def run_scoped(params, root):
    """Internal factory adapter; root is fixed by trusted code, never a job parameter."""
    dest, lock = _scope(params, root)
    with acquire_runtime_lock(lock, process_lock=_LOCK) as acquired:
        if not acquired:
            raise RuntimeError('GOLD_AI_BUSY')
        if dest.exists():
            return _resume_research(dest, params['attempt'])
        return _run_research(params, root=root)


def run_research(params):
    _, lock = _scope(params)
    with acquire_runtime_lock(lock, process_lock=_LOCK) as acquired:
        if not acquired:
            raise RuntimeError('GOLD_AI_BUSY')
        return _run_research(params)


def _selected_input(dest, raw, proposal):
    arguments = load_inputs(raw)
    arguments['document'] = apply_candidate(arguments['document'], proposal['candidate_id'])
    selected_raw = dump_inputs(arguments)
    origin = {'schema': 'GOLD_AI_COST_ORIGIN_V1', 'base_input': json.loads(raw),
        'proposal_plan': load_json_without_duplicate_keys(_read(dest/'research', 'plan.json')),
        'proposal': proposal, 'selected_input_sha256': hashlib.sha256(selected_raw).hexdigest()}
    request=load_json_without_duplicate_keys(_read(dest,'request.json'))
    if 'research_memory' in request:
        origin.update(schema='GOLD_AI_COST_ORIGIN_V2',research_memory=request['research_memory'])
    verify_origin(origin, selected_raw)
    return selected_raw, origin


def _ai_summary(result, proposal):
    return {**result, 'schema': 'GOLD_AI_RESEARCH_V1', 'candidate_id': proposal['candidate_id'],
            'proposal_hash': proposal['result_hash'], 'status': 'COMPLETED'}


def resume_research(params):
    dest, lock = _scope(params)
    with acquire_runtime_lock(lock, process_lock=_LOCK) as acquired:
        if not acquired:
            raise RuntimeError('GOLD_AI_BUSY')
        return _resume_research(dest, params['attempt'])


def _resume_research(dest, attempt):
    raw = _read(dest, 'input.json')
    request = load_json_without_duplicate_keys(_read(dest, 'request.json'))
    path = dest/'result.json'
    original = None
    if path.exists() or path.is_symlink():
        original = load_json_without_duplicate_keys(_read(dest, path.name))
        if (not isinstance(original, dict) or original.get('status') != 'COMPLETED'
                or not (dest/'trial/manifest.json').is_file()):
            raise ValueError('GOLD_AI_SUMMARY_BINDING')
    # Incomplete model calls are consumed, not retried or declared terminated.
    if not (dest/'research/result.json').is_file():
        raise ValueError('GOLD_AI_PROPOSAL_UNRESOLVED')
    for name in ('plan.json', 'result.json', 'roles/0.json', 'roles/0.started.json'):
        _read(dest/'research', name)
    proposal = verify_collaboration(dest/'research')
    selected_raw, origin = _selected_input(dest, raw, proposal)
    if (origin['proposal_plan'].get('remaining') != _request_remaining(request)
            or origin['proposal_plan'].get('objective') != request['objective']):
        raise ValueError('GOLD_AI_REQUEST_BINDING')
    trial = dest/'trial'
    if trial.exists() or trial.is_symlink():
        if not (trial/'manifest.json').is_file():
            raise ValueError('GOLD_AI_TRIAL_TERMINAL_UNKNOWN')
        if (_read(trial, 'input.json') != selected_raw
                or load_json_without_duplicate_keys(_read(trial, 'research-origin.json')) != origin):
            raise ValueError('GOLD_AI_TRIAL_ORIGIN_BINDING')
        result = read_completed_experiment(trial)
    else:
        if load_inputs(selected_raw)['release_identity'] != source_identity():
            raise ValueError('GOLD_AI_SOURCE_VERSION_MISMATCH')
        result = archive_experiment(selected_raw, trial, attempt=attempt, origin=origin)
    if result['attempt'] != attempt:
        raise ValueError('GOLD_AI_ATTEMPT_BINDING')
    summary = _ai_summary(result, proposal)
    if original is not None:
        if (original.get('status') != 'COMPLETED'
                or {k: v for k, v in original.items() if k != 'verification_status'} != {
                    k: v for k, v in summary.items() if k != 'verification_status'}):
            raise ValueError('GOLD_AI_SUMMARY_BINDING')
    else:
        _write(path, summary)
    # Original completion is immutable; later expiry/revocation checks are distinct.
    area = dest/'recovery-checks'
    if area.resolve() != area.absolute():
        raise ValueError('GOLD_AI_UNSAFE_OUTPUT')
    area.mkdir(exist_ok=True)
    _write(area/f'{time.time_ns()}.json', summary)
    return summary


def _run_research(params, root=None):
    if (type(params) is not dict or set(params) != {'attempt'}
            or type(params['attempt']) is not int or not 1 <= params['attempt'] <= 1000):
        raise ValueError('INVALID_GOLD_AI_ATTEMPT')
    root = root if root is not None else runtime_state_root()/'data/cache/gold_ai_research'
    request_root = root/'requests'/str(params['attempt'])
    raw = _read(request_root, 'input.json')
    request_raw = _read(request_root, 'request.json')
    request = load_json_without_duplicate_keys(request_raw)
    remaining = _request_remaining(request)
    arguments = load_inputs(raw)
    release = source_identity()
    if arguments['release_identity'] != release:
        raise ValueError('GOLD_AI_SOURCE_VERSION_MISMATCH')
    dest = root/'results'/str(params['attempt'])
    if dest.resolve() != dest.absolute():
        raise ValueError('GOLD_AI_UNSAFE_OUTPUT')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir()  # A crashed or failed attempt remains consumed; never silently retry.
    for name, body in (('input.json', raw), ('request.json', request_raw)):
        with (dest/name).open('xb') as stream:
            stream.write(body)
    try:
        proposal = bounded_proposal(request['objective'], remaining, [],
            protocol='SINGLE_V1', output=dest/'research',
            options={'candidate_catalog': CATALOG,**({'research_memory':request['research_memory']} if 'research_memory' in request else {})}, propose_fn=propose)
        if verify_collaboration(dest/'research') != proposal:
            raise ValueError('GOLD_AI_COLLABORATION_CHANGED')
        selected_raw, origin = _selected_input(dest, raw, proposal)
        if source_identity() != release:
            raise ValueError('GOLD_AI_SOURCE_CHANGED')
        result = archive_experiment(selected_raw, dest/'trial', attempt=params['attempt'], origin=origin)
        result = _ai_summary(result, proposal)
    except Exception as exc:
        _write(dest/'result.json', {'status': 'FAILED', 'error_type': type(exc).__name__,
                                   'authoritative': False, 'economic_eligible': False})
        raise
    _write(dest/'result.json', result)
    return result
