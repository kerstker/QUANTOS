"""Indicative observed spot values; no complete-account NAV or execution authority."""
from decimal import Context, localcontext
import re

from services.data.kraken_account import canonical, number
from services.data.kraken_public import require


def value(inventory, quotes, *, quote_asset, as_of_ms):
    require(isinstance(quote_asset,str) and re.fullmatch(r'[A-Za-z0-9.]{1,48}',quote_asset), 'KRAKEN_VALUE_QUOTE')
    require(type(as_of_ms) is int and type(inventory.get('as_of_ms')) is int and
            0<inventory['as_of_ms']<=as_of_ms,'KRAKEN_VALUE_TIME')
    require(inventory.get('schema')=='KRAKEN_INVENTORY_V1' and inventory.get('submission_enabled') is False
            and inventory.get('authoritative') is False,'KRAKEN_VALUE_BOUNDARY')
    issues=list(inventory['issues'])
    if inventory['status']!='INVENTORY_MAPPED':issues.append('INVENTORY_UNRESOLVED')
    if as_of_ms-inventory['as_of_ms']>120000:issues.append('INVENTORY_STALE')
    indexed={}
    for quote in quotes:
        key=quote['instrument_id'];require(key not in indexed,'KRAKEN_VALUE_DUPLICATE_QUOTE');indexed[key]=quote
    rows=[];total=number('0');bid_total=number('0');inferred=False
    with localcontext(Context(prec=180)):
        for balance in inventory['balances']:
            row={'account_asset':balance['account_asset'],'mid_value':None,'bid_value':None}
            try:
                quantity=number(balance['base_quantity'],signed=True)
                require(quantity>=0,'KRAKEN_VALUE_NEGATIVE_BALANCE')
                if balance['role']=='QUOTE_BALANCE' and balance['base_asset']==quote_asset:
                    mid_value=bid_value=quantity
                elif quantity==0:mid_value=bid_value=number('0')
                else:
                    require(balance['role']=='BASE_ASSET','KRAKEN_VALUE_UNMAPPED')
                    quote=indexed[balance['instrument_id']]
                    require(quote['quote']==quote_asset and quote['base']==balance['base_asset'], 'KRAKEN_VALUE_CURRENCY')
                    require(quote['execution_eligible'] is False and quote['authoritative'] is False and
                            quote['scope']=='INDICATIVE_QUOTE_ONLY','KRAKEN_VALUE_QUOTE_BOUNDARY')
                    require(type(quote['received_at_ms']) is int and
                            0<=as_of_ms-quote['received_at_ms']<=120000 and
                            0<=as_of_ms-balance['units_received_at_ms']<=120000,'KRAKEN_VALUE_STALE')
                    require(quote['price_quantity_basis']==balance['display_basis'],'KRAKEN_VALUE_BASIS')
                    quantity=number(balance['display_quantity']) if balance['display_basis']=='NATIVE_ASSET' else None
                    # Unit conversion may have more than 18 fractional places; reconstruct
                    # from validated inputs with a context sufficient for all products.
                    if quantity is not None:
                        require(canonical(quantity)==balance['base_quantity'],'KRAKEN_VALUE_NATIVE_QUANTITY')
                    if quantity is None:
                        quantity=number(balance['base_quantity'])*number(balance['token_multiplier'])
                        require(canonical(quantity)==balance['display_quantity'],'KRAKEN_VALUE_QUANTITY')
                        require(quote['basis_evidence']=='INFERRED_FROM_KRAKEN_XSTOCKS_FAQ','KRAKEN_VALUE_BASIS_EVIDENCE')
                        inferred=True
                    bid=number(quote['bid']);ask=number(quote['ask'])
                    require(0<bid<=ask,'KRAKEN_VALUE_SPREAD')
                    mid=(bid+ask)/2
                    require(canonical(mid)==quote['mid'],'KRAKEN_VALUE_MID')
                    mid_value=quantity*mid;bid_value=quantity*bid
                row.update(mid_value=canonical(mid_value),bid_value=canonical(bid_value))
                total+=mid_value;bid_total+=bid_value
            except (ValueError,KeyError,TypeError):issues.append('UNVALUED_BALANCE:'+balance['account_asset'])
            rows.append(row)
    limitations=['NON_ATOMIC_ACCOUNT','FULL_ACCOUNT_SCOPE_UNVERIFIED','SOURCE_QUOTE_TIME_UNKNOWN',
                 'FEES_AND_MARKET_IMPACT_NOT_INCLUDED','COST_BASIS_UNKNOWN']
    if inferred:limitations.append('XSTOCKS_PRICE_BASIS_INFERRED')
    return {'schema':'KRAKEN_INDICATIVE_VALUATION_V1','status':'INCOMPLETE' if issues else 'INDICATIVE',
            'quote_asset':quote_asset,'as_of_ms':as_of_ms,'rows':rows,'issues':sorted(set(issues)),
            'observed_spot_mid_value':None if issues else canonical(total),
            'observed_spot_bid_value':None if issues else canonical(bid_total),
            'net_asset_value':None,'limitations':limitations,'execution_eligible':False,'authoritative':False}
