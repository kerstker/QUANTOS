"""PIT gold quote alignment only: no causality claim or trading permission.

Input adapters must establish venue identity, contract month, unit conversion,
session calendars and source provenance. Normalized values are not authenticated
by this pure function. GC futures basis is not assumed to be mispricing.
"""
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
import hashlib
import json
import re


@dataclass(frozen=True)
class Quote:
    venue: str
    instrument: str
    unit: str
    bid: Decimal
    ask: Decimal
    event_ms: int
    received_ms: int
    source_hash: str


def _time(value):
    return type(value) is int and 0<=value<=2**63-1


def _text(value):
    if value==0:
        return '0'
    result=format(value,'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def _latest(rows, *, venue, instrument, unit, decision_ms, max_age_ms):
    if not isinstance(rows,list) or len(rows)>20000:
        raise ValueError('INVALID_SOURCE_ROWS')
    available=[]
    for q in rows:
        if not isinstance(q,Quote) or not _time(q.received_ms):
            raise ValueError('INVALID_RECEIVE_TIME')
        if q.received_ms<=decision_ms:
            available.append(q)
    if not available:
        return None
    latest=max(q.received_ms for q in available)
    candidates=[q for q in available if q.received_ms==latest]
    q=candidates[0]
    if any(other!=q for other in candidates):
        raise ValueError('AMBIGUOUS_LATEST_QUOTE')
    if not (q.venue==venue and q.instrument==instrument and q.unit==unit
            and _time(q.event_ms) and q.event_ms<=q.received_ms
            and decision_ms-q.event_ms<=max_age_ms
            and all(type(x) is Decimal and x.is_finite() and x>0
                    and len(x.as_tuple().digits)<=64 and abs(x.as_tuple().exponent)<=64
                    for x in (q.bid,q.ask))
            and q.bid<=q.ask and isinstance(q.source_hash,str)
            and re.fullmatch('[0-9a-f]{64}',q.source_hash) is not None):
        raise ValueError('INVALID_QUOTE_IDENTITY_PRICE_OR_AGE')
    return q


def compare_at(local, external, fx, *, external_contract, decision_ms,
               max_age_ms, max_skew_ms, external_session_open):
    result={'schema':'GOLD_CROSS_MARKET_QUOTES_V1','status':'INPUT_INVALID',
        'decision_ms':decision_ms,'trade_intent':None,'causal_driver':None,
        'authoritative':False,'source_hashes':[],
        'limitations':['NORMALIZED_INPUT_NOT_AUTHENTICATED','GC_BASIS_NOT_ARBITRAGE_PROOF',
            'NO_FEES_SLIPPAGE_OR_EXECUTION','NO_ORDER_FLOW_CONFIRMATION']}
    if not (_time(decision_ms) and type(max_age_ms) is int and max_age_ms>0
            and type(max_skew_ms) is int and max_skew_ms>=0
            and isinstance(external_contract,str)
            and re.fullmatch(r'GC[FGHJKMNQUVXZ]20[0-9]{2}',external_contract)
            and type(external_session_open) is bool):
        result['reason']='INVALID_COMPARISON_CONFIG'
        return result
    if not external_session_open:
        result.update(status='EXTERNAL_SESSION_CLOSED',reason='NO_EXTERNAL_CONFIRMATION')
        return result
    try:
        common={'decision_ms':decision_ms,'max_age_ms':max_age_ms}
        a=_latest(local,venue='BINANCE',instrument='XAUUSDT',unit='USDT_PER_TROY_OUNCE',**common)
        b=_latest(external,venue='COMEX',instrument=external_contract,unit='USD_PER_TROY_OUNCE',**common)
        rate=_latest(fx,venue='FX_REFERENCE',instrument='USDTUSD',unit='USD_PER_USDT',**common)
        if any(q is None for q in (a,b,rate)):
            result.update(status='INPUT_MISSING',reason='LOCAL_EXTERNAL_OR_FX_MISSING')
            return result
        if max(q.event_ms for q in (a,b,rate))-min(q.event_ms for q in (a,b,rate))>max_skew_ms:
            raise ValueError('CROSS_SOURCE_TIME_SKEW')
        with localcontext(Context(prec=150)):
            external_bid=b.bid/rate.ask
            external_ask=b.ask/rate.bid
            local_mid=(a.bid+a.ask)/2
            external_mid=(external_bid+external_ask)/2
            basis=(external_mid/local_mid-1)*10000
        result.update(status='ALIGNED_QUOTES_ONLY',external_contract=external_contract,
            source_hashes=[a.source_hash,b.source_hash,rate.source_hash],
            source_clocks=[{'event_ms':q.event_ms,'received_ms':q.received_ms} for q in (a,b,rate)],
            local_mid_usdt=_text(local_mid),external_bid_usdt=_text(external_bid),
            external_ask_usdt=_text(external_ask),basis_mid_bps=_text(basis),
            numeric_policy='DECIMAL_150_HALF_EVEN',
            max_age_ms=max_age_ms,max_skew_ms=max_skew_ms)
        result['comparison_id']=hashlib.sha256(json.dumps(result,sort_keys=True,
            separators=(',',':')).encode()).hexdigest()
    except ValueError as exc:
        result['reason']=str(exc)
    return result
