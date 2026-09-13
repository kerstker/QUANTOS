"""Observed Demo liquidity and one-hour realized volatility, never a forecast."""
from decimal import Decimal,ROUND_CEILING
import hashlib
import json

from quantos.contracts.binance_demo_clock import bracket_bounds
from services.execution.perpetual_ledger_v2 import ledger_decimal_context,canonical_decimal
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research import demo_display_store,demo_observation_store
from services.research.demo_execution_origin import fields,sha
from services.research.demo_oms_status import _time

SCHEMA='DEMO_MARKET_INPUTS_V1'
KINDS=('clock_before','ticker','klines','clock_after')
VOLATILITY_POLICY='SQRT_SUM_LOG_RETURNS_SQUARED_60X1M_CEIL18_V1'
MAX_AGE_MS=30000
BODY_LIMITS={'clock_before':1024,'ticker':4096,'klines':32768,'clock_after':1024}
REASONS=('REQUEST_FAILED','RATE_LIMITED','RESPONSE_INVALID','INPUTS_INVALID','CAPTURE_BUDGET_EXHAUSTED')


def require(ok,reason):
    if not ok:raise ValueError(reason)


def number(value,*,positive=False):
    from quantos.contracts.binance_demo_account import _decimal
    result=Decimal(_decimal(value));require(result>0 if positive else result>=0,'MARKET_NONNEGATIVE_VALUE')
    return result


def data(read):return load_json_without_duplicate_keys(read['body'].encode())


def validate(raw):
    fields(raw,'schema environment symbol status source_sha256 observed_at_ms completed_at_ms reads failure source_verified submission_enabled economic_authority')
    require(raw['schema']==SCHEMA and raw['environment']=='BINANCE_USDM_DEMO' and raw['symbol'] in ('BTCUSDC','BTCUSDT'), 'MARKET_INPUT_SCOPE')
    require(raw['status'] in ('CAPTURED','FAILED') and all(raw[k] is False for k in ('source_verified','submission_enabled','economic_authority')),'MARKET_INPUT_STATUS')
    sha(raw['source_sha256']);_time(raw['observed_at_ms']);_time(raw['completed_at_ms'])
    duration=raw['completed_at_ms']-raw['observed_at_ms']
    require(duration>=0 and (raw['status']=='FAILED' or duration<=40000),'MARKET_CAPTURE_WINDOW')
    reads=raw['reads'];require(type(reads) is list and len(reads)<=4,'MARKET_READ_COUNT')
    previous=raw['observed_at_ms']
    for index,read in enumerate(reads):
        fields(read,'kind requested_at_ms received_at_ms response_sha256 body')
        require(read['kind']==KINDS[index],'MARKET_READ_ORDER')
        for name in ('requested_at_ms','received_at_ms'):_time(read[name])
        require(previous<=read['requested_at_ms']<=read['received_at_ms']<=raw['completed_at_ms'],'MARKET_READ_CLOCK')
        previous=read['received_at_ms'];sha(read['response_sha256'])
        require(type(read['body']) is str and 0<len(read['body'].encode())<=BODY_LIMITS[read['kind']] and
            hashlib.sha256(read['body'].encode()).hexdigest()==read['response_sha256'],'MARKET_RESPONSE_DIGEST')
    if raw['status']=='CAPTURED':
        require(len(reads)==4 and raw['failure'] is None,'MARKET_CAPTURE_INCOMPLETE');metrics(raw)
    else:
        fields(raw['failure'],'kind reason');require(raw['failure']['kind'] in (*KINDS,'validation','worker') and
            raw['failure']['reason'] in REASONS,'MARKET_FAILURE_FIELDS')
    return raw


