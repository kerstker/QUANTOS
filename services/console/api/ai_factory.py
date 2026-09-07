"""Read-only workflow view; never connects to a model or reads credentials."""
import os
import sqlite3
import time
from fastapi import APIRouter,Query
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory_store import FactoryStore
from services.console.api.forward_dashboard import _read

router=APIRouter()

def factory_payload(root,attempt=None):
    errors=[]
    try: data=FactoryStore.snapshot(root/'data/cache/ai_factory/factory.db',attempt)
    except (OSError,ValueError,sqlite3.Error):
        data={'sessions':[],'selected':None,'trials':[],'events':[]};errors=['FACTORY_STORE_UNAVAILABLE']
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
        path=root/'data/cache/ai_factory'/str(data['selected']['attempt'])/'process-comparison.json'
        if path.exists():
            try:comparison=_read(path)
            except (OSError,ValueError):errors.append('COMPARISON_UNAVAILABLE')
    # Avoid returning every historical equity curve and duplicate event payload on each poll.
    data['sessions']=[{**row,'result':{'selected':row['result'].get('selected'),'model_hash':row['result'].get('model_hash')}} for row in data['sessions']]
    data['events']=[{**row,'payload':{k:v for k,v in row['payload'].items() if k in ('reason','action','operator','run_id','trial_id','candidate_id','ordinal')}} for row in data['events']]
    configured=bool(os.environ.get('QUANTOS_FACTORY_ENDPOINT') and os.environ.get('QUANTOS_FACTORY_MODEL'))
    return {**data,'resident':resident,'forward':forward,'assessment':assessment,'verification':verification,'pipeline':pipeline,'comparison':comparison,'errors':errors,'source':'BACKEND_COMPUTED_SNAPSHOT','read_only':True,'api_version':'0.1.0',
            'data_provenance':'REAL_AI_FACTORY_DIAGNOSTIC','snapshot_status':'REAL' if data['sessions'] else 'NO_SNAPSHOT',
            'authoritative':False,'as_of_ms':time.time_ns()//1000000,
            'provider':{'status':'CONFIGURED_UNVERIFIED' if configured else 'NOT_CONFIGURED',
                        'model':os.environ.get('QUANTOS_FACTORY_MODEL',''),'connected':False}}

@router.get('/api/v1/console/ai-factory')
def factory(attempt: int|None=Query(default=None,ge=1,le=1000)):
    return factory_payload(runtime_state_root(),attempt)
