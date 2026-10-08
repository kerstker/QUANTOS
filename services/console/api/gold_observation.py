"""Read-only projection of standardized gold fills; no private API or orders."""
from pathlib import Path
from decimal import DecimalException
import hashlib
import json
import os
import time

from fastapi import APIRouter
from quantos.runtime_paths import runtime_state_root
from services.console.api.common import with_snapshot_metadata
from services.console.api.state_artifact import read_json_artifact, VALID, MISSING
from services.research.gold_trade_observation import assess
from services.research.gold_monitor_view import validate_view
from services.research.gold_history_view import project_history
from services.research.binance_wallet_view import project_wallets
from services.data.gold_capture_snapshot import summarize_capture
from functools import partial
from services.data.gold_execution_tape import new_gold_trade_cursor
from services.research.gold_replay import replay as _replay

replay = partial(_replay, cursor_factory=new_gold_trade_cursor)
from services.research.gold_trade_links import associate
from services.research.gold_cash_observation import assess as assess_cash, reconcile

router = APIRouter()
MAX_COVERAGE_AGE_MS = 120000


def _market(root, now_ms):
    try:
        if root.resolve()!=root.absolute():raise ValueError('SYMLINK_ROOT')
        state,document=read_json_artifact(root/'public-capture.json',trusted_root=root)
        if state==MISSING:return 'NOT_CONNECTED',None
        if state!=VALID:raise ValueError('INVALID_MARKET_INPUT')
        report=summarize_capture(document,as_of_ms=now_ms)
        return report['status'],report
    except (OSError,ValueError,TypeError,KeyError):
        return 'DATA_BLOCKED',None


def _cash(root, now_ms):
    try:
        if root.resolve() != root.absolute():
            raise ValueError('SYMLINK_ROOT')
        state, document = read_json_artifact(root / 'external-cash.json', trusted_root=root)
        if state == MISSING:
            return 'NOT_CONNECTED', None, None
        if state != VALID:
            raise ValueError('INVALID_CASH_INPUT')
        report = assess_cash(document)
        if report['exported_at_ms'] > now_ms:
            raise ValueError('FUTURE_EXPORT')
        age = now_ms - report['end_ms']
        view = {key: report[key] for key in ('start_ms', 'end_ms', 'exported_at_ms',
                                            'unique_events', 'duplicate_events')}
        view.update(coverage_age_ms=age,
                    funding_by_asset=report['amounts_by_kind_asset'].get('FUNDING', {}))
        return 'STALE' if age > MAX_COVERAGE_AGE_MS else 'OBSERVED', view, document
    except (OSError, ValueError, TypeError, KeyError):
        return 'DATA_BLOCKED', None, None


def _cash_check(fills, cash):
    try:
        report = reconcile(fills, cash)
    except (ValueError, TypeError, KeyError):
        return 'SCOPE_MISMATCH'
    checks = [*report['commission_checks'].values(), report['realized_pnl_check']]
    if any(row['status'] == 'MISMATCH' for row in checks):
        return 'TOTALS_DIFFER'
    if (any(row['status'] != 'MATCHING_DECLARED_TOTALS' for row in checks)
            or report['uncompared_realized_pnl_by_asset']):
        return 'INCOMPLETE_CATEGORIES'
    return 'DECLARED_TOTALS_MATCH'


