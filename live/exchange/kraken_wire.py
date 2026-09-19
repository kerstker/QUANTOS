"""Offline exact Kraken order-validation payload. No signing or network capability.

Venue quantity semantics and economic authority are NOT established by encoding.
Always validate-only: a separate reviewed issuer is required for real submission.
"""
from datetime import datetime, timezone
from decimal import Context, localcontext
import hashlib
import re

from services.data.kraken_public import decimal, require


def _form_component(value):
    # Pure UTF-8 form encoding: importing a networking package is unnecessary
    # for this offline byte protocol. Space uses '+', other bytes use '%HH'.
    return ''.join(chr(b) if 65<=b<=90 or 97<=b<=122 or 48<=b<=57 or b in (45,46,95,126)
                   else '+' if b==32 else f'%{b:02X}' for b in value.encode('utf-8'))


def prepare(order, *, spec, now_ms, nonce):
    require(isinstance(order,dict) and set(order)=={'instrument_id','side','volume','limit_price',
            'client_order_id','deadline_ms'},'KRAKEN_WIRE_FIELDS')
    require(type(now_ms) is int and 0<now_ms<=253402300739999 and type(nonce) is int and 0<nonce<2**63,'KRAKEN_WIRE_CLOCK')
    require(type(order['deadline_ms']) is int and 2000<=order['deadline_ms']-now_ms<=60000,'KRAKEN_WIRE_DEADLINE')
    require(order['instrument_id']==spec['instrument_id'] and spec['asset_class'] in ('currency','tokenized_asset') and
            spec['status']=='online','KRAKEN_WIRE_INSTRUMENT')
    require(isinstance(spec['altname'],str) and re.fullmatch(r'[A-Za-z0-9.]{1,48}',spec['altname']),'KRAKEN_WIRE_PAIR')
    require(order['side'] in ('buy','sell'),'KRAKEN_WIRE_SIDE')
    require(isinstance(order['client_order_id'],str) and re.fullmatch('[a-f0-9]{32}',order['client_order_id']),
            'KRAKEN_WIRE_CLIENT_ID')
    quantity=decimal(order['volume']);price=decimal(order['limit_price'])
    require(type(spec['lot_decimals']) is int and 0<=spec['lot_decimals']<=18 and
            type(spec['pair_decimals']) is int and 0<=spec['pair_decimals']<=18 and
            type(spec['lot_multiplier']) is int and spec['lot_multiplier']==1,'KRAKEN_WIRE_PRECISION')
    with localcontext(Context(prec=100)):
        require(quantity>=decimal(spec['ordermin']) and quantity*price>=decimal(spec['costmin']),
                'KRAKEN_WIRE_MINIMUM')
        require(quantity.scaleb(spec['lot_decimals'])==quantity.scaleb(spec['lot_decimals']).to_integral_value() and
                price.scaleb(spec['pair_decimals'])==price.scaleb(spec['pair_decimals']).to_integral_value(),
                'KRAKEN_WIRE_PRECISION')
        require(price%decimal(spec['tick_size'])==0,'KRAKEN_WIRE_TICK')
    deadline=datetime.fromtimestamp(order['deadline_ms']//1000,tz=timezone.utc)
    deadline_text=deadline.strftime('%Y-%m-%dT%H:%M:%S')+f".{order['deadline_ms']%1000:03d}Z"
    params={'nonce':str(nonce),'ordertype':'limit','type':order['side'],'volume':order['volume'],
            'pair':spec['altname'],'price':order['limit_price'],'cl_ord_id':order['client_order_id'],
            'timeinforce':'IOC','oflags':'fciq','deadline':deadline_text,'validate':'true'}
    if spec['asset_class']=='tokenized_asset':params['asset_class']='tokenized_asset'
    body='&'.join(_form_component(k)+'='+_form_component(v) for k,v in params.items()).encode('ascii')
    return {'body':body,'body_sha256':hashlib.sha256(body).hexdigest(),'execution_eligible':False,
            'quantity_semantics_verified':False,'scope':'OFFLINE_VENUE_VALIDATION_PAYLOAD_ONLY'}
