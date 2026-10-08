"""Inventory comparison and durable capture storage; no order/authority writes."""
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from quantos.contracts.binance_demo_account import DemoAccountCapture, normalize_account_read

normalize_response=normalize_account_read
MAX_CAPTURE_BYTES=4_000_000


def _canonical(value):
    return format(value,'f').rstrip('0').rstrip('.') if '.' in format(value,'f') else format(value,'f')


def _inventory(rows):
    return {(r['symbol'],r['positionSide']):Decimal(r['positionAmt']) for r in rows if Decimal(r['positionAmt'])!=0}


def build_baseline(capture: DemoAccountCapture, *, as_of_ms: int):
    status='NOT_REQUESTED' if capture.status=='NOT_REQUESTED' else 'CAPTURE_FAILED'
    data={r.kind:r.data for r in capture.reads}
    assets=[]; positions=[]; issues=[]; orders=[]; comparisons=[]; stable=False; count=None
    config=data.get('config_after')
    if capture.status=='CAPTURED':
        before,after=data['account_before'],data['account_after']
        count=len(_inventory(after['positions']))
        wallet=lambda account:{r['asset']:(Decimal(r['walletBalance']),Decimal(r['crossWalletBalance'])) for r in account['assets']}
        comparisons = [{'kind': kind, 'status': 'MATCHED_OBSERVATIONS' if same else 'DIFFERENT'} for kind, same in (
            ('wallet', wallet(before)==wallet(after)),
            ('positions', _inventory(before['positions'])==_inventory(after['positions'])==_inventory(data['positions'])),
            ('account_config', data['config_before']==config),
            ('symbol_config', data['symbols_before']==data['symbols_after']))]
        stable = all(c['status']=='MATCHED_OBSERVATIONS' for c in comparisons)
        status='STABLE_OBSERVATIONS' if stable else 'CHANGED_DURING_CAPTURE'
        if not stable: issues.append('ACCOUNT_CHANGED_DURING_CAPTURE')
        if config['dualSidePosition']: issues.append('HEDGE_MODE_UNSUPPORTED_BY_SINGLE_POSITION_LEDGER')
        if config['multiAssetsMargin']: issues.append('MULTI_ASSET_MARGIN_REQUIRES_COLLATERAL_MODEL')
        if not config['canTrade']: issues.append('VENUE_ACCOUNT_TRADING_DISABLED')
        symbols={r['symbol']:r for r in data['symbols_after']}
        asset_names={r['asset'] for r in after['assets']}
        with localcontext() as ctx:
            ctx.prec=100
            for r in data['positions']:
                qty=Decimal(r['positionAmt'])
                if qty==0: continue
                price,mark=Decimal(r['entryPrice']),Decimal(r['markPrice'])
                # This estimate uses the observed display entry price, not an
                # authenticated exact historic cost basis. Never erase residual.
                estimated=abs(qty)*price
                residual=qty*(mark-price)-Decimal(r['unRealizedProfit'])
                symbol_config=symbols.get(r['symbol'])
                if symbol_config is None: issues.append('POSITION_CONFIGURATION_MISSING')
                if r['marginAsset'] not in asset_names: issues.append('POSITION_MARGIN_ASSET_MISSING')
                if r['marginAsset'] not in ('USDT','USDC') or not r['symbol'].endswith(r['marginAsset']):
                    issues.append('POSITION_CONTRACT_REQUIRES_REVIEW')
                positions.append({'symbol':r['symbol'],'position_side':r['positionSide'],'margin_asset':r['marginAsset'],
                    'quantity':r['positionAmt'],'observed_entry_price':r['entryPrice'],'observed_mark_price':r['markPrice'],
                    'observed_unrealized':r['unRealizedProfit'],'estimated_entry_notional':_canonical(estimated),
                    'basis_pnl_residual':_canonical(residual),'isolated_wallet':r['isolatedWallet'],
                    'margin_mode':symbol_config['marginType'] if symbol_config else None,
                    'leverage':symbol_config['leverage'] if symbol_config else None,
                    'ownership':'PREEXISTING_ACCOUNT_POSITION'})
            for r in after['assets']:
                assets.append({'asset':r['asset'],'observed_wallet':r['walletBalance'],'observed_cross_wallet':r['crossWalletBalance'],
                    'observed_unrealized':r['unrealizedProfit'],'observed_margin_balance':r['marginBalance'],
                    'observed_available':r['availableBalance'],
                    'account_identity_residual':_canonical(Decimal(r['walletBalance'])+Decimal(r['unrealizedProfit'])-Decimal(r['marginBalance']))})
        orders=data['orders']
        if orders: issues.append('PREEXISTING_ORDERS_REQUIRE_OWNERSHIP_ISOLATION')
        if positions: issues.append('ENTRY_BASIS_UNVERIFIED')
        issues.append('REST_OBSERVATIONS_NOT_ATOMIC')
    if capture.completed_at_ms>as_of_ms: status='DATA_BLOCKED'; assets=[]; positions=[]; orders=[]
    elif as_of_ms-capture.observed_at_ms>120000: status='STALE'
    return {'schema':'DEMO_ACCOUNT_BASELINE_V1','environment':'BINANCE_USDM_DEMO',
        'capture_hash':hashlib.sha256(capture.model_dump_json().encode()).hexdigest(),
        'source_sha256':capture.source_sha256,'observed_at_ms':capture.observed_at_ms,'completed_at_ms':capture.completed_at_ms,
        'inventory_status':status,'capture_reason':capture.reason,'issues':sorted(set(issues)),
        'nonzero_positions':count if status!='DATA_BLOCKED' else None,
        'flat_account':count==0 if stable and status!='DATA_BLOCKED' else None,
        'account_config':config if status!='DATA_BLOCKED' else None,'assets':assets,'positions':positions,'open_orders':orders,
        'comparisons':comparisons if status!='DATA_BLOCKED' else [],
        'atomic_snapshot_verified':False,'new_strategy_pnl':None,'ledger_reconciliation':'UNKNOWN','submission_enabled':False,
        'authoritative':False,'read_intervals':[{'kind':r.kind,'started_at_ms':r.started_at_ms,
            'received_at_ms':r.received_at_ms,'response_sha256':r.response_sha256} for r in capture.reads]}


