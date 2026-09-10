"""Offline USD-M order observations, never authenticated account evidence.

The supported subset is fixed ordinary LIMIT/GTX orders in one-way mode.
Native messages remain in the immutable input; this projection does not place
orders, query a private endpoint, or apply real fills to simulated admission.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context

SCHEMA = 'BINANCE_USDM_ORDER_RECEIPTS_V1'
CALIBRATION_SCHEMA = 'PASSIVE_BINANCE_ORDER_RECEIPTS_V1'
REQUIRED_ORDER = {'s','c','S','o','f','q','p','x','X','i','l','z','L','T','t','m','R','ps'}
OPTIONAL_ORDER = {'ap','sp','N','n','b','a','wt','ot','cp','AP','cr','pP','si','ss','rp','V','pm','gtd'}
STATES = {('NEW','NEW'):'ACK', ('TRADE','PARTIALLY_FILLED'):'PARTIALLY_FILLED',
    ('TRADE','FILLED'):'FILLED', ('CANCELED','CANCELED'):'CANCELLED',
    ('EXPIRED','EXPIRED'):'EXPIRED', ('EXPIRED','EXPIRED_IN_MATCH'):'EXPIRED'}
ZERO = Decimal('0')


def _fields(value, required, optional=frozenset()):
    if not isinstance(value,dict) or not required <= set(value) <= required | optional:
        raise ValueError('incomplete or unsupported native order receipt fields')


def _integer(value, maximum=8640000000000000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('exact bounded native integer required')
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
        ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _sha(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):
        raise ValueError('native source digest required')
    return value


def _binding_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',value):
        raise ValueError('native binding order ID must be an exact decimal string')
    return _integer(int(value),2**63-1)


def _amount(value, *, signed=False):
    pattern = r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?' if signed else r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?'
    if not isinstance(value,str) or len(value)>64 or not re.fullmatch(pattern,value):
        raise ValueError('exact native decimal string required')
    result=Decimal(value)
    if abs(result)>Decimal('1000000000000000') or result.as_tuple().exponent < -24:
        raise ValueError('native amount outside supported bounds')
    return result


def _optional_order_fields(order):
    for key in ('ap','sp','b','a','AP','cr','rp'):
        if key in order:_amount(order[key],signed=key=='rp')
    for key in ('si','ss','gtd'):
        if key in order:_integer(order[key])
    if ('pP' in order and type(order['pP']) is not bool
            or order.get('wt','CONTRACT_PRICE') not in ('CONTRACT_PRICE','MARK_PRICE')
            or order.get('V','NONE') not in ('NONE','EXPIRE_TAKER','EXPIRE_MAKER','EXPIRE_BOTH')
            or order.get('gtd',0)!=0):
        raise ValueError('unsupported ordinary order options')


@ledger_decimal_context()
def normalize_order_receipts(document, actions, symbol, action_hash, *, reduce_only_ids=frozenset()):
    """Return a normalized quantity trace and separate unverified fee facts."""
    try:
        return _normalize(document,actions,symbol,action_hash,reduce_only_ids)
    except (KeyError,TypeError,ArithmeticError,UnicodeError,RecursionError) as exc:
        raise ValueError('malformed native order receipt input') from exc


def _normalize(document, actions, symbol, action_hash, reduce_only_ids):
    _fields(document,{'schema','symbol','account_ref_sha256','action_hash','provenance','coverage','bindings','messages'})
    if (document['schema']!=SCHEMA or document['symbol']!=symbol or symbol not in ('BTCUSDC','BTCUSDT')
            or document['action_hash']!=action_hash or document['provenance'] not in ('SYNTHETIC_TEST_FIXTURE','IMPORTED_UNVERIFIED')):
        raise ValueError('same frozen actions and explicit unverified native source required')
    _sha(action_hash);_sha(document['account_ref_sha256'])
    coverage=document['coverage'];_fields(coverage,{'start_ms','end_ms','gaps'})
    start,end=_integer(coverage['start_ms']),_integer(coverage['end_ms'])
    if not start<end or end-start>150000:raise ValueError('bounded native receipt window required')
    fixed={a.order_id:a for a in actions if a.kind=='ORDER'}
    bindings=document['bindings'];messages=document['messages']
    if not isinstance(bindings,list) or len(bindings)>200 or not isinstance(messages,list) or len(messages)>10000:
        raise ValueError('bounded native bindings and messages required')
    by_venue={};by_client={};rows={}
    for binding in bindings:
        _fields(binding,{'order_id','venue_order_id','client_order_id'})
        name=binding['order_id'];venue=_binding_id(binding['venue_order_id']);client=binding['client_order_id']
        if (name not in fixed or name in rows or venue in by_venue or not isinstance(client,str)
                or not re.fullmatch(r'[.A-Z\:/a-z0-9_-]{1,36}',client) or client in by_client
                or client.startswith(('autoclose-','adl_autoclose','settlement_autoclose-'))):
            raise ValueError('unique ordinary fixed-order binding required')
        action=fixed[name];by_venue[venue]=binding;by_client[client]=binding
        rows[name]={'order_id':name,'side':action.side,'quantity':canonical_decimal(action.quantity),
            'limit_price':canonical_decimal(action.price),'events':[]}
    filled={name:ZERO for name in rows};seen_raw=set();seen_trade={};duplicates=[];trade_rows=[];message_rows=[]
    last_receive=start;last_event=0;last_transaction=0;total_bytes=0
    fees={};missing=0;all_quote=True
    for index,record in enumerate(messages):
        _fields(record,{'received_at_ms','raw_utf8','response_sha256'})
        received=_integer(record['received_at_ms']);raw=record['raw_utf8']
        if not isinstance(raw,str):raise ValueError('native raw UTF-8 message required')
        encoded=raw.encode();total_bytes+=len(encoded)
        if len(encoded)>16384 or total_bytes>3000000 or not last_receive<=received<=end:
            raise ValueError('bounded monotonic native receipt time and payload required')
        last_receive=received;raw_hash=hashlib.sha256(encoded).hexdigest()
        if raw_hash!=record['response_sha256']:raise ValueError('native raw response digest mismatch')
        payload=load_json_without_duplicate_keys(raw);_fields(payload,{'e','E','T','o'})
        if payload['e']!='ORDER_TRADE_UPDATE':raise ValueError('unsupported native event type')
        event_ms,transaction_ms=_integer(payload['E']),_integer(payload['T'])
        order=payload['o'];_fields(order,REQUIRED_ORDER,OPTIONAL_ORDER)
        _optional_order_fields(order)
        trade_ms=_integer(order['T']);venue=_integer(order['i'],2**63-1)
        trade_id=_integer(order['t'],2**63-1);binding=by_venue.get(venue)
        if binding is None or order['c']!=binding['client_order_id']:
            raise ValueError('native order lacks a frozen binding')
        name=binding['order_id'];action=fixed[name]
        if (order['s']!=symbol or order['S']!=action.side or order['o']!='LIMIT' or order['f']!='GTX'
                or order['ps']!='BOTH' or _amount(order['q'])!=action.quantity or _amount(order['p'])!=action.price
                or type(order['m']) is not bool or type(order['R']) is not bool
                or order['R']!=(name in reduce_only_ids) or order.get('cp',False) is not False
                or order.get('ot','LIMIT')!='LIMIT' or order.get('pm','NONE')!='NONE'):
            raise ValueError('native order conflicts with fixed ordinary intent')
        if not action.timestamp<=trade_ms<=transaction_ms<=event_ms<=received:
            raise ValueError('noncausal native event clocks')
        state=STATES.get((order['x'],order['X']))
        if state is None:raise ValueError('unsupported native execution and state pair')
        quantity,total,price=_amount(order['l']),_amount(order['z']),_amount(order['L'])
        trade=order['x']=='TRADE';fingerprint=_digest(order);key=(venue,trade_id)
        fee=_amount(order['n'],signed=True) if 'n' in order else None
        asset=order.get('N')
        if asset is not None and (not isinstance(asset,str) or not re.fullmatch('[A-Z0-9]{1,20}',asset)):
            raise ValueError('exact commission asset required')
        if ('n' in order)!=('N' in order):raise ValueError('commission amount and asset must occur together')
        if raw_hash in seen_raw or trade and key in seen_trade:
            if trade and seen_trade.get(key)!=fingerprint:raise ValueError('conflicting duplicate native trade')
            duplicates.append({'message_index':index,'response_sha256':raw_hash,'received_at_ms':received})
            continue
        if event_ms<last_event or transaction_ms<last_transaction:
            raise ValueError('out-of-order unique native event in one declared stream')
        last_event,last_transaction=event_ms,transaction_ms;seen_raw.add(raw_hash)
        if trade:
            if quantity<=ZERO or price<=ZERO or total-filled[name]!=quantity:
                raise ValueError('native last fill and cumulative quantity disagree')
            if (action.side=='BUY' and price>action.price) or (action.side=='SELL' and price<action.price):
                raise ValueError('native fill violates fixed limit price')
            seen_trade[key]=fingerprint
            if fee is None or asset is None:missing+=1;all_quote=False
            else:
                fees[asset]=fees.get(asset,ZERO)+fee;all_quote&=asset==symbol[3:]
            trade_rows.append({'order_id':name,'venue_order_id':str(venue),'trade_id':str(trade_id),
                'event_ms':event_ms,'transaction_ms':transaction_ms,'venue_trade_ms':trade_ms,'received_at_ms':received,
                'quantity':canonical_decimal(quantity),'price':canonical_decimal(price),'liquidity_role':'MAKER' if order['m'] else 'TAKER',
                'reported_commission':canonical_decimal(fee) if fee is not None else None,'commission_asset':asset,
                'response_sha256':raw_hash})
        elif quantity!=ZERO or total!=filled[name] or fee not in (None,ZERO):
            raise ValueError('nontrade event cannot introduce fills or commission')
        filled[name]=total
        rows[name]['events'].append({'receipt_id':raw_hash,'venue_ms':trade_ms,'received_ms':received,
            'state':state,'cumulative_filled':canonical_decimal(total)})
        message_rows.append({'message_index':index,'order_id':name,'execution_type':order['x'],'native_state':order['X'],
            'event_ms':event_ms,'transaction_ms':transaction_ms,'venue_trade_ms':trade_ms,
            'received_at_ms':received,'response_sha256':raw_hash})
    source_hash=_digest(document)
    observed={'action_hash':action_hash,'symbol':symbol,'coverage':deepcopy(coverage),
        'provenance':{'kind':'SYNTHETIC_TEST_FIXTURE' if document['provenance']=='SYNTHETIC_TEST_FIXTURE' else 'IMPORTED_PRIVATE_RECEIPTS',
            'declared_source_sha256':source_hash},'orders':list(rows.values())}
    evidence={'schema':SCHEMA,'input_hash':source_hash,'account_ref_sha256':document['account_ref_sha256'],
        'symbol':symbol,'quote':symbol[3:],'provenance':document['provenance'],'source_verified':False,'actual_paid_fees':None,
        'messages_count':len(messages),'unique_messages':len(message_rows),'duplicate_messages':len(duplicates),
        'trades_count':len(trade_rows),'missing_commission_trades':missing,'all_commissions_in_quote':all_quote,
        'reported_commission_by_asset':{asset:canonical_decimal(value) for asset,value in sorted(fees.items())},
        'reported_quote_commission':canonical_decimal(fees.get(symbol[3:],ZERO)) if all_quote else None,
        'reported_filled_quantity':canonical_decimal(sum(filled.values(),ZERO)),
        'unobserved_order_ids':[name for name,action in fixed.items() if action.timestamp<=end and not rows.get(name,{}).get('events')],
        'trade_rows':trade_rows,'message_rows':message_rows,'duplicates':duplicates,
        'coverage':deepcopy(coverage),'economic_eligible':False,
        'limitations':['IMPORTED_SOURCE_UNVERIFIED','DECLARED_COVERAGE_NOT_TRANSPORT_ATTESTATION',
            'NO_FUNDING_OR_EXTERNAL_CASHFLOW_RECONCILIATION','NO_CURRENCY_CONVERSION','FIXED_LIMIT_GTX_ONE_WAY_ONLY']}
    return observed,evidence


@ledger_decimal_context()
def native_fee_comparisons(evidence, simulations, tape, profiles):
    """Compare declared window totals after selection, with quantity differences.

