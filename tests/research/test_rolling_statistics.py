"""Bit-for-bit stdlib comparison at cancellation, scale and receipt boundaries."""

import math
import random
import statistics

import pytest

from quantos.analytics.rolling_statistics import rolling_population_deviation


def series(kind):
    randomizer = random.Random(20261006)
    if kind == 'mixed':
        return [randomizer.uniform(-0.02, 0.02) for _ in range(300)]
    if kind == 'cancellation':
        return [1.0, math.nextafter(1.0, 2.0), math.nextafter(1.0, 0.0)] * 100
    if kind == 'extreme':
        maximum = float.fromhex('0x1.fffffffffffffp+1023')
        tiny = float.fromhex('0x0.0000000000001p-1022')
        return [0.0, tiny, -tiny, maximum, -maximum, 1e-200, -1e-200] * 40
    return [0.0, -0.0, 1.0, 1.0, 1.0, -0.0] * 50


@pytest.mark.parametrize('kind', ['mixed', 'cancellation', 'extreme', 'constant'])
@pytest.mark.parametrize('window', [1, 2, 3, 60, 257])
def test_each_received_window_matches_correctly_rounded_stdlib(kind, window):
    values = series(kind)
    expected = [None if index + 1 < window else
        statistics.pstdev(values[index + 1 - window:index + 1]) for index in range(len(values))]
    observed = list(rolling_population_deviation(iter(values), window))
    assert [None if value is None else value.hex() for value in observed] == [
        None if value is None else value.hex() for value in expected]


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_receipt_keeps_original_full_window_error(bad):
    values = [1.0, bad, 3.0]
    deviations = rolling_population_deviation(iter(values), 3)
    assert next(deviations) is next(deviations) is None
    with pytest.raises(Exception) as expected:
        statistics.pstdev(values)
    with pytest.raises(type(expected.value)) as observed:
        next(deviations)
    assert str(observed.value) == str(expected.value)


def test_generator_consumes_only_the_current_receipt_and_restarts_fresh():
    consumed = []
    values = [0.0, 0.01, -0.02, 0.03]

    def received():
        for value in values:
            consumed.append(value)
            yield value

    deviations = rolling_population_deviation(received(), 2)
    assert consumed == []
    assert next(deviations) is None and consumed == values[:1]
    assert next(deviations).hex() == statistics.pstdev(values[:2]).hex()
    assert consumed == values[:2]
    changed = [0.0, 0.25, -0.02, 0.03]
    assert list(rolling_population_deviation(changed, 2)) != list(rolling_population_deviation(values, 2))


@pytest.mark.parametrize('bad', [0, -1, True, 2.0])
def test_invalid_window_refused_before_input_consumption(bad):
    consumed = []

    def values():
        consumed.append(1.0)
        yield 1.0

    with pytest.raises(ValueError, match='positive integer'):
        list(rolling_population_deviation(values(), bad))
    assert consumed == []


@pytest.mark.parametrize('bad', [True, 1, '1'])
def test_non_float_input_is_not_silently_reinterpreted(bad):
    with pytest.raises(TypeError, match='binary floats'):
        list(rolling_population_deviation([bad], 2))
