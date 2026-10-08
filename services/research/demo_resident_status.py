"""Unverified resident progress for display; it never authorizes recovery."""
import re

from services.research import demo_display_store
from services.research.demo_oms_status import resolution_digest, _hash, _time

SCHEMA = 'DEMO_RESIDENT_STATUS_V1'
PHASES = ('NO_RESERVATIONS', 'INPUTS_UNRESOLVED', 'WAITING_QUERY_INTERVAL', 'RESIDENT_BUSY',
    'QUERY_RECOVERY_REQUIRED', 'QUERY_OBSERVED_RECEIPTS_REQUIRED', 'OMS_TERMINAL_CASH_SEPARATE')


def validate(raw):
    fields = '''schema environment scope phase reason observed_at_ms executor_boot_sha256 source_commit
        attempt_count last_attempt_at_ms next_query_at_ms reservation_sha256 query_status query_reason
        query_sha256 resolution_status resolution_issues resolution_sha256 report_sha256 source_verified
        new_order_submission_enabled economic_authority cash_reconciled'''.split()
    if type(raw) is not dict or set(raw) != set(fields):
        raise ValueError('RESIDENT_STATUS_FIELDS')
    if (raw['schema'] != SCHEMA or raw['environment'] != 'BINANCE_USDM_DEMO' or
        raw['scope'] != 'TECHNICAL_VALIDATION_ONLY' or raw['phase'] not in PHASES):
        raise ValueError('RESIDENT_STATUS_SCHEMA')
    _time(raw['observed_at_ms'])
    for key in ('source_verified', 'new_order_submission_enabled', 'economic_authority', 'cash_reconciled'):
        if raw[key] is not False:
            raise ValueError('RESIDENT_STATUS_AUTHORITY')
    for key in ('executor_boot_sha256', 'report_sha256'):
        _hash(raw[key])
    if type(raw['source_commit']) is not str or re.fullmatch('[a-f0-9]{40}', raw['source_commit']) is None:
        raise ValueError('RESIDENT_STATUS_SOURCE')
    for key in ('reservation_sha256', 'query_sha256', 'resolution_sha256'):
        if raw[key] is not None:
            _hash(raw[key])
    for key in ('reason', 'query_reason'):
        if raw[key] is not None and (type(raw[key]) is not str or re.fullmatch('[A-Z][A-Z0-9_]{0,100}', raw[key]) is None):
            raise ValueError('RESIDENT_STATUS_REASON')
    count, last, next_at = (raw[k] for k in ('attempt_count', 'last_attempt_at_ms', 'next_query_at_ms'))
    if count is None or count == 0:
        if count is not None and type(count) is not int or last is not None or next_at is not None:
            raise ValueError('RESIDENT_STATUS_UNKNOWN_SCHEDULE')
    else:
        if type(count) is not int or not 1 <= count <= 10000:
            raise ValueError('RESIDENT_STATUS_COUNT')
        _time(last); _time(next_at)
        if last > raw['observed_at_ms'] or next_at != last+30000:
            raise ValueError('RESIDENT_STATUS_SCHEDULE')
    if raw['phase'] == 'WAITING_QUERY_INTERVAL' and (next_at is None or next_at <= raw['observed_at_ms']):
        raise ValueError('RESIDENT_STATUS_WAIT')
    if raw['query_status'] not in (None, 'QUERY_RECOVERY_REQUIRED', 'ORDER_SNAPSHOTS_OBSERVED'):
        raise ValueError('RESIDENT_STATUS_QUERY')
    if (raw['query_status'] is None) != (raw['query_sha256'] is None) or raw['query_status'] is None and raw['query_reason'] is not None:
        raise ValueError('RESIDENT_STATUS_QUERY_BINDING')
    if raw['resolution_status'] not in (None, 'UNRESOLVED', 'READY_FOR_OMS_PROJECTION', 'OMS_TERMINAL_PROJECTED'):
        raise ValueError('RESIDENT_STATUS_RESOLUTION')
    if (raw['resolution_status'] is None) != (raw['resolution_sha256'] is None):
        raise ValueError('RESIDENT_STATUS_RESOLUTION_BINDING')
    issues = raw['resolution_issues']
    if (type(issues) is not list or len(issues) > 30 or any(type(v) is not str or
        re.fullmatch('[A-Z][A-Z0-9_]{0,100}', v) is None for v in issues) or len(set(issues)) != len(issues) or
        raw['resolution_status'] is None and issues):
        raise ValueError('RESIDENT_STATUS_ISSUES')
    return raw


def build_status(report, *, executor_boot_sha256, source_commit):
    query = report.get('query'); resolution = report.get('resolution')
    return validate({'schema': SCHEMA, 'environment': 'BINANCE_USDM_DEMO', 'scope': 'TECHNICAL_VALIDATION_ONLY',
        'phase': report['status'], 'reason': report['reason'], 'observed_at_ms': report['observed_at_ms'],
        'executor_boot_sha256': executor_boot_sha256, 'source_commit': source_commit,
        **{key: report[key] for key in ('attempt_count', 'last_attempt_at_ms', 'next_query_at_ms', 'reservation_sha256')},
        'query_status': query['status'] if query else None, 'query_reason': query.get('reason') if query else None,
        'query_sha256': resolution_digest(query) if query else None,
        'resolution_status': resolution['status'] if resolution else None,
        'resolution_issues': resolution.get('issues', []) if resolution else [],
        'resolution_sha256': resolution_digest(resolution) if resolution else None,
        'report_sha256': resolution_digest(report), 'source_verified': False,
        'new_order_submission_enabled': False, 'economic_authority': False, 'cash_reconciled': False})


def save(directory, raw):
    return demo_display_store.save(directory, raw, validator=validate)


def load_latest(directory):
    return demo_display_store.load_latest(directory, validator=validate)
