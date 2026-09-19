"""Join observed account quantities to public products, without valuation authority."""
from decimal import Context, localcontext

from services.data.kraken_account import canonical, number
from services.data.kraken_public import require
from services.data.kraken_units import convert_quantity


def reconcile(account, markets, *, as_of_ms):
    require(type(as_of_ms) is int and as_of_ms>0, 'KRAKEN_INVENTORY_TIME')
    require(account.get('schema')=='KRAKEN_ACCOUNT_OBSERVATION_V1' and
            account.get('quantity_basis')=='base' and account.get('submission_enabled') is False and
            account.get('authoritative') is False, 'KRAKEN_INVENTORY_ACCOUNT')
    require(account.get('status') in ('OBSERVED_STABLE','UNRESOLVED','STALE'), 'KRAKEN_INVENTORY_ACCOUNT_STATUS')
    require(type(account.get('observed_at_ms')) is int and 0<account['observed_at_ms']<=account['as_of_ms']<=as_of_ms,
            'KRAKEN_INVENTORY_FUTURE_ACCOUNT')
    issues=list(account['issues'])
    if account['status']!='OBSERVED_STABLE':issues.append('ACCOUNT_UNRESOLVED')
    reads=account['reads']
    require(len(reads)==4 and type(reads[0]['requested_at_ms']) is int and
            0<reads[0]['requested_at_ms']<=account['observed_at_ms'], 'KRAKEN_INVENTORY_ACCOUNT_READS')
    if as_of_ms-reads[0]['requested_at_ms']>120000:issues.append('ACCOUNT_STALE')
    products={};quotes=set();aliases={};order_aliases={}
    for market in markets:
        spec=market['spec'];base=spec['base'];key=spec['instrument_id']
        require(base not in products, 'KRAKEN_INVENTORY_DUPLICATE_BASE')
        products[base]=market;quotes.add(spec['quote'])
        for alias in (base,base+'.T') if spec['asset_class']=='tokenized_asset' else (base,):
            require(alias not in aliases, 'KRAKEN_INVENTORY_ALIAS_COLLISION')
            aliases[alias]=base
        for alias in (spec['altname'],spec['pair'],key.rsplit(':',1)[-1]):
            require(alias not in order_aliases or order_aliases[alias]==key, 'KRAKEN_INVENTORY_ORDER_COLLISION')
            order_aliases[alias]=key
    require(not quotes.intersection(aliases), 'KRAKEN_INVENTORY_BASE_QUOTE_COLLISION')
    mapped={};rows=[]
    with localcontext(Context(prec=120)):
        for asset,balance in sorted(account['balances'].items()):
            values={k:number(balance[k],signed=k in ('balance','available'))
                    for k in ('balance','hold_trade','credit','credit_used','available')}
            require(values['available']==values['balance']+values['credit']-values['credit_used']-values['hold_trade'],
                    'KRAKEN_INVENTORY_AVAILABLE_MISMATCH')
            row={'account_asset':asset,**{k:canonical(v) for k,v in values.items()},
                 'base_quantity':canonical(values['balance']),'role':'UNMAPPED','display_quantity':None}
            if values['balance']<0 or values['credit_used']!=0:issues.append('CREDIT_EXPOSURE:'+asset)
            if values['available']<0:issues.append('NEGATIVE_AVAILABLE:'+asset)
            if asset in aliases:
                base=aliases[asset];market=products[base];spec=market['spec']
                row.update(base_asset=base,instrument_id=spec['instrument_id'],role='BASE_ASSET')
                mapped.setdefault(base,[]).append(asset)
                try:
                    units=convert_quantity(row['base_quantity'],market['payload'],spec=spec,
                        received_at_ms=market['received_at_ms'],as_of_ms=as_of_ms)
                    row.update(display_quantity=units['display_quantity'],display_basis=units['display_basis'],
                               token_multiplier=units['token_multiplier'],units_received_at_ms=units['received_at_ms'])
                except (ValueError,KeyError,TypeError):issues.append('UNITS_UNAVAILABLE:'+base)
            elif asset in quotes:row.update(role='QUOTE_BALANCE',base_asset=asset)
            elif any(v!=0 for v in values.values()):issues.append('UNMAPPED_BALANCE:'+asset)
            rows.append(row)
    for base,assets in mapped.items():
        if len(assets)>1:issues.append('AMBIGUOUS_BALANCE_ALIAS:'+base)
    orders=[]
    for order in account['open_orders']:
        key=order_aliases.get(order['pair']);row=dict(order,instrument_id=key)
        if key is None:issues.append('UNMAPPED_OPEN_ORDER:'+order['order_id'])
        if order['leverage']!='none':issues.append('MARGIN_ORDER:'+order['order_id'])
        orders.append(row)
    return {'schema':'KRAKEN_INVENTORY_V1','status':'UNRESOLVED' if issues else 'INVENTORY_MAPPED',
            'as_of_ms':as_of_ms,'account_observed_at_ms':account['observed_at_ms'],
            'account_ref_sha256':account['account_ref_sha256'],'quantity_basis':'base',
            'balances':rows,'open_orders':orders,'issues':sorted(set(issues)),
            'net_asset_value':None,'valuation_status':'NOT_VALUED','cost_basis_status':'UNKNOWN',
            'account_scope_verified':False,'atomic_snapshot':False,'open_positions_status':'NOT_OBSERVED',
            'portfolio_management_ready':False,'submission_enabled':False,'authoritative':False}
