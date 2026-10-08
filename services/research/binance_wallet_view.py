"""Separate stablecoin wallet observations; no trading or FX valuation."""
from decimal import Decimal,localcontext
from services.research.gold_trade_observation import _amount,_text_amount,_require,_time

ACCOUNTS=('futures','spot','funding')
ASSETS=('USDT','USDC')
FIELDS=('wallet','available','locked','frozen','withdrawing','unrealized','margin_balance','margin_used')


def normalize_wallet(kind,raw):
    _require(kind in ACCOUNTS,'INVALID_WALLET_KIND')
    rows=raw['assets'] if kind=='futures' else raw['balances'] if kind=='spot' else raw
    _require(isinstance(rows,list) and len(rows)<=10000,'INVALID_WALLET_ROWS')
    by_asset={}
    for row in rows:
        _require(isinstance(row,dict) and isinstance(row.get('asset'),str),'INVALID_WALLET_ROW')
        asset=row['asset']
        if asset not in ASSETS:continue
        _require(asset not in by_asset,'DUPLICATE_WALLET_ASSET')
        values={f:None for f in FIELDS}
        with localcontext() as ctx:
            ctx.prec=64
            if kind=='futures':
                for target,source in [('wallet','walletBalance'),('available','availableBalance'),('unrealized','unrealizedProfit'),('margin_balance','marginBalance')]:
                    values[target]=_text_amount(_amount(row[source]))
                if 'initialMargin' in row:values['margin_used']=_text_amount(_amount(row['initialMargin']))
            else:
                for target,source in [('available','free'),('locked','locked')]+([('frozen','freeze'),('withdrawing','withdrawing')] if kind=='funding' else []):
                    amount=_amount(row[source]);_require(amount>=0,'NEGATIVE_WALLET_AMOUNT');values[target]=_text_amount(amount)
                values['wallet']=_text_amount(sum((Decimal(values[f] or '0') for f in ('available','locked','frozen','withdrawing')),Decimal(0)))
        by_asset[asset]={'asset':asset,**values}
    # Sparse spot/funding balances omit zero assets. Missing futures margin
    # assets stay unknown: their availability cannot be inferred from a zero.
    return [by_asset.get(asset,{'asset':asset,**{f:('0' if kind!='futures' and f in ('wallet','available','locked','frozen','withdrawing') else None) for f in FIELDS}}) for asset in ASSETS]


def project_wallets(value,*,now_ms):
    _require(value['schema']=='BINANCE_WALLET_OBSERVATIONS_V1' and value['submission_enabled'] is False,'INVALID_WALLETS')
    at=_time(value['observed_at_ms']);_require(at<=now_ms,'FUTURE_WALLETS')
    _require(set(value['accounts'])==set(ACCOUNTS),'INVALID_WALLET_SCOPE')
    accounts={};totals={a:Decimal(0) for a in ASSETS};complete=True
    for kind in ACCOUNTS:
        v=value['accounts'][kind];t=_time(v['observed_at_ms']);_require(t<=at,'FUTURE_WALLET')
        _require(v['status'] in ('OBSERVED','UNAVAILABLE'),'INVALID_WALLET_STATUS')
        status='STALE' if now_ms-t>180000 else v['status']
        rows=[]
        if v['status']=='OBSERVED':
            _require(isinstance(v['balances'],list) and len(v['balances'])==2 and {r['asset'] for r in v['balances']}==set(ASSETS),'INVALID_WALLET_BALANCES')
            for row in v['balances']:
                safe={'asset':row['asset']}
                for field in FIELDS:safe[field]=None if row[field] is None else _text_amount(_amount(row[field]))
                rows.append(safe)
        if status!='OBSERVED':complete=False
        for asset in ASSETS:
            amount=next((r['wallet'] for r in rows if r['asset']==asset),None)
            if status!='OBSERVED' or amount is None:totals[asset]=None
            elif totals[asset] is not None:
                with localcontext() as ctx:ctx.prec=64;totals[asset]+=Decimal(amount)
        accounts[kind]={'status':status,'observed_at_ms':t,'balances':rows}
    return dict(status='STALE' if now_ms-at>180000 else 'OBSERVED' if complete else 'PARTIAL',
                observed_at_ms=at,accounts=accounts,totals={a:None if v is None else _text_amount(v) for a,v in totals.items()},
                atomic_snapshot_verified=False,submission_enabled=False)
