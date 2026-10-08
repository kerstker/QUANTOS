"""Read fixed portfolio observation files and reproduce a preissued forecast.

Hashes bind content, not economic authority. Runtime callers must select the
research root and source binding from protected operator configuration. Public
funding is a research input, not a claim about Demo account settlements.
"""
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
from decimal import Decimal

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from live.execution import demo_portfolio_task
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory import HOUR, candidates
from services.research.ai_factory_forward import forward_features, forecast_document
from services.research.ai_factory_store import digest
from tools.ai_factory_portfolio_forward import _prediction
from tools.btc_forward_observation import _get, _now

MAX_BYTES=16*1024*1024


def bars_path(slot):
    return f'/fapi/v1/klines?symbol=BTCUSDC&interval=1h&startTime={slot-25*HOUR}&endTime={slot-1}&limit=25'


def funding_path(now_ms):
    return f'/fapi/v1/fundingRate?symbol=BTCUSDC&endTime={now_ms-1}&limit=9'


def capture_funding():
    """One approved keyless public GET; no account access or retry loop."""
    return _get(funding_path(_now()))


def _sealed(value):
    require(type(value) is dict and value.get('result_hash')==digest({k:v for k,v in value.items() if k!='result_hash'}),
        'PORTFOLIO_SOURCE_DIGEST')
    return value


