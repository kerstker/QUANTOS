"""Fixed capacity for a predeclared 32-day, ten-second Gold comparison.

This versioned capacity does not change statistical thresholds or grant authority.
Legacy declarations and calculations keep their existing limits.
"""
import copy

SCHEMA = 'GOLD_LONG_STATISTICS_BUDGET_V1'
MAX_CLOSED_BARS = 3073
MAX_POINTS = 276482
MAX_MATRIX_CELLS = 4 * MAX_POINTS
MAX_WORK = 20_000_000
_VALUE = dict(schema=SCHEMA, maximum_points=MAX_POINTS,
              maximum_matrix_cells=MAX_MATRIX_CELLS, maximum_work=MAX_WORK)


def declaration():
    return copy.deepcopy(_VALUE)


def validate(value):
    if (type(value) is not dict or set(value) != set(_VALUE)
            or any(type(value[key]) is not type(expected) or value[key] != expected
                   for key, expected in _VALUE.items())):
        raise ValueError('GOLD_LONG_STATISTICS_BUDGET')
    return copy.deepcopy(value)
