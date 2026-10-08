"""Recheck a completed factory trial with the original independent verifier."""
import time

from quantos.local_artifacts import atomic_write_json, with_result_hash
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory_store import FactoryStore
from services.research.gold_review_watch import observation
from tools import gold_cost_receipt


def run_review(params):
    if (type(params) is not dict or set(params)!={'attempt'} or type(params['attempt']) is not int
            or not 1<=params['attempt']<=1000):raise ValueError('invalid factory review attempt')
    base=runtime_state_root()/'data/cache/ai_factory';attempt=params['attempt']
    with FactoryStore(base/'factory.db') as store:session=store.get(attempt)
    if session['config'].get('market')!='XAUUSDT' or session['state']!='COMPLETED':
        raise ValueError('completed gold factory trial required')
    trial=base/'gold/results'/str(attempt)/'trial'
    now=time.time_ns()//1000000
    identity=observation(trial,gold_cost_receipt.REGISTRY,now=now)
    result=gold_cost_receipt.check_receipt(trial,now_ms=now)
    if (result['trial_id']!=session['result']['trial_id']
            or observation(trial,gold_cost_receipt.REGISTRY,now=time.time_ns()//1000000)!=identity):
        raise ValueError('factory review identity changed')
    latest=with_result_hash({'schema':'GOLD_FACTORY_REVIEW_VIEW_V1','attempt':attempt,
        'observation_hash':identity,'review':result,'authoritative':False,'economic_eligible':False})
    atomic_write_json(str(trial/'latest-review.json'),latest)
    return result
