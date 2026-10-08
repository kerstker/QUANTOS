"""Point-in-time public XAUUSDT filter evidence, not an execution profile.

Exchange information does not establish account fee tiers, leverage brackets,
cash-flow multiplier, liquidation policy, or historical rules before receipt.
"""
from decimal import Decimal, localcontext
import hashlib
import json
import re


class ProfileError(ValueError):
    """Fixed public-input rejection code."""


def _require(condition, reason):
    if not condition:
        raise ProfileError(reason)


def _number(value):
    _require(isinstance(value,str) and re.fullmatch(
        r'(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,18})?',value) is not None,
        'INVALID_FILTER_NUMBER')
    result=Decimal(value)
    _require(result > 0,'NONPOSITIVE_FILTER')
    return result


def _text(value):
    result=format(value,'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def _range(row, low, high, step):
    a,b,c=(_number(row[k]) for k in (low,high,step))
    _require(a<=b and c<=b,'INVALID_FILTER_RANGE')
    with localcontext() as context:
        context.prec=80
        _require(a % c == 0 and b % c == 0,'FILTER_BOUND_OFF_GRID')
    return {'min':_text(a),'max':_text(b),'step':_text(c)}


def parse_profile(response, *, requested_at_ms, received_at_ms, as_of_ms, max_age_ms):
    for value in (requested_at_ms,received_at_ms,as_of_ms):
        _require(type(value) is int and 0<=value<=2**63-1,'INVALID_PROFILE_TIME')
    _require(type(max_age_ms) is int and max_age_ms>0,'INVALID_PROFILE_AGE')
    _require(requested_at_ms<=received_at_ms<=as_of_ms,'PROFILE_NOT_AVAILABLE')
    # Include request latency instead of making a very late response look fresh.
    _require(as_of_ms-requested_at_ms<=max_age_ms,'STALE_PROFILE')
    try:
        _require(isinstance(response,dict) and isinstance(response.get('symbols'),list),
                 'INVALID_EXCHANGE_RESPONSE')
        _require(len(response['symbols'])<=10000,'EXCESSIVE_SYMBOLS')
        _require(all(isinstance(row,dict) for row in response['symbols']), 'INVALID_SYMBOL_ROW')
        rows=[row for row in response['symbols'] if row.get('symbol')=='XAUUSDT']
        _require(len(rows)==1,'GOLD_SYMBOL_NOT_UNIQUE')
        row=rows[0]
        expected={'symbol':'XAUUSDT','pair':'XAUUSDT','contractType':'TRADIFI_PERPETUAL',
            'status':'TRADING','baseAsset':'XAU','quoteAsset':'USDT','marginAsset':'USDT'}
        _require(all(row.get(k)==v for k,v in expected.items()),'WRONG_GOLD_CONTRACT')
        _require(isinstance(row.get('filters'),list) and len(row['filters'])<=100,
                 'INVALID_FILTERS')
        filters={}
        for f in row['filters']:
            _require(isinstance(f,dict),'INVALID_FILTER')
            name=f.get('filterType')
            _require(isinstance(name,str) and re.fullmatch('[A-Z_]{1,64}',name) is not None,
                     'INVALID_FILTER_NAME')
            _require(name not in filters,'DUPLICATE_FILTER')
            filters[name]=f
        required={'PRICE_FILTER','LOT_SIZE','MARKET_LOT_SIZE','MIN_NOTIONAL'}
        _require(required<=set(filters),'MISSING_FILTER')
        price=_range(filters['PRICE_FILTER'],'minPrice','maxPrice','tickSize')
        price['tick']=price.pop('step')
        limit=_range(filters['LOT_SIZE'],'minQty','maxQty','stepSize')
        market=_range(filters['MARKET_LOT_SIZE'],'minQty','maxQty','stepSize')
        minimum=_text(_number(filters['MIN_NOTIONAL']['notional']))
        encoded=json.dumps(response,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        _require(len(encoded)<=8_000_000,'PROFILE_TOO_LARGE')
    except (KeyError,TypeError,ValueError,RecursionError) as exc:
        if isinstance(exc,ProfileError):
            raise
        raise ProfileError('INVALID_EXCHANGE_RESPONSE') from None
    return {'schema':'GOLD_PUBLIC_FILTER_PROFILE_V1',**expected,
        'requested_at_ms':requested_at_ms,'available_at_ms':received_at_ms,
        'response_sha256':hashlib.sha256(encoded).hexdigest(),
        'price':price,'limit_quantity':limit,'market_quantity':market,
        'min_notional':minimum,'unmodeled_filters':sorted(set(filters)-required),
        'contract_multiplier':None,'execution_eligible':False,'authoritative':False,
        'limitations':['PUBLIC_FILTERS_ONLY','NO_ACCOUNT_FEES_OR_RISK_BRACKETS',
            'NO_VERIFIED_CONTRACT_MULTIPLIER','NOT_HISTORICAL_BEFORE_RECEIPT']}
