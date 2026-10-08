"""Build or recheck frozen diagnostics from prior requests in one capture group."""
import hashlib

from quantos.local_artifacts import with_result_hash
from services.data.gold_capture_snapshot import summarize_capture
from services.execution.perpetual_public_replay import prepare_public_replay
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.gold_capture_memory import validate_capture_memory
from tools.gold_capture_design import verify_design
from tools.gold_cost_inputs import load_inputs
from tools.gold_cost_review import _read


def _row(base,entry,now):
    request=entry['request'];input_id=request['input_id'];area=base/'gold-collections'/str(input_id)
    raw={}
    for name in ('request.json','template.json','design.json','trades.json','quotes.json','design-memory.json','research-memory.json'):
        if (area/name).exists() or (area/name).is_symlink():raw[name]=_read(area,name)
    row={'input_id':input_id,'request_hash':entry['request_hash'],
         'files':{name:hashlib.sha256(body).hexdigest() for name,body in raw.items()},
         'capture_status':'NO_CAPTURE',**{k:None for k in ('ended_at_ms','duration_ms','unique_aggregates',
             'window_ms','max_age_ms','window_count','first_boundary_delay_ms')}}
    if 'template.json' in raw:
        if row['files']['template.json']!=request['config']['input_sha256']:
            raise ValueError('GOLD_CAPTURE_MEMORY_TEMPLATE_CHANGED')
        doc=load_inputs(raw['template.json'])['document']
        row.update(window_ms=doc['rule']['window_ms'],max_age_ms=doc['rule']['max_age_ms'],
                   window_count=len(doc['decision_times_ms']))
    if 'request.json' in raw:
        captured=load_json_without_duplicate_keys(raw['request.json'])
        expected={'attempt':input_id,'template_id':request['config']['input_id'],'seconds':request['seconds'],
                  'template_sha256':request['config']['input_sha256'],'authoritative':False,
                  'policy':request.get('decision_policy','FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1')}
        if 'decision_policy' in request:expected['decision_policy']=request['decision_policy']
        if 'design-memory.json' in raw:
            prior=validate_capture_memory(load_json_without_duplicate_keys(raw['design-memory.json']))
            expected['design_memory_hash']=prior['result_hash']
        if 'research-memory.json' in raw:
            from services.research.gold_research_memory import validate_memory
            research=validate_memory(load_json_without_duplicate_keys(raw['research-memory.json']))
            expected['research_memory_hash']=research['result_hash']
        if captured!=expected:raise ValueError('GOLD_CAPTURE_MEMORY_REQUEST_CHANGED')
    if 'design.json' in raw:
        receipt=load_json_without_duplicate_keys(raw['design.json'])
        design=verify_design(receipt,seconds=request['seconds'])
        if receipt['received_at_ms']>now:raise ValueError('FUTURE_GOLD_CAPTURE_DESIGN')
        row.update({k:design[k] for k in ('window_ms','max_age_ms','window_count')})
    if 'trades.json' not in raw:return row
    try:
        trades=load_json_without_duplicate_keys(raw['trades.json'])
    except ValueError:
        return {**row,'capture_status':'UNVERIFIABLE_CAPTURE'}
    if type(trades) is dict and any(type(trades.get(k)) is int and trades[k]>now
                                    for k in ('started_at_ms','ended_at_ms')):
        raise ValueError('FUTURE_GOLD_CAPTURE_MEMORY')
    try:
        summarize_capture(trades,as_of_ms=now)
    except (ValueError,TypeError,KeyError):
        return {**row,'capture_status':'UNVERIFIABLE_CAPTURE'}
    row.update(capture_status=trades['status'],ended_at_ms=trades['ended_at_ms'],
               duration_ms=trades['ended_at_ms']-trades['started_at_ms'],unique_aggregates=trades['unique_aggregates'])
    if 'quotes.json' in raw and trades['packets'] and row['window_ms'] is not None:
        try:
            frames=prepare_public_replay(load_json_without_duplicate_keys(raw['quotes.json']))['frames']
        except (ValueError,TypeError,KeyError):
            return row  # Trade facts remain valid; quote-dependent coverage is unknown.
        if frames[-1].timestamp>now:raise ValueError('FUTURE_GOLD_CAPTURE_QUOTES')
        start=max(frames[0].timestamp,trades['packets'][0]['received_at_ms']+1)
        end=start+row['window_ms']
        boundary=next((p for p in trades['packets'] if p['payload']['T']>=end),None)
        if boundary is not None:row['first_boundary_delay_ms']=max(0,boundary['received_at_ms']-end)
    return row


def collection_memory(base,entry,prior,*,now):
    if not prior:return None
    if len(prior)>5:raise ValueError('GOLD_CAPTURE_MEMORY_BUDGET')
    frozen=base/'gold-collections'/str(entry['request']['input_id'])/'design-memory.json'
    if frozen.exists() or frozen.is_symlink():
        memory=validate_capture_memory(load_json_without_duplicate_keys(_read(frozen.parent,frozen.name)))
        if memory['as_of_ms']>now:raise ValueError('FUTURE_GOLD_CAPTURE_MEMORY')
        if memory['records']!=[_row(base,item,memory['as_of_ms']) for item in prior]:
            raise ValueError('GOLD_CAPTURE_MEMORY_SOURCE_CHANGED')
        return memory
    return validate_capture_memory(with_result_hash({'schema':'GOLD_CAPTURE_MEMORY_V1','as_of_ms':now,
        'records':[_row(base,item,now) for item in prior],'authoritative':False}))
