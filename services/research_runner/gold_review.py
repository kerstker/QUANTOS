"""Schedule only new or time-invalidated gold review evidence on the existing queue."""
from services.research.ai_factory_store import FactoryStore
from services.research import gold_review_watch as watch
from services.research_runner.run_kinds import RUN_KINDS
from services.research_runner.runner import validate_run_provenance


def review_tick(root,queue,registry,*,now):
    if any(row.status in ('queued','running') for row in registry.list(limit=200)):
        return {'status':'WAITING_RUNNER'}
    base=root/'data/cache/ai_factory'
    for session in FactoryStore.snapshot(base/'factory.db')['sessions']:
        if session['config'].get('market')!='XAUUSDT' or session['state']!='COMPLETED':continue
        attempt=session['attempt']
        trial=base/'gold/results'/str(attempt)/'trial'
        identity=watch.observation(trial,watch.REGISTRY,now=now)
        key=f'gold-review-{attempt}-{identity}'
        previous=registry.get_by_invocation(key)
        if previous is not None:
            if previous.kind!='gold_factory_review' or previous.params!={'attempt':attempt}:
                raise ValueError('gold review invocation mismatch')
            validate_run_provenance(previous,RUN_KINDS['gold_factory_review'])
            continue
        run_id=queue.submit('gold_factory_review',{'attempt':attempt},now=now//1000,
            operator='factory-review-monitor',invocation_id=key)
        return {'status':'SUBMITTED_GOLD_REVIEW','attempt':attempt,'run_id':run_id}
    return {'status':'NO_REVIEW_CHANGES'}
