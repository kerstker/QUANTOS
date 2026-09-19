"""Exact indicative base/display quantities; not REST order-unit certification."""
from decimal import Context, Decimal, localcontext
from fractions import Fraction

from services.data.kraken_account import canonical, number
from services.data.kraken_public import require
from services.portfolio.kraken_allocation import prepare as allocate, amount


def _sequence(quantities, *, available_cash):
    """Review stages only; settled reductions always require a fresh plan."""
    active=[row for row in quantities if amount(row['base_quantity'])>0]
    skipped=[row['instrument_id'] for row in active if row['below_declared_minimum_on_planning_basis']]
    eligible=[row for row in active if not row['below_declared_minimum_on_planning_basis']]
    sells=[row['instrument_id'] for row in eligible if row['side']=='sell']
    buys=[row['instrument_id'] for row in eligible if row['side']=='buy']
    cost=sum((amount(row['indicative_quote_notional']) for row in eligible if row['side']=='buy'),Decimal(0))
    result={'status':'NO_ACTION_WITHIN_PLANNING_GRID','steps':[],
            'below_minimum_instrument_ids':skipped,'deferred_buy_instrument_ids':[],
            'fee_budget_verified':False,'execution_eligible':False,'submission_enabled':False,'authoritative':False}
    if sells:
        result.update(status='REDUCTIONS_FIRST',deferred_buy_instrument_ids=buys,steps=[
            {'kind':'REVIEW_REDUCTIONS','instrument_ids':sells},
            {'kind':'RECONCILE_AND_REPLAN','reuse_current_buy_quantities':False}])
    elif buys:
        if cost>available_cash:result.update(status='WAITING_FOR_FUNDS',deferred_buy_instrument_ids=buys)
        else:result.update(status='BUYS_REQUIRE_PREFLIGHT',steps=[{'kind':'REVIEW_BUYS','instrument_ids':buys}])
    elif skipped:result['status']='BELOW_PLANNING_MINIMUM'
    return result


