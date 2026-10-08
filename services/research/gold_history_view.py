"""Identifier-free projection of bounded historical account observations."""
from decimal import Decimal,localcontext
from services.research.gold_trade_review import review
from services.research.gold_trade_observation import assess,_require,_time,_text_amount
from services.research.gold_account_history import normalize_income,normalize_orders


def project_history(value,*,now_ms):
    _require(value['schema']=='GOLD_ACCOUNT_BACKFILL_V1' and value['status'] in ('PARTIAL','QUERIED'),'INVALID_HISTORY')
    _require(value['history_complete'] is False and value['order_lifecycle_complete'] is False and
             value['submission_enabled'] is False and value['net_pnl'] is None,'INVALID_HISTORY_AUTHORITY')
    start,end,at=(_time(value[k]) for k in ('start_ms','end_ms','observed_at_ms'))
    _require(start<end<=at<=now_ms,'INVALID_HISTORY_TIME')
    complete,total=(_time(value[k]) for k in ('completed_tasks','total_tasks'))
    _require(0<=complete<=total<=56 and total>0 and (value['status']!='QUERIED' or complete==total),'INVALID_HISTORY_PROGRESS')
    gaps=value.get('unqueried_ranges',[])
    _require(isinstance(gaps,list) and len(gaps)<=56,'INVALID_HISTORY_GAPS')
    for gap in gaps:
        _require(gap['kind'] in ('orders','algos') and start<=_time(gap['start_ms'])<_time(gap['end_ms'])<=end and gap['reason']=='RETENTION_BOUNDARY','INVALID_HISTORY_GAP')
    report=assess(value['fills']);_require(report['start_ms']==start and report['end_ms']==end,'HISTORY_SCOPE_MISMATCH')
    rows=sorted({r['trade_id']:r for r in value['fills']['fills']}.values(),key=lambda r:(r['time_ms'],r['trade_id']))
    cash=normalize_income(value['income'],start_ms=start,end_ms=end)
    orders=normalize_orders(value['orders'],algo=False);algos=normalize_orders(value['algos'],algo=True)
    daily={};amounts={}
    with localcontext() as ctx:
        ctx.prec=64
        for r in rows:
            day=r['time_ms']//86400000*86400000
            d=daily.setdefault(day,dict(time_ms=day,buy_count=0,sell_count=0))
            d['buy_count' if r['side']=='BUY' else 'sell_count']+=1
        for r in cash:
            if r['symbol']!='XAUUSDT':continue
            kind=amounts.setdefault(r['incomeType'],{})
            kind[r['asset']]=kind.get(r['asset'],Decimal(0))+Decimal(r['income'])
    checks=review(value['fills'],value['orders'],value['algos'],value['income'])
    checks['total_trades']=len(checks['trades'])
    checks['mismatched_trades']=sum(any(r[k]=='MISMATCH' for k in ('order_check','commission_check','realized_pnl_check')) for r in checks['trades'])
    checks['trades']=checks['trades'][-500:]
    checks['orders']=checks['orders'][-500:]
    checks['conditional_orders']=checks['conditional_orders'][-500:]
    fields=('time_ms','side','position_side','price','quantity','commission','commission_asset')
    return dict(status=value['status'],start_ms=start,end_ms=end,observed_at_ms=at,
        completed_tasks=complete,total_tasks=total,retention_gap_count=len(gaps),trade_count=len(rows),income_count=len(cash),
        order_count=len(orders),conditional_order_count=len(algos),display_limit=500,
        first_trade_ms=rows[0]['time_ms'] if rows else None,last_trade_ms=rows[-1]['time_ms'] if rows else None,
        trades=[{k:r[k] for k in fields} for r in rows[-500:]],daily=list(daily.values()),
        xau_income_by_type_asset={k:{a:_text_amount(n) for a,n in v.items()} for k,v in amounts.items()},
        review=checks,history_complete=False,net_pnl=None)
