"""Deterministic White RC / Hansen SPA validation primitives.

This module is an offline, pure statistical kernel.  It has no lifecycle,
execution, broker, credential, clock, network, or persistence authority.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
import hashlib
import math
import sys
import unicodedata

from quantos.contracts.research import (
    BootstrapPrefixHash,
    MonteCarloTailResult,
    PeriodKey,
    RealityCheckResult,
    SpaResult,
    SpaVariantResult,
)


_UINT64_MODULUS = 1 << 64
_UINT64_MASK = _UINT64_MODULUS - 1
_MINIMUM_PERIOD_COUNT = 20
_PUBLISHABLE_REPLICATION_COUNT = 10_000


class RealityCheckInputError(ValueError):
    """Raised when a family cannot be assessed without changing its inputs."""


class SplitMix64:
    """Versioned local unsigned-64-bit generator used by the bootstrap."""

    def __init__(self, seed: int) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < _UINT64_MODULUS:
            raise RealityCheckInputError("seed must be an unsigned 64-bit integer")
        self._state = seed

    def next_u64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _UINT64_MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _UINT64_MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _UINT64_MASK
        return (value ^ (value >> 31)) & _UINT64_MASK

    def uniform_below(self, n: int) -> int:
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= _UINT64_MODULUS:
            raise RealityCheckInputError("n must be in [1, 2**64]")
        limit = _UINT64_MODULUS - (_UINT64_MODULUS % n)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % n

    def restart_uniform(self) -> float:
        return (self.next_u64() >> 11) * (2.0**-53)


def _indices_to_little_endian_bytes(indices: Sequence[int]) -> bytes:
    return b"".join(index.to_bytes(8, byteorder="little", signed=False) for index in indices)


def iter_stationary_bootstrap_indices(
    period_count: int,
    block_length: int,
    seed: int,
    replication_count: int,
) -> Iterator[tuple[int, ...]]:
    """Yield stationary-bootstrap index vectors in replication-major order."""

    if isinstance(period_count, bool) or not isinstance(period_count, int) or period_count <= 0:
        raise RealityCheckInputError("period_count must be a positive integer")
    if (
        isinstance(block_length, bool)
        or not isinstance(block_length, int)
        or not 1 <= block_length <= period_count // 2
    ):
        raise RealityCheckInputError("block_length must be in [1, period_count // 2]")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < _UINT64_MODULUS:
        raise RealityCheckInputError("seed must be an unsigned 64-bit integer")
    if (
        isinstance(replication_count, bool)
        or not isinstance(replication_count, int)
        or replication_count <= 0
    ):
        raise RealityCheckInputError("replication_count must be a positive integer")

    generator = SplitMix64(seed)
    restart_probability = 1.0 / block_length
    for _ in range(replication_count):
        index = generator.uniform_below(period_count)
        indices = [index]
        for _ in range(period_count - 1):
            if generator.restart_uniform() < restart_probability:
                index = generator.uniform_below(period_count)
            else:
                index = (index + 1) % period_count
            indices.append(index)
        yield tuple(indices)


def block_length_grid_v1(period_count: int) -> tuple[int, ...]:
    """Return the preregisterable V1 block grid using integer arithmetic only."""

    if (
        isinstance(period_count, bool)
        or not isinstance(period_count, int)
        or period_count < _MINIMUM_PERIOD_COUNT
    ):
        raise RealityCheckInputError("period_count must be an integer of at least 20")

    low = 1
    high = period_count
    cube_root = 1
    while low <= high:
        midpoint = (low + high) // 2
        cube = midpoint * midpoint * midpoint
        if cube <= period_count:
            cube_root = midpoint
            low = midpoint + 1
        else:
            high = midpoint - 1
    return tuple(
        sorted(
            {
                max(1, cube_root // 2),
                cube_root,
                min(period_count // 2, 2 * cube_root),
            }
        )
    )


def _float64(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RealityCheckInputError(f"{name} must contain float64 values")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise RealityCheckInputError(f"{name} must contain float64 values") from exc
    if not math.isfinite(normalized):
        raise RealityCheckInputError(f"{name} must contain finite float64 values")
    return 0.0 if normalized == 0.0 else normalized


def _float_sequence(value: object, *, name: str) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (Mapping, str, bytes, bytearray)):
        raise RealityCheckInputError(f"{name} must be a sequence")
    return tuple(_float64(item, name=name) for item in value)


def _validate_period_keys(value: object, *, period_count: int) -> tuple[PeriodKey, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (Mapping, str, bytes, bytearray)):
        raise RealityCheckInputError("period_keys must be a sequence")
    keys = tuple(value)
    if len(keys) != period_count or not keys:
        raise RealityCheckInputError("period_keys must align exactly with every return series")

    event_times: list[int] = []
    frequencies: set[str] = set()
    horizons: set[str] = set()
    for key in keys:
        if type(key) is not PeriodKey:
            raise RealityCheckInputError("period_keys must contain exact PeriodKey values")
        event_time = key.event_time
        frequency_id = key.frequency_id
        label_horizon = key.label_horizon
        if isinstance(event_time, bool) or not isinstance(event_time, int) or event_time <= 0:
            raise RealityCheckInputError("period event_time must be a positive integer")
        for field_name, field_value in (
            ("frequency_id", frequency_id),
            ("label_horizon", label_horizon),
        ):
            if (
                not isinstance(field_value, str)
                or not field_value
                or unicodedata.normalize("NFC", field_value) != field_value
            ):
                raise RealityCheckInputError(f"period {field_name} must be non-empty NFC text")
        event_times.append(event_time)
        frequencies.add(frequency_id)
        horizons.add(label_horizon)
    if any(later <= earlier for earlier, later in zip(event_times, event_times[1:])):
        raise RealityCheckInputError("period keys must be unique and strictly increasing")
    if len(frequencies) != 1 or len(horizons) != 1:
        raise RealityCheckInputError("period keys must share frequency and label horizon")
    return keys  # type: ignore[return-value]


def _validate_prefix_counts(value: object, *, replication_count: int) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (Mapping, str, bytes, bytearray)):
        raise RealityCheckInputError("prefix_replication_counts must be a sequence")
    counts = tuple(value)
    if not counts:
        raise RealityCheckInputError("prefix_replication_counts must not be empty")
    if any(isinstance(count, bool) or not isinstance(count, int) or count <= 0 for count in counts):
        raise RealityCheckInputError("prefix replication counts must be positive integers")
    if tuple(sorted(counts)) != counts or len(set(counts)) != len(counts):
        raise RealityCheckInputError("prefix replication counts must be strictly increasing and unique")
    if any(count > replication_count for count in counts) or counts[-1] != replication_count:
        raise RealityCheckInputError("prefix schedule must end exactly at replication_count")
    return counts  # type: ignore[return-value]


def _validate_candidates(
    value: object,
    *,
    period_count: int,
) -> tuple[tuple[str, tuple[float, ...]], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (Mapping, str, bytes, bytearray)):
        raise RealityCheckInputError("candidate_net_returns must be an identity-bearing sequence")
    candidates: list[tuple[str, tuple[float, ...]]] = []
    for item in value:
        if (
            not isinstance(item, Sequence)
            or isinstance(item, (Mapping, str, bytes, bytearray))
            or len(item) != 2
        ):
            raise RealityCheckInputError("each candidate must be (config_hash, returns)")
        config_hash = item[0]
        if (
            not isinstance(config_hash, str)
            or not config_hash
            or unicodedata.normalize("NFC", config_hash) != config_hash
        ):
            raise RealityCheckInputError("config hashes must be non-empty NFC strings")
        returns = _float_sequence(item[1], name=f"candidate {config_hash}")
        if len(returns) != period_count:
            raise RealityCheckInputError("candidate columns must align exactly with the common calendar")
        candidates.append((config_hash, returns))
    if len(candidates) < 2:
        raise RealityCheckInputError("at least two candidate configurations are required")
    identities = tuple(config_hash for config_hash, _ in candidates)
    if len(set(identities)) != len(identities):
        raise RealityCheckInputError("candidate config hashes must be unique")
    return tuple(sorted(candidates, key=lambda item: item[0]))


def _tail_result(exceedance_count: int, replication_count: int, alpha: float) -> tuple[MonteCarloTailResult, str]:
    p_value = (1 + exceedance_count) / (1 + replication_count)
    radius = math.sqrt(replication_count) / (replication_count + 1)
    ambiguous = abs(p_value - alpha) <= radius
    decision = "AMBIGUOUS" if ambiguous else ("REJECT" if p_value < alpha else "NON_REJECT")
    return (
        MonteCarloTailResult(
            exceedance_count=exceedance_count,
            replication_count=replication_count,
            p_numerator=1 + exceedance_count,
            p_denominator=1 + replication_count,
            p_value=p_value,
            conditional_variance_formula_id="BINOMIAL_CONDITIONAL_V1",
            conservative_radius=radius,
            ambiguous=ambiguous,
        ),
        decision,
    )


def compute_reality_check_and_spa(
    candidate_net_returns: Sequence[tuple[str, Sequence[float]]],
    benchmark_net_returns: Sequence[float],
    period_keys: Sequence[PeriodKey],
    block_length: int,
    alpha: float,
    seed: int,
    replication_count: int,
    prefix_replication_counts: Sequence[int],
) -> tuple[RealityCheckResult, SpaResult]:
    """Compute deterministic per-block-length White RC and all Hansen SPA variants."""

    benchmark = _float_sequence(benchmark_net_returns, name="benchmark_net_returns")
    period_count = len(benchmark)
    if period_count < _MINIMUM_PERIOD_COUNT:
        raise RealityCheckInputError("period_count must be at least 20")
    _validate_period_keys(period_keys, period_count=period_count)
    candidates = _validate_candidates(candidate_net_returns, period_count=period_count)
    if (
        isinstance(block_length, bool)
        or not isinstance(block_length, int)
        or not 1 <= block_length <= period_count // 2
    ):
        raise RealityCheckInputError("block_length must be in [1, period_count // 2]")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise RealityCheckInputError("alpha must be a finite number in (0, 1)")
    alpha_value = _float64(alpha, name="alpha")
    if not 0.0 < alpha_value < 1.0:
        raise RealityCheckInputError("alpha must be in (0, 1)")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < _UINT64_MODULUS:
        raise RealityCheckInputError("seed must be an unsigned 64-bit integer")
    if (
        isinstance(replication_count, bool)
        or not isinstance(replication_count, int)
        or replication_count <= 0
    ):
        raise RealityCheckInputError("replication_count must be a positive integer")
    prefix_counts = _validate_prefix_counts(
        prefix_replication_counts,
        replication_count=replication_count,
    )

    candidate_order = tuple(config_hash for config_hash, _ in candidates)
    candidate_count = len(candidates)
    differential = tuple(
        tuple(
            0.0 if (candidate_returns[row] - benchmark[row]) == 0.0 else candidate_returns[row] - benchmark[row]
            for row in range(period_count)
        )
        for _, candidate_returns in candidates
    )
    means = tuple(sum(column) / period_count for column in differential)
    centered = tuple(
        tuple(0.0 if (value - means[column]) == 0.0 else value - means[column] for value in differential[column])
        for column in range(candidate_count)
    )

    gamma_zero: list[float] = []
    degeneracy_floors: list[float] = []
    long_run_variances: list[float] = []
    restart_probability = 1.0 / block_length
    for column in centered:
        gamma = tuple(
            sum(column[row] * column[row + lag] for row in range(period_count - lag)) / period_count
            for lag in range(period_count)
        )
        omega_squared = gamma[0]
        for lag in range(1, period_count):
            kappa = (
                (1.0 - lag / period_count) * ((1.0 - restart_probability) ** lag)
                + (lag / period_count) * ((1.0 - restart_probability) ** (period_count - lag))
            )
            omega_squared += 2.0 * kappa * gamma[lag]
        degeneracy_floor = 64.0 * sys.float_info.epsilon * gamma[0]
        if not math.isfinite(gamma[0]) or gamma[0] == 0.0:
            raise RealityCheckInputError("candidate has zero or non-finite gamma[0]")
        if not math.isfinite(omega_squared) or omega_squared <= degeneracy_floor:
            raise RealityCheckInputError("candidate has non-positive or numerically degenerate omega squared")
        gamma_zero.append(0.0 if gamma[0] == 0.0 else gamma[0])
        degeneracy_floors.append(0.0 if degeneracy_floor == 0.0 else degeneracy_floor)
        long_run_variances.append(0.0 if omega_squared == 0.0 else omega_squared)

    sqrt_period_count = math.sqrt(period_count)
    rc_observed = max(sqrt_period_count * mean for mean in means)
    spa_observed = max(
        0.0,
        max(
            sqrt_period_count * means[column] / math.sqrt(long_run_variances[column])
            for column in range(candidate_count)
        ),
    )
    thresholds = tuple(
        -math.sqrt(
            (long_run_variances[column] / period_count)
            * 2.0
            * math.log(math.log(period_count))
        )
        for column in range(candidate_count)
    )
    centers = {
        "LOWER": tuple(max(mean, 0.0) for mean in means),
        "CONSISTENT": tuple(
            means[column] if means[column] >= thresholds[column] else 0.0
            for column in range(candidate_count)
        ),
        "UPPER": means,
    }
    computed_floats = (
        *means,
        *gamma_zero,
        *degeneracy_floors,
        *long_run_variances,
        *thresholds,
        rc_observed,
        spa_observed,
        *(value for variant_centers in centers.values() for value in variant_centers),
    )
    if any(not math.isfinite(value) for value in computed_floats):
        raise RealityCheckInputError("statistical calculation produced a non-finite float64 value")

    rc_exceedances = 0
    spa_exceedances = {variant: 0 for variant in centers}
    prefix_set = set(prefix_counts)
    stream_hash = hashlib.sha256()
    prefix_hashes: list[BootstrapPrefixHash] = []
    for replication, indices in enumerate(
        iter_stationary_bootstrap_indices(
            period_count,
            block_length,
            seed,
            replication_count,
        ),
        start=1,
    ):
        for index in indices:
            stream_hash.update(index.to_bytes(8, byteorder="little", signed=False))
        if replication in prefix_set:
            prefix_hashes.append(
                BootstrapPrefixHash(
                    algorithm_version="QUANTOS_STATIONARY_BOOTSTRAP_V1",
                    block_length=block_length,
                    replication_count=replication,
                    index_stream_sha256=stream_hash.hexdigest(),
                )
            )
        means_star = tuple(
            sum(differential[column][index] for index in indices) / period_count
            for column in range(candidate_count)
        )
        rc_star = max(
            sqrt_period_count * (means_star[column] - means[column])
            for column in range(candidate_count)
        )
        if rc_star >= rc_observed:
            rc_exceedances += 1
        for variant, variant_centers in centers.items():
            spa_star = max(
                0.0,
                max(
                    sqrt_period_count
                    * (means_star[column] - variant_centers[column])
                    / math.sqrt(long_run_variances[column])
                    for column in range(candidate_count)
                ),
            )
            if spa_star >= spa_observed:
                spa_exceedances[variant] += 1

    rc_tail, rc_decision = _tail_result(rc_exceedances, replication_count, alpha_value)
    spa_variants: list[SpaVariantResult] = []
    for variant in ("LOWER", "CONSISTENT", "UPPER"):
        tail, decision = _tail_result(spa_exceedances[variant], replication_count, alpha_value)
        spa_variants.append(
            SpaVariantResult(
                variant=variant,
                centers=centers[variant],
                tail_result=tail,
                decision=decision,
            )
        )
    run_status = (
        "PUBLISHABLE"
        if replication_count >= _PUBLISHABLE_REPLICATION_COUNT
        else "NON_PUBLISHABLE_TEST_RUN"
    )
    prefixes = tuple(prefix_hashes)
    reality_check = RealityCheckResult(
        algorithm_version="WHITE_REALITY_CHECK_V1",
        objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
        period_count=period_count,
        candidate_count=candidate_count,
        block_length=block_length,
        candidate_order=candidate_order,
        column_means=means,
        observed_statistic=rc_observed,
        tail_result=rc_tail,
        prefix_hashes=prefixes,
        decision=rc_decision,
        run_status=run_status,
    )
    spa = SpaResult(
        algorithm_version="HANSEN_SPA_V1",
        objective_version="MEAN_NET_RETURN_DIFFERENTIAL",
        period_count=period_count,
        candidate_count=candidate_count,
        block_length=block_length,
        candidate_order=candidate_order,
        observed_statistic=spa_observed,
        gamma_zero=tuple(gamma_zero),
        degeneracy_floors=tuple(degeneracy_floors),
        long_run_variances=tuple(long_run_variances),
        thresholds=thresholds,
        variants=tuple(spa_variants),
        primary_variant="CONSISTENT",
        prefix_hashes=prefixes,
        run_status=run_status,
    )
    return reality_check, spa


__all__ = [
    "RealityCheckInputError",
    "SplitMix64",
    "block_length_grid_v1",
    "compute_reality_check_and_spa",
    "iter_stationary_bootstrap_indices",
]
