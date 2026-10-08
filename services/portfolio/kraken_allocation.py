"""Explicit-policy allocation review; never an executable rebalance or mandate."""
from decimal import Context, Decimal, localcontext
import re

from services.data.kraken_account import canonical, number
from services.data.kraken_public import require


def amount(value):
    require(isinstance(value,str) and re.fullmatch(r'\d{1,180}(\.\d{1,180})?',value),'KRAKEN_ALLOCATION_VALUE')
    return Decimal(value)


def validate_policy(policy):
    require(isinstance(policy,dict) and set(policy)=={'schema','account_ref_sha256','quote_asset',
        'capital_limit','cash_reserve','max_asset_weight','target_weights'},'KRAKEN_ALLOCATION_POLICY_FIELDS')
    require(policy['schema']=='KRAKEN_ALLOCATION_POLICY_V1','KRAKEN_ALLOCATION_POLICY_VERSION')
    require(isinstance(policy['account_ref_sha256'],str) and re.fullmatch('[a-f0-9]{64}',policy['account_ref_sha256']),
            'KRAKEN_ALLOCATION_ACCOUNT')
    require(isinstance(policy['quote_asset'],str) and re.fullmatch(r'[A-Za-z0-9.]{1,48}',policy['quote_asset']),
            'KRAKEN_ALLOCATION_CURRENCY')
    require(number(policy['capital_limit'])>0 and number(policy['cash_reserve'])>=0,'KRAKEN_ALLOCATION_LIMIT')
    maximum=number(policy['max_asset_weight']);require(0<maximum<=1,'KRAKEN_ALLOCATION_WEIGHT')
    weights=policy['target_weights'];require(isinstance(weights,dict) and 1<=len(weights)<=32,'KRAKEN_ALLOCATION_UNIVERSE')
    with localcontext(Context(prec=120)):
        total=Decimal(0)
        for key,weight in weights.items():
            require(isinstance(key,str) and re.fullmatch(r'KRAKEN:(currency|tokenized_asset):[A-Za-z0-9.]{1,48}',key),
                    'KRAKEN_ALLOCATION_PRODUCT')
            value=number(weight);require(0<=value<=maximum,'KRAKEN_ALLOCATION_CONCENTRATION');total+=value
        require(total<=1,'KRAKEN_ALLOCATION_WEIGHT_SUM')
    return policy


def prepare(inventory, policy, *, as_of_ms):
    validate_policy(policy)
    require(inventory.get('schema')=='KRAKEN_INVENTORY_V1' and inventory.get('submission_enabled') is False and
            inventory.get('authoritative') is False,'KRAKEN_ALLOCATION_INPUT')
    require(inventory['account_ref_sha256']==policy['account_ref_sha256'],'KRAKEN_ALLOCATION_ACCOUNT_MISMATCH')
    require(type(as_of_ms) is int and type(inventory.get('as_of_ms')) is int and
            0<inventory['as_of_ms']<=as_of_ms,'KRAKEN_ALLOCATION_TIME')
    issues=list(inventory['issues']);valuation=inventory.get('indicative_valuation',{})
    if inventory['status']!='INVENTORY_MAPPED':issues.append('INVENTORY_UNRESOLVED')
    if as_of_ms-inventory['as_of_ms']>120000:issues.append('INVENTORY_STALE')
    if inventory['open_orders']:issues.append('OPEN_ORDERS_REQUIRE_RECONCILIATION')
    if valuation.get('status')!='INDICATIVE' or valuation.get('quote_asset')!=policy['quote_asset']:
        issues.append('VALUATION_UNAVAILABLE')
    if type(valuation.get('as_of_ms')) is not int or not 0<=as_of_ms-valuation['as_of_ms']<=120000:
        issues.append('VALUATION_STALE')
    report={'schema':'KRAKEN_ALLOCATION_REVIEW_V1','as_of_ms':as_of_ms,'status':'BLOCKED','targets':[],
        'quote_asset':policy['quote_asset'],'allocation_budget':None,'cash_available_after_reserve':None,
        'issues':issues,'limitations':list(valuation.get('limitations',[]))+['NOT_AN_ORDER_PLAN','FEES_NOT_SIZED'],
        'submission_enabled':False,'authoritative':False}
    if issues:return report
    rows=inventory['balances'];values={r['account_asset']:r for r in valuation['rows']}
    require(len(values)==len(valuation['rows']),'KRAKEN_ALLOCATION_DUPLICATE_VALUE')
    holdings={};cash=[]
    for row in rows:
        if row['role']=='BASE_ASSET':
            key=row['instrument_id'];require(key not in holdings,'KRAKEN_ALLOCATION_DUPLICATE_HOLDING');holdings[key]=row
        elif row['role']=='QUOTE_BALANCE' and row['base_asset']==policy['quote_asset']:cash.append(row)
    if len(cash)!=1:issues.append('QUOTE_BALANCE_UNAVAILABLE')
    quotes={q['instrument_id']:q for q in inventory.get('planning_quotes',[])}
    for key in policy['target_weights']:
        q=quotes.get(key)
        if not q or q.get('quote')!=policy['quote_asset'] or type(q.get('received_at_ms')) is not int or not 0<=as_of_ms-q['received_at_ms']<=120000:
            issues.append('TARGET_QUOTE_UNAVAILABLE:'+key)
    if issues:return report
    with localcontext(Context(prec=400)):
        c=cash[0]
        require(number(c['credit_used'])==0,'KRAKEN_ALLOCATION_BORROWING')
        owned=number(c['balance'])-number(c['hold_trade'])
        spendable=max(Decimal(0),min(owned,number(c['available'],signed=True))-number(policy['cash_reserve']))
        current={key:amount(values[holdings[key]['account_asset']]['mid_value']) if key in holdings else Decimal(0)
                 for key in policy['target_weights']}
        managed=sum(current.values(),Decimal(0))
        budget=min(number(policy['capital_limit']),managed+spendable)
        targets=[];gross_buy=Decimal(0);gross_sell=Decimal(0)
        for key,weight in sorted(policy['target_weights'].items()):
            target=budget*number(weight);delta=target-current[key]
            if delta<0 and key in holdings:
                holding=holdings[key];base=number(holding['base_quantity'])
                available=max(Decimal(0),min(base,number(holding['available'],signed=True)))
                if -delta*base>current[key]*available:issues.append('REDUCTION_EXCEEDS_AVAILABLE_HOLDING:'+key)
            targets.append({'instrument_id':key,'current_value':canonical(current[key]),'target_value':canonical(target),
                            'value_difference':canonical(delta),'side':'INCREASE' if delta>0 else 'REDUCE' if delta<0 else 'UNCHANGED'})
            gross_buy+=max(delta,Decimal(0));gross_sell+=max(-delta,Decimal(0))
        report.update(status='REVIEW_ONLY',targets=targets,allocation_budget=canonical(budget),
            cash_available_after_reserve=canonical(spendable),indicative_buy_value=canonical(gross_buy),
            indicative_reduce_value=canonical(gross_sell),unallocated_budget=canonical(budget-sum(amount(t['target_value']) for t in targets)),
            requires_completed_sales_before_buys=gross_buy>spendable)
    if issues:report.update(status='BLOCKED',targets=[])
    return report