def _read(path):
    path=Path(path)
    require(path.is_absolute() and '..' not in path.parts,'PORTFOLIO_SOURCE_PATH')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child
        leaf=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
        with os.fdopen(leaf,'rb') as stream:
            before=os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and 0<before.st_size<=MAX_BYTES,'PORTFOLIO_SOURCE_SIZE')
            raw=stream.read(MAX_BYTES+1);after=os.fstat(stream.fileno())
        require(len(raw)==before.st_size and all(getattr(before,k)==getattr(after,k) for k in
            ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')),'PORTFOLIO_SOURCE_CHANGED')
        return _sealed(load_json_without_duplicate_keys(raw))
    finally:os.close(fd)


def _capture(value,path,now_ms):
    require(value.get('path')==path,'PORTFOLIO_CAPTURE_PATH')
    requested,received=value['requested_at_ms'],value['received_at_ms']
    require(type(requested) is int and type(received) is int and 0<=requested<=received<=now_ms,
        'PORTFOLIO_CAPTURE_CLOCK')
    encoded=value['raw_base64']
    require(type(encoded) is str and 0<len(encoded)<=2_666_668,'PORTFOLIO_CAPTURE_SIZE')
    raw=base64.b64decode(encoded,validate=True)
    require(0<len(raw)<=2_000_000 and hashlib.sha256(raw).hexdigest()==value['sha256'] and
        load_json_without_duplicate_keys(raw)==value['payload'],'PORTFOLIO_CAPTURE_RAW')
    return value['payload']


def load_forecast(*,root,binding,allocation,now_ms):
    demo_portfolio_task.strategy_id(allocation)
    _fields(binding,'schema attempt freeze_sha256 observer_source_sha256')
    require(binding['schema']=='DEMO_PORTFOLIO_SOURCE_BINDING_V1' and type(binding['attempt']) is int and
        1<=binding['attempt']<=1000,'PORTFOLIO_SOURCE_BINDING')
    require(allocation['schema']=='DEMO_PORTFOLIO_ALLOCATION_V2' and
        allocation['source_binding_sha256']==_digest(binding),'PORTFOLIO_SOURCE_NOT_APPROVED')
    for key in ('freeze_sha256','observer_source_sha256'):
        require(type(binding[key]) is str and re.fullmatch('[a-f0-9]{64}',binding[key]),'PORTFOLIO_SOURCE_BINDING')
    require(type(now_ms) is int and now_ms>26*HOUR,'PORTFOLIO_SOURCE_CLOCK')
    base=Path(root)/'data/cache/ai_factory'/str(binding['attempt'])/'portfolio/forward'
    freeze=_read(base/'freeze.json');source=_read(base/'source.json')
    require(freeze['result_hash']==binding['freeze_sha256'] and source['result_hash']==binding['observer_source_sha256'],
        'PORTFOLIO_FROZEN_SOURCE_CHANGED')
    slot=(now_ms-300000)//HOUR*HOUR
    model=_sealed(freeze['model'])
    require(freeze['schema']=='QUANTOS_PORTFOLIO_FORWARD_V1' and freeze['attempt']==binding['attempt'] and
        type(freeze['activated_at_ms']) is int and 0<freeze['activated_at_ms']<=slot and
        freeze['authoritative'] is False and freeze['account_pnl_computed'] is False and
        model['schema']=='QUANTOS_FACTORY_PORTFOLIO_V1' and model['result_hash']==allocation['model_hash'] and
        type(model['training_last_label_ms']) is int and model['training_last_label_ms']<freeze['activated_at_ms'] and
        all(model[k] is False for k in ('authoritative','live_eligible','economic_eligible')),
        'PORTFOLIO_FROZEN_MODEL')
    require(1<=len(model['components'])<=3 and all(c in candidates(2) for c in model['components']) and
        32<=len(model['training_x'])<=32768,'PORTFOLIO_MODEL_BUDGET')
    forecast=_read(base/'forecasts'/f'{slot}.json')
    identity=forecast.get('input_hash')
    require(type(identity) is str and re.fullmatch('[a-f0-9]{64}',identity),'PORTFOLIO_CAPTURE_IDENTITY')
    capture=_read(base/'captures'/f'{identity}.json')
    require(capture['result_hash']==identity,'PORTFOLIO_CAPTURE_IDENTITY')
    rows=_capture(capture,bars_path(slot),now_ms)
    require(capture['requested_at_ms']>=slot,'PORTFOLIO_CAPTURE_BEFORE_BAR_CLOSE')
    features=forward_features(rows,slot);options=_prediction(model,features)
    expected=forecast_document(slot=slot,available=capture['received_at_ms'],issued=forecast['issued_at_ms'],
        activated=freeze['activated_at_ms'],model_hash=model['result_hash'],input_hash=identity,features=features,
        **{k:v for k,v in options.items() if k!='extra'})
    expected.update(options['extra']);expected['result_hash']=digest(expected)
    require(expected==forecast,'PORTFOLIO_FORECAST_RECOMPUTATION')
    return forecast


def funding_rows(receipt,*,now_ms):
    match=re.fullmatch(r'/fapi/v1/fundingRate\?symbol=BTCUSDC&endTime=(\d+)&limit=9',receipt.get('path',''))
    require(match is not None,'PORTFOLIO_FUNDING_PATH')
    end=int(match[1])
    require(now_ms-30000<=end<now_ms and now_ms-30000<=receipt['requested_at_ms'],
        'PORTFOLIO_FUNDING_STALE')
    rows=_capture(receipt,receipt['path'],now_ms)
    require(type(rows) is list and 3<=len(rows)<=9,'PORTFOLIO_FUNDING_COUNT')
    result=[];previous=None
    for row in rows:
        at=row['fundingTime'];rate=Decimal(row['fundingRate']);mark=Decimal(row['markPrice'])
        require(row['symbol']=='BTCUSDC' and type(at) is int and 0<at<=end and rate.is_finite() and
            mark.is_finite() and mark>0 and (previous is None or 0<at-previous<=8*HOUR+300000),
            'PORTFOLIO_FUNDING_ROWS')
        result.append({'time':at,'available_at_ms':receipt['received_at_ms'],'rate':row['fundingRate']})
        previous=at
    require(now_ms-previous<=8*HOUR+300000,'PORTFOLIO_FUNDING_COVERAGE')
    return result


def build_from_sources(*,allocation,binding,root,funding_receipt,**request_inputs):
    now=request_inputs['now_ms']
    forecast=load_forecast(root=root,binding=binding,allocation=allocation,now_ms=now)
    funding=funding_rows(funding_receipt,now_ms=now)
    result=demo_portfolio_task.build(allocation=allocation,forecast=forecast,funding=funding,**request_inputs)
    return {**result,'source_inputs':{'binding_sha256':_digest(binding),'forecast_sha256':forecast['result_hash'],
        'funding_receipt_sha256':digest(funding_receipt),'source_authenticated':False,
        'economic_authority':False}}
