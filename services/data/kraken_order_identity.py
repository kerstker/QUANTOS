"""Match reserved drafts to observed order identities; never settlement permission."""
from decimal import Context, localcontext
import re

from services.data.kraken_account import canonical, number, payload
from services.data.kraken_order_query import assess
from services.data.kraken_public import require


def _uuid(value):
    if not isinstance(value,str):return None
    if re.fullmatch('[a-fA-F0-9]{32}',value):return value.lower()
    if re.fullmatch('[a-fA-F0-9]{8}(-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}',value):
        return value.replace('-','').lower()
    return None


def match(read, drafts, specs, *, account_ref, not_before_ms, as_of_ms):
    orders=assess(read,as_of_ms=as_of_ms)
    require(orders['account_ref_sha256']==account_ref,'KRAKEN_IDENTITY_ACCOUNT')
    require(type(not_before_ms) is int and 0<not_before_ms<=as_of_ms,'KRAKEN_IDENTITY_CLOCK')
    require(isinstance(drafts,list) and 1<=len(drafts)<=32,'KRAKEN_IDENTITY_DRAFTS')
    require(isinstance(specs,list) and 1<=len(specs)<=32,'KRAKEN_IDENTITY_SPECS')
    products={s['instrument_id']:s for s in specs}
    require(len(products)==len(specs),'KRAKEN_IDENTITY_DUPLICATE_PRODUCT')
    raw=payload(read['body']);by_client={};seen=set();issues=[];matches=[]
    for row in orders['orders']:
        client=_uuid(raw[row['order_id']].get('cl_ord_id'))
        if client is not None:by_client.setdefault(client,[]).append(row)
    with localcontext(Context(prec=100)):
        for draft in drafts:
            require(isinstance(draft,dict) and set(draft)=={'instrument_id','side','volume','limit_price',
                    'client_order_id','deadline_ms'},'KRAKEN_IDENTITY_DRAFT_FIELDS')
            client=draft['client_order_id']
            require(isinstance(client,str) and re.fullmatch('[a-f0-9]{32}',client) and client not in seen,
                    'KRAKEN_IDENTITY_CLIENT_ID')
            seen.add(client)
            require(draft['side'] in ('buy','sell') and number(draft['volume'])>0 and number(draft['limit_price'])>0 and
                    type(draft['deadline_ms']) is int and draft['deadline_ms']>not_before_ms,'KRAKEN_IDENTITY_DRAFT')
            candidates=by_client.get(client,[])
            if len(candidates)!=1:
                issues.append(('CLIENT_ID_AMBIGUOUS:' if candidates else 'CLIENT_ID_NOT_OBSERVED:')+client);continue
            order=candidates[0];original=raw[order['order_id']];spec=products.get(draft['instrument_id'])
            if spec is None or order['pair'] not in (spec['pair'],spec['pair_key'],spec['altname']):
                issues.append('ORDER_PRODUCT_MISMATCH:'+client);continue
            if order['side']!=draft['side'] or order['order_type']!='limit' or number(order['volume'])!=number(draft['volume']):
                issues.append('ORDER_PARAMETERS_MISMATCH:'+client);continue
            price=original['descr'].get('price');opened=original.get('opentm')
            if price is None or opened is None:
                issues.append('ORDER_IDENTITY_FIELDS_MISSING:'+client);continue
            if number(price)!=number(draft['limit_price']):
                issues.append('ORDER_LIMIT_MISMATCH:'+client);continue
            opened_ms=number(opened)*1000
            if not not_before_ms<=opened_ms<=min(draft['deadline_ms'],read['received_at_ms']):
                issues.append('ORDER_CREATION_OUTSIDE_WINDOW:'+client);continue
            matches.append({'client_order_id':client,'order_id':order['order_id'],
                            'instrument_id':draft['instrument_id'],'opened_at_ms':canonical(opened_ms),
                            'status':order['status']})
    return {'schema':'KRAKEN_RESERVED_ORDER_IDENTITY_V1','account_ref_sha256':account_ref,
            'as_of_ms':as_of_ms,'status':'UNRESOLVED' if issues else 'OBSERVED_ORDER_IDENTITIES_MATCHED',
            'orders':matches,'issues':issues,'source_body_sha256':orders['source_body_sha256'],
            'release_allowed':False,'submission_enabled':False,'authoritative':False,
            'limitations':['ORDER_QUANTITY_SEMANTICS_UNVERIFIED','ISSUER_RECEIPT_NOT_BOUND','SETTLEMENT_NOT_BOUND']}
