"""Strict, read-only Demo recovery-point display without private archive paths."""
import re

from services.research import demo_display_store

SCHEMA = 'DEMO_BACKUP_STATUS_V1'
NAMESPACES = ('research', 'executor', 'issuer', 'controller')
MAX_AGE_MS = 30 * 60 * 1000
SAFETY = 'source_verified submission_enabled economic_authority cross_namespace_atomic production_restore_performed'
SUMMARY_FIELDS = ('schema namespace observed_at_ms status recovery_point_at_ms last_verified_at_ms rpo_seconds '
    'source_capture_age_ms capture_slots_remaining archive_bytes_reverified automatic_history_deletion '
    'verified_cycles pending_cycles failed_cycles interrupted_cycles seal_failures ' + SAFETY)
TIMERS = ('ACTIVE', 'INACTIVE', 'NOT_INSTALLED', 'UNKNOWN')


def require(value, reason):
    if not value:
        raise ValueError(reason)


def exact(value, fields):
    require(type(value) is dict and set(value) == set(fields.split()), 'BACKUP_DISPLAY_FIELDS')


def integer(value, maximum=8640000000000000):
    return type(value) is int and 0 <= value <= maximum


def phase(rows):
    count = sum(row['summary'] is not None and row['summary']['status'] == 'CURRENT' for row in rows)
    return 'ALL_RECOVERY_POINTS_CURRENT' if count == 4 else 'PARTIAL_RECOVERY_POINTS' if count else 'NO_CURRENT_RECOVERY_POINTS'


def validate_summary(raw, *, namespace, observed_at_ms):
    exact(raw, SUMMARY_FIELDS)
    require(raw['schema'] == 'DEMO_CHECKPOINT_CYCLE_STATUS_V1' and raw['namespace'] == namespace, 'BACKUP_DISPLAY_SCOPE')
    require(all(raw[k] is False for k in (SAFETY + ' archive_bytes_reverified automatic_history_deletion').split()), 'BACKUP_DISPLAY_AUTHORITY')
    require(integer(raw['observed_at_ms']) and 0 < raw['observed_at_ms'] <= observed_at_ms, 'BACKUP_DISPLAY_TIME')
    counts = ('verified_cycles', 'pending_cycles', 'failed_cycles', 'interrupted_cycles')
    require(all(integer(raw[k], 512) for k in (*counts, 'capture_slots_remaining')) and
            sum(raw[k] for k in counts) + raw['capture_slots_remaining'] == 512 and
            integer(raw['seal_failures'], 4096), 'BACKUP_DISPLAY_COUNTS')
    require(integer(raw['rpo_seconds'], 7 * 86400) and raw['rpo_seconds'] >= 60, 'BACKUP_DISPLAY_RPO')
    if raw['verified_cycles'] == 0:
        require(raw['status'] == 'NO_VERIFIED_BACKUP' and
                all(raw[k] is None for k in ('recovery_point_at_ms', 'last_verified_at_ms', 'source_capture_age_ms')),
                'BACKUP_DISPLAY_NO_POINT')
    else:
        require(all(integer(raw[k]) for k in ('recovery_point_at_ms', 'last_verified_at_ms', 'source_capture_age_ms')) and
                0 < raw['recovery_point_at_ms'] <= raw['last_verified_at_ms'] <= raw['observed_at_ms'] and
                raw['source_capture_age_ms'] == raw['observed_at_ms'] - raw['recovery_point_at_ms'], 'BACKUP_DISPLAY_POINT')
        expected = 'STALE' if raw['source_capture_age_ms'] > raw['rpo_seconds'] * 1000 else 'CURRENT'
        require(raw['status'] == expected, 'BACKUP_DISPLAY_RPO_STATUS')
    return raw


def validate(raw):
    exact(raw, 'schema environment scope source_commit observed_at_ms phase components ' + SAFETY)
    require(raw['schema'] == SCHEMA and raw['environment'] == 'BINANCE_USDM_DEMO' and
            raw['scope'] == 'TECHNICAL_VALIDATION_ONLY', 'BACKUP_DISPLAY_SCOPE')
    require(all(raw[k] is False for k in SAFETY.split()), 'BACKUP_DISPLAY_AUTHORITY')
    require(type(raw['source_commit']) is str and re.fullmatch('[a-f0-9]{40}', raw['source_commit']), 'BACKUP_DISPLAY_SOURCE')
    require(integer(raw['observed_at_ms']) and raw['observed_at_ms'] > 0, 'BACKUP_DISPLAY_TIME')
    require(type(raw['components']) is list and len(raw['components']) == 4, 'BACKUP_DISPLAY_COMPONENTS')
    for namespace, row in zip(NAMESPACES, raw['components']):
        exact(row, 'namespace status summary capture_timer seal_timer')
        require(row['namespace'] == namespace and row['status'] in
                ('OBSERVED', 'NO_SPOOL', 'NO_BACKUP_DIRECTORY', 'UNREADABLE', 'BACKUP_VOLUME_UNAVAILABLE'), 'BACKUP_DISPLAY_COMPONENT_SCOPE')
        require(row['capture_timer'] in TIMERS and row['seal_timer'] in TIMERS, 'BACKUP_DISPLAY_TIMER')
        if row['status'] == 'OBSERVED':
            validate_summary(row['summary'], namespace=namespace, observed_at_ms=raw['observed_at_ms'])
        else:
            require(row['summary'] is None, 'BACKUP_DISPLAY_MISSING')
    require(raw['phase'] == phase(raw['components']), 'BACKUP_DISPLAY_PHASE')
    return raw


def save(directory, raw):
    return demo_display_store.save(directory, raw, validator=validate)


def load_latest(directory):
    return demo_display_store.load_latest(directory, validator=validate)
