"""Read-only workflow view; never connects to a model or reads credentials."""
import os
import sqlite3
import time
from fastapi import APIRouter,Query
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory_store import FactoryStore

router=APIRouter()

def factory_payload(root,attempt=None):
    errors=[]
    try: data=FactoryStore.snapshot(root/'data/cache/ai_factory/factory.db',attempt)
    except (OSError,ValueError,sqlite3.Error):
        data={'sessions':[],'selected':None,'trials':[],'events':[]};errors=['FACTORY_STORE_UNAVAILABLE']
    configured=bool(os.environ.get('QUANTOS_FACTORY_ENDPOINT') and os.environ.get('QUANTOS_FACTORY_MODEL'))
    return {**data,'errors':errors,'source':'BACKEND_COMPUTED_SNAPSHOT','read_only':True,'api_version':'0.1.0',
            'data_provenance':'REAL_AI_FACTORY_DIAGNOSTIC','snapshot_status':'REAL' if data['sessions'] else 'NO_SNAPSHOT',
            'authoritative':False,'as_of_ms':time.time_ns()//1000000,
            'provider':{'status':'CONFIGURED_UNVERIFIED' if configured else 'NOT_CONFIGURED',
                        'model':os.environ.get('QUANTOS_FACTORY_MODEL',''),'connected':False}}

@router.get('/api/v1/console/ai-factory')
def factory(attempt: int|None=Query(default=None,ge=1,le=1000)):
    return factory_payload(runtime_state_root(),attempt)
