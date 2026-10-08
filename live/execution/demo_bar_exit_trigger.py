"""Executor-owned, write-once exit intent; no cash, orders or permissions.

Records use the original protected executor root. Call only after the
original position/episode and fresh origin checks. There is no reset API.
"""
import re

from live.execution import demo_position_context as position
from live.execution.demo_batch_risk import _digest
from live.execution.demo_gold_pilot import read, retain_once

REASONS = ('STOP', 'TIMEOUT', 'SIGNAL_EXIT', 'PILOT_LOSS_LIMIT', 'PILOT_DURATION_COMPLETE')


def require(value, reason):
    if not value:
        raise ValueError('BAR_EXIT_TRIGGER_' + reason)


def path_for(binding):
    require(type(binding) is dict and set(binding) == {'account_ref_sha256', 'definition_hash',
        'entry_client_order_id', 'reservation_sha256', 'opened_venue_at_ms'}, 'BINDING')
    for key in ('account_ref_sha256', 'definition_hash', 'reservation_sha256'):
        require(type(binding[key]) is str and re.fullmatch('[a-f0-9]{64}', binding[key]) is not None, 'DIGEST')
    require(type(binding['entry_client_order_id']) is str and
        re.fullmatch('QD[a-f0-9]{32}', binding['entry_client_order_id']) is not None, 'ENTRY_ID')
    require(type(binding['opened_venue_at_ms']) is int and 0 <= binding['opened_venue_at_ms'] <= 2**63-1, 'OPEN_TIME')
    area = position._ROOT
    position._custody(area, directory=True)
    require(area.is_dir() and not area.is_symlink(), 'DIRECTORY_REQUIRED')
    return area / ('bar-exit-trigger-' + _digest(binding) + '.json')


def validate(value, binding, now_ms):
    fields = {'schema', 'binding', 'reason', 'observed_at_ms', 'origin_sha256',
              'position_context_sha256', 'submission_enabled', 'record_hash'}
    require(type(value) is dict and set(value) == fields, 'FIELDS')
    require(value['schema'] == 'DEMO_BAR_EXIT_TRIGGER_V1' and value['binding'] == binding
            and value['reason'] in REASONS and value['submission_enabled'] is False, 'SCOPE')
    require(type(now_ms) is int and type(value['observed_at_ms']) is int
            and 0 <= value['observed_at_ms'] <= now_ms <= 2**63-1, 'TIME')
    for key in ('origin_sha256', 'position_context_sha256'):
        require(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}', value[key]) is not None, 'DIGEST')
    require(value['record_hash'] == _digest({k:v for k,v in value.items() if k != 'record_hash'}), 'HASH')
    return value


def load(binding, *, now_ms):
    path = path_for(binding)
    if not path.exists() and not path.is_symlink():
        return None
    return validate(read(path), binding, now_ms)


def retain(binding, *, reason, now_ms, origin_sha256, position_context_sha256):
    path = path_for(binding)
    value = dict(schema='DEMO_BAR_EXIT_TRIGGER_V1', binding=binding, reason=reason,
        observed_at_ms=now_ms, origin_sha256=origin_sha256, position_context_sha256=position_context_sha256,
        submission_enabled=False)
    value['record_hash'] = _digest(value)
    validate(value, binding, now_ms)
    return validate(retain_once(path, value), binding, now_ms)
