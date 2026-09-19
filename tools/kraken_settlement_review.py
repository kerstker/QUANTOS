"""Replay stored private source observations before permitting another review."""
import re
from pathlib import Path

from services.data import kraken_cash_reconciliation as cash_model
from services.data import kraken_order_query as order_model
from services.data import kraken_trade_query as trade_model
from services.data.kraken_settlement import link
from services.data.kraken_public import require
from tools import kraken_inventory_report as inventory
from tools.kraken_public_capture import digest


def archive(root, directory, key):
    require(isinstance(key,str) and re.fullmatch('[a-f0-9]{64}',key),'KRAKEN_SETTLEMENT_ARCHIVE_KEY')
    path=Path(root)/directory/(key+'.json')
    require(path.resolve()==path.absolute(),'KRAKEN_SETTLEMENT_SYMLINK')
    value=inventory.load(path)
    require(digest(value)==key,'KRAKEN_SETTLEMENT_ARCHIVE_HASH')
    return value


def build(root, cash, recovery, specs, *, as_of_ms):
    inventory.sealed(cash);inventory.sealed(recovery)
    require(recovery['status']=='ORDERS_OBSERVED' and recovery.get('trade_status')=='TRADE_TOTALS_MATCHED',
            'KRAKEN_SETTLEMENT_RECOVERY_UNRESOLVED')
    window=archive(root,'cash-windows',cash['evidence_hash'])
    opening=inventory.sealed(window['opening']);closing=inventory.sealed(window['closing'])
    bridge=cash_model.reconcile(opening,closing,window['reads'],as_of_ms=window['report']['as_of_ms'])
    bridge.update(opening_account_hash=opening['result_hash'],closing_account_hash=closing['result_hash'])
    require(bridge==window['report'],'KRAKEN_SETTLEMENT_BALANCE_REPLAY')
    require({k:v for k,v in cash.items() if k not in ('result_hash','evidence_hash')}==bridge,
            'KRAKEN_SETTLEMENT_BALANCE_POINTER')
    order_source=archive(root,'order-queries',recovery['query_hash'])
    orders=order_model.assess(order_source['read'],as_of_ms=order_source['read']['received_at_ms'])
    require(orders==order_source['report'] and orders['account_ref_sha256']==recovery['account_ref_sha256'] and
            sorted(o['order_id'] for o in orders['orders'])==recovery['order_ids'],'KRAKEN_SETTLEMENT_ORDER_REPLAY')
    trade_source=archive(root,'trade-queries',recovery['trade_query_hash'])
    fills=trade_model.assess(trade_source['reads'],orders=orders,as_of_ms=trade_source['report']['as_of_ms'])
    require(fills==trade_source['report'],'KRAKEN_SETTLEMENT_TRADE_REPLAY')
    result=link(bridge,orders,fills,specs,as_of_ms=as_of_ms)
    result.update(cash_source_hash=cash['result_hash'],recovery_source_hash=recovery['result_hash'],
                  closing_account_hash=closing['result_hash'])
    result['result_hash']=digest(result)
    return result
