"""Bind active-factory research memory to real receipts and scoped knowledge time."""
import time
from services.research.ai_factory_store import digest
from services.research.ai_factory_context import ResearchContext
from services.research.ai_factory_episodes import ResearchEpisodes
from tools.btc_factory_research import seal,verified
from tools.profitability_study import write_new


def inner_context(bound,split,market_cutoff,*,now_ms=None):
    return ResearchContext(experiment_hash=digest(bound),snapshot_hash=bound['panel_hash'],
        partition_hash=digest([r.tolist() for r in split]),as_of_ms=time.time_ns()//1000000 if now_ms is None else now_ms,
        market_as_of_ms=int(market_cutoff),scope='INNER_SEARCH')


def retain_inner(dest,context,receipt):
    identity=digest({'kind':'INNER_TRIAL','trial_id':receipt['trial_id']})
    with ResearchEpisodes(dest/'research-episodes.db') as store:
        old=store.get(identity)
        if old:
            if old['evidence_hash']!=digest(receipt):raise ValueError('research episode receipt changed')
            return old
        parents=store.read(context)
        summary={'candidate_id':receipt.get('candidate_id'),'status':receipt['status'],
            'score':receipt.get('metrics',{}).get('score',receipt.get('score'))}
        if receipt.get('reason'):summary['reason']=receipt['reason']
        value={'episode_id':identity,'experiment_hash':context.experiment_hash,'snapshot_hash':context.snapshot_hash,
            'partition_hash':context.partition_hash,'scope':'INNER_SEARCH','known_at_ms':context.as_of_ms,
            'market_max_ms':context.market_as_of_ms,'evidence_hash':digest(receipt),
            'parent_ids':[p['episode_id'] for p in parents],'summary':summary}
        store.append(value);return value


def proposal_context(dest,context,definition):
    evidence=[]
    if (dest/'research-episodes.db').exists():
        with ResearchEpisodes(dest/'research-episodes.db',read_only=True) as store:
            evidence=[r for r in store.read(context) if r['summary']['candidate_id'] is not None]
    history=[{k:r['summary'][k] for k in ('candidate_id','score','status')} for r in evidence]
    request={'context':context.document(),'history_evidence':evidence,
        'legacy_cross_experiment_memory':'NOT_RETRIEVED_WITHOUT_TIME_AND_PARTITION_METADATA',
        'source_role':'METHOD_ONLY_NOT_MARKET_FEATURES','llm_pretraining_knowledge':'UNKNOWN'}
    path=dest/'research-requests'/f'{definition}.json';path.parent.mkdir(exist_ok=True)
    if path.exists():
        if verified(path)!=seal(request):raise ValueError('frozen research request changed')
    else:write_new(path,request)
    return history,{'research_context':context.document(),'history_evidence':evidence}


def verify_records(dest,receipts):
    """Restore checks citations against the original trial payload, not just a hash chain."""
    path=dest/'research-episodes.db'
    if not path.exists():return
    import json
    originals={digest({'kind':'INNER_TRIAL','trial_id':r['trial_id']}):r for r in receipts}
    with ResearchEpisodes(path,read_only=True) as store:
        store.c.execute('BEGIN');store.verify()
        entries={}
        for row in store.c.execute('SELECT payload,receipt_hash FROM episodes'):
            value=json.loads(row[0]);entries[value['episode_id']]={**value,'receipt_hash':row[1]}
            if value['scope']=='INNER_SEARCH':
                original=originals.get(value['episode_id'])
                if original is None or digest(original)!=value['evidence_hash']:
                    raise ValueError('research episode trial evidence conflict')
            elif value['scope']=='OUTER_EXPLANATION':
                record=verified(dest/'verdicts'/f"{value['episode_id']}.json")
                if record['result_hash']!=value['evidence_hash']:raise ValueError('research verdict citation conflict')
        requests=list((dest/'research-requests').glob('*.json'))
        if len(requests)>18:raise ValueError('research request budget exceeded')
        for path in requests:
            record=verified(path);document=record['context']
            context=ResearchContext(**{k:v for k,v in document.items() if k not in ('schema','context_hash')})
            if context.document()!=document:raise ValueError('research request context mismatch')
            for reference in record['history_evidence']:
                if reference!=entries.get(reference['episode_id']) or not context.allows(reference):
                    raise ValueError('research request citation unavailable at context time')
