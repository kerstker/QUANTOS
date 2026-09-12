"""Unverified display inventory of existing executor records, with no authority."""
import re

from services.research import demo_display_store, demo_oms_status, demo_resident_status
from services.research.demo_oms_status import _hash, _time

SCHEMA='DEMO_EXECUTOR_OBSERVATION_V1'
NAMES=('private_stream','origin_BTCUSDC','origin_BTCUSDT','resident','oms','cancellation')
PHASES={
    'private_stream':('CONNECTING','CONNECTED','DISCONNECTED','STOPPED','STALE'),
    'origin_BTCUSDC':('OBSERVED_FLAT_QUOTE','UNRESOLVED'),
    'origin_BTCUSDT':('OBSERVED_FLAT_QUOTE','UNRESOLVED'),
    'resident':demo_resident_status.PHASES, 'oms':demo_oms_status.PHASES,
    'cancellation':('RESERVED_AWAITING_ATTEMPTS','ORIGINAL_ID_QUERY_RECOVERY_REQUIRED'),
}


def validate(raw):
    names='schema environment scope phase observed_at_ms account_ref_sha256 source_commit components source_verified submission_enabled cash_reconciled economic_authority'.split()
    if type(raw) is not dict or set(raw)!=set(names):raise ValueError('EXECUTOR_OBSERVATION_FIELDS')
    if raw['schema']!=SCHEMA or raw['environment']!='BINANCE_USDM_DEMO' or raw['scope']!='TECHNICAL_VALIDATION_ONLY':
        raise ValueError('EXECUTOR_OBSERVATION_SCHEMA')
    _time(raw['observed_at_ms']);_hash(raw['account_ref_sha256'])
    if type(raw['source_commit']) is not str or re.fullmatch('[a-f0-9]{40}',raw['source_commit']) is None:
        raise ValueError('EXECUTOR_OBSERVATION_SOURCE')
    if any(raw[k] is not False for k in ('source_verified','submission_enabled','cash_reconciled','economic_authority')):
        raise ValueError('EXECUTOR_OBSERVATION_AUTHORITY')
    rows=raw['components']
    if type(rows) is not list or len(rows)!=len(NAMES):raise ValueError('EXECUTOR_OBSERVATION_COMPONENTS')
    for name,row in zip(NAMES,rows):
        if type(row) is not dict or set(row)!=set('name status phase observed_at_ms report_sha256 counts'.split()) or row['name']!=name:
            raise ValueError('EXECUTOR_OBSERVATION_COMPONENT_FIELDS')
        if row['status']!='AVAILABLE':
            if row['status'] not in ('NO_SNAPSHOT','CORRUPT_OR_UNREADABLE') or any(row[k] is not None for k in ('phase','observed_at_ms','report_sha256','counts')):
                raise ValueError('EXECUTOR_OBSERVATION_MISSING')
            continue
        _time(row['observed_at_ms']);_hash(row['report_sha256'])
        if row['observed_at_ms']>raw['observed_at_ms'] or row['phase'] not in PHASES[name]:
            raise ValueError('EXECUTOR_OBSERVATION_TIME_OR_PHASE')
        counts=row['counts']
        if name=='private_stream':
            if type(counts) is not dict or set(counts)!=set('records messages unique_messages gaps'.split()) or any(type(v) is not int or not 0<=v<=200000 for v in counts.values()):
                raise ValueError('EXECUTOR_OBSERVATION_COUNTS')
            if not 1<=counts['records'] or counts['unique_messages']>counts['messages'] or counts['messages']+counts['gaps']>=counts['records']:
                raise ValueError('EXECUTOR_OBSERVATION_COUNT_RELATION')
        elif counts is not None:raise ValueError('EXECUTOR_OBSERVATION_UNEXPECTED_COUNTS')
    phase='RECORDS_OBSERVED' if all(r['status']=='AVAILABLE' for r in rows) else 'PARTIAL_RECORDS'
    if raw['phase']!=phase:raise ValueError('EXECUTOR_OBSERVATION_PHASE')
    return raw


def save(directory,raw):return demo_display_store.save(directory,raw,validator=validate)


def load_latest(directory):return demo_display_store.load_latest(directory,validator=validate)