def _signal(root, now_ms):
    try:
        if root.resolve() != root.absolute():
            raise ValueError('SYMLINK_ROOT')
        state, source = read_json_artifact(root / 'signal-input.json', trusted_root=root, with_digest=True)
        document = source['document'] if state == VALID else None
        if state == MISSING:
            return 'NOT_CONNECTED', None, None
        if state != VALID or not isinstance(document, dict):
            raise ValueError('INVALID_SIGNAL_INPUT')
        times = document.get('decision_times_ms')
        # Console polling is bounded; longer experiments use the research runner.
        if (not isinstance(times, list) or not 1 <= len(times) <= 60
                or any(type(t) is not int or not 0 <= t <= now_ms for t in times)):
            raise ValueError('INVALID_CONSOLE_SIGNAL_SCHEDULE')
        report = replay(document)
        report['input_sha256'] = source['input_sha256']
        decisions = [row['decision'] for row in report['records']]
        last = decisions[-1] if decisions else None
        view = {'rule_id': report['rule_id'], 'decision_count': len(decisions),
            'entry_signal_count': sum(d['intent'] == 'LONG_RESEARCH_SIGNAL' for d in decisions),
            'last_decision_ms': last['decision_ms'] if last else None,
            'last_state': last['state'] if last else None,
            'reason': report.get('reason'), 'preregistered': False, 'authoritative': False,
            'input_digest': hashlib.sha256(json.dumps(document, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode()).hexdigest()}
        return ('INPUT_INCOMPLETE' if report['status'] == 'INPUT_INCOMPLETE'
                else 'HISTORICAL_REPLAY'), view, report
    except (OSError, ValueError, TypeError, KeyError, DecimalException):
        return 'DATA_BLOCKED', None, None


def _links(root, now_ms, fills, signal_report):
    try:
        if root.resolve() != root.absolute():
            raise ValueError('SYMLINK_ROOT')
        state, annotations = read_json_artifact(root / 'trade-links.json', trusted_root=root)
        if state == MISSING:
            return 'NOT_CONNECTED', None
        if state != VALID:
            raise ValueError('INVALID_LINK_INPUT')
        if fills is None or signal_report is None:
            return 'WAITING_FOR_EVIDENCE', None
        report = associate(fills, signal_report, annotations, as_of_ms=now_ms)
        return 'DECLARED_ASSOCIATIONS_ONLY', report['review_counts'] | {
            'evidence_mode': 'CURRENT_SOURCE_RECOMPUTATION',
            'signals_without_declared_entry': len(report['signals_without_declared_entry'])}
    except (OSError, ValueError, TypeError, KeyError):
        return 'DATA_BLOCKED', None


def _monitor(root,now_ms):
    try:
        if root.resolve()!=root.absolute():raise ValueError('SYMLINK_ROOT')
        state,document=read_json_artifact(root/'account-monitor.json',trusted_root=root)
        if state==MISSING:return 'NOT_CONNECTED',None
        if state!=VALID:raise ValueError('INVALID_MONITOR')
        result=validate_view(document,now_ms=now_ms)
        return result['status'],result
    except (OSError,ValueError,TypeError,KeyError,DecimalException):
        return 'DATA_BLOCKED',None


def _history(root,now_ms):
    try:
        if root.resolve()!=root.absolute():raise ValueError('SYMLINK_ROOT')
        state,document=read_json_artifact(root/'account-history.json',trusted_root=root)
        if state==MISSING:return 'NOT_IMPORTED',None
        if state!=VALID:raise ValueError('INVALID_HISTORY')
        result=project_history(document,now_ms=now_ms)
        return result['status'],result
    except (OSError,ValueError,KeyError,TypeError,DecimalException):return 'DATA_BLOCKED',None


def _wallets(root,now_ms):
    try:
        if root.resolve()!=root.absolute():raise ValueError('SYMLINK_ROOT')
        state,document=read_json_artifact(root/'account-wallets.json',trusted_root=root)
        if state==MISSING:return 'NOT_CONNECTED',None
        if state!=VALID:raise ValueError('INVALID_WALLETS')
        result=project_wallets(document,now_ms=now_ms)
        return result['status'],result
    except (OSError,ValueError,KeyError,TypeError,DecimalException):return 'DATA_BLOCKED',None


def build_snapshot(root: Path, *, now_ms: int):
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError('INVALID_TIME')
    monitor_status,monitor=_monitor(root,now_ms)
    history_status,history=_history(root,now_ms)
    wallet_status,wallets=_wallets(root,now_ms)
    market_status,market=_market(root,now_ms)
    cash_status, cash, cash_document = _cash(root, now_ms)
    signal_status, signal, signal_report = _signal(root, now_ms)
    fill_document = None
    result = dict(source='BACKEND_COMPUTED_SNAPSHOT', read_only=True, api_version='0.1.0',
        data_provenance='DECLARED_EXTERNAL_GOLD_FILL_FACTS', snapshot_status='NO_SNAPSHOT',
        symbol='XAUUSDT', as_of_ms=now_ms, observed_at_ms=None, coverage_age_ms=None,
        max_coverage_age_ms=MAX_COVERAGE_AGE_MS, account_connected=False,
        source_authenticated=False, history_complete=False, authoritative=False,
        submission_enabled=False, position=None, net_pnl=None, fills=None,
        monitor_status=monitor_status,monitor=monitor,history_status=history_status,history=history,wallet_status=wallet_status,wallets=wallets,
        market_status=market_status, market=market, signal_status=signal_status, signal=signal,
        cash_status=cash_status, cash=cash, links_status='NOT_CONNECTED', links=None,
        cash_reconciliation_status='WAITING_FOR_FILLS' if cash_document is not None else 'NOT_AVAILABLE')
    try:
        if root.resolve() != root.absolute():
            raise ValueError('SYMLINK_ROOT')
        state, document = read_json_artifact(root / 'external-fills.json', trusted_root=root)
        if state == MISSING:
            result['links_status'], result['links'] = _links(root, now_ms, None, signal_report)
            return with_snapshot_metadata(result)
        if state != VALID:
            raise ValueError('INVALID_INPUT')
        report = assess(document)
        if report['exported_at_ms'] > now_ms:
            raise ValueError('FUTURE_EXPORT')
        fill_document = document
        age = now_ms - report['end_ms']
        # The digest binds declared facts, never authenticates their origin.
        result.update(snapshot_status='STALE' if age > MAX_COVERAGE_AGE_MS else 'REAL',
            snapshot_id=report['facts_sha256'], observed_at_ms=report['exported_at_ms'],
            coverage_age_ms=age, fills={key: report[key] for key in (
                'status', 'start_ms', 'end_ms', 'exported_at_ms', 'unique_fills',
                'duplicate_fills', 'commission_by_asset', 'reported_realized_pnl_usdt')})
        if cash_document is not None:
            result['cash_reconciliation_status'] = _cash_check(document, cash_document)
    except (OSError, ValueError, TypeError, KeyError):
        result.update(snapshot_status='DATA_BLOCKED')
    result['links_status'], result['links'] = _links(root, now_ms, fill_document, signal_report)
    return with_snapshot_metadata(result)


@router.get('/api/v1/console/gold-observation')
def gold_observation():
    configured=os.environ.get('QUANTOS_GOLD_OBSERVATION_ROOT')
    root=Path(configured).absolute() if configured else runtime_state_root() / 'data/gold/observation'
    return build_snapshot(root,
                          now_ms=time.time_ns() // 1000000)
