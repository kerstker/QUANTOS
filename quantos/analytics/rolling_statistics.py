"""Exact received-observation population deviation without repeated conversion.

Each finite binary float contributes its integer ratio once. Sliding integer
moments preserve the exact variance used by statistics.pstdev; the final square
root uses CPython's correctly rounded integer construction. No input or result
is cached between invocations.
"""

from collections import deque
from collections.abc import Iterable, Iterator
import math
import statistics
import sys


def _sqrt_ratio(numerator: int, denominator: int) -> float:
    """Correctly rounded sqrt(numerator/denominator), as in CPython statistics."""
    width = 2 * sys.float_info.mant_dig + 3
    shift = (numerator.bit_length() - denominator.bit_length() - width) // 2
    if shift >= 0:
        scaled_n, scaled_d = numerator, denominator << (2 * shift)
        root = math.isqrt(scaled_n // scaled_d)
        return (root | (root * root * scaled_d != scaled_n)) * (1 << shift) / 1
    scaled_n, scaled_d = numerator << (-2 * shift), denominator
    root = math.isqrt(scaled_n // scaled_d)
    return (root | (root * root * scaled_d != scaled_n)) / (1 << -shift)


def rolling_population_deviation(values: Iterable[float], window: int) -> Iterator[float | None]:
    """Match pstdev on each trailing received window, including warmup and errors.

    Missing observations are omitted by the caller. Non-finite windows use the
    original stdlib path so its failure remains visible at the same full window.
    All finite denominators are powers of two; growing the common scale changes
    no value and requires no floating point subtraction of accumulated moments.
    """
    if type(window) is not int or window < 1:
        raise ValueError('population window must be a positive integer')
    pending: deque[tuple[float, tuple[int, int] | None]] = deque()
    scale, total, squares, invalid = 1, 0, 0, 0
    for value in values:
        if type(value) is not float:
            raise TypeError('population observations must be binary floats')
        ratio = value.as_integer_ratio() if math.isfinite(value) else None
        pending.append((value, ratio))
        if ratio is None:
            invalid += 1
        else:
            numerator, denominator = ratio
            if denominator > scale:
                multiplier = denominator // scale
                total *= multiplier
                squares *= multiplier * multiplier
                scale = denominator
            scaled = numerator * (scale // denominator)
            total += scaled
            squares += scaled * scaled
        if len(pending) > window:
            _, removed = pending.popleft()
            if removed is None:
                invalid -= 1
            else:
                numerator, denominator = removed
                scaled = numerator * (scale // denominator)
                total -= scaled
                squares -= scaled * scaled
        if len(pending) < window:
            yield None
        elif invalid:
            yield statistics.pstdev(item[0] for item in pending)
        else:
            numerator = window * squares - total * total
            denominator = window * window * scale * scale
            yield _sqrt_ratio(numerator, denominator)
