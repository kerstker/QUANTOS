"""Explicit technical replay capacity; grants no economic or trading authority."""
from copy import deepcopy

SCHEMA = 'LINEAR_PERPETUAL_REPLAY_BUDGET_V1'
MAXIMUM_ENVIRONMENT_SEGMENTS = 4096
MAXIMUM_ELAPSED_MS = 32 * 86_400_000


def validate(value):
    if (type(value) is not dict
            or set(value) != {'schema', 'maximum_environment_segments', 'maximum_elapsed_ms'}
            or value['schema'] != SCHEMA
            or type(value['maximum_environment_segments']) is not int
            or not 1 <= value['maximum_environment_segments'] <= MAXIMUM_ENVIRONMENT_SEGMENTS
            or type(value['maximum_elapsed_ms']) is not int
            or not 1 <= value['maximum_elapsed_ms'] <= MAXIMUM_ELAPSED_MS):
        raise ValueError('INVALID_PERPETUAL_REPLAY_BUDGET')
    return deepcopy(value)