@ledger_decimal_context()
def metrics(raw):
    reads=raw['reads'];parsed={r['kind']:data(r) for r in reads}
    for kind in ('clock_before','clock_after'):fields(parsed[kind],'serverTime');_time(parsed[kind]['serverTime'])
    before,after=reads[0],reads[-1]
    def probe(read):
        return {k:read[k] for k in ('requested_at_ms','received_at_ms','response_sha256')} | {
            'server_time_ms':parsed[read['kind']]['serverTime'],'source_sha256':raw['source_sha256']}
    bounds=bracket_bounds({'schema':'BINANCE_USDM_CLOCK_BRACKET_V1','provenance':'IMPORTED_UNVERIFIED',
        'before':probe(before),'after':probe(after)},start_ms=reads[1]['requested_at_ms'],end_ms=reads[2]['received_at_ms'])
    first_clock=parsed['clock_before']['serverTime'];last_clock=parsed['clock_after']['serverTime']
    require(last_clock>=first_clock,'MARKET_SERVER_CLOCK_REVERSED')
    ticker=parsed['ticker'];require(type(ticker) is dict and ticker.get('symbol')==raw['symbol'],'MARKET_TICKER_SYMBOL')
    opened,closed=ticker['openTime'],ticker['closeTime'];_time(opened);_time(closed)
    duration=closed-opened
    # The venue also returns a minute-aligned start with an intra-minute
    # close. Retain that observed rolling window; never scale a shorter day.
    whole_day=abs(duration-86400000)<=1000 or opened%60000==0 and 86400000<=duration<86460000
    require(whole_day and first_clock-30000<=closed<=last_clock+500,'MARKET_TICKER_WINDOW')
    volume=number(ticker['volume'],positive=True)
    bars=parsed['klines'];require(type(bars) is list and len(bars)==61,'MARKET_BAR_COUNT')
    end=first_clock//60000*60000-1;closes=[]
    for index,row in enumerate(bars):
        require(type(row) is list and len(row)==12,'MARKET_BAR_FIELDS')
        start=end+1-(61-index)*60000
        require(type(row[0]) is int and type(row[6]) is int and row[0]==start and row[6]==start+59999,'MARKET_CLOSED_CONTIGUOUS_BARS')
        o,h,l,c=(number(row[i],positive=True) for i in (1,2,3,4))
        require(l<=min(o,c)<=max(o,c)<=h,'MARKET_OHLC_RELATION')
        for i in (5,7,9,10):number(row[i])
        require(type(row[8]) is int and row[8]>=0 and number(row[9])<=number(row[5]) and number(row[10])<=number(row[7]),'MARKET_BAR_VOLUME')
        closes.append(c)
    rv=sum(((closes[i]/closes[i-1]).ln()**2 for i in range(1,61)),Decimal(0)).sqrt().quantize(Decimal('1e-18'),rounding=ROUND_CEILING)
    return {'daily_volume_base':canonical_decimal(volume),'instrument_vol':canonical_decimal(rv),
        'volatility_policy':VOLATILITY_POLICY,'closed_bars':61,'return_intervals':60,'last_close_ms':end,
        'ticker_close_ms':closed,'available_at_ms':raw['completed_at_ms'],'clock_bounds':bounds}


def assess(raw,*,as_of_ms):
    validate(raw);_time(as_of_ms)
    status='FUTURE_INPUT' if raw['completed_at_ms']>as_of_ms else 'STALE' if as_of_ms-raw['completed_at_ms']>MAX_AGE_MS else 'INPUTS_UNRESOLVED' if raw['status']=='FAILED' else 'OBSERVED'
    result={'schema':SCHEMA,'symbol':raw['symbol'],'status':status,'observed_at_ms':raw['observed_at_ms'],
        'available_at_ms':raw['completed_at_ms'],'daily_volume_base':None,'instrument_vol':None,
        'source_verified':False,'submission_enabled':False,'economic_authority':False}
    if status=='OBSERVED':result.update(metrics(raw))
    return result


def save(directory,raw,*,archive=False):
    require(type(archive) is bool,'MARKET_STORAGE_MODE')
    return (demo_observation_store if archive else demo_display_store).save(directory,raw,validator=validate)


def load_latest(directory):return demo_observation_store.load_latest(directory,validator=validate)


def display(raw):
    """Preserve historical measurements without making them current risk inputs."""
    validate(raw);observed=None
    if raw['status']=='CAPTURED':
        values=metrics(raw)
        observed={key:values[key] for key in ('daily_volume_base','instrument_vol','volatility_policy',
            'last_close_ms','ticker_close_ms','return_intervals')}
        observed['bars']=[dict(close_ms=row[6],open=row[1],high=row[2],low=row[3],close=row[4],volume_base=row[5])
            for row in data(raw['reads'][2])]
    return {'schema':'DEMO_MARKET_INPUTS_VIEW_V1','environment':raw['environment'],'symbol':raw['symbol'],
        'capture_status':raw['status'],'observed_at_ms':raw['observed_at_ms'],'completed_at_ms':raw['completed_at_ms'],
        'source_sha256':raw['source_sha256'],
        'input_sha256':hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
        'failure':raw['failure'],'metrics':observed,'source_verified':False,'submission_enabled':False,'economic_authority':False}
