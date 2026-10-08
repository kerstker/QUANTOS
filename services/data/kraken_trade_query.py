"""Match individual queried spot trades to order totals; not a cash ledger."""
from decimal import Context, Decimal, localcontext
import hashlib

from services.data.kraken_account import payload, number, canonical
from services.data.kraken_order_query import identifiers
from services.data.kraken_public import require


def assess(reads, *, orders, as_of_ms):
    require(orders.get('schema')=='KRAKEN_ORDER_QUERY_V1' and orders.get('quantity_basis')=='base' and
            orders.get('authoritative') is False and orders.get('submission_enabled') is False,'KRAKEN_TRADES_ORDER_SCOPE')
    require(type(as_of_ms) is int and type(orders['observed_at_ms']) is int and
            0<=as_of_ms-orders['observed_at_ms']<=120000,'KRAKEN_TRADES_ORDER_TIME')
    by_order={o['order_id']:o for o in orders['orders']};expected={}
    for oid,order in by_order.items():
        for tid in order['trade_ids']:
            require(tid not in expected,'KRAKEN_TRADE_SHARED_ID');expected[tid]=oid
    require(isinstance(reads,list) and len(reads)<=50 and len(expected)<=1000,'KRAKEN_TRADES_LIMIT')
    found={};hashes=[];previous=orders['observed_at_ms']
    with localcontext(Context(prec=160)):
        for read in reads:
            params=read['params']
            require(read['endpoint']=='QueryTrades' and set(params)=={'txid','trades','rebase_multiplier'} and
                    params['trades']=='false' and params['rebase_multiplier']=='base','KRAKEN_TRADES_SCOPE')
            ids=identifiers(params['txid']);require(len(ids)<=20,'KRAKEN_TRADES_BATCH_LIMIT')
            require(read['account_ref_sha256']==orders['account_ref_sha256'],'KRAKEN_TRADES_ACCOUNT')
            sent,received=read['requested_at_ms'],read['received_at_ms']
            require(type(sent) is int and type(received) is int and previous<=sent<=received<=as_of_ms and
                    received-sent<=30000,'KRAKEN_TRADES_TIME');previous=received
            rows=payload(read['body']);require(set(rows)==set(ids),'KRAKEN_TRADES_MISSING')
            hashes.append(hashlib.sha256(read['body'].encode()).hexdigest())
            for tid,row in rows.items():
                require(tid in expected and tid not in found,'KRAKEN_TRADES_UNEXPECTED_OR_DUPLICATE')
                order=by_order[expected[tid]]
                require(row['ordertxid']==order['order_id'] and row['pair']==order['pair'] and
                        row['type']==order['side'] and row['ordertype']==order['order_type'],'KRAKEN_TRADE_IDENTITY')
                require(number(row['margin'])==0,'KRAKEN_TRADE_MARGIN')
                ts=number(row['time']);require(0<ts*1000<=received,'KRAKEN_TRADE_FUTURE')
                vol=number(row['vol']);price=number(row['price']);require(vol>0 and price>0,'KRAKEN_TRADE_SIZE')
                found[tid]={'trade_id':tid,'order_id':order['order_id'],'pair':row['pair'],'side':row['type'],
                    'order_type':row['ordertype'],'time_seconds':canonical(ts),'volume':canonical(vol),
                    'price':canonical(price),'cost':canonical(number(row['cost'])),'fee':canonical(number(row['fee']))}
        require(set(found)==set(expected),'KRAKEN_TRADES_INCOMPLETE')
        for order in orders['orders']:
            for source,target in (('volume','filled'),('cost','cumulative_cost'),('fee','cumulative_fee')):
                total=sum((number(found[t][source]) for t in order['trade_ids']),Decimal(0))
                require(total==number(order[target]),'KRAKEN_TRADE_TOTAL_MISMATCH')
    return {'schema':'KRAKEN_TRADE_RECONCILIATION_V1','status':'TRADE_TOTALS_MATCHED',
            'account_ref_sha256':orders['account_ref_sha256'],'as_of_ms':as_of_ms,'quantity_basis':'base',
            'trades':[found[t] for t in sorted(found)],'source_body_hashes':hashes,'fee_asset_status':'UNKNOWN',
            'cash_reconciled':False,'submission_enabled':False,'authoritative':False}
