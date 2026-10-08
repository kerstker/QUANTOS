"""Continue one persisted collection request into the original bounded campaign."""
import hashlib
import time

from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory_store import FactoryStore
from tools.gold_cost_review import _read
from tools.gold_input_collection import run_collect


def run_request(params):
    if (type(params) is not dict or set(params)!={'attempt'} or type(params['attempt']) is not int
            or not 1<=params['attempt']<=1000):
        raise ValueError('invalid collection campaign id')
    base=runtime_state_root()/'data/cache/ai_factory'
    with FactoryStore(base/'factory.db') as store:
        entry=store.get_collection(params['attempt'])
        prior=[]
        if entry is not None and entry['request'].get('decision_policy') in ('AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'):
            for group in store.collection_programs_snapshot():
                current=next((b for b in group['batches'] if b['campaign_id']==params['attempt']),None)
                if current is not None:
                    prior=[store.get_collection(b['campaign_id']) for b in group['batches'] if b['ordinal']<current['ordinal']]
                    break
    if entry is None:raise ValueError('collection request missing')
    request=entry['request'];config=request['config']
    if hashlib.sha256(_read(base/'gold-inputs',f"{config['input_id']}.json")).hexdigest()!=config['input_sha256']:
        raise ValueError('collection template changed')
    from tools.gold_capture_memory import collection_memory
    memory=collection_memory(base,entry,prior,now=time.time_ns()//1000000)
    research=None
    if request.get('decision_policy')=='AI_DESIGNED_ZONE_V1':
        from tools.gold_research_memory import collection_strategy_memory
        research=collection_strategy_memory(base,entry,now=time.time_ns()//1000000)
    result=run_collect({'attempt':request['input_id'],'template_id':config['input_id'],'seconds':request['seconds'],
        **({'decision_policy':request['decision_policy']} if 'decision_policy' in request else {})},
        **({'design_memory':memory} if memory is not None else {}),
        **({'research_memory':research} if research is not None else {}))
    # The capture owns a frozen copy: catch replacement between check and capture.
    saved=_read(base/'gold-collections'/str(request['input_id']),'template.json')
    if hashlib.sha256(saved).hexdigest()!=config['input_sha256']:
        raise ValueError('captured template does not match collection request')
    actual={**config,'input_id':result['input_id'],'input_sha256':result['input_sha256']}
    with FactoryStore(base/'factory.db') as store:
        if store.get_collection(params['attempt'])!=entry:raise ValueError('collection request changed')
        store.create_campaign(actual,operator=request['operator'],now=time.time_ns()//1000000,
                              collection_hash=entry['request_hash'])
    return {**result,'campaign_id':params['attempt']}
