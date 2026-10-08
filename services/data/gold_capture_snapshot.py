"""Revalidate saved public aggregates for display; never infer a live connection."""
import hashlib
import json

from services.data.gold_execution_tape import GoldTradeCursor

URL='wss://fstream.binance.com/market/stream?streams=xauusdt@aggTrade'
MAX_AGE_MS=30000


def _time(t):return type(t) is int and 0<=t<=2**63-1


def _decode(raw):
    def pairs(items):
        result=dict(items)
        if len(result)!=len(items):raise ValueError('DUPLICATE_FRAME_KEY')
        return result
    def constant(_):raise ValueError('NONFINITE_FRAME_CONSTANT')
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    except RecursionError:raise ValueError('EXCESSIVE_FRAME_NESTING') from None


def summarize_capture(d, *, as_of_ms):
    fields={'schema','source_url','symbol','started_at_ms','ended_at_ms','status','stop_reason',
        'unique_aggregates','raw_frames','packets','raw_frames_sha256','history_complete','authoritative','limitations'}
    if not (isinstance(d,dict) and set(d)==fields and d['schema']=='GOLD_PUBLIC_CAPTURE_V1'
            and d['source_url']==URL and d['symbol']=='XAUUSDT'
            and d['history_complete'] is False and d['authoritative'] is False
            and all(_time(t) for t in (d['started_at_ms'],d['ended_at_ms'],as_of_ms))
            and d['started_at_ms']<=d['ended_at_ms']<=as_of_ms
            and isinstance(d['raw_frames'],list) and isinstance(d['packets'],list)
            and len(d['raw_frames'])==len(d['packets'])<=20000
            and type(d['unique_aggregates']) is int):
        raise ValueError('INVALID_CAPTURE_ENVELOPE')
    cursor=GoldTradeCursor('XAUUSDT');unique=0;last=None;previous=d['started_at_ms'];size=0
    for raw,packet in zip(d['raw_frames'],d['packets']):
        if not isinstance(raw,str) or len(raw)>131072:raise ValueError('INVALID_RAW_FRAME')
        size+=len(raw.encode())
        if size>3000000:raise ValueError('CAPTURE_TOO_LARGE')
        frame=_decode(raw)
        if not (isinstance(frame,dict) and set(frame)=={'stream','data'}
                and frame['stream']=='xauusdt@aggTrade' and isinstance(packet,dict)
                and set(packet)=={'received_at_ms','payload'}
                and frame['data']==packet['payload'] and _time(packet['received_at_ms'])
                and previous<=packet['received_at_ms']<=d['ended_at_ms']):
            raise ValueError('CAPTURE_FRAME_MISMATCH')
        previous=packet['received_at_ms']
        trade=cursor.apply(frame['data'],received_at_ms=previous)
        if trade is not None:unique+=1;last=trade
    digest=hashlib.sha256(json.dumps(d['raw_frames'],ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
    if digest!=d['raw_frames_sha256'] or unique!=d['unique_aggregates']:
        raise ValueError('CAPTURE_DIGEST_OR_COUNT_MISMATCH')
    expected=('CAPTURED_PUBLIC_PREFIX' if unique else 'NO_DATA') if d['stop_reason']=='DURATION_REACHED' else 'INTERRUPTED'
    if d['stop_reason'] not in ('DURATION_REACHED','STREAM_INTERRUPTED') or d['status']!=expected:
        raise ValueError('UNUSABLE_CAPTURE_STATUS')
    age=as_of_ms-last.transaction_time_ms if last else None
    status='INTERRUPTED' if expected=='INTERRUPTED' else 'NO_DATA' if last is None else 'STALE' if age>MAX_AGE_MS else 'OBSERVED_RECENTLY'
    return {'status':status,'unique_aggregates':unique,'last_trade_price':str(last.price) if last else None,
        'last_event_ms':last.transaction_time_ms if last else None,
        'last_received_ms':last.received_at_ms if last else None,'age_ms':age,
        'max_age_ms':MAX_AGE_MS,'capture_ended_ms':d['ended_at_ms'],'raw_frames_sha256':digest,
        'stream_connected':False,'source_authenticated':False,'authoritative':False}
