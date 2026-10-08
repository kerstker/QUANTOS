"""Independent golden tests for the versioned stationary bootstrap stream."""

from __future__ import annotations

import hashlib

import pytest

from quantos.contracts.research import PeriodKey
from services.validation import reality_check as kernel


_MASK64 = (1 << 64) - 1


def _reference_splitmix64(seed: int, count: int) -> tuple[int, ...]:
    """Test-only scalar transcription of the published SplitMix64 constants."""

    state = seed
    values: list[int] = []
    for _ in range(count):
        state = (state + 0x9E3779B97F4A7C15) & _MASK64
        z = state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        values.append((z ^ (z >> 31)) & _MASK64)
    return tuple(values)


def _reference_indices(
    period_count: int,
    block_length: int,
    seed: int,
    replication_count: int,
) -> tuple[tuple[int, ...], ...]:
    """Independent scalar stationary-bootstrap reference used only by tests."""

    draws = iter(_reference_splitmix64(seed, replication_count * period_count * 3))

    def uniform_below(n: int) -> int:
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            draw = next(draws)
            if draw < limit:
                return draw % n

    rows: list[tuple[int, ...]] = []
    for _ in range(replication_count):
        index = uniform_below(period_count)
        row = [index]
        for _ in range(period_count - 1):
            restart_uniform = (next(draws) >> 11) * (2.0**-53)
            if restart_uniform < 1.0 / block_length:
                index = uniform_below(period_count)
            else:
                index = (index + 1) % period_count
            row.append(index)
        rows.append(tuple(row))
    return tuple(rows)


