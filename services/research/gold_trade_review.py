"""Join recorded orders, fills and native income; never reconstruct missing positions.

These are observations within a bounded query window. Matching rows do not prove
full history, order lifecycle completeness, provenance or strategy profitability.
"""
from decimal import Decimal, Context, localcontext
from services.research.gold_trade_observation import assess, _text_amount
from services.research.gold_account_history import normalize_orders, normalize_income


def review(fills, orders, algos, income):
    assess(fills)
    orders=normalize_orders(orders,algo=False)
    algos=normalize_orders(algos,algo=True)
    cash=normalize_income(income,start_ms=fills['start_ms'],end_ms=fills['end_ms'])
    trades=sorted({r['trade_id']:r for r in fills['fills']}.values(),key=lambda r:(r['time_ms'],int(r['trade_id'])))
    order_map={str(r['orderId']):r for r in orders}
    grouped={};cash_map={};orphans=[];extra_assets={}
    trade_map={r['trade_id']:r for r in trades}
    for row in trades:grouped.setdefault(row['order_id'],[]).append(row)
    for row in cash:
        if row['symbol']!='XAUUSDT':continue
        trade=trade_map.get(row['tradeId'])
        if trade and ((row['incomeType']=='REALIZED_PNL' and row['asset']!='USDT') or
                      (row['incomeType']=='COMMISSION' and row['asset']!=trade['commission_asset'])):
            extra_assets.setdefault(row['tradeId'],set()).add(row['asset'])
        if row['incomeType'] in ('COMMISSION','REALIZED_PNL'):
            if row['tradeId'] not in trade_map:orphans.append(row)
            else:cash_map.setdefault((row['tradeId'],row['incomeType'],row['asset']),[]).append(row)
    with localcontext(Context(prec=64)):
        def check(row,kind,asset,expected):
            observed=cash_map.get((row['trade_id'],kind,asset),[])
            if not observed:return 'NOT_OBSERVED'
            total=sum((Decimal(r['income']) for r in observed),Decimal(0))
            return 'MATCHING_REPORTED_CASH' if total==expected else 'MISMATCH'
        rows=[]
        for row in trades:
            order=order_map.get(row['order_id'])
            order_check='NOT_OBSERVED' if order is None else (
                'MATCHING_OBSERVED_IDENTITY' if order['side']==row['side'] and order['positionSide']==row['position_side'] else 'MISMATCH')
            rows.append({k:row[k] for k in ('time_ms','side','position_side','quantity','price','commission','commission_asset','reported_realized_pnl')}|dict(
                order_check=order_check,
                commission_check=check(row,'COMMISSION',row['commission_asset'],-Decimal(row['commission'])),
                realized_pnl_check=check(row,'REALIZED_PNL','USDT',Decimal(row['reported_realized_pnl'])),
                unexamined_cash_assets=sorted(extra_assets.get(row['trade_id'],set()))))
        observed_orders=[]
        for order in orders:
            linked=grouped.get(str(order['orderId']),[])
            qty=sum((Decimal(r['quantity']) for r in linked),Decimal(0))
            reported=Decimal(order['executedQty'])
            identity_ok=all(r['side']==order['side'] and r['position_side']==order['positionSide'] for r in linked)
            observed_orders.append(dict(status=order['status'],side=order['side'],position_side=order['positionSide'],
                created_ms=order['time'],updated_ms=order['updateTime'],fill_count=len(linked),
                reported_executed_quantity=order['executedQty'],observed_fill_quantity=_text_amount(qty),
                unfilled_quantity=_text_amount(Decimal(order['origQty'])-reported),
                quantity_check='MISMATCH' if not identity_ok or qty>reported else 'WINDOW_INCOMPLETE' if qty<reported else 'MATCHING_OBSERVED_QUANTITY'))
        conditional=[]
        for algo in algos:
            child=order_map.get(algo.get('actualOrderId',''))
            conditional.append(dict(status=algo['algoStatus'],side=algo['side'],updated_ms=algo['updateTime'],
                child_check='NOT_OBSERVED' if child is None else 'MATCHING_OBSERVED_IDENTITY' if
                child['side']==algo['side'] and child['positionSide']==algo['positionSide'] else 'MISMATCH'))
    return dict(schema='GOLD_TRADE_REVIEW_V1',status='OBSERVED_LINKS_ONLY',trades=rows,
        orders=observed_orders,conditional_orders=conditional,unmatched_cash_count=len(orphans),
        other_market_income_count=sum(r['symbol'] not in ('','XAUUSDT') for r in cash),
        order_lifecycle_complete=False,history_complete=False,net_pnl=None,submission_enabled=False,
        limitations=['NO_INITIAL_POSITION_OR_COST_BASIS','BOUNDED_QUERY_WINDOW','ORDER_STATES_NOT_COMPLETE_LIFECYCLES',
                     'REPORTED_REALIZED_PNL_NOT_ACCOUNT_NET_PNL','NO_MANUAL_OR_STRATEGY_ATTRIBUTION'])
