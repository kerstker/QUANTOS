"""Replay a complete observed batch settlement against its local debit claim."""
from decimal import Context, Decimal, localcontext

from services.data.kraken_account import canonical, number
from services.data.kraken_order_identity import match
from services.data.kraken_public import require
from tools.kraken_inventory_report import sealed
from tools.kraken_public_capture import validate
from tools.kraken_settlement_review import archive, build, replay_cash


def _check_reservation_snapshot(window, bridge, account):
    opening=window['opening']
    if opening==account:return
    first=account['reads'][0]['requested_at_ms'];last=account['observed_at_ms']
    require(account['account_ref_sha256']==opening['account_ref_sha256'] and
            opening['observed_at_ms']<=first<=last<=window['closing']['reads'][0]['requested_at_ms'],
            'KRAKEN_RESERVED_SETTLEMENT_SNAPSHOT_WINDOW')
    with localcontext(Context(prec=160)):
        balances={asset:number(row['balance'],signed=True) for asset,row in opening['balances'].items()}
        for entry in bridge['ledger_entries']:
            at=number(entry['time_seconds'])*1000
            require(not first<=at<=last,'KRAKEN_RESERVED_SETTLEMENT_SNAPSHOT_ACTIVITY')
            if opening['observed_at_ms']<at<first:
                asset=entry['asset']
                balances[asset]=balances.get(asset,Decimal(0))+number(entry['amount'],signed=True)-number(entry['fee'],signed=True)
        require(all(balances.get(asset,Decimal(0))==number(account['balances'].get(asset,{}).get('balance','0'),signed=True)
                    for asset in set(balances)|set(account['balances'])),
                'KRAKEN_RESERVED_SETTLEMENT_SNAPSHOT_BALANCE')


def prove(root, reservation, proof, *, as_of_ms):
    require(set(proof)=={'cash','recovery','market_captures'},'KRAKEN_RESERVED_SETTLEMENT_FIELDS')
    cash=proof['cash'];recovery=proof['recovery'];raws=proof['market_captures']
    require(isinstance(raws,list) and 1<=len(raws)<=32,'KRAKEN_RESERVED_SETTLEMENT_MARKETS')
    markets=[validate(raw) for raw in raws]
    require(all(raw['status']=='CAPTURED' and 0<=as_of_ms-raw['completed_at_ms']<=120000 for raw in markets),
            'KRAKEN_RESERVED_SETTLEMENT_MARKET_TIME')
    settlement=build(root,cash,recovery,[raw['instrument'] for raw in markets],as_of_ms=as_of_ms)
    require(settlement['next_review_allowed'] is True,'KRAKEN_RESERVED_SETTLEMENT_UNRESOLVED')
    source=archive(root,'order-queries',recovery['query_hash'])
    identity=match(source['read'],reservation['drafts'],[raw['instrument'] for raw in markets],
                   account_ref=reservation['account']['account_ref_sha256'],
                   not_before_ms=reservation['as_of_ms'],as_of_ms=as_of_ms)
    require(identity['status']=='OBSERVED_ORDER_IDENTITIES_MATCHED' and
            {o['order_id'] for o in identity['orders']}<=set(recovery['order_ids']),
            'KRAKEN_RESERVED_SETTLEMENT_ORDER_SET')
    window=archive(root,'cash-windows',cash['evidence_hash'])
    closing=sealed(window['closing'])
    require(closing['status']=='OBSERVED_STABLE' and not closing['issues'] and not closing['open_orders'],
            'KRAKEN_RESERVED_SETTLEMENT_CLOSING')
    bridge=replay_cash(root,cash)
    _check_reservation_snapshot(window,bridge,reservation['account'])
    batch_orders={o['order_id'] for o in identity['orders']}
    ledger_ids={lid for trade in settlement['trades'] if trade['order_id'] in batch_orders for lid in trade['ledger_ids']}
    actual={}
    with localcontext(Context(prec=160)):
        for entry in bridge['ledger_entries']:
            if entry['ledger_id'] not in ledger_ids:continue
            debit=max(Decimal(0),-number(entry['amount'],signed=True))+max(Decimal(0),number(entry['fee'],signed=True))
            if debit:actual[entry['asset']]=actual.get(entry['asset'],Decimal(0))+debit
        bounds=reservation['budget_review']['assets']
        require(all(asset in bounds and debit<=Decimal(bounds[asset]['new_debits']) for asset,debit in actual.items()),
                'KRAKEN_RESERVED_SETTLEMENT_DEBIT_BOUND_EXCEEDED')
    return {'account':closing,'actual_debits':{k:canonical(v) for k,v in sorted(actual.items())},
            'identity_review':identity,'settlement_review':settlement}
