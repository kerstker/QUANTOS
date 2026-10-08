"""Recheck frozen diagnostic notes against their original gold artifacts."""
import hashlib
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.gold_research_memory import validate_memory, signal_diagnostics
from tools.gold_cost_review import _read, review_request
from tools.gold_ai_origin import verify_origin


def _verify_failed(base,row):
    from services.research.ai_factory_store import FactoryStore, digest
    from tools.ai_factory_collaboration import verify_collaboration
    snapshot=FactoryStore.snapshot(base/'factory.db',attempt=row['attempt'])
    session=snapshot['selected'];receipts=snapshot['trials']
    directory=base/'gold/results'/str(row['attempt'])
    if (session is None or session['state']!='FAILED' or session['config_hash']!=row['config_sha256']
            or session['config']['input_sha256']!=row['input_sha256']
            or receipts!=[{'candidate_id':row['candidate_id'],'status':'FAILED'}]
            or digest(receipts)!=row['trial_receipts_sha256']
            or hashlib.sha256(_read(directory,'input.json')).hexdigest()!=row['input_sha256']):
        raise ValueError('gold failed research memory source mismatch')
    proposal=directory/'research'
    if row['candidate_id'] is None:
        if (proposal/'result.json').exists() or (proposal/'result.json').is_symlink():
            raise ValueError('gold failed research memory source mismatch')
    elif verify_collaboration(proposal)['candidate_id']!=row['candidate_id']:
        raise ValueError('gold failed research memory source mismatch')


def verify_memory(base,memory):
    memory=validate_memory(memory)
    for row in memory['records']:
        if row['outcome']=='RESEARCH_FAILED':
            _verify_failed(base,row)
            continue
        directory=base/'gold/results'/str(row['attempt']);trial=directory/'trial'
        _read(trial,'independent-request.json')
        request=review_request(trial)
        raw=_read(trial,'report.json');report=load_json_without_duplicate_keys(raw)
        origin=load_json_without_duplicate_keys(_read(trial,'research-origin.json'))
        candidate=verify_origin(origin,_read(trial,'input.json'))
        if (request['terminal']!='PASSED' or request['trial_id']!=row['trial_id']
                or request['attempt']!=row['attempt'] or candidate!=row['candidate_id']
                or request['files']['report.json']!=row['report_sha256']
                or hashlib.sha256(raw).hexdigest()!=row['report_sha256']
                or hashlib.sha256(_read(directory,'input.json')).hexdigest()!=row['input_sha256']
                or report['outcome']!=row['outcome']):
            raise ValueError('gold research memory source mismatch')
        if 'signal_diagnostics' in row and row['signal_diagnostics']!=signal_diagnostics(report):
            raise ValueError('gold signal diagnostics source mismatch')
    return memory


def collection_strategy_memory(base,entry,*,now):
    """Only earlier batches of this collection group may inform a new design."""
    from services.research.ai_factory_store import FactoryStore
    from services.research.gold_research_memory import campaign_memory
    frozen=base/'gold-collections'/str(entry['request']['input_id'])/'research-memory.json'
    retained=None
    if frozen.exists() or frozen.is_symlink():
        retained=validate_memory(load_json_without_duplicate_keys(_read(frozen.parent,frozen.name)))
        if retained['as_of_ms']>now:raise ValueError('future gold strategy memory')
    with FactoryStore(base/'factory.db') as store:
        memory=campaign_memory(store,entry['campaign_id'],
            now=retained['as_of_ms'] if retained is not None else now,include_current=False)
    if retained is not None and memory!=retained:
        raise ValueError('gold strategy memory scope changed')
    return verify_memory(base,memory) if memory is not None else None
