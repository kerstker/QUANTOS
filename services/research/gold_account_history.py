"""Observed cash/quantity conservation checks, not a strategy or PnL ledger."""
from decimal import Decimal, localcontext
import re

from services.research.gold_trade_observation import assess, _amount, _time, _require, _text_amount
from services.research.gold_account_snapshot import _text, _side


def normalize_income(rows,*,start_ms,end_ms):
    _require(type(rows) is list and len(rows)<=20000,'INVALID_INCOME_ROWS')
    result={}
    for row in rows:
        _require(isinstance(row,dict),'INVALID_INCOME_ROW')
        v={k:row[k] for k in ('symbol','incomeType','income','asset','time','tranId','tradeId')}
        _require(start_ms<=_time(v['time'])<end_ms,'INCOME_OUTSIDE_WINDOW')
        _time(v['tranId']);_text(v['incomeType']);_text(v['asset'])
        if v['symbol']:_text(v['symbol'])
        _require(isinstance(v['symbol'],str) and isinstance(v['tradeId'],str) and
                 (v['tradeId']=='' or re.fullmatch('[0-9]{1,20}',v['tradeId'])),'INVALID_INCOME_ID')
        v['income']=_text_amount(_amount(v['income']))
        key=(v['incomeType'],v['tranId'])
        _require(key not in result or result[key]==v,'CONFLICTING_INCOME_ID')
        result[key]=v
    return sorted(result.values(),key=lambda r:(r['time'],r['incomeType'],r['tranId']))


def reconcile_inventory(before,after,fills,income):
    assess(fills)
    income=normalize_income(income,start_ms=fills['start_ms'],end_ms=fills['end_ms'])
    def wallets(a):
        values={_text(r['asset']):_amount(r['walletBalance']) for r in a['assets']}
        _require(len(values)==len(a['assets']),'DUPLICATE_ASSET')
        return values
    def positions(a):
        values={(_text(r['symbol']),_side(r['positionSide'])):_amount(r['positionAmt']) for r in a['positions']}
        _require(len(values)==len(a['positions']),'DUPLICATE_POSITION')
        return values
    b,e=wallets(before),wallets(after);bp,ep=positions(before),positions(after)
    with localcontext() as ctx:
        ctx.prec=64
        cash={};qty={};commission={};realized=Decimal(0)
        for row in income:
            asset=row['asset'];cash[asset]=cash.get(asset,Decimal(0))+Decimal(row['income'])
            if row['symbol']=='XAUUSDT':
                if row['incomeType']=='COMMISSION':commission[asset]=commission.get(asset,Decimal(0))+Decimal(row['income'])
                if row['incomeType']=='REALIZED_PNL' and asset=='USDT':realized+=Decimal(row['income'])
        fees={};pnl=Decimal(0)
        for row in {r['trade_id']:r for r in fills['fills']}.values():
            key=('XAUUSDT',row['position_side'])
            qty[key]=qty.get(key,Decimal(0))+Decimal(row['quantity'])*(1 if row['side']=='BUY' else -1)
            asset=row['commission_asset'];fees[asset]=fees.get(asset,Decimal(0))+Decimal(row['commission'])
            pnl+=Decimal(row['reported_realized_pnl'])
        wallet={a:_text_amount(e[a]-b[a]-cash.get(a,Decimal(0))) if a in b and a in e else None
                for a in b.keys()|e.keys()|cash.keys()}
        quantities={':'.join(k):_text_amount(ep.get(k,Decimal(0))-bp.get(k,Decimal(0))-qty.get(k,Decimal(0)))
                    for k in bp.keys()|ep.keys()|qty.keys() if k[0]=='XAUUSDT'}
        other=any(k[0]!='XAUUSDT' and v for a in (bp,ep) for k,v in a.items()) or any(
            r['symbol'] not in ('','XAUUSDT') for r in income)
        fee_diff={a:_text_amount(fees.get(a,Decimal(0))+commission.get(a,Decimal(0))) for a in fees.keys()|commission.keys()}
        pnl_diff=_text_amount(pnl-realized)
    return {'schema':'GOLD_INVENTORY_CHECK_V1',
        'wallet_status':'MISSING_ASSET' if None in wallet.values() else 'MISMATCH' if any(v!='0' for v in wallet.values()) else 'MATCHING_OBSERVED_TOTALS',
        'position_status':'OTHER_MARKETS_UNCHECKED' if other else 'MISMATCH' if any(v!='0' for v in quantities.values()) else 'MATCHING_OBSERVED_TOTALS',
        'wallet_differences':wallet,'position_differences':quantities,'commission_differences':fee_diff,
        'realized_pnl_difference':pnl_diff,'cash_fill_status':'MISMATCH' if any(v!='0' for v in fee_diff.values()) or pnl_diff!='0' else 'MATCHING_OBSERVED_TOTALS',
        'history_complete':False,'net_pnl':None,'source_authenticated':False}


def normalize_orders(rows,*,algo):
    _require(isinstance(rows,list) and len(rows)<=20000,'INVALID_ORDER_ROWS')
    result={};id_key='algoId' if algo else 'orderId'
    for row in rows:
        v={k:row[k] for k in ('symbol',id_key,'side','positionSide')}
        _require(v['symbol']=='XAUUSDT' and v['side'] in ('BUY','SELL'),'ORDER_SCOPE_MISMATCH')
        _side(v['positionSide']);_require(_time(v[id_key])>0,'INVALID_ORDER_ID')
        fields=('algoStatus','orderType') if algo else ('status','type')
        for k in fields:v[k]=_text(row[k])
        states=('NEW','CANCELED','TRIGGERING','TRIGGERED','FINISHED','REJECTED','EXPIRED') if algo else (
            'NEW','PARTIALLY_FILLED','FILLED','CANCELED','PENDING_CANCEL','REJECTED','EXPIRED','EXPIRED_IN_MATCH')
        _require(v[fields[0]] in states,'INVALID_ORDER_STATUS')
        for k in (('quantity','price','triggerPrice') if algo else ('origQty','executedQty','price')):
            v[k]=_text_amount(_amount(row[k]));_require(Decimal(v[k])>=0,'NEGATIVE_ORDER_AMOUNT')
        for k in (('createTime','updateTime') if algo else ('time','updateTime')):v[k]=_time(row[k])
        if algo and 'actualOrderId' in row:
            value=row['actualOrderId']
            _require((isinstance(value,str) and (value=='' or re.fullmatch('[0-9]{1,20}',value))) or
                     (type(value) is int and 0<=value<2**63),'INVALID_ACTUAL_ORDER_ID')
            v['actualOrderId']=str(value)
        if not algo:_require(Decimal(v['executedQty'])<=Decimal(v['origQty']),'OVERFILLED_ORDER')
        _require(v[id_key] not in result or result[v[id_key]]==v,'CONFLICTING_ORDER_ID')
        result[v[id_key]]=v
    return sorted(result.values(),key=lambda r:r[id_key])
