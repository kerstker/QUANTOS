"""Keyless, bounded BTC frozen-model observation worker; no order or account access."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import time
import urllib.request
import numpy as np

from quantos.runtime_paths import runtime_state_root
from services.research.btc_forward_observation import (
    DAY, MINUTE, freeze_models, record_forecast, record_outcome, observation_summary,
)
from services.research.realtime_alpha_shadow_store import RealtimeAlphaShadowStore
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.btc_factory_research import verified, seal
from tools.btc_mechanism_study import digest, imbalance
from tools.cache_io import atomic_write_json
from tools.perpetual_public_capture import _NoRedirect
from tools import btc_online_factory as online

HOST='https://fapi.binance.com'
SCHEMA='QUANTOS_BTC_FORWARD_OBSERVATION_V1'


def _now():return time.time_ns()//1_000_000


def _get(path):
    if not (path.startswith('/fapi/v1/klines?symbol=BTCUSDC&')
            or path.startswith('/fapi/v1/fundingRate?symbol=BTCUSDC&')):
        raise ValueError('non-public observation endpoint')
    requested=_now()
    request=urllib.request.Request(HOST+path,headers={'Accept':'application/json'},method='GET')
    with urllib.request.build_opener(_NoRedirect()).open(request,timeout=15) as response:
        if response.geturl()!=HOST+path:raise ValueError('unexpected observation redirect')
        raw=response.read(2_000_001)
    received=_now()
    if received<requested or len(raw)>2_000_000:raise ValueError('invalid observation response clock/size')
    payload=json.loads(raw)
    if not isinstance(payload,list):raise ValueError('public observation response must be a list')
    return {'path':path,'requested_at_ms':requested,'received_at_ms':received,'payload':payload,
            'raw_base64':base64.b64encode(raw).decode(),'sha256':digest(raw)}


def _features(slot,records):
    bars=_get(f'/fapi/v1/klines?symbol=BTCUSDC&interval=1d&startTime={slot-29*DAY}&endTime={slot-1}&limit=29')
    records.append(bars)
    funds=_get(f'/fapi/v1/fundingRate?symbol=BTCUSDC&startTime={slot-3*DAY}&endTime={slot-1}&limit=1000')
    records.append(funds)
    rows=bars['payload'];funding=funds['payload']
    if len(rows)!=29 or any(int(r[0])!=slot-(29-i)*DAY or int(r[6])!=int(r[0])+DAY-1 for i,r in enumerate(rows)):
        raise ValueError('missing complete forward daily bar')
    close=np.asarray([float(r[4]) for r in rows])
    if not np.isfinite(close).all() or (close<=0).any():raise ValueError('invalid forward close')
    expected=list(range(slot-3*DAY,slot,8*60*MINUTE))
    if [int(r['fundingTime']) for r in funding]!=expected or any(r['symbol']!='BTCUSDC' for r in funding):
        raise ValueError('forward funding schedule missing or unsupported')
    rates=np.asarray([float(r['fundingRate']) for r in funding])
    if not np.isfinite(rates).all():raise ValueError('invalid funding rate')
    flow=imbalance(np.array([float(rows[-1][7])]),np.array([float(rows[-1][10])]))
    returns=close[1:]/close[:-1]-1
    return [float(returns[-1]),float(close[-1]/close[-8]-1),float(np.std(returns,ddof=1)),flow,float(rates.sum())],max(bars['received_at_ms'],funds['received_at_ms'])


def _reference(at,records):
    response=_get(f'/fapi/v1/klines?symbol=BTCUSDC&interval=1m&startTime={at}&endTime={at+MINUTE-1}&limit=1')
    records.append(response)
    rows=response['payload']
    if len(rows)!=1 or int(rows[0][0])!=at or int(rows[0][6])!=at+MINUTE-1:
        raise ValueError('mature reference minute missing')
    return {'event_ms':at,'available_at_ms':response['received_at_ms'],'open':float(rows[0][1]),'source_hash':response['sha256']}


def _persist_capture(directory,records):
    if not records:return None
    captures=directory/'captures';captures.mkdir(exist_ok=True)
    capture=seal({'records':records,'received_at_ms':_now()})
    atomic_write_json(str(captures/(capture['result_hash']+'.json')),capture,indent=2)
    return capture


def _run_forward(params):
    if (set(params)-{'attempt','action'} or type(params.get('attempt')) is not int or not 1<=params['attempt']<=1000
            or type(params.get('action',0)) is not int or params.get('action',0) not in (0,1,2,3)):
        raise ValueError('invalid forward request')
    root=runtime_state_root()/'data/cache';attempt=params['attempt'];action=params.get('action',0)
    directory=root/'btc_forward'/str(attempt);freeze_path=directory/'freeze.json'
    if action==0 and not freeze_path.exists():
        return seal({'schema':SCHEMA,'status':'NO_FREEZE','models':[],'authoritative':False,'account_pnl_computed':False,
                     'attempt':attempt,'observed_at_ms':_now(),'reason':'先冻结已完成的工厂实验模型。'})
    directory.mkdir(parents=True,exist_ok=True)
    with acquire_runtime_lock(directory/'worker.lock') as acquired:
        if not acquired:raise ValueError('forward worker already running')
        if action==1 and not freeze_path.exists():
            source=verified(root/'btc_factory/results'/str(attempt)/'result.json')
            if source['schema']!='QUANTOS_BTC_FACTORY_REPLAY_V1' or not source['trial_ledger_verified'] or any(c['status']!='PASSED' for c in source['cases']):
                raise ValueError('complete factory source required')
            freeze=seal({'source_hash':source['result_hash'],'frozen_at_ms':_now(),
                         'fits':{name:rows[-1] for name,rows in source['fits'].items()}})
            atomic_write_json(str(freeze_path),freeze,indent=2)
        freeze=verified(freeze_path)
        with RealtimeAlphaShadowStore(str(directory/'observations.db'),create=True) as store:
            ids=freeze_models(store,freeze['fits'],source_hash=freeze['source_hash'],now_ms=freeze['frozen_at_ms'])
            if action==3:online.enable(directory,freeze['result_hash'],_now())
            months,groups=online.prepare_month(directory,store,ids,freeze['result_hash'],_now(),clock=_now) if action==2 else (
                online.versions(directory,freeze['result_hash'])[1],[])
            if action!=2:groups=online.admit_versions(store,months)
            all_ids=[*ids.values(),*(candidate for _,group in groups for candidate in group.values())]
            status='FROZEN';reason='等待下一个 UTC 00:00–00:05 决策窗口。'
            if action==3:
                status='ONLINE_ENABLED';reason='在线工厂已启用；每日推进观察，月度仅使用实际收到的成熟前向样本。'
            if action==2:
                now=_now();slot=now//DAY*DAY
                # Publish each successful receipt before any decision, including partial failures.
                if now<slot+5*MINUTE:
                    input_path=directory/'daily_inputs'/f'{slot}.json'
                    if input_path.exists():
                        captured=verified(input_path)
                        if captured['slot_ms']!=slot:raise ValueError('daily observation input identity mismatch')
                        capture=verified(directory/'captures'/(captured['capture_hash']+'.json'))
                        features=captured['features'];available=captured['available_at_ms']
                    else:
                        records=[]
                        try:
                            features,available=_features(slot,records)
                        finally:
                            capture=_persist_capture(directory,records)
                        input_path.parent.mkdir(exist_ok=True)
                        atomic_write_json(str(input_path),seal({'slot_ms':slot,'features':features,
                            'available_at_ms':available,'capture_hash':capture['result_hash']}),indent=2)
                    now=_now()
                    if now<slot+5*MINUTE:
                        active=[*ids.values(),*(candidate for row,group in groups
                            if row['cutoff_ms']//DAY*DAY<=slot and row['month']==time.strftime('%Y-%m',time.gmtime(slot/1000))
                            for candidate in group.values())]
                        for candidate in active:
                            record_forecast(store,candidate,features=features,information_end_ms=slot,
                                            available_at_ms=available,now_ms=_now(),source_hash=capture['result_hash'])
                        online.record_factory_day(directory,store,months,groups,_now(),source_hash=capture['result_hash'])
                        status='OBSERVING';reason='新预测已记录，等待24小时标签自然成熟。'
                now=_now()
                due=sorted({r['payload']['entry_time_ms'] for candidate in all_ids for r in store.pending(candidate)
                            if r['payload']['exit_time_ms']+MINUTE<=now})[:7]
                if due:
                    records=[];refs={}
                    try:
                        for entry in due:
                            for at in (entry,entry+DAY):
                                if at not in refs:refs[at]=_reference(at,records)
                    finally:
                        _persist_capture(directory,records)
                    for candidate in all_ids:
                        for row in store.pending(candidate):
                            decision=row['payload'];entry=decision['entry_time_ms']
                            if entry in due:
                                record_outcome(store,candidate,decision,entry=refs[entry],exit=refs[entry+DAY],now_ms=_now())
                    status='LABELS_UPDATED';reason='已追加所有版本的成熟标签；原始预测保持原值。'
                elif status=='FROZEN':
                    status='WAITING_WINDOW';reason='当前不在决策窗口且没有待结算的成熟标签；没有补写历史预测。'
            report=seal({'schema':SCHEMA,'status':status,'reason':reason,'attempt':attempt,
                         'observed_at_ms':_now(),'freeze_hash':freeze['result_hash'],
                         'models':[observation_summary(store,candidate) for candidate in ids.values()],
                         'online_factory':online.report(directory,store,ids,freeze['result_hash']),
                         'authoritative':False,'account_pnl_computed':False})
            atomic_write_json(str(directory/'status.json'),report,indent=2)
            return report


def run_forward(params):
    try:
        return _run_forward(params)
    except Exception as exc:
        attempt=params.get('attempt') if isinstance(params,dict) else None
        if type(attempt) is int and 1<=attempt<=1000:
            directory=runtime_state_root()/'data/cache/btc_forward'/str(attempt)
            if directory.exists():
                errors=directory/'errors';errors.mkdir(exist_ok=True)
                error=seal({'observed_at_ms':_now(),'error_type':type(exc).__name__,'message':str(exc),
                            'action':params.get('action',0)})
                atomic_write_json(str(errors/(error['result_hash']+'.json')),error,indent=2)
        raise
