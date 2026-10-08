"""Finite research scheduling through the existing Runner invocation contract."""
import json
from services.research.ai_factory_store import FactoryStore,digest
from services.research.ai_factory_lifecycle import research_trigger
from services.research_runner.run_kinds import RUN_KINDS
from services.research_runner.runner import validate_run_provenance


def _feedback(root,key):
    import re
    from services.governance.json_integrity import load_json_without_duplicate_keys
    if type(key) is not str or re.fullmatch('[a-f0-9]{64}',key) is None:raise ValueError('invalid execution feedback key')
    path=root/'data/cache/ai_factory/execution-feedback'/(key+'.json')
    import os,stat
    if not path.exists() and not path.is_symlink():return None
    if path.resolve()!=path.absolute():raise ValueError('unsafe execution feedback')
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as handle:
            before=os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=2000000:
                raise ValueError('unsafe execution feedback')
            body=handle.read(2000001);after=os.fstat(handle.fileno())
        if len(body)!=before.st_size or any(getattr(before,k)!=getattr(after,k) for k in
                ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):
            raise ValueError('execution feedback changed during read')
    except OSError:raise ValueError('execution feedback unavailable') from None
    return load_json_without_duplicate_keys(body)


def _automatic_feedback(root,config,rounds,store,now):
    """Choose a delivered review for the preceding round, never create a budget."""
    from itertools import islice
    from services.research.gold_execution_feedback import research_trigger as ready, is_completed_episode
    rounds=sorted(rounds,key=lambda r:r['ordinal'])
    if not rounds:return config,None
    latest=rounds[-1]
    if latest['state']=='RESERVED':
        key=store.get(latest['attempt'])['config'].get('execution_feedback_sha256')
        return (dict(config,execution_feedback_sha256=key),_feedback(root,key)) if key else (config,None)
    selected=[];directory=root/'data/cache/ai_factory/execution-feedback'
    if directory.exists():
        if directory.resolve()!=directory.absolute():raise ValueError('unsafe execution feedback directory')
        paths=list(islice(directory.iterdir(),1001))
        if len(paths)>1000:raise ValueError('execution feedback directory budget exceeded')
        total_bytes=0
        for path in paths:
            if path.name.startswith('.feedback-') and path.suffix=='.tmp':continue
            if path.suffix!='.json':raise ValueError('unexpected execution feedback file')
            total_bytes+=path.lstat().st_size
            if total_bytes>16_000_000:raise ValueError('execution feedback scan byte budget exceeded')
            value=_feedback(root,path.stem)
            if value is None:continue
            if type(value) is not dict:raise ValueError('invalid execution feedback document')
            identity=value.get('research_identity')
            if type(identity) is not dict or identity.get('attempt')!=latest['attempt']:continue
            candidate=dict(config,execution_feedback_sha256=path.stem)
            if not is_completed_episode(value):continue
            if ready(candidate,value,now=now)['ready']:selected.append(value)
    if not selected:return dict(config,execution_feedback_sha256=None),None
    value=min(selected,key=lambda v:(v['available_at_ms'],v['result_hash']))
    return dict(config,execution_feedback_sha256=value['result_hash']),value