def save_capture(directory: Path, capture: DemoAccountCapture):
    directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    raw=(capture.model_dump_json(indent=2)+'\n').encode()
    if len(raw)>MAX_CAPTURE_BYTES: raise ValueError('capture too large')
    digest=hashlib.sha256(raw).hexdigest(); target=directory/(digest+'.json')
    try:
        with target.open('xb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    except FileExistsError:
        if target.is_symlink() or target.read_bytes()!=raw: raise ValueError('capture content collision') from None
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=directory,delete=False) as f:
            temporary=Path(f.name); json.dump({'sha256':digest},f); f.flush(); os.fsync(f.fileno())
        os.replace(temporary,directory/'latest.json')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def load_capture(directory: Path):
    path=directory/'latest.json'
    if path.is_symlink(): raise ValueError('capture pointer symlink')
    with path.open('rb') as f: raw=f.read(1025)
    if len(raw)>1024: raise ValueError('capture pointer too large')
    pointer=json.loads(raw)
    if (not isinstance(pointer,dict) or set(pointer)!={'sha256'} or not isinstance(pointer['sha256'],str)
            or not re.fullmatch('[a-f0-9]{64}',pointer['sha256'])): raise ValueError('invalid capture pointer')
    path=directory/(pointer['sha256']+'.json')
    if path.is_symlink(): raise ValueError('capture source symlink')
    with path.open('rb') as f: raw=f.read(MAX_CAPTURE_BYTES+1)
    if len(raw)>MAX_CAPTURE_BYTES or hashlib.sha256(raw).hexdigest()!=pointer['sha256']: raise ValueError('capture digest mismatch')
    return DemoAccountCapture.model_validate_json(raw)