Fees are never a calibration score and never another deduction from the ledger.
Different currencies, incomplete windows and missing fields have no net total.
"""
    complete=(evidence['coverage']['start_ms']==tape.start_ms
        and evidence['coverage']['end_ms']==tape.end_ms and not evidence['coverage']['gaps']
        and not evidence['unobserved_order_ids'] and evidence['reported_quote_commission'] is not None)
    rows=[]
    for profile in profiles:
        simulation=simulations.get(profile)
        modeled=simulation['reconciliation']['fee'] if simulation is not None else None
        available=complete and modeled is not None
        difference=Decimal(evidence['reported_quote_commission'])-Decimal(modeled) if available else None
        rows.append({'profile_ms':profile,'simulated_fee':modeled,
            'reported_minus_simulated_fee':canonical_decimal(difference) if difference is not None else None,
            'comparison_available':available,'comparison_scope':'DECLARED_WHOLE_WINDOW_INCLUDES_QUANTITY_DIFFERENCES',
            'source_verified':False,'actual_paid_fees':None})
    return rows


def native_summary(evidence):
    return {k:v for k,v in evidence.items() if k not in ('trade_rows','message_rows','duplicates')} | {
        'trade_rows':evidence['trade_rows'][-50:],
        'trade_rows_total':len(evidence['trade_rows']),
        'projection':'LAST_50_TRADES_FULL_MESSAGES_IN_IMMUTABLE_INPUT',
        'artifact':'native-observations.json'}
