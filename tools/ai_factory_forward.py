"""Natural-time frozen hourly model observations, using only approved public REST."""
from __future__ import annotations
from pathlib import Path
import threading
import time
import numpy as np
from quantos.runtime_paths import runtime_state_root
from services.research.ai_factory import HOUR,fit_predict,candidates
from services.research.ai_factory_store import FactoryStore,digest
from services.research.ai_factory_forward import forward_features,forecast_document,label_document,diagnostics
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.btc_factory_research import seal,verified
from tools.btc_forward_observation import _get
from tools.cache_io import atomic_write_json

_LOCK=threading.Lock()
SCHEMA='QUANTOS_AI_FACTORY_FORWARD_V1'

def _now():return time.time_ns()//1000000

def _immutable(path,document):
    sealed=seal(document)
    if path.exists():
        if verified(path)!=sealed:raise ValueError('immutable observation conflict')
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        atomic_write_json(str(path),sealed)
    return sealed


def run_observation(params,*,root=None,clock=_now,get=_get):
    if set(params)!={'attempt','action'} or type(params['attempt']) is not int or not 1<=params['attempt']<=1000 or type(params['action']) is not int or params['action'] not in (0,1,2):
        raise ValueError('invalid hourly observation request')
    root=runtime_state_root() if root is None else Path(root)
    attempt=params['attempt'];base=root/'data/cache/ai_factory';dest=base/str(attempt)/'forward'
    if dest.is_symlink() or dest.parent.is_symlink():raise ValueError('unsafe observation path')
    dest.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(dest/'worker.lock',process_lock=_LOCK) as acquired:
        if not acquired:raise ValueError('hourly observation busy')
        try:return _run(base,dest,attempt,params['action'],clock,get)
        except Exception as exc:
            report=seal({'schema':SCHEMA,'attempt':attempt,'status':'ERROR','reason':type(exc).__name__,
                         'observed_at_ms':clock(),'authoritative':False,'account_pnl_computed':False})
            _immutable(dest/'errors'/f"{report['observed_at_ms']}.json",{k:v for k,v in report.items() if k!='result_hash'})
            atomic_write_json(str(dest/'status.json'),report)
            raise