def _stream_hash(rows: tuple[tuple[int, ...], ...]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        for index in row:
            digest.update(index.to_bytes(8, byteorder="little", signed=False))
    return digest.hexdigest()


def test_splitmix64_first_ten_outputs_match_published_transition():
    expected = (
        0xE220A8397B1DCDAF,
        0x6E789E6AA1B965F4,
        0x06C45D188009454F,
        0xF88BB8A8724C81EC,
        0x1B39896A51A8749B,
        0x53CB9F0C747EA2EA,
        0x2C829ABE1F4532E1,
        0xC584133AC916AB3C,
        0x3EE5789041C98AC3,
        0xF3B8488C368CB0A6,
    )

    generator = kernel.SplitMix64(0)

    assert expected == _reference_splitmix64(0, 10)
    assert tuple(generator.next_u64() for _ in range(10)) == expected


def test_uniform_below_rejects_out_of_range_draw_instead_of_using_modulo(monkeypatch):
    generator = kernel.SplitMix64(0)
    draws = iter(((1 << 64) - 1, 1 << 63))
    call_count = 0

    def scripted_next() -> int:
        nonlocal call_count
        call_count += 1
        return next(draws)

    monkeypatch.setattr(generator, "next_u64", scripted_next)

    assert generator.uniform_below((1 << 63) + 1) == 1 << 63
    assert call_count == 2


def test_uniform_below_accepts_inclusive_uint64_modulus(monkeypatch):
    generator = kernel.SplitMix64(0)
    controlled_raw_value = (1 << 64) - 17
    monkeypatch.setattr(generator, "next_u64", lambda: controlled_raw_value)

    assert generator.uniform_below(1 << 64) == controlled_raw_value


def test_uniform_below_one_always_returns_zero(monkeypatch):
    generator = kernel.SplitMix64(0)
    monkeypatch.setattr(generator, "next_u64", lambda: (1 << 64) - 1)

    assert generator.uniform_below(1) == 0


@pytest.mark.parametrize("n", (True, False))
def test_uniform_below_rejects_bool_exactly(n: bool):
    with pytest.raises(kernel.RealityCheckInputError) as caught:
        kernel.SplitMix64(0).uniform_below(n)

    assert type(caught.value) is kernel.RealityCheckInputError


@pytest.mark.parametrize("seed", (True, False))
def test_splitmix64_rejects_bool_seed_exactly(seed: bool):
    with pytest.raises(kernel.RealityCheckInputError) as caught:
        kernel.SplitMix64(seed)

    assert type(caught.value) is kernel.RealityCheckInputError


def test_restart_uniform_uses_top_53_bits_exactly(monkeypatch):
    generator = kernel.SplitMix64(0)
    monkeypatch.setattr(generator, "next_u64", lambda: 1 << 63)

    assert generator.restart_uniform() == 0.5


def test_stationary_bootstrap_restart_comparison_is_strict(monkeypatch):
    class BoundaryGenerator:
        instances: list["BoundaryGenerator"] = []

        def __init__(self, seed: int) -> None:
            self.seed = seed
            self.uniform_calls = 0
            self.__class__.instances.append(self)

        def uniform_below(self, n: int) -> int:
            self.uniform_calls += 1
            return 1

        def restart_uniform(self) -> float:
            return 0.5

    monkeypatch.setattr(kernel, "SplitMix64", BoundaryGenerator)

    rows = tuple(kernel.iter_stationary_bootstrap_indices(4, 2, 7, 1))

    assert rows == ((1, 2, 3, 0),)
    assert BoundaryGenerator.instances[0].uniform_calls == 1


def test_index_stream_encoding_is_unsigned_uint64_little_endian():
    encoded = kernel._indices_to_little_endian_bytes((0, 1, 256))

    assert encoded == (
        b"\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x01\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x01\x00\x00\x00\x00\x00\x00"
    )


def test_bootstrap_matches_independent_reference_and_reuses_one_vector_across_columns():
    rows = tuple(kernel.iter_stationary_bootstrap_indices(23, 5, 0x0123456789ABCDEF, 10))
    expected = _reference_indices(23, 5, 0x0123456789ABCDEF, 10)
    first_column = tuple(float(i) for i in range(23))
    second_column = tuple(10.0 * value for value in first_column)

    assert rows == expected
    for row in rows:
        first_mean = sum(first_column[index] for index in row) / len(row)
        second_mean = sum(second_column[index] for index in row) / len(row)
        assert second_mean == pytest.approx(10.0 * first_mean)


def test_each_block_length_restarts_from_the_same_declared_seed():
    short_block = next(kernel.iter_stationary_bootstrap_indices(23, 3, 12345, 1))
    long_block = next(kernel.iter_stationary_bootstrap_indices(23, 7, 12345, 1))

    assert short_block[0] == long_block[0]


def test_prefix_hashes_are_exact_prefixes_of_one_continuous_stream():
    expected_rows = _reference_indices(23, 5, 0x0123456789ABCDEF, 1000)
    actual_rows = tuple(
        kernel.iter_stationary_bootstrap_indices(23, 5, 0x0123456789ABCDEF, 1000)
    )

    assert actual_rows == expected_rows
    assert {
        count: _stream_hash(actual_rows[:count]) for count in (10, 100, 1000)
    } == {
        count: _stream_hash(expected_rows[:count]) for count in (10, 100, 1000)
    }


def test_bootstrap_iterator_is_lazy_before_first_replication(monkeypatch):
    class InstrumentedGenerator:
        instances: list["InstrumentedGenerator"] = []

        def __init__(self, seed: int) -> None:
            self.calls = 0
            self.__class__.instances.append(self)

        def uniform_below(self, n: int) -> int:
            self.calls += 1
            return 0

        def restart_uniform(self) -> float:
            self.calls += 1
            return 1.0

    monkeypatch.setattr(kernel, "SplitMix64", InstrumentedGenerator)

    stream = kernel.iter_stationary_bootstrap_indices(20, 2, 7, 1000)

    assert InstrumentedGenerator.instances == []
    assert next(stream) == tuple(range(20))
    assert len(InstrumentedGenerator.instances) == 1
    assert InstrumentedGenerator.instances[0].calls == 20


def test_compute_hashes_each_replication_before_requesting_the_next(monkeypatch):
    period_count = 20
    real_sha256 = hashlib.sha256

    class HashTracker:
        def __init__(self) -> None:
            self._digest = real_sha256()
            self.update_count = 0
            self.checked_before_second = False

        def update(self, payload: bytes) -> None:
            self.update_count += 1
            self._digest.update(payload)

        def hexdigest(self) -> str:
            return self._digest.hexdigest()

    tracker = HashTracker()

    def instrumented_indices(*args, **kwargs):
        del args, kwargs
        indices = tuple(range(period_count))
        yield indices
        assert tracker.update_count == period_count
        tracker.checked_before_second = True
        yield indices

    monkeypatch.setattr(kernel, "iter_stationary_bootstrap_indices", instrumented_indices)
    monkeypatch.setattr(kernel.hashlib, "sha256", lambda: tracker)
    pattern = (-2.0, -1.0, 0.0, 1.0, 2.0)
    candidate_a = tuple(0.01 + 0.001 * pattern[index % len(pattern)] for index in range(period_count))
    candidate_b = tuple(0.02 + 0.002 * pattern[(index + 1) % len(pattern)] for index in range(period_count))
    period_keys = tuple(
        PeriodKey(
            event_time=1_700_000_000_000_000 + index,
            frequency_id="UTC_DAILY",
            label_horizon="P1D",
        )
        for index in range(period_count)
    )

    kernel.compute_reality_check_and_spa(
        candidate_net_returns=(("a", candidate_a), ("b", candidate_b)),
        benchmark_net_returns=(0.0,) * period_count,
        period_keys=period_keys,
        block_length=2,
        alpha=0.05,
        seed=7,
        replication_count=2,
        prefix_replication_counts=(2,),
    )

    assert tracker.checked_before_second is True
    assert tracker.update_count == 2 * period_count


@pytest.mark.parametrize(
    ("period_count", "block_length", "seed", "replication_count"),
    (
        (0, 1, 0, 1),
        (20, 0, 0, 1),
        (20, 11, 0, 1),
        (20, 2, -1, 1),
        (20, 2, 1 << 64, 1),
        (20, 2, 0, 0),
        (True, 1, 0, 1),
        (20, True, 0, 1),
        (20, 2, True, 1),
        (20, 2, 0, True),
    ),
)
def test_bootstrap_rejects_invalid_stream_parameters(
    period_count: int,
    block_length: int,
    seed: int,
    replication_count: int,
):
    with pytest.raises(kernel.RealityCheckInputError) as caught:
        tuple(
            kernel.iter_stationary_bootstrap_indices(
                period_count,
                block_length,
                seed,
                replication_count,
            )
        )

    assert type(caught.value) is kernel.RealityCheckInputError
