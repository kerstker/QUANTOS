"""PIT public reference-session and funding terms, not execution authority.

Binance's COMMODITY schedule is not a COMEX calendar or a venue outage flag.
Current funding terms cannot reconstruct historical settled funding cash flows.
"""
from decimal import Decimal
import hashlib
import json
import re


def _time(value):
    return type(value) is int and 0 <= value <= 2**63-1


def _receipt(response, requested_at_ms, received_at_ms, as_of_ms, max_age_ms):
    if not (all(_time(v) for v in (requested_at_ms,received_at_ms,as_of_ms))
            and type(max_age_ms) is int and max_age_ms>0
            and requested_at_ms<=received_at_ms<=as_of_ms
            and as_of_ms-requested_at_ms<=max_age_ms):
        raise ValueError('UNAVAILABLE_OR_STALE_REFERENCE_RULES')
    try:
        raw=json.dumps(response,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        if len(raw)>2000000:raise ValueError('size')
    except (ValueError,TypeError,RecursionError):
        raise ValueError('INVALID_REFERENCE_RESPONSE') from None
    return {'symbol':'XAUUSDT','requested_at_ms':requested_at_ms,
        'available_at_ms':received_at_ms,'as_of_ms':as_of_ms,
        'response_sha256':hashlib.sha256(raw).hexdigest(),'authoritative':False,
        'execution_eligible':False}


def session_at(response, *, requested_at_ms, received_at_ms, as_of_ms, max_age_ms):
    result=_receipt(response,requested_at_ms,received_at_ms,as_of_ms,max_age_ms)
    try:
        updated=response['updateTime']
        sessions=response['marketSchedules']['COMMODITY']['sessions']
        if not (_time(updated) and updated<=received_at_ms and isinstance(sessions,list)
                and 0<len(sessions)<=1000):raise ValueError('shape')
        previous=None; selected=None
        for session in sessions:
            if not isinstance(session,dict) or set(session)!={'startTime','endTime','type'}:
                raise ValueError('session shape')
            start,end,kind=session['startTime'],session['endTime'],session['type']
            if not (_time(start) and _time(end) and start<end
                    and kind in ('REGULAR','NO_TRADING')
                    and (previous is None or start==previous)):
                raise ValueError('invalid or discontinuous session')
            previous=end
            if start<=as_of_ms<end:selected=dict(session)
        if selected is None:raise ValueError('uncovered time')
    except (KeyError,TypeError,ValueError):
        raise ValueError('INVALID_OR_UNCOVERED_COMMODITY_SCHEDULE') from None
    return {**result,'schema':'GOLD_REFERENCE_SESSION_V1','source_update_ms':updated,
        'reference_session':selected['type'],'session_start_ms':selected['startTime'],
        'session_end_ms':selected['endTime'],'binance_order_submission_available':None,
        'comex_session_open':None,'index_calculation_mode':None,
        'limitations':['BINANCE_REFERENCE_CALENDAR_ONLY','NOT_COMEX_SESSION_EVIDENCE',
            'NO_TRADING_IS_NOT_BINANCE_ORDER_HALT','MODE_REQUIRES_EFFECTIVE_NOTICE',
            'NOT_AVAILABLE_BEFORE_RECEIPT']}


def funding_info(response, *, requested_at_ms, received_at_ms, as_of_ms, max_age_ms):
    result=_receipt(response,requested_at_ms,received_at_ms,as_of_ms,max_age_ms)
    try:
        if not isinstance(response,list) or len(response)>10000:raise ValueError('shape')
        if not all(isinstance(row,dict) for row in response):raise ValueError('rows')
        rows=[r for r in response if r.get('symbol')=='XAUUSDT']
        if len(rows)!=1:raise ValueError('missing or duplicate symbol')
        row=rows[0]; interval=row['fundingIntervalHours'];updated=row.get('updateTime')
        if not (type(interval) is int and 0<interval<=24):raise ValueError('interval')
        if updated is not None and not (_time(updated) and updated<=received_at_ms):
            raise ValueError('update time')
        cap,floor=(row[k] for k in ('adjustedFundingRateCap','adjustedFundingRateFloor'))
        if not all(isinstance(v,str) and re.fullmatch(r'-?(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,18})?',v)
                   for v in (cap,floor)):raise ValueError('rates')
        cap,floor=Decimal(cap),Decimal(floor)
        if not -1<=floor<=0<=cap<=1 or cap==floor:raise ValueError('rate bounds')
    except (KeyError,TypeError,ValueError):
        raise ValueError('INVALID_OR_MISSING_GOLD_FUNDING_TERMS') from None
    def text(value):
        if value==0:return '0'
        s=format(value,'f')
        return s.rstrip('0').rstrip('.') if '.' in s else s
    return {**result,'schema':'GOLD_PUBLIC_FUNDING_TERMS_V1','source_update_ms':updated,
        'interval_hours':interval,'cap':text(cap),'floor':text(floor),
        'effective_from_ms':None,'realized_funding_rate':None,
        'limitations':['CURRENT_TERMS_NOT_HISTORICAL_EPOCH','NO_SETTLED_CASH_FLOW',
            'MISSING_SYMBOL_MUST_NOT_USE_DEFAULT','NOT_AVAILABLE_BEFORE_RECEIPT']}
