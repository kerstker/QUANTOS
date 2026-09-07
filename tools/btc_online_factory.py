"""Durable monthly forward model succession, called under the observation worker lock."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from services.research.btc_forward_observation import (
    COLUMNS, DAY, MINUTE, freeze_models, forward_training_rows, online_month_fit, online_month_selection, observation_summary,
)
from services.research.btc_factory_replay import prediction_target
from tools.btc_factory_research import seal, verified
from tools.cache_io import atomic_write_json

SCHEMA='QUANTOS_BTC_ONLINE_FACTORY_V1'
RULES={'schema':SCHEMA,'train_min':180,'training':'expanding_received_forward_only',
       'selection_min':60,'selection_max':90,'threshold_bps':19.2,'month_cutoff':'first_observed_day_midnight'}


def implementation_hash():
    root=Path(__file__).resolve().parents[1]
    files=('tools/btc_online_factory.py','tools/btc_forward_observation.py',
           'services/research/btc_forward_observation.py','services/research/btc_factory_replay.py')
    return seal({'files':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}})['result_hash']


def _write(path,value):
    value=seal(value)
    atomic_write_json(str(path),value,indent=2)
    return value


def enable(directory,freeze_hash,now_ms):
    path=directory/'online.json'
    if not path.exists():
        _write(path,{'rules':RULES,'freeze_hash':freeze_hash,'enabled_at_ms':now_ms,'implementation_hash':implementation_hash()})
    return verified(path)


def versions(directory,freeze_hash):
    config_path=directory/'online.json'
    if not config_path.exists():return None,[]
    config=verified(config_path)
    if config['rules']!=RULES or config['freeze_hash']!=freeze_hash:
        raise ValueError('online factory rules or original freeze changed')
    if config.get('implementation_hash')!=implementation_hash():
        raise ValueError('online factory implementation changed; use a new observation attempt')
    rows=[]
    for path in sorted((directory/'months').glob('*.json')):
        row=verified(path)
        if row['config_hash']!=config['result_hash'] or path.stem!=row['month']:
            raise ValueError('online month identity mismatch')
        rows.append(row)
    return config,rows


def admit_versions(store,months):
    return [(row,freeze_models(store,row['fits'],source_hash=row['result_hash'],now_ms=row['created_at_ms']))
            for row in months if row['fits']]


def _selection_rows(store,groups):
    combined={}
    for _,ids in groups:
        panels={name:{r['entry_time_ms']:r for r in forward_training_rows(store,candidate)}
                for name,candidate in ids.items()}
        common=set.intersection(*(set(panel) for panel in panels.values()))
        for entry in common:
            if entry in combined:raise ValueError('overlapping online model versions')
            items=[panels[name][entry] for name in COLUMNS]
            if len({item['label'] for item in items})!=1:raise ValueError('online labels disagree')
            combined[entry]={'entry_time_ms':entry,'label':items[0]['label'],
                             'available_at_ms':max(item['available_at_ms'] for item in items),
                             'predictions':{name:panels[name][entry]['prediction'] for name in COLUMNS}}
    return [combined[entry] for entry in sorted(combined)]


def prepare_month(directory,store,frozen_ids,freeze_hash,now_ms,*,clock=None):
    config,months=versions(directory,freeze_hash)
    groups=admit_versions(store,months)
    slot=now_ms//DAY*DAY
    if config is None or not config['enabled_at_ms']<=now_ms<slot+5*MINUTE:return months,groups
    month=datetime.fromtimestamp(slot/1000,timezone.utc).strftime('%Y-%m')
    if not any(row['month']==month for row in months):
        training=[row for row in forward_training_rows(store,frozen_ids['PRICE']) if row['available_at_ms']<=slot]
        fits=online_month_fit(training,slot)
        selection_rows=_selection_rows(store,groups)
        selected=online_month_selection(selection_rows,slot)
        created=clock() if clock is not None else now_ms
        if not now_ms<=created<slot+5*MINUTE:raise ValueError('monthly fit missed observation window')
        row={'schema':SCHEMA,'month':month,'created_at_ms':created,'cutoff_ms':slot,
             'config_hash':config['result_hash'],'training_n':len(training),
             'training_hash':seal({'rows':training})['result_hash'],
             'selection_input_hash':seal({'rows':[r for r in selection_rows if r['available_at_ms']<=slot]})['result_hash'],
             'status':'READY' if fits else 'WAITING_TRAINING','fits':fits,'selection':selected}
        destination=directory/'months';destination.mkdir(exist_ok=True)
        row=_write(destination/(month+'.json'),row)
        months.append(row)
        groups=admit_versions(store,months)
    return months,groups


def record_factory_day(directory,store,months,groups,now_ms,*,source_hash):
    slot=now_ms//DAY*DAY
    if now_ms>=slot+5*MINUTE:return
    month=datetime.fromtimestamp(slot/1000,timezone.utc).strftime('%Y-%m')
    current=next((row for row in months if row['month']==month),None)
    if current is None:return
    selected=current['selection']['selected'];prediction=None;ids={};decision_hashes={}
    if current['fits']:
        ids=next(ids for row,ids in groups if row['result_hash']==current['result_hash'])
        for name,candidate in ids.items():
            decisions=[r for r in store.decisions(candidate) if r['payload']['entry_time_ms']==slot+5*MINUTE]
            if len(decisions)!=1:return
            decision_hashes[name]=decisions[0]['row_hash']
            if name==selected:prediction=decisions[0]['payload']['prediction']
    path=directory/'factory_days'/f'{slot}.json'
    if path.exists():
        old=verified(path)
        if old['month_hash']!=current['result_hash'] or old['decision_hashes']!=decision_hashes or old['source_hash']!=source_hash:
            raise ValueError('factory decision conflicts with retained version')
        return
    path.parent.mkdir(exist_ok=True)
    _write(path,{'schema':SCHEMA,'month_hash':current['result_hash'],'decision_time_ms':now_ms,
                 'entry_time_ms':slot+5*MINUTE,'selected':selected,'prediction':prediction,
                 'target_weight':prediction_target(prediction) if prediction is not None else 0.,
                 'candidate_ids':ids,'decision_hashes':decision_hashes,'source_hash':source_hash,
                 'account_pnl_computed':False,'order_submission_allowed':False})


def report(directory,store,frozen_ids,freeze_hash):
    config,months=versions(directory,freeze_hash)
    if config is None:return None
    latest=months[-1] if months else None
    summaries={row['result_hash']:[observation_summary(store,candidate) for candidate in ids.values()]
               for row,ids in admit_versions(store,months)}
    days=sorted((directory/'factory_days').glob('*.json'))
    return {'enabled_at_ms':config['enabled_at_ms'],'training_required':180,
            'training_available':len(forward_training_rows(store,frozen_ids['PRICE'])),
            'status':latest['status'] if latest else 'WAITING_WINDOW',
            'versions':[{'month':r['month'],'version_hash':r['result_hash'],'status':r['status'],
                         'created_at_ms':r['created_at_ms'],'training_n':r['training_n'],
                         'selection':r['selection'],'models':summaries.get(r['result_hash'],[])} for r in months],
            'factory_decisions':len(days),'latest_decision':verified(days[-1]) if days else None}
