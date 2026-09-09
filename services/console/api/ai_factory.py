"""Read-only workflow view; never connects to a model or reads credentials."""
import os
import sqlite3
import time
from fastapi import APIRouter,Query
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory_store import FactoryStore
from services.research.ai_factory_provider_contract import provider_status, provider_identity
from services.console.api.forward_dashboard import _read

from services.console.api.ai_factory_workflow import workflow_payload
from services.console.api.profitability_views import profitability_payload
from services.research.profitability_forward import forward_summary
from services.console.api.factory_portfolio import portfolio_payload

router=APIRouter()

def factory_payload(root,attempt=None):
    errors=[]
    try: data=FactoryStore.snapshot(root/'data/cache/ai_factory/factory.db',attempt)
    except (OSError,ValueError,sqlite3.Error):
        data={'sessions':[],'selected':None,'trials':[],'events':[]};errors=['FACTORY_STORE_UNAVAILABLE']
    source_capture=None
    portfolio=None
    forward=None;assessment=None;verification=None;pipeline=None;comparison=None;resident=None
    path=root/'data/cache/ai_factory/resident.json'
    if path.exists():
        try:
            resident=_read(path)
            if resident.get('authoritative') is not False or resident.get('account_pnl_computed') is not False:
                raise ValueError('invalid resident boundary')
        except (OSError,ValueError):resident=None;errors.append('RESIDENT_UNAVAILABLE')
    if data['selected']:
        path=root/'data/cache/ai_factory'/str(data['selected']['attempt'])/'forward/status.json'
        if path.exists():
            try:
                forward=_read(path)
                if forward.get('authoritative') is not False or forward.get('account_pnl_computed') is not False:
                    raise ValueError('invalid forward boundary')
            except (OSError,ValueError):forward=None;errors.append('HOURLY_FORWARD_UNAVAILABLE')
    if data['selected']:
        path=root/'data/cache/ai_factory'/str(data['selected']['attempt'])/'assessment.json'
        if path.exists():
            try:
                assessment=_read(path)
                if assessment.get('authoritative') is not False:raise ValueError('invalid assessment')
            except (OSError,ValueError):assessment=None;errors.append('ASSESSMENT_UNAVAILABLE')
    if data['selected']:
        path=root/'data/cache/ai_factory'/str(data['selected']['attempt'])/'verification.json'
        if path.exists():
            try:
                verification=_read(path)
                if verification.get('authoritative') is not False:raise ValueError('invalid verification')
            except (OSError,ValueError):verification=None;errors.append('VERIFICATION_UNAVAILABLE')
    if data['selected']:
        path=root/'data/cache/ai_factory'/str(data['selected']['attempt'])/'pipeline.json'
        if path.exists():
            try:pipeline=_read(path)
            except (OSError,ValueError):errors.append('PIPELINE_UNAVAILABLE')
    if data['selected']:
        directory=root/'data/cache/ai_factory'/str(data['selected']['attempt'])
        path=directory/'process-v2/process-comparison.json'
        if not path.exists():path=directory/'process-comparison.json'
        if path.exists():
            try:comparison=_read(path)
            except (OSError,ValueError):errors.append('COMPARISON_UNAVAILABLE')
    if data['selected']:
        path=root/'data/cache/ai_factory'/str(data['selected']['attempt'])/'sources.json'
        if path.exists():
            try:
                captured=_read(path)
                if captured.get('authoritative') is not False:raise ValueError('invalid source capture')
                source_capture={**captured,'records':[{k:v for k,v in r.items() if k!='excerpt'} for r in captured['records']]}
            except (OSError,ValueError,TypeError,KeyError):errors.append('SOURCE_CAPTURE_UNAVAILABLE')
    if data['selected']:
        try:portfolio=portfolio_payload(root/'data/cache/ai_factory'/str(data['selected']['attempt'])/'portfolio')
        except (OSError,ValueError,KeyError,TypeError):errors.append('PORTFOLIO_RESEARCH_UNAVAILABLE')
    data['portfolio_research']=portfolio
    # Avoid returning every historical equity curve and duplicate event payload on each poll.
    data['sessions']=[{**row,'result':{'selected':row['result'].get('selected'),'model_hash':row['result'].get('model_hash')}} for row in data['sessions']]
    data['events']=[{**row,'payload':{k:v for k,v in row['payload'].items() if k in ('reason','action','operator','run_id','trial_id','candidate_id','ordinal')}} for row in data['events']]
    provider=provider_status(os.environ)
    now=time.time_ns()//1000000
    if provider['status']!='NOT_CONFIGURED':
        for trial in data['trials']:
            receipt=trial.get('proposal',{})
            received=receipt.get('received_at_ms')
            if (receipt.get('provider_id')==provider_identity(os.environ)
                and receipt.get('model')==provider['model'] and type(received) is int
                and 0<=now-received<=900000):
                provider={**provider,'status':'RECENT_CALL_SUCCEEDED','connected':True}
                break
    forward_profitability=None
    if forward:
        try:
            forward_profitability=forward_summary(forward.get('recent_forecasts',[]),forward.get('recent_labels',[]),as_of_ms=now)
        except (ValueError,KeyError,TypeError):
            forward_profitability={'status':'UNKNOWN','reason':'FORWARD_WINDOW_CONTRACT_FAILED','economic_eligible':False}
    return {**data,'forward_profitability':forward_profitability,'profitability':profitability_payload(root,data['selected']['attempt'] if data['selected'] else None),'source_capture':source_capture,'workflow':workflow_payload(root,data['selected'],data['events'],pipeline),'resident':resident,'forward':forward,'assessment':assessment,'verification':verification,'pipeline':pipeline,'comparison':comparison,'errors':errors,'source':'BACKEND_COMPUTED_SNAPSHOT','read_only':True,'api_version':'0.1.0',
            'data_provenance':'REAL_AI_FACTORY_DIAGNOSTIC','snapshot_status':'REAL' if data['sessions'] else 'NO_SNAPSHOT',
            'authoritative':False,'as_of_ms':time.time_ns()//1000000,
            'provider':{key:provider[key] for key in ('status','model','connected')}}

@router.get('/api/v1/console/ai-factory')
def factory(attempt: int|None=Query(default=None,ge=1,le=1000)):
    return factory_payload(runtime_state_root(),attempt)


@router.get('/api/v1/console/ai-factory-lifecycle')
def lifecycle():
    from services.console.api.ai_factory_lifecycle import lifecycle_payload
    return lifecycle_payload(runtime_state_root())
