"""Digest the ledger prefix actually read by the Gold loss control.

This covers loss-policy inputs in observation order. It authenticates neither
the producer nor callback completeness and creates no separate cash ledger.
"""

import hashlib
import json
from collections.abc import Mapping

from quantos.contracts.decimal_arithmetic import canonical_decimal

SCHEMA = 'QUANTOS_GOLD_LOSS_HISTORY_OBSERVATION_V1'
FIELDS = frozenset(('schema', 'observed_points', 'observed_prefix_sha256'))


def new_digest():
    return hashlib.sha256((SCHEMA + '\n').encode('ascii'))


def update_digest(state, *, timestamp, sequence, phase, equity):
    if (type(timestamp) is not int or not 0 <= timestamp < 2**63
            or type(sequence) is not int or not 0 <= sequence < 2**63
            or type(phase) is not str or not 0 < len(phase) <= 128):
        raise ValueError('GOLD_LOSS_OBSERVATION_POINT')
    value = canonical_decimal(equity)
    if len(value) > 512:
        raise ValueError('GOLD_LOSS_OBSERVATION_DECIMAL_BUDGET')
    encoded = json.dumps([timestamp, sequence, phase, value], ensure_ascii=True,
                         separators=(',', ':')).encode('ascii')
    state.update(encoded + b'\n')


def validate_observation(value, *, maximum_points):
    if (not isinstance(value, Mapping) or len(value) != len(FIELDS)
            or set(value) != FIELDS or value['schema'] != SCHEMA
            or type(value['observed_points']) is not int
            or not 0 <= value['observed_points'] <= maximum_points
            or type(value['observed_prefix_sha256']) is not str
            or len(value['observed_prefix_sha256']) != 64
            or any(char not in '0123456789abcdef' for char in value['observed_prefix_sha256'])):
        raise ValueError('GOLD_EXECUTION_RISK_LOSS_OBSERVATION_FIELDS')
    return value
