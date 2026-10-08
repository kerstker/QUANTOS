"""Bounded decimal wire values shared by data and offline research parsers."""
from decimal import Decimal
import re

_NUMBER = re.compile(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')


def nonnegative_decimal_string(value: object, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str) or len(value) > 64 or not _NUMBER.fullmatch(value):
        raise ValueError('INVALID_DECIMAL_STRING')
    result = Decimal(value)
    if positive and result == 0:
        raise ValueError('NONPOSITIVE_AMOUNT')
    return result
