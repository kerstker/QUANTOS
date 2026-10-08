"""Dispatch the next bounded collection batch through the existing job queue."""
from services.research.ai_factory_store import FactoryStore
from services.research_runner.run_kinds import RUN_KINDS
from services.research_runner.runner import validate_run_provenance


def program_tick(root,queue,registry,*,now):
    if any(r.status in ('queued','running') for r in registry.list(limit=200)):
        return {'status':'WAITING_RUNNER'}
    base=root/'data/cache/ai_factory'
    if not (base/'factory.db').exists():return {'status':'NO_COLLECTION_READY'}
    with FactoryStore(base/'factory.db') as store:
        for program in store.collection_programs_snapshot():
            if program['state']!='RUNNING':continue
            last=store.get_collection(program['batches'][-1]['campaign_id'])
            last_run=registry.get_by_invocation(f"factory-collection-{last['campaign_id']}-{last['request_hash']}")
            if last_run is not None:
                if last_run.kind!='gold_collect_campaign' or last_run.params!={'attempt':last['campaign_id']}:
                    raise ValueError('collection program invocation mismatch')
                validate_run_provenance(last_run,RUN_KINDS['gold_collect_campaign'])
                if last_run.status=='cancelled':
                    store.control_collection_program(program['root_id'],'stop',operator='factory-cancel-propagation')
                    continue
            used={r['request']['input_id'] for r in FactoryStore.read_collections(base/'factory.db')}
            available=next((n for n in range(1,1001) if n not in used and
                all(not p.exists() and not p.is_symlink() for p in
                    (base/'gold-inputs'/f'{n}.json',base/'gold-collections'/str(n),base/'gold-preparation'/str(n)))),None)
            if available is None:return {'status':'COLLECTION_INPUT_CAPACITY_REACHED'}
            entry=store.advance_collection_program(program['root_id'],now=now,input_id=available,failed=last_run is not None and last_run.status=='error')
            if entry is None:continue
            key=f"factory-collection-{entry['campaign_id']}-{entry['request_hash']}"
            previous=registry.get_by_invocation(key)
            if previous is not None:
                if previous.kind!='gold_collect_campaign' or previous.params!={'attempt':entry['campaign_id']}:
                    raise ValueError('collection program invocation mismatch')
                validate_run_provenance(previous,RUN_KINDS['gold_collect_campaign'])
                continue
            run_id=queue.submit('gold_collect_campaign',{'attempt':entry['campaign_id']},now=now//1000,
                operator=entry['request']['operator'],invocation_id=key)
            return {'status':'SUBMITTED_COLLECTION_BATCH','root_id':program['root_id'],
                    'campaign_id':entry['campaign_id'],'run_id':run_id}
    return {'status':'NO_COLLECTION_READY'}
