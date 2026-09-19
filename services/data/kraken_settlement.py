"""Link terminal spot orders, individual trades and observed balance movements."""
from decimal import Context, Decimal, localcontext

from services.data.kraken_account import number, canonical
from services.data.kraken_public import require


def link(bridge, orders, fills, specs, *, as_of_ms):
    require(type(as_of_ms) is int,'KRAKEN_SETTLEMENT_TIME')
    ref=bridge['account_ref_sha256'];issues=[]
    for report, schema, time_field in ((bridge,'KRAKEN_BALANCE_BRIDGE_V1','as_of_ms'),
        (orders,'KRAKEN_ORDER_QUERY_V1','observed_at_ms'),(fills,'KRAKEN_TRADE_RECONCILIATION_V1','as_of_ms')):
        require(report.get('schema')==schema and report.get('quantity_basis')=='base' and
                report['account_ref_sha256']==ref and report.get('authoritative') is False and
                report.get('submission_enabled') is False,'KRAKEN_SETTLEMENT_SOURCE')
        require(type(report[time_field]) is int and 0<=as_of_ms-report[time_field]<=120000,'KRAKEN_SETTLEMENT_STALE')
    if bridge['status']!='BALANCE_MOVEMENTS_MATCHED' or bridge['issues']:issues.append('BALANCES_UNRESOLVED')
    if fills['status']!='TRADE_TOTALS_MATCHED':issues.append('TRADES_UNRESOLVED')
    for order in orders['orders']:
        if order['status'] not in ('closed','canceled','expired'):issues.append('ORDER_NOT_TERMINAL:'+order['order_id'])
    expected={t for o in orders['orders'] for t in o['trade_ids']}
    actual={t['trade_id'] for t in fills['trades']}
    require(expected==actual and len(actual)==len(fills['trades']),'KRAKEN_SETTLEMENT_TRADE_SET')
    aliases={}
    for spec in specs:
        for alias in (spec['pair'],spec['pair_key'],spec['altname']):
            require(alias not in aliases or aliases[alias]==spec,'KRAKEN_SETTLEMENT_AMBIGUOUS_PRODUCT')
            aliases[alias]=spec
    start,end=bridge['movement_start_exclusive_ms'],bridge['movement_end_exclusive_ms']
    require(type(start) is int and type(end) is int and 0<start<end,'KRAKEN_SETTLEMENT_WINDOW')
    grouped={};linked=[]
    with localcontext(Context(prec=160)):
        for entry in bridge['ledger_entries']:
            at=number(entry['time_seconds'])*1000
            if not start<at<end:continue
            if entry['type']!='trade':issues.append('NON_TRADE_MOVEMENT:'+entry['ledger_id']);continue
            if entry['refid'] not in actual:issues.append('UNLINKED_TRADE_LEDGER:'+entry['ledger_id']);continue
            grouped.setdefault(entry['refid'],[]).append(entry)
        for trade in fills['trades']:
            tid=trade['trade_id'];legs=grouped.get(tid,[]);spec=aliases.get(trade['pair'])
            if not start<number(trade['time_seconds'])*1000<end:issues.append('TRADE_OUTSIDE_BALANCE_WINDOW:'+tid)
            if spec is None:issues.append('TRADE_PRODUCT_UNKNOWN:'+tid);continue
            bases={spec['base']}
            if spec['asset_class']=='tokenized_asset':bases.add(spec['base']+'.T')
            base_legs=[e for e in legs if e['asset'] in bases and e['asset_class']==spec['asset_class']]
            quote_legs=[e for e in legs if e['asset']==spec['quote'] and e['asset_class']=='currency']
            if not base_legs or not quote_legs or len(base_legs)+len(quote_legs)!=len(legs):
                issues.append('TRADE_LEGS_INCOMPLETE:'+tid);continue
            if len({e['asset'] for e in base_legs})!=1:issues.append('AMBIGUOUS_BASE_ASSET:'+tid)
            sign=Decimal(1) if trade['side']=='buy' else Decimal(-1)
            base=sum((number(e['amount'],signed=True) for e in base_legs),Decimal(0))
            quote=sum((number(e['amount'],signed=True) for e in quote_legs),Decimal(0))
            if base!=sign*number(trade['volume']) or quote!=-sign*number(trade['cost']):
                issues.append('TRADE_LEDGER_AMOUNT_MISMATCH:'+tid)
            fees={}
            for entry in legs:fees[entry['asset']]=fees.get(entry['asset'],Decimal(0))+number(entry['fee'],signed=True)
            base_fee=sum((number(e['fee'],signed=True) for e in base_legs),Decimal(0))
            if base_fee!=0:issues.append('BASE_FEE_SUMMARY_UNIT_UNVERIFIED:'+tid)
            elif fees.get(spec['quote'],Decimal(0))!=number(trade['fee']):issues.append('TRADE_LEDGER_FEE_MISMATCH:'+tid)
            linked.append({'trade_id':tid,'order_id':trade['order_id'],'instrument_id':spec['instrument_id'],
                'ledger_ids':sorted(e['ledger_id'] for e in legs),'actual_fees':{k:canonical(v) for k,v in sorted(fees.items())}})
    return {'schema':'KRAKEN_SETTLEMENT_REVIEW_V1','status':'UNRESOLVED' if issues else 'OBSERVED_SETTLEMENT_MATCHED',
        'account_ref_sha256':ref,'as_of_ms':as_of_ms,'trades':linked,'issues':sorted(set(issues)),
        'next_review_allowed':not issues,'next_cycle_allowed':False,'submission_enabled':False,'authoritative':False,
        'scope':'OBSERVED_DEFAULT_WALLET_SETTLEMENT_ONLY','full_account_scope_verified':False}
