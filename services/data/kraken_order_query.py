"""Exact, explicit known-order observations; cumulative totals are not fill events."""
import hashlib
import re
from decimal import Context, localcontext

from services.data.kraken_account import payload, number, canonical
from services.data.kraken_order_state import state, assert_progress
from services.data.kraken_public import require


def identifiers(value):
    require(isinstance(value,str),'KRAKEN_QUERY_IDS')
    ids=value.split(',')
    require(1<=len(ids)<=50 and len(set(ids))==len(ids) and
            all(re.fullmatch(r'[A-Za-z0-9-]{1,80}',v) for v in ids),'KRAKEN_QUERY_IDS')
    return ids


def assess(read, *, as_of_ms):
    p=read['params']
    require(read['endpoint']=='QueryOrders' and set(p)=={'txid','trades','rebase_multiplier'} and
            p['trades']=='true' and p['rebase_multiplier']=='base','KRAKEN_QUERY_SCOPE')
    ids=identifiers(p['txid']);ref=read['account_ref_sha256']
    require(isinstance(ref,str) and re.fullmatch('[a-f0-9]{64}',ref),'KRAKEN_QUERY_ACCOUNT')
    sent,received=read['requested_at_ms'],read['received_at_ms']
    require(all(type(v) is int for v in (sent,received,as_of_ms)) and 0<sent<=received<=as_of_ms and
            received-sent<=30000 and as_of_ms-sent<=120000,'KRAKEN_QUERY_TIME')
    rows=payload(read['body']);require(set(rows)==set(ids),'KRAKEN_QUERY_MISSING_ORDER')
    orders=[]
    with localcontext(Context(prec=100)):
        for oid in sorted(ids):
            row=rows[oid];d=row['descr'];volume=number(row['vol']);filled=number(row['vol_exec'])
            canonical_state=state(row['status'],volume,filled)
            require(isinstance(d['pair'],str) and re.fullmatch(r'[A-Za-z0-9./]{1,48}',d['pair']) and
                    d['type'] in ('buy','sell') and d['leverage']=='none','KRAKEN_QUERY_SPOT_IDENTITY')
            require(isinstance(d['ordertype'],str) and re.fullmatch('[a-z-]{1,40}',d['ordertype']),'KRAKEN_QUERY_ORDER_TYPE')
            trades=row.get('trades',[])
            require(isinstance(trades,list) and len(trades)<=10000 and all(isinstance(t,str) and
                    re.fullmatch(r'[A-Za-z0-9-]{1,80}',t) for t in trades) and len(set(trades))==len(trades),
                    'KRAKEN_QUERY_TRADE_IDS')
            require(filled==0 or len(trades)>0,'KRAKEN_QUERY_FILL_EVIDENCE_MISSING')
            cost=number(row['cost']);fee=number(row['fee'])
            require(filled>0 or (cost==0 and fee==0 and not trades),'KRAKEN_QUERY_ZERO_FILL_TOTALS')
            orders.append({'order_id':oid,'pair':d['pair'],'side':d['type'],'order_type':d['ordertype'],
                'status':row['status'],'canonical_state':canonical_state.value,'volume':canonical(volume),
                'filled':canonical(filled),'remaining':canonical(volume-filled),'cumulative_cost':canonical(cost),
                'cumulative_fee':canonical(fee),'fee_asset_status':'UNKNOWN','trade_ids':sorted(trades)})
    return {'schema':'KRAKEN_ORDER_QUERY_V1','account_ref_sha256':ref,'observed_at_ms':received,
        'quantity_basis':'base','orders':orders,'source_body_sha256':hashlib.sha256(read['body'].encode()).hexdigest(),
        'cash_reconciled':False,'submission_enabled':False,'authoritative':False}


def check_progress(previous, current):
    require(previous['account_ref_sha256']==current['account_ref_sha256'] and
            current['observed_at_ms']>=previous['observed_at_ms'],'KRAKEN_QUERY_IDENTITY_TIME_CHANGED')
    old={r['order_id']:r for r in previous['orders']};new={r['order_id']:r for r in current['orders']}
    require(set(old)<=set(new),'KRAKEN_QUERY_TRACKED_ORDER_MISSING')
    for oid,before in old.items():
        after=new[oid]
        require(all(before[k]==after[k] for k in ('pair','side','order_type','volume')),'KRAKEN_QUERY_AMENDMENT_UNRESOLVED')
        assert_progress(before['status'],after['status'],number(before['volume']),number(before['filled']),number(after['filled']))
        require(set(before['trade_ids'])<=set(after['trade_ids']),'KRAKEN_QUERY_TRADE_REGRESSION')
        require(number(after['cumulative_cost'])>=number(before['cumulative_cost']) and
                number(after['cumulative_fee'])>=number(before['cumulative_fee']),'KRAKEN_QUERY_TOTAL_REGRESSION')
        if before['status'] in ('closed','canceled','expired'):
            require(before==after,'KRAKEN_QUERY_TERMINAL_CHANGED')
