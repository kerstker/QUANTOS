"""Finite research scheduling through the existing Runner invocation contract."""
import json
from services.research.ai_factory_store import FactoryStore,digest
from services.research.ai_factory_lifecycle import research_trigger
from services.research_runner.run_kinds import RUN_KINDS
from services.research_runner.runner import validate_run_provenance


def campaign_tick(root,queue,registry,*,now):
    path=root/'data/cache/ai_factory/factory.db'
    if not path.exists():return {'status':'NO_CAMPAIGNS'}
    with FactoryStore(path) as store:
        snapshot=store.campaign_snapshot()
        for campaign in snapshot['campaigns']:
            cid=campaign['campaign_id']
            for round_ in [r for r in snapshot['rounds'] if r['campaign_id']==cid and r['state']=='RESERVED']:
                key=f"factory-campaign-{cid}-{round_['ordinal']}-{campaign['config_hash']}"
                run=registry.get_by_invocation(key)
                if run is not None and run.status in ('done','error','cancelled'):
                    if run.kind!='ai_factory_pipeline' or run.params!={'attempt':round_['attempt']}:raise ValueError('campaign run binding mismatch')
                    validate_run_provenance(run,RUN_KINDS['ai_factory_pipeline'])
                    with store.c:store.event(round_['attempt'],'OPERATOR_ACTION',{'action':'create','operator':campaign['operator'],'run_id':run.run_id})
                    session=store.get(round_['attempt'])
                    status='COMPLETED' if run.status=='done' and session['state']=='COMPLETED' else 'FAILED'
                    store.finish_campaign_round(cid,round_['ordinal'],status,now=now)
            if campaign['state']!='RUNNING':continue
            # The global Runner has one output-capturing worker. Do not build a hidden queue backlog.
            if any(r.status in ('queued','running') for r in registry.list(limit=200)):
                return {'status':'WAITING_RUNNER'}
            forward=None
            if campaign['config'].get('trigger')=='drift':
                champion=campaign['config']['champion_attempt']
                forward_path=root/'data/cache/ai_factory'/str(champion)/'forward/status.json'
                if forward_path.exists():
                    if forward_path.is_symlink() or forward_path.stat().st_size>2000000:raise ValueError('unsafe observation')
                    forward=json.loads(forward_path.read_text())
                    if forward.get('result_hash')!=digest({k:v for k,v in forward.items() if k!='result_hash'}) or forward.get('attempt')!=champion or forward.get('authoritative') is not False or forward.get('account_pnl_computed') is not False:raise ValueError('invalid trigger evidence')
                    expected=store.get(champion)['result'].get('model_hash')
                    if not expected or any(r.get('model_hash')!=expected for r in forward.get('recent_labels',[])):raise ValueError('champion label binding mismatch')
            pending=any(r['campaign_id']==cid and r['state']=='RESERVED' for r in snapshot['rounds'])
            trigger=research_trigger(campaign['config'],forward,now=now)
            if not pending and not trigger['ready']:continue
            if not pending:
                enabled=0
                for control in (root/'data/cache/ai_factory').glob('*/forward/control.json'):
                    document=json.loads(control.read_text())
                    if document.get('result_hash')!=digest({k:v for k,v in document.items() if k!='result_hash'}):raise ValueError('observation control integrity failed')
                    enabled+=document.get('enabled') is True
                if enabled>=8:return {'status':'WAITING_OBSERVATION_CAPACITY'}
            reservation=store.reserve_campaign(cid,now=now,trigger_key=trigger['key'])
            if reservation is None:continue
            key=f"factory-campaign-{cid}-{reservation['ordinal']}-{campaign['config_hash']}"
            run_id=queue.submit('ai_factory_pipeline',{'attempt':reservation['attempt']},now=now//1000,
                operator=campaign['operator'],invocation_id=key)
            with store.c:
                # An outbox reservation precedes enqueue; a crash repeats the same invocation identity.
                store.event(reservation['attempt'],'OPERATOR_ACTION',{'action':'create','operator':campaign['operator'],'run_id':run_id})
            return {'status':'SUBMITTED','campaign_id':cid,'attempt':reservation['attempt'],'run_id':run_id}
    return {'status':'WAITING_POLICY'}