def _run(base,dest,attempt,action,clock,get,*,load_model=None,predict=None,schema=SCHEMA):
    freeze_path=dest/'freeze.json';control_path=dest/'control.json'
    now=clock()
    if action==1:
        if load_model is not None:model=load_model(now)
        else:
            session=FactoryStore.snapshot(base/'factory.db',attempt)['selected']
            if session['state']!='COMPLETED':raise ValueError('completed research required')
            model=verified(dest.parent/'model.json');result=verified(dest.parent/'result.json')
            if (model['result_hash']!=result['model_hash'] or model['result_hash']!=session['result']['model_hash']
                or model['schema']!='QUANTOS_AI_FACTORY_V1' or model['spec'] not in candidates(2)
                or model['authoritative'] is not False or model['live_eligible'] is not False
                or model['training_last_label_ms']>=now):raise ValueError('model activation identity failed')
        if not freeze_path.exists():
            _immutable(freeze_path,{'schema':schema,'attempt':attempt,'activated_at_ms':now,
                                    'model':model,'authoritative':False,'account_pnl_computed':False})
        atomic_write_json(str(control_path),seal({'enabled':True,'updated_ms':now}))
    elif action==2:
        atomic_write_json(str(control_path),seal({'enabled':False,'updated_ms':now}))
    status='NOT_ACTIVATED'
    if freeze_path.exists():
        freeze=verified(freeze_path);model=freeze['model']
        if seal({k:v for k,v in model.items() if k!='result_hash'})!=model:raise ValueError('frozen model integrity failed')
        enabled=control_path.exists() and verified(control_path)['enabled'] is True
        status='WAITING_WINDOW' if enabled else 'DISABLED'
        if enabled and action==0:
            slot=now//HOUR*HOUR;forecast_path=dest/'forecasts'/f'{slot}.json'
            if freeze['activated_at_ms']<=slot and now<slot+300000 and not forecast_path.exists():
                capture=get(f'/fapi/v1/klines?symbol=BTCUSDC&interval=1h&startTime={slot-25*HOUR}&endTime={slot-1}&limit=25')
                captured=_immutable(dest/'captures'/f"{digest(capture)}.json",capture)
                features=forward_features(capture['payload'],slot)
                options=predict(model,features) if predict is not None else {
                    'prediction':float(fit_predict(model['spec'],model['training_x'],model['training_y'],[features])[0]),
                    'threshold_bps':model['spec']['threshold_bps']}
                issued=clock()
                if issued<slot+300000:
                    document=forecast_document(slot=slot,available=capture['received_at_ms'],issued=issued,
                        activated=freeze['activated_at_ms'],model_hash=model['result_hash'],input_hash=captured['result_hash'],
                        features=features,**{k:v for k,v in options.items() if k!='extra'})
                    document.update(options.get('extra',{}))
                    _immutable(forecast_path,document);status='OBSERVING'
                else:status='MISSED_WINDOW'
            # Mature outcomes may be collected after downtime; predictions are never backfilled.
            forecasts=sorted((dest/'forecasts').glob('*.json'))
            due=[p for p in forecasts if not (dest/'labels'/p.name).exists() and int(p.stem)+4*HOUR+360000<=clock()][:12]
            for path in due:
                forecast=verified(path);refs=[]
                for at in (forecast['entry_ms'],forecast['exit_ms']):
                    capture=get(f'/fapi/v1/klines?symbol=BTCUSDC&interval=1m&startTime={at}&endTime={at+59999}&limit=1')
                    captured=_immutable(dest/'captures'/f"{digest(capture)}.json",capture)
                    rows=capture['payload']
                    if len(rows)!=1 or int(rows[0][0])!=at or int(rows[0][6])!=at+59999:raise ValueError('mature reference unavailable')
                    refs.append({'event_ms':at,'available_at_ms':capture['received_at_ms'],'open':float(rows[0][1]),'source_hash':captured['result_hash']})
                _immutable(dest/'labels'/path.name,label_document(forecast,*refs,now=clock()));status='LABELS_UPDATED'
    forecast_paths=sorted((dest/'forecasts').glob('*.json'));label_paths=sorted((dest/'labels').glob('*.json'))
    # Bounded UI history; immutable full receipts remain on disk.
    labels=[verified(p) for p in label_paths[-1000:]]
    report={'schema':schema,'attempt':attempt,'status':status,'observed_at_ms':clock(),
            'activated_at_ms':verified(freeze_path)['activated_at_ms'] if freeze_path.exists() else None,
            'predictions':len(forecast_paths),'labels':len(label_paths),'pending':len(forecast_paths)-len(label_paths),
            'diagnostics':diagnostics(labels),'recent_forecasts':[verified(p) for p in forecast_paths[-48:]],
            'recent_labels':labels[-200:],'authoritative':False,'account_pnl_computed':False}
    document=seal(report);atomic_write_json(str(dest/'status.json'),document)
    return document


def advance_factory(root,*,now_ms):
    base=Path(root)/'data/cache/ai_factory';enabled=[];results=[]
    from tools.profitability_causal_workflow import advance_registered_information
    if not base.exists():return advance_registered_information(root)
    for attempt in range(1,1001):
        control=base/str(attempt)/'forward/control.json'
        if control.exists() and verified(control)['enabled'] is True:enabled.append(attempt)
    if len(enabled)>8:raise ValueError('at most eight hourly observations may be scheduled')
    for attempt in enabled:
        try:
            r=run_observation({'attempt':attempt,'action':0},root=root)
            from tools.profitability_forward_account import advance_cost_account
            cost=advance_cost_account(root,attempt)
            results.append({'attempt':attempt,'status':r['status'],'observed_at_ms':r['observed_at_ms'],
                            'forward_cost_status':cost['status'] if cost else 'NOT_REGISTERED'})
        except Exception as exc:results.append({'attempt':attempt,'status':'ERROR','reason':type(exc).__name__})
    from tools.ai_factory_portfolio_forward import advance_portfolios
    results.extend(advance_portfolios(root))
    results.extend(advance_portfolios(root,sleeves=True))
    results.extend(advance_registered_information(root))
    return results
