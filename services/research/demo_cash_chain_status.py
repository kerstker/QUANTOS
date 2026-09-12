"""Compact unverified view of a canonical multi-batch cash comparison."""
from decimal import Decimal
import re

from services.research import demo_display_store
from services.research.ai_factory_store import digest

SCHEMA = 'DEMO_CASH_CHAIN_STATUS_V1'
AMOUNTS = ('initial_equity','canonical_wallet','reported_wallet','wallet_residual','quantity','entry_notional','fee','funding','external_cash_flow')
HASHES = ('report_sha256','cash_capture_sha256','replay_source_sha256','projection_source_sha256')
FLAGS = ('source_verified','coverage_verified','authoritative','submission_enabled','cash_reconciled')


def validate(raw):
    fields = ('schema environment scope phase observed_at_ms account_ref_sha256 symbol quote_asset batch_count '
        'continuations_matched issues ledger_state_hash strategy_pnl batches '+ ' '.join(AMOUNTS+HASHES+FLAGS)).split()
    if type(raw) is not dict or set(raw) != set(fields):raise ValueError('CASH_CHAIN_STATUS_FIELDS')
    if (raw['schema'] != SCHEMA or raw['environment'] != 'BINANCE_USDM_DEMO' or raw['scope'] != 'TECHNICAL_VALIDATION_ONLY' or
        raw['phase'] not in ('UNRESOLVED','MATCHED_REPORTED_INPUTS','DIFFERENCE_FOUND')):raise ValueError('CASH_CHAIN_STATUS_SCHEMA')
    if any(raw[k] is not False for k in FLAGS) or raw['strategy_pnl'] is not None:raise ValueError('CASH_CHAIN_STATUS_AUTHORITY')
    if type(raw['observed_at_ms']) is not int or not 0 < raw['observed_at_ms'] < 2**53:raise ValueError('CASH_CHAIN_STATUS_TIME')
    for key in HASHES:
        demo_display_store._hash(raw[key])
    for key in ('ledger_state_hash','account_ref_sha256'):
        if raw[key] is not None:demo_display_store._hash(raw[key])
    if not (raw['symbol'] is None and raw['quote_asset'] is None) and (
        raw['quote_asset'] not in ('USDT','USDC') or raw['symbol'] != 'BTC'+raw['quote_asset']):raise ValueError('CASH_CHAIN_STATUS_QUOTE')
    for k in AMOUNTS:
        if raw[k] is not None and (type(raw[k]) is not str or re.fullmatch(r'-?\d{1,35}(\.\d{1,24})?',raw[k]) is None):
            raise ValueError('CASH_CHAIN_STATUS_DECIMAL')
    for k in ('batch_count','continuations_matched'):
        if type(raw[k]) is not int or not 0 <= raw[k] <= 32:raise ValueError('CASH_CHAIN_STATUS_COUNT')
    if raw['continuations_matched'] > max(0,raw['batch_count']-1):raise ValueError('CASH_CHAIN_STATUS_CONTINUATIONS')
    issues = raw['issues']
    if type(issues) is not list or len(issues) > 100 or len(set(issues)) != len(issues) or any(
        type(i) is not str or re.fullmatch('[A-Z][A-Z0-9_]{0,99}',i) is None for i in issues):raise ValueError('CASH_CHAIN_STATUS_ISSUES')
    batches = raw['batches']
    if type(batches) is not list or len(batches) != raw['batch_count']:raise ValueError('CASH_CHAIN_STATUS_BATCHES')
    previous = -1
    for b in batches:
        if type(b) is not dict or set(b) != set('manifest_hash journal_tail orders_count trades_count start_ms end_ms'.split()):raise ValueError('CASH_CHAIN_STATUS_BATCH_FIELDS')
        demo_display_store._hash(b['manifest_hash']);demo_display_store._hash(b['journal_tail'])
        if any(type(b[k]) is not int for k in ('orders_count','trades_count','start_ms','end_ms')) or not (
            1 <= b['orders_count'] <= 200 and 0 <= b['trades_count'] <= 10000 and
            0 <= b['start_ms'] < b['end_ms'] <= raw['observed_at_ms'] and 2 <= b['end_ms']-b['start_ms'] <= 150000):
            raise ValueError('CASH_CHAIN_STATUS_BATCH_RANGE')
        # An unresolved report can retain reversed/gapped declared inputs.
        if raw['phase'] != 'UNRESOLVED' and b['start_ms'] <= previous:raise ValueError('CASH_CHAIN_STATUS_BATCH_ORDER')
        previous=b['start_ms']
    known = ('initial_equity','canonical_wallet','wallet_residual','quantity','entry_notional','fee','funding','external_cash_flow')
    if raw['phase'] == 'UNRESOLVED':
        if not issues or raw['ledger_state_hash'] is not None or any(raw[k] is not None for k in known):raise ValueError('CASH_CHAIN_STATUS_UNKNOWN')
    else:
        if not raw['batch_count'] or raw['continuations_matched'] != raw['batch_count']-1 or any(raw[k] is None for k in AMOUNTS) or raw['ledger_state_hash'] is None:
            raise ValueError('CASH_CHAIN_STATUS_INCOMPLETE')
        if raw['account_ref_sha256'] is None or raw['symbol'] is None:raise ValueError('CASH_CHAIN_STATUS_IDENTITY')
        if Decimal(raw['entry_notional']) < 0 or Decimal(raw['fee']) < 0:raise ValueError('CASH_CHAIN_STATUS_COST')
        if raw['phase'] == 'MATCHED_REPORTED_INPUTS' and (issues or Decimal(raw['wallet_residual']) != 0 or
            Decimal(raw['canonical_wallet']) != Decimal(raw['reported_wallet'])):raise ValueError('CASH_CHAIN_STATUS_FALSE_MATCH')
        if raw['phase'] == 'DIFFERENCE_FOUND' and not issues:raise ValueError('CASH_CHAIN_STATUS_DIFFERENCE')
    return raw


def build_status(report):
    rec = report['reconciliation']; positions = report['positions']
    position = positions.get(report['symbol']) if positions is not None else None
    return validate(dict(schema=SCHEMA,environment=report['environment'],scope=report['scope'],phase=report['status'],
        observed_at_ms=report['observed_at_ms'],account_ref_sha256=report['account_ref_sha256'],symbol=report['symbol'],quote_asset=report['quote_asset'],
        batch_count=len(report['batches']),continuations_matched=len(report['continuations']),issues=list(report['issues']),
        ledger_state_hash=report['ledger_state_hash'],strategy_pnl=None,
        **{k:False for k in FLAGS},report_sha256=digest(report),**{k:report[k] for k in HASHES if k!='report_sha256'},
        **{k:report[k] for k in ('canonical_wallet','reported_wallet','wallet_residual')},
        **{k:rec[k] if rec is not None else None for k in ('initial_equity','fee','funding','external_cash_flow')},
        quantity=position['quantity'] if position else '0' if positions is not None else None,
        entry_notional=position['entry_notional'] if position else '0' if positions is not None else None,
        batches=[dict(manifest_hash=b['manifest_hash'],journal_tail=b['journal_tail'],orders_count=b['orders_count'],trades_count=b['trades_count'],
                      start_ms=b['coverage']['start_ms'],end_ms=b['coverage']['end_ms']) for b in report['batches']]))


def save(directory,raw):return demo_display_store.save(directory,raw,validator=validate)
def load_latest(directory):return demo_display_store.load_latest(directory,validator=validate)
