"""Publish immutable evidence explanations without feeding outer results into search."""
from pathlib import Path
import time
import threading
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory_store import digest
from services.research.ai_factory_episodes import ResearchEpisodes
from services.research.ai_factory_verdict import research_verdict,SCHEMA
from tools.btc_factory_research import verified,seal
from tools.profitability_study import write_new
from tools.ai_factory_portfolio_verify import verify_portfolio
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.cache_io import atomic_write_json

_LOCK=threading.Lock()


def run_verdict(params):
    if set(params)!={'attempt'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000:
        raise ValueError('bounded research explanation required')
    dest=runtime_state_root()/'data/cache/ai_factory'/str(params['attempt'])
    if any(p.is_symlink() for p in (dest,*dest.parents)):raise ValueError('unsafe research explanation path')
    with acquire_runtime_lock(dest/'verdict.lock',process_lock=_LOCK) as acquired:
        if not acquired:raise ValueError('research explanation busy')
        primary=verified(dest/'result.json');model=verified(dest/'model.json');plan=verified(dest/'plan.json')
        if primary['model_hash']!=model['result_hash'] or model['identity_hash']!=digest(plan['identity']):
            raise ValueError('research explanation model identity conflict')
        portfolio=None
        if (dest/'portfolio/result.json').exists():
            verify_portfolio(dest/'portfolio');portfolio=verified(dest/'portfolio/result.json')
        document=research_verdict(primary,portfolio)
        identity=digest({'schema':SCHEMA,'sources':document['source_hashes']})
        path=dest/'verdicts'/f'{identity}.json';path.parent.mkdir(exist_ok=True)
        if path.exists():
            stored=verified(path)
            if any(stored.get(k)!=v for k,v in document.items()):raise ValueError('immutable research explanation changed')
        else:
            write_new(path,{**document,'known_at_ms':time.time_ns()//1000000,'attempt':params['attempt']})
            stored=verified(path)
        with ResearchEpisodes(dest/'research-episodes.db') as episodes:
            old=episodes.get(identity)
            if old and old['evidence_hash']!=stored['result_hash']:raise ValueError('research explanation episode changed')
            if not old:
                cutoff=primary.get('outer',{}).get('end_ms',model['training_last_label_ms'])
                episodes.append({'episode_id':identity,'experiment_hash':digest(plan['identity']),
                    'snapshot_hash':plan['identity']['panel_hash'],'partition_hash':digest(['OUTER_EXPLANATION',identity]),
                    'scope':'OUTER_EXPLANATION','known_at_ms':time.time_ns()//1000000,'market_max_ms':int(cutoff),
                    'evidence_hash':stored['result_hash'],'parent_ids':[],
                    'summary':{'candidate_id':None,'status':'UNKNOWN','score':None,'reason':stored['decision']}})
        atomic_write_json(str(dest/'research-verdict.json'),stored)
        return stored