def prepare(inventory, policy, *, as_of_ms):
    allocation=allocate(inventory,policy,as_of_ms=as_of_ms)
    result={'schema':'KRAKEN_QUANTITY_REVIEW_V1','as_of_ms':as_of_ms,'status':'BLOCKED','quantities':[],
            'allocation_review':allocation,'indicative_buy_cost_before_fees':None,
            'indicative_sell_proceeds_before_fees':None,'requires_settled_cash_before_buys':None,
            'issues':list(allocation['issues']),'drafts':[],'sequencing_review':None,'submission_enabled':False,'authoritative':False,
            'blockers':['ORDER_QUANTITY_SEMANTICS_UNVERIFIED','FEE_DEBITS_NOT_SIZED','LIVE_ISSUER_NOT_CONNECTED']}
    if allocation['status']!='REVIEW_ONLY':return result
    indexed=[]
    for field in ('instrument_specs','planning_units','planning_quotes'):
        rows=inventory.get(field,[])
        require(isinstance(rows,list) and len(rows)<=32,'KRAKEN_SIZING_INPUTS')
        mapping={row['instrument_id']:row for row in rows}
        require(len(mapping)==len(rows),'KRAKEN_SIZING_DUPLICATE_PRODUCT')
        indexed.append(mapping)
    specs,units,quotes=indexed
    holdings={r['instrument_id']:r for r in inventory['balances'] if r['role']=='BASE_ASSET'}
    quantities=[];buys=Decimal(0);sales=Decimal(0)
    with localcontext(Context(prec=400)):
        for target in allocation['targets']:
            key=target['instrument_id']
            try:
                spec=specs[key];unit=units[key];quote=quotes[key]
                require(spec['status']=='online' and spec['asset_class'] in ('currency','tokenized_asset') and
                        spec['base']==unit['base']==quote['base'] and spec['quote']==quote['quote']==policy['quote_asset'] and
                        spec['asset_class']==unit['asset_class'],'KRAKEN_SIZING_PRODUCT')
                expected='UNDERLYING_EQUITY' if spec['asset_class']=='tokenized_asset' else 'NATIVE_ASSET'
                require(unit['display_basis']==quote['price_quantity_basis']==expected,'KRAKEN_SIZING_BASIS')
                require(unit['submission_enabled'] is False and unit['authoritative'] is False and
                        quote['execution_eligible'] is False and quote['authoritative'] is False,
                        'KRAKEN_SIZING_BOUNDARY')
                require(all(type(row['received_at_ms']) is int and 0<=as_of_ms-row['received_at_ms']<=120000
                            for row in (unit,quote)),'KRAKEN_SIZING_STALE')
                multiplier=number(unit['token_multiplier']);bid=number(quote['bid']);ask=number(quote['ask'])
                require(multiplier>0 and 0<bid<=ask and (expected!='NATIVE_ASSET' or multiplier==1),
                        'KRAKEN_SIZING_PRICE_UNITS')
                mid=(bid+ask)/2
                require(canonical(mid)==quote['mid'],'KRAKEN_SIZING_MID')
                require(type(spec['lot_decimals']) is int and 0<=spec['lot_decimals']<=18 and
                        type(unit['decimals']) is int and 0<=unit['decimals']<=18 and spec['lot_multiplier']==1,
                        'KRAKEN_SIZING_PRECISION')
                holding=holdings.get(key);current=number(holding['base_quantity']) if holding else Decimal(0)
                require(current*multiplier*mid==amount(target['current_value']),'KRAKEN_SIZING_VALUATION_CHANGED')
                # Exact rational division avoids rounding a value just below a
                # lot boundary upward before truncation. This is a planning
                # grid, not a claim that REST xStocks volume uses this basis.
                step=Decimal(1).scaleb(-min(spec['lot_decimals'],unit['decimals']))
                delta=Fraction(amount(target['target_value']))/Fraction(mid*multiplier)-Fraction(current)
                count=int(abs(delta)/Fraction(step));quantity=Decimal(count)*step
                side='buy' if delta>0 else 'sell' if delta<0 else 'hold'
                if side=='sell':
                    available=max(Decimal(0),min(current,number(holding['available'],signed=True)))
                    require(quantity<=available,'KRAKEN_SIZING_UNAVAILABLE_HOLDING')
                display=quantity*multiplier;price=ask if side=='buy' else bid
                notional=display*price
                residual=amount(target['target_value'])-(current+(quantity if side=='buy' else -quantity))*multiplier*mid
                below=quantity<number(spec['ordermin']) or notional<number(spec['costmin'])
                quantities.append({'instrument_id':key,'side':side,'base_quantity':canonical(quantity),
                    'display_quantity':canonical(display),'display_basis':expected,'token_multiplier':canonical(multiplier),
                    'planning_base_step':canonical(step),'reference_price':canonical(price),
                    'indicative_quote_notional':canonical(notional),'residual_target_value':canonical(residual),
                    'below_declared_minimum_on_planning_basis':below,
                    'basis_status':'INFERRED_XSTOCKS_QUOTE_BASIS' if expected=='UNDERLYING_EQUITY' else 'NATIVE_ASSET',
                    'venue_quantity_eligible':False})
                if side=='buy':buys+=notional
                elif side=='sell':sales+=notional
            except (ValueError,KeyError,TypeError):result['issues'].append('QUANTITY_INPUT_UNRESOLVED:'+key)
        if not result['issues']:
            result.update(status='INDICATIVE_QUANTITIES',quantities=quantities,
                indicative_buy_cost_before_fees=canonical(buys),indicative_sell_proceeds_before_fees=canonical(sales),
                requires_settled_cash_before_buys=buys>amount(allocation['cash_available_after_reserve']),
                sequencing_review=_sequence(quantities,available_cash=amount(allocation['cash_available_after_reserve'])))
    return result
