"""Adapt reserved factory sessions to the existing gold AI experiment worker."""
import hashlib
import time

from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory_store import FactoryStore
from services.research.gold_factory_campaign import validate_session
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.gold_ai_research import run_scoped
from tools.gold_ai_origin import encode
from tools.gold_cost_review import _read
from tools.ai_factory_collaboration import verify_collaboration


def run_session(params):
    return _run_session(params, recover_only=False)


def recover_session(params):
    return _run_session(params, recover_only=True)


def _run_session(params, *, recover_only):
    if (type(params) is not dict or set(params) != {'attempt'}
            or type(params['attempt']) is not int or not 1 <= params['attempt'] <= 1000):
        raise ValueError('INVALID_GOLD_FACTORY_ATTEMPT')
    attempt = params['attempt']
    base = runtime_state_root()/'data/cache/ai_factory'
    if base.resolve() != base.absolute():
        raise ValueError('UNSAFE_GOLD_FACTORY_ROOT')
    with acquire_runtime_lock(base/'gold-campaign.lock') as acquired:
        if not acquired:
            raise RuntimeError('GOLD_FACTORY_BUSY')
        with FactoryStore(base/'factory.db') as store:
            store.verify_campaigns()
            session = store.get(attempt)
            config = validate_session(session['config'])
            if session['command'] != 'run' or session['state'] in ('FAILED', 'CANCELLED'):
                raise ValueError('GOLD_FACTORY_SESSION_NOT_RUNNABLE')
            raw = _read(base/'gold-inputs', f'{config["input_id"]}.json')
            if hashlib.sha256(raw).hexdigest() != config['input_sha256']:
                raise ValueError('GOLD_FACTORY_INPUT_CHANGED')
            root = base/'gold'
            if recover_only and not (root/'results'/str(attempt)).is_dir():
                raise ValueError('GOLD_FACTORY_RECOVERY_EVIDENCE_MISSING')
            request = root/'requests'/str(attempt)
            if request.resolve() != request.absolute():
                raise ValueError('UNSAFE_GOLD_FACTORY_REQUEST')
            request.mkdir(parents=True, exist_ok=True)
            if 'execution_feedback_sha256' in config:
                from services.governance.json_integrity import load_json_without_duplicate_keys
                from services.research.gold_execution_feedback import research_trigger
                feedback_raw=_read(base/'execution-feedback',config['execution_feedback_sha256']+'.json')
                feedback=load_json_without_duplicate_keys(feedback_raw)
                if not research_trigger(config,feedback,now=time.time_ns()//1000000)['ready']:
                    raise ValueError('GOLD_FACTORY_FEEDBACK_NOT_AVAILABLE')
                feedback_path=request/'execution-feedback.json'
                if feedback_path.exists() or feedback_path.is_symlink():
                    if _read(request,feedback_path.name)!=feedback_raw:raise ValueError('GOLD_FACTORY_FEEDBACK_CHANGED')
                else:
                    with feedback_path.open('xb') as stream:stream.write(feedback_raw)
                with store.c:
                    store.event(attempt,'EXECUTION_REVIEW_INPUT',{'feedback_sha256':feedback['result_hash'],
                        'source_trial_id':feedback['research_identity']['trial_id'],'usage':'RESEARCH_TRIGGER_ONLY'})
            document = {'objective': config['objective'], 'remaining_candidates': config['remaining_candidates']}
            if 'research_memory' in config:
                from tools.gold_research_memory import verify_memory
                document['research_memory']=verify_memory(base,config['research_memory'])
            for name, body in (('input.json', raw), ('request.json', encode(document))):
                path = request/name
                if path.exists() or path.is_symlink():
                    if _read(request, name) != body:
                        raise ValueError('GOLD_FACTORY_REQUEST_CHANGED')
                else:
                    with path.open('xb') as stream:
                        stream.write(body)
            store.update(attempt, 'RUNNING')
            try:
                result = run_scoped(params, root)
                from services.governance.json_integrity import load_json_without_duplicate_keys
                from services.research.gold_research_memory import signal_diagnostics
                report_raw = _read(root/'results'/str(attempt)/'trial', 'report.json')
                if hashlib.sha256(report_raw).hexdigest() != result['report_sha256']:
                    raise ValueError('GOLD_FACTORY_REPORT_CHANGED')
                diagnostic = signal_diagnostics(load_json_without_duplicate_keys(report_raw))
                from tools.gold_execution_identity import retain_strategy_definition
                retain_strategy_definition(root/'results'/str(attempt)/'trial')
            except BaseException:
                # A known choice remains consumed even when its cost trial failed.
                proposal_path = root/'results'/str(attempt)/'research'
                proposal = verify_collaboration(proposal_path) if (proposal_path/'result.json').is_file() else None
                store.record_trial(attempt, 0, {'candidate_id': proposal['candidate_id'] if proposal else None,
                                               'status': 'FAILED'})
                store.update(attempt, 'FAILED')
                raise
            store.record_trial(attempt, 0, {'candidate_id': result['candidate_id'], 'status': 'PASSED',
                'trial_id': result['trial_id'], 'result_hash': result['result_hash'],
                'proposal_hash': result['proposal_hash']})
            store.update(attempt, 'COMPLETED', {**result, 'signal_diagnostics': diagnostic})
            return result