def campaign_tick(root,queue,registry,*,now):
    path=root/'data/cache/ai_factory/factory.db'
    if not path.exists():return {'status':'NO_CAMPAIGNS'}
    with FactoryStore(path) as store:
        store.verify_campaigns()
        snapshot=store.campaign_snapshot()
        for campaign in snapshot['campaigns']:
            cid=campaign['campaign_id']
            collection=store.get_collection(cid)
            if collection is not None:
                collection_key=f"factory-collection-{cid}-{collection['request_hash']}"
                collected=registry.get_by_invocation(collection_key)
                # Soft cancellation may let a collector finish local writes.
                # Only a completed registered collection can release research.
                if collected is None or collected.status!='done':continue
                if collected.kind!='gold_collect_campaign' or collected.params!={'attempt':cid}:
                    raise ValueError('collection campaign run binding mismatch')
                validate_run_provenance(collected,RUN_KINDS['gold_collect_campaign'])
                if any(collected.metrics.get(k)!=campaign['config'][k] for k in ('input_id','input_sha256')):
                    raise ValueError('collection campaign input binding mismatch')
            run_kind = 'gold_factory_research' if campaign['config'].get('market') == 'XAUUSDT' else 'ai_factory_pipeline'
            for round_ in [r for r in snapshot['rounds'] if r['campaign_id']==cid and r['state']=='RESERVED']:
                key=f"factory-campaign-{cid}-{round_['ordinal']}-{campaign['config_hash']}"
                run=registry.get_by_invocation(key)
                if run is not None and run.status in ('done','error','cancelled'):
                    if run.kind!=run_kind or run.params!={'attempt':round_['attempt']}:raise ValueError('campaign run binding mismatch')
                    validate_run_provenance(run,RUN_KINDS[run_kind])
                    with store.c:store.event(round_['attempt'],'OPERATOR_ACTION',{'action':'create','operator':campaign['operator'],'run_id':run.run_id})
                    session=store.get(round_['attempt'])
                    status='COMPLETED' if run.status=='done' and session['state']=='COMPLETED' else 'FAILED'
                    if run_kind == 'gold_factory_research':
                        recovery_key = key+'-recovery'
                        recovery = registry.get_by_invocation(recovery_key)
                        if session['state'] == 'RUNNING' or recovery is not None:
                            # Queue termination does not settle an interrupted workflow.
                            # One distinct, idempotent recovery invocation cannot call a model.
                            if recovery is None:
                                if campaign['state'] != 'RUNNING':
                                    return {'status':'WAITING_CAMPAIGN_CONTROL', 'campaign_id':cid}
                                if any(r.status in ('queued','running') for r in registry.list(limit=200)):
                                    return {'status':'WAITING_RUNNER'}
                                recovery_id = queue.submit('gold_factory_recover',
                                    {'attempt':round_['attempt']}, now=now//1000,
                                    operator=campaign['operator'], invocation_id=recovery_key)
                                return {'status':'SUBMITTED_GOLD_RECOVERY', 'campaign_id':cid,
                                        'attempt':round_['attempt'], 'run_id':recovery_id}
                            if recovery.kind != 'gold_factory_recover' or recovery.params != run.params:
                                raise ValueError('gold campaign recovery binding mismatch')
                            validate_run_provenance(recovery,RUN_KINDS['gold_factory_recover'])
                            session=store.get(round_['attempt'])
                            if recovery.status != 'done' or session['state'] != 'COMPLETED':
                                return {'status':'WAITING_GOLD_RECONCILIATION', 'campaign_id':cid,
                                        'attempt':round_['attempt'], 'run_id':recovery.run_id}
                            with store.c:
                                store.event(round_['attempt'],'CAMPAIGN_RECOVERED',
                                    {'original_run_id':run.run_id,'recovery_run_id':recovery.run_id})
                            status='COMPLETED'
                    store.finish_campaign_round(cid,round_['ordinal'],status,now=now)
            if campaign['state']!='RUNNING':continue
            own_rounds=[r for r in snapshot['rounds'] if r['campaign_id']==cid]
            if len(own_rounds)>=campaign['config']['max_cycles'] and not any(r['state']=='RESERVED' for r in own_rounds):
                store.reserve_campaign(cid,now=now)
                continue
            # The global Runner has one output-capturing worker. Do not build a hidden queue backlog.
            if any(r.status in ('queued','running') for r in registry.list(limit=200)):
                return {'status':'WAITING_RUNNER'}
            forward=None
            execution_feedback=None
            trigger_config=campaign['config']
            if trigger_config.get('execution_feedback_mode')=='after_first_round':
                trigger_config,execution_feedback=_automatic_feedback(root,trigger_config,
                    [r for r in snapshot['rounds'] if r['campaign_id']==cid],store,now)
            elif 'execution_feedback_sha256' in trigger_config:
                execution_feedback=_feedback(root,trigger_config['execution_feedback_sha256'])
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
            if 'execution_feedback_sha256' in trigger_config:
                from services.research.gold_execution_feedback import research_trigger as execution_trigger
                trigger=execution_trigger(trigger_config,execution_feedback,now=now)
            else:
                trigger=research_trigger(campaign['config'],forward,now=now)
            if not pending and not trigger['ready']:continue
            if not pending:
                enabled=0
                for control in (root/'data/cache/ai_factory').glob('*/forward/control.json'):
                    document=json.loads(control.read_text())
                    if document.get('result_hash')!=digest({k:v for k,v in document.items() if k!='result_hash'}):raise ValueError('observation control integrity failed')
                    enabled+=document.get('enabled') is True
                if enabled>=8:return {'status':'WAITING_OBSERVATION_CAPACITY'}
            reservation=store.reserve_campaign(cid,now=now,trigger_key=trigger['key'],execution_feedback=execution_feedback)
            if reservation is None:continue
            key=f"factory-campaign-{cid}-{reservation['ordinal']}-{campaign['config_hash']}"
            run_id=queue.submit(run_kind,{'attempt':reservation['attempt']},now=now//1000,
                operator=campaign['operator'],invocation_id=key)
            with store.c:
                # An outbox reservation precedes enqueue; a crash repeats the same invocation identity.
                store.event(reservation['attempt'],'OPERATOR_ACTION',{'action':'create','operator':campaign['operator'],'run_id':run_id})
            return {'status':'SUBMITTED','campaign_id':cid,'attempt':reservation['attempt'],'run_id':run_id}
    return {'status':'WAITING_POLICY'}
