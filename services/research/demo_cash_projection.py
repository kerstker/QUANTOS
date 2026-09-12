"""Compare bounded reported Demo cash with the existing canonical V2 ledger.

No private reads, second cash ledger, submission, or claimed venue completeness.
Matching declared/imported inputs remains technical evidence only.
"""
from decimal import Decimal
import hashlib
from pathlib import Path

from services.execution.perpetual_ledger_v2 import (
    ReportedCashFlowEvent, canonical_decimal, ledger_decimal_context,
)
from services.research.ai_factory_store import digest
from services.research.demo_cash_window import assess, _sweep
from services.research.demo_execution_journal import observations, replay_ledger, replay_report

SCHEMA = 'DEMO_CANONICAL_CASH_COMPARISON_V1'
PROJECTION_SOURCE_SHA256 = hashlib.sha256(b'\n'.join(
    name.encode()+b':'+hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest().encode()
    for name in ('demo_cash_projection.py','demo_cash_window.py','demo_statement.py'))).hexdigest()


@ledger_decimal_context()
def project_cash_window(journal, capture):
    cash = assess(capture)
    base = replay_report(journal)
    manifest = journal.manifest
    spec = journal.parsed[0]
    quote = spec.margin_asset
    result = {'schema': SCHEMA, 'environment': 'BINANCE_USDM_DEMO', 'scope': 'TECHNICAL_VALIDATION_ONLY',
        'status': 'UNRESOLVED', 'account_ref_sha256': manifest['native']['account_ref_sha256'],
        'symbol': spec.instrument_id, 'quote_asset': quote, 'manifest_hash': journal.manifest_hash,
        'journal_tail': base['journal_tail'], 'cash_capture_sha256': digest(capture),
        'replay_source_sha256': base['replay_source_sha256'],
        'projection_source_sha256': PROJECTION_SOURCE_SHA256,
        'cash_input_status': cash['status'], 'cash_input_duplicates': cash['duplicate_rows'],
        'observed_at_ms': capture['completed_at_ms'], 'issues': [],
        'canonical_wallet': None, 'reported_wallet': None, 'wallet_residual': None,
        'fee_comparison': 'UNKNOWN', 'realized_pnl_comparison': 'UNKNOWN', 'reconciliation': None,
        'ledger_state_hash': None, 'source_verified': False, 'coverage_verified': False,
        'authoritative': False, 'submission_enabled': False, 'strategy_pnl': None,
        'limitations': ['IMPORTED_SOURCE_UNVERIFIED', 'NONATOMIC_WALLET_OBSERVATIONS',
                        'DECLARED_STREAM_COVERAGE_ONLY', 'FLAT_SINGLE_QUOTE_START_ONLY',
                        'VALUATION_MARKS_ARE_INPUTS_NOT_TERMINAL_VENUE_MARKS']}
    issues = result['issues']
    if capture['account_ref_sha256'] != result['account_ref_sha256']:
        issues.append('ACCOUNT_NAMESPACE_MISMATCH')
    if cash['issues']:
        issues.extend('CASH_'+i for i in cash['issues'])
    if base['projection_status'] != 'REPLAYED_TECHNICAL_ONLY':
        issues.append('RECEIPT_PROJECTION_UNRESOLVED')
    if base['position_comparison'] == 'DECLARED_POSITION_MISMATCH':
        issues.append('DECLARED_POSITION_MISMATCH')
    opening = base['opening']
    if Decimal(manifest['initial_position']) != 0 or opening is not None and opening['positions_count'] != 0:
        issues.append('FLAT_SINGLE_QUOTE_START_REQUIRED')
    if any(o['state'] not in ('FILLED', 'CANCELLED', 'EXPIRED') for o in base['orders']):
        issues.append('ORDERS_NOT_TERMINAL')
    wallet = next((a for a in cash['assets'] if a['asset'] == quote), None)
    if wallet is None or wallet['opening_wallet'] is None or wallet['closing_wallet'] is None:
        issues.append('QUOTE_WALLET_MISSING')
    else:
        result['reported_wallet'] = wallet['closing_wallet']
        if Decimal(wallet['opening_wallet']) != Decimal(manifest['initial_collateral']):
            issues.append('OPENING_WALLET_MISMATCH')
    bounds = cash['bounds']; window = manifest['native']['coverage']
    # A bound flat account origin can lie inside the initial cash read guard.
    # Its inventory is the separate starting fact; replay begins strictly after
    # it. Any reported flow at/before that origin is rejected below, and the
    # initial wallet must match. Do not manufacture receipts before the origin.
    required_start = None if bounds is None else max(bounds['query_start_ms'],
        opening['opening_at_ms']+1 if opening is not None else bounds['query_start_ms'])
    if bounds is None or not (window['start_ms'] <= required_start and window['end_ms'] >= bounds['query_end_ms']):
        issues.append('RECEIPT_WINDOW_DOES_NOT_COVER_CASH')
    # No initial position/unknown old flows may be carried into this bounded
    # flat projection. Every planned action must follow the opening observation.
    if bounds and any(not bounds['inner_start_ms'] < a['timestamp'] < bounds['inner_end_ms'] for a in manifest['actions']):
        issues.append('ORDER_OUTSIDE_CASH_INTERIOR')
    records, tail, _ = journal.read()
    if tail != base['journal_tail']:
        issues.append('RECEIPT_SNAPSHOT_CHANGED')
    evidence = observations(manifest, records, journal.parsed)['native_observations']
    trades = evidence['trade_rows']
    if bounds and any(not bounds['inner_start_ms'] < t['venue_trade_ms'] < bounds['inner_end_ms'] for t in trades):
        issues.append('TRADE_OUTSIDE_CASH_INTERIOR')
    rows, _, _ = _sweep([r for r in capture['reads'] if r['kind']=='income' and r['sweep']==2])
    flows = [r for r in rows.values() if r['asset']==quote]
    if any(r['incomeType'] not in ('COMMISSION','REALIZED_PNL','FUNDING_FEE','TRANSFER') for r in flows):
        issues.append('UNSUPPORTED_INCOME_TYPE')
    if any(r['incomeType'] != 'TRANSFER' and r['symbol'] != spec.instrument_id for r in flows):
        issues.append('UNBOUND_SAME_QUOTE_SYMBOL')
    if opening is not None and any(r['time'] <= opening['opening_at_ms'] for r in flows):
        issues.append('CASH_BEFORE_BOUND_OPENING')
    by_trade = {t['trade_id']: t for t in trades}
    if len(by_trade) != len(trades):
        issues.append('TRADE_ID_CONFLICT')
    fees = [r for r in flows if r['incomeType']=='COMMISSION']
    fee_ids = [r['tradeId'] for r in fees]
    if len(set(fee_ids)) != len(fee_ids) or set(fee_ids) != set(by_trade):
        issues.append('COMMISSION_TRADE_SCOPE_MISMATCH')
    elif any(by_trade[r['tradeId']]['reported_commission'] is None or
             Decimal(r['income']) != -Decimal(by_trade[r['tradeId']]['reported_commission']) for r in fees):
        issues.append('REPORTED_COMMISSION_DIFFERENCE')
    else:
        result['fee_comparison'] = 'MATCHED_REPORTED_TRADES' if trades else 'NO_REPORTED_TRADES'
    pnl_rows = [r for r in flows if r['incomeType']=='REALIZED_PNL']
    if any(r['tradeId'] not in by_trade for r in pnl_rows) or len({r['tradeId'] for r in pnl_rows}) != len(pnl_rows):
        issues.append('REALIZED_PNL_TRADE_SCOPE_MISMATCH')
    if issues:
        result['issues'] = sorted(set(issues))
        return result
    try:
        events = [ReportedCashFlowEvent(timestamp=r['time'], account_ref_sha256=result['account_ref_sha256'],
            asset=quote, income_type=r['incomeType'], transaction_id=r['tranId'], amount=Decimal(r['income']),
            instrument_id=r['symbol'] if r['incomeType']=='FUNDING_FEE' else None,
            source_sha256=result['cash_capture_sha256']) for r in flows if r['incomeType'] in ('FUNDING_FEE','TRANSFER')]
        ledger = replay_ledger(manifest, journal.parsed, evidence, cash_events=events)
        state = ledger.state_as_canonical_dict(); reconciliation = ledger.reconciliation_snapshot().as_canonical_dict()
        result.update(canonical_wallet=state['wallet_balance'], reconciliation=reconciliation, ledger_state_hash=digest(state))
        # Realized price cash is distinct from mark-to-market price PnL.
        realized = (Decimal(state['wallet_balance'])-Decimal(manifest['initial_collateral'])+
                    Decimal(reconciliation['fee'])-Decimal(reconciliation['funding'])-Decimal(reconciliation['external_cash_flow']))
        reported_realized = sum((Decimal(r['income']) for r in pnl_rows), Decimal(0))
        result['realized_pnl_comparison'] = 'MATCHED_REPORTED_TOTAL' if realized == reported_realized else 'DIFFERENCE_FOUND'
        if realized != reported_realized: issues.append('REPORTED_REALIZED_PNL_DIFFERENCE')
        residual = Decimal(result['reported_wallet'])-Decimal(state['wallet_balance'])
        result['wallet_residual'] = canonical_decimal(residual)
        if residual != 0: issues.append('CANONICAL_WALLET_DIFFERENCE')
        result['status'] = 'DIFFERENCE_FOUND' if issues else 'MATCHED_REPORTED_INPUTS'
    except (ValueError, TypeError, ArithmeticError, RuntimeError) as exc:
        issues.append('CANONICAL_REPLAY_'+type(exc).__name__.upper())
    result['issues'] = sorted(set(issues))
    return result
