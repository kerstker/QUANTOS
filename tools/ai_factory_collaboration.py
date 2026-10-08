"""One or three bounded logical research roles over the same candidate universe."""
import time
from pathlib import Path
from services.research.ai_factory_store import digest
from tools.ai_factory_provider import propose
from tools.btc_factory_research import verified,seal
from tools.profitability_study import write_new

PROTOCOLS={'SINGLE_V1':('PROPOSE',),'THREE_ROLES_V1':('PROPOSE','COUNTER_PROPOSE','SYNTHESIZE')}


def usage_summary(research):
    reservations=list(Path(research).glob('research-roles/*/roles/*.started.json'))
    receipts=[]
    for path in reservations:
        receipt=path.with_name(path.name.replace('.started.json','.json'))
        if receipt.exists():receipts.append(verified(receipt))
    complete=len(receipts)==len(reservations)
    return {'model_calls':len(reservations),'model_completed_calls':len(receipts),
        'model_elapsed_ms':sum(r['wall_elapsed_ms'] for r in receipts) if complete and all(type(r.get('wall_elapsed_ms')) is int for r in receipts) else None,
        'model_total_tokens':sum(r['proposal']['usage']['total_tokens'] for r in receipts) if complete and all(type(r.get('proposal',{}).get('usage',{}).get('total_tokens')) is int for r in receipts) else None}


def verify_collaboration(output):
    output=Path(output);plan=verified(output/'plan.json');raw={k:v for k,v in plan.items() if k!='result_hash'}
    roles=PROTOCOLS.get(plan['protocol'])
    if not roles or list(roles)!=plan['roles'] or plan['maximum_calls']!=len(roles):raise ValueError('research role plan conflict')
    peers=[];records=[];elapsed=0
    for index,role in enumerate(roles):
        path=output/'roles'/f'{index}.json';reservation=output/'roles'/f'{index}.started.json'
        identity={'plan_hash':digest(raw),'role':role,'peer_candidates':list(peers)}
        if reservation.exists() and verified(reservation)!=seal(identity):raise ValueError('research role reservation conflict')
        if not path.exists():break
        record=verified(path)
        if not reservation.exists() or record['identity']!=identity or record['role']!=role:raise ValueError('research role receipt conflict')
        if record['status']!='PASSED':break
        candidate=record['proposal']['candidate_id'];wall=record['wall_elapsed_ms']
        if type(candidate) is not int or candidate not in plan['remaining'] or type(wall) is not int or wall<0:raise ValueError('research role value conflict')
        elapsed+=wall;peers.append(candidate);records.append(record)
    result_path=output/'result.json'
    if not result_path.exists():return None
    result=verified(result_path)
    if (len(records)!=len(roles) or elapsed>60000 or result.get('plan_hash')!=plan['result_hash']
            or result['candidate_id']!=peers[-1] or result['elapsed_ms']!=elapsed
            or result['roles']!=[{'role':r['role'],**r['proposal']} for r in records]):raise ValueError('research role result conflict')
    return result


def bounded_proposal(objective,remaining,history,*,protocol,output,options,propose_fn=propose):
    if protocol not in PROTOCOLS or set(options)-{'research_context','history_evidence','search_plan','source_context','candidate_catalog','research_memory'}:
        raise ValueError('unknown bounded research protocol')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    roles=PROTOCOLS[protocol]
    plan={'schema':'QUANTOS_BOUNDED_ROLES_V1','protocol':protocol,'objective':objective,'remaining':remaining,
        'history':history,'options_hash':digest(options),'roles':list(roles),'maximum_calls':len(roles),
        'wall_budget_seconds':60,'maximum_completion_tokens_per_call':400,'search_feedback_scope':'INNER_ONLY',
        'economic_eligible':False}
    path=output/'plan.json'
    if path.exists():
        if verified(path)!=seal(plan):raise ValueError('research role identity changed')
    else:write_new(path,plan)
    if (output/'result.json').exists():return verify_collaboration(output)
    (output/'roles').mkdir(exist_ok=True);records=[];peers=[];started=time.monotonic();cached_seconds=0.
    for index,role in enumerate(roles):
        path=output/'roles'/f'{index}.json';reservation=output/'roles'/f'{index}.started.json'
        identity={'plan_hash':digest(plan),'role':role,'peer_candidates':list(peers)}
        if path.exists():
            record=verified(path)
            if record['identity']!=identity:raise ValueError('research role receipt conflict')
            if record['status']=='PASSED':cached_seconds+=record['wall_elapsed_ms']/1000
        elif reservation.exists():
            if verified(reservation)!=seal(identity):raise ValueError('research role reservation conflict')
            record={'identity':identity,'role':role,'status':'FAILED','reason':'INTERRUPTED'}
            write_new(path,record)
        else:
            write_new(reservation,identity)
            remaining_seconds=60-cached_seconds-(time.monotonic()-started);call_start=time.monotonic()
            try:
                if remaining_seconds<1:raise TimeoutError('role wall budget exhausted')
                reply=propose_fn(objective,remaining,history,**options,role=role,peer_candidates=list(peers),
                    timeout_seconds=min(30,max(1,int(remaining_seconds))))
                if type(reply['candidate_id']) is not int or reply['candidate_id'] not in remaining:raise ValueError('role selected unavailable candidate')
                if time.monotonic()-call_start>remaining_seconds:raise TimeoutError('role wall budget exceeded')
                record={'identity':identity,'role':role,'status':'PASSED','proposal':reply}
            except Exception as exc:record={'identity':identity,'role':role,'status':'FAILED','reason':type(exc).__name__}
            record['wall_elapsed_ms']=int((time.monotonic()-call_start)*1000)
            write_new(path,record)
        if record['status']!='PASSED':raise ValueError('research role failed; receipt retained')
        records.append(record);peers.append(record['proposal']['candidate_id'])
    replies=[r['proposal'] for r in records]
    if len({(r['model'],r.get('provider_id')) for r in replies})!=1:raise ValueError('research role model changed')
    usage={}
    for key in ('prompt_tokens','completion_tokens','total_tokens'):
        if all(type(r.get('usage',{}).get(key)) is int for r in replies):usage[key]=sum(r['usage'][key] for r in replies)
    result={'plan_hash':seal(plan)['result_hash'],'protocol':protocol,'candidate_id':peers[-1],'model':replies[-1]['model'],
        'request_hash':digest([r.get('request_hash') for r in replies]),'response_hash':digest([r.get('response_hash') for r in replies]),
        'roles':[{'role':row['role'],**row['proposal']} for row in records],
        'elapsed_ms':sum(r['wall_elapsed_ms'] for r in records),'usage':usage,'disagreement':len(set(peers))>1,
        'context_hash':options.get('research_context',{}).get('context_hash'),
        'provider_id':replies[-1].get('provider_id'),'received_at_ms':replies[-1].get('received_at_ms'),
        'economic_eligible':False,'limitations':['SAME_MODEL_ROLES_NOT_INDEPENDENT_EVIDENCE','MODEL_WEIGHTS_IDENTITY_UNVERIFIED']}
    write_new(output/'result.json',result);return verified(output/'result.json')
