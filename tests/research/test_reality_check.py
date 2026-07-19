"""Independent scalar oracle tests for White RC and Hansen SPA."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import pytest

from quantos.contracts.canonical import canonical_json_bytes
from quantos.contracts.research import PeriodKey
from services.validation import reality_check as kernel


_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "reality_check_v1.json"
_MASK64 = (1 << 64) - 1
_EPSILON = sys.float_info.epsilon
_CYCLE = (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0, -1.0, -2.0)


class _ReferenceDegeneracy(ValueError):
    def __init__(self, message: str, *, gamma_zero: float, omega_squared: float, floor: float) -> None:
        super().__init__(message)
        self.gamma_zero = gamma_zero
        self.omega_squared = omega_squared
        self.floor = floor


class _MappingSequence(Mapping[Any, Any], Sequence[Any]):
    """Adversarial object that satisfies both ABCs while behaving sequentially."""

    def __init__(self, values: Sequence[Any]) -> None:
        self._values = tuple(values)

    def __getitem__(self, key: Any) -> Any:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class _DuckPeriodMapping(Mapping[str, object]):
    def __init__(self, key: PeriodKey) -> None:
        self._values = key.model_dump(mode="python")

    @property
    def event_time(self) -> int:
        return self._values["event_time"]  # type: ignore[return-value]

    @property
    def frequency_id(self) -> str:
        return self._values["frequency_id"]  # type: ignore[return-value]

    @property
    def label_horizon(self) -> str:
        return self._values["label_horizon"]  # type: ignore[return-value]

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class _PeriodKeySubclass(PeriodKey):
    pass


def _positive_zero(value: float) -> float:
    value = float(value)
    return 0.0 if value == 0.0 else value


def _series(spec: Mapping[str, Any], period_count: int) -> tuple[float, ...]:
    offset = float(spec["offset"])
    amplitude = float(spec["amplitude"])
    phase = int(spec.get("phase", 0))
    if spec.get("kind") == "alternating":
        return tuple(offset + amplitude * (1.0 if index % 2 == 0 else -1.0) for index in range(period_count))
    return tuple(
        offset + amplitude * _CYCLE[(index + phase) % len(_CYCLE)]
        for index in range(period_count)
    )


def _fixture_cases() -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))["cases"])


def _valid_period_keys(period_count: int) -> tuple[PeriodKey, ...]:
    return tuple(
        PeriodKey(
            event_time=1_700_000_000_000_000 + index * 86_400_000_000,
            frequency_id="UTC_DAILY",
            label_horizon="P1D",
        )
        for index in range(period_count)
    )


def _case_inputs(case: Mapping[str, Any]) -> dict[str, Any]:
    period_count = int(case["period_count"])
    candidates = tuple(
        (item["config_hash"], _series(item, period_count))
        for item in case["candidates"]
    )
    return {
        "candidate_net_returns": candidates,
        "benchmark_net_returns": _series(case["benchmark"], period_count),
        "period_keys": _valid_period_keys(period_count),
        "block_length": int(case["block_length"]),
        "alpha": float(case["alpha"]),
        "seed": int(case["seed"]),
        "replication_count": int(case["replication_count"]),
        "prefix_replication_counts": tuple(int(value) for value in case["prefix_replication_counts"]),
    }


class _ReferenceSplitMix64:
    def __init__(self, seed: int) -> None:
        self.state = seed

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & _MASK64
        value = self.state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK64
        return (value ^ (value >> 31)) & _MASK64

    def uniform_below(self, n: int) -> int:
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % n


def _reference_indices(period_count: int, block_length: int, seed: int, count: int):
    generator = _ReferenceSplitMix64(seed)
    for _ in range(count):
        index = generator.uniform_below(period_count)
        row = [index]
        for _ in range(period_count - 1):
            restart_uniform = (generator.next_u64() >> 11) * (2.0**-53)
            if restart_uniform < 1.0 / block_length:
                index = generator.uniform_below(period_count)
            else:
                index = (index + 1) % period_count
            row.append(index)
        yield tuple(row)


def _decision(p_value: float, alpha: float, radius: float) -> str:
    if abs(p_value - alpha) <= radius:
        return "AMBIGUOUS"
    return "REJECT" if p_value < alpha else "NON_REJECT"


def _reference_compute(inputs: Mapping[str, Any]) -> dict[str, Any]:
    ordered_candidates = tuple(sorted(inputs["candidate_net_returns"], key=lambda item: item[0]))
    order = tuple(item[0] for item in ordered_candidates)
    columns = tuple(
        tuple(_positive_zero(value) for value in item[1])
        for item in ordered_candidates
    )
    benchmark = tuple(_positive_zero(value) for value in inputs["benchmark_net_returns"])
    period_count = len(benchmark)
    block_length = inputs["block_length"]
    replication_count = inputs["replication_count"]
    alpha = inputs["alpha"]
    differential = tuple(
        tuple(_positive_zero(columns[column][row] - benchmark[row]) for row in range(period_count))
        for column in range(len(order))
    )
    means = tuple(sum(column) / period_count for column in differential)
    centered = tuple(
        tuple(_positive_zero(value - means[column]) for value in differential[column])
        for column in range(len(order))
    )
    gamma_zero: list[float] = []
    omega_squared: list[float] = []
    floors: list[float] = []
    p_restart = 1.0 / block_length
    for column in centered:
        gamma = tuple(
            sum(column[row] * column[row + lag] for row in range(period_count - lag)) / period_count
            for lag in range(period_count)
        )
        omega = gamma[0]
        for lag in range(1, period_count):
            kappa = (
                (1.0 - lag / period_count) * ((1.0 - p_restart) ** lag)
                + (lag / period_count) * ((1.0 - p_restart) ** (period_count - lag))
            )
            omega += 2.0 * kappa * gamma[lag]
        floor = 64.0 * _EPSILON * gamma[0]
        gamma_zero.append(_positive_zero(gamma[0]))
        omega_squared.append(_positive_zero(omega))
        floors.append(_positive_zero(floor))
        if not math.isfinite(gamma[0]) or gamma[0] == 0.0:
            raise _ReferenceDegeneracy(
                "zero gamma[0]",
                gamma_zero=gamma[0],
                omega_squared=omega,
                floor=floor,
            )
        if not math.isfinite(omega) or omega <= floor:
            raise _ReferenceDegeneracy(
                "degenerate omega squared",
                gamma_zero=gamma[0],
                omega_squared=omega,
                floor=floor,
            )

    sqrt_t = math.sqrt(period_count)
    rc_observed = max(sqrt_t * value for value in means)
    spa_observed = max(
        0.0,
        max(sqrt_t * means[index] / math.sqrt(omega_squared[index]) for index in range(len(order))),
    )
    log_log_t = math.log(math.log(period_count))
    thresholds = tuple(
        -math.sqrt((omega_squared[index] / period_count) * 2.0 * log_log_t)
        for index in range(len(order))
    )
    centers = {
        "LOWER": tuple(max(value, 0.0) for value in means),
        "CONSISTENT": tuple(
            means[index] if means[index] >= thresholds[index] else 0.0
            for index in range(len(order))
        ),
        "UPPER": means,
    }
    rc_exceedances = 0
    rc_equalities = 0
    spa_exceedances = {variant: 0 for variant in centers}
    spa_equalities = {variant: 0 for variant in centers}
    prefix_hashes: dict[int, str] = {}
    stream_hash = hashlib.sha256()

    for replication, indices in enumerate(
        _reference_indices(period_count, block_length, inputs["seed"], replication_count),
        start=1,
    ):
        for index in indices:
            stream_hash.update(index.to_bytes(8, byteorder="little", signed=False))
        if replication in inputs["prefix_replication_counts"]:
            prefix_hashes[replication] = stream_hash.hexdigest()
        means_star = tuple(
            sum(differential[column][index] for index in indices) / period_count
            for column in range(len(order))
        )
        rc_star = max(sqrt_t * (means_star[index] - means[index]) for index in range(len(order)))
        if rc_star >= rc_observed:
            rc_exceedances += 1
        if rc_star == rc_observed:
            rc_equalities += 1
        for variant, variant_centers in centers.items():
            spa_star = max(
                0.0,
                max(
                    sqrt_t * (means_star[index] - variant_centers[index])
                    / math.sqrt(omega_squared[index])
                    for index in range(len(order))
                ),
            )
            if spa_star >= spa_observed:
                spa_exceedances[variant] += 1
            if spa_star == spa_observed:
                spa_equalities[variant] += 1

    radius = math.sqrt(replication_count) / (replication_count + 1)

    def tail(exceedance_count: int) -> dict[str, Any]:
        p_value = (1 + exceedance_count) / (1 + replication_count)
        return {
            "count": exceedance_count,
            "p_value": _positive_zero(p_value),
            "radius": _positive_zero(radius),
            "ambiguous": abs(p_value - alpha) <= radius,
            "decision": _decision(p_value, alpha, radius),
        }

    return {
        "candidate_order": order,
        "column_means": tuple(_positive_zero(value) for value in means),
        "rc_observed": _positive_zero(rc_observed),
        "spa_observed": _positive_zero(spa_observed),
        "gamma_zero": tuple(gamma_zero),
        "floors": tuple(floors),
        "omega_squared": tuple(omega_squared),
        "thresholds": tuple(_positive_zero(value) for value in thresholds),
        "centers": {key: tuple(_positive_zero(value) for value in values) for key, values in centers.items()},
        "rc_tail": tail(rc_exceedances),
        "spa_tails": {variant: tail(value) for variant, value in spa_exceedances.items()},
        "rc_equalities": rc_equalities,
        "spa_equalities": spa_equalities,
        "prefix_hashes": tuple((count, prefix_hashes[count]) for count in inputs["prefix_replication_counts"]),
    }


def _assert_float_hex_vector(actual: Sequence[str], expected: Sequence[float]) -> None:
    assert tuple(float.fromhex(value) for value in actual) == tuple(expected)


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda case: case["name"])
def test_white_rc_and_spa_match_independent_scalar_fixture(case: Mapping[str, Any]):
    inputs = _case_inputs(case)
    if case.get("expect_input_error"):
        with pytest.raises(_ReferenceDegeneracy):
            _reference_compute(inputs)
        with pytest.raises(kernel.RealityCheckInputError):
            kernel.compute_reality_check_and_spa(**inputs)
        return

    expected = _reference_compute(inputs)
    reality_check, spa = kernel.compute_reality_check_and_spa(**inputs)

    assert reality_check.candidate_order == spa.candidate_order == expected["candidate_order"]
    _assert_float_hex_vector(reality_check.column_means, expected["column_means"])
    assert float.fromhex(reality_check.observed_statistic) == expected["rc_observed"]
    assert float.fromhex(spa.observed_statistic) == expected["spa_observed"]
    _assert_float_hex_vector(spa.gamma_zero, expected["gamma_zero"])
    _assert_float_hex_vector(spa.degeneracy_floors, expected["floors"])
    _assert_float_hex_vector(spa.long_run_variances, expected["omega_squared"])
    _assert_float_hex_vector(spa.thresholds, expected["thresholds"])
    assert reality_check.tail_result.exceedance_count == expected["rc_tail"]["count"]
    assert float.fromhex(reality_check.tail_result.p_value) == expected["rc_tail"]["p_value"]
    assert float.fromhex(reality_check.tail_result.conservative_radius) == expected["rc_tail"]["radius"]
    assert reality_check.tail_result.ambiguous is expected["rc_tail"]["ambiguous"]
    assert reality_check.decision == expected["rc_tail"]["decision"]
    assert tuple((item.replication_count, item.index_stream_sha256) for item in reality_check.prefix_hashes) == expected["prefix_hashes"]
    assert reality_check.prefix_hashes == spa.prefix_hashes
    assert reality_check.run_status == spa.run_status == "NON_PUBLISHABLE_TEST_RUN"

    variants = {variant.variant: variant for variant in spa.variants}
    assert tuple(variants) == ("LOWER", "CONSISTENT", "UPPER")
    assert spa.primary_variant == "CONSISTENT"
    for variant_name, variant in variants.items():
        _assert_float_hex_vector(variant.centers, expected["centers"][variant_name])
        assert variant.tail_result.exceedance_count == expected["spa_tails"][variant_name]["count"]
        assert float.fromhex(variant.tail_result.p_value) == expected["spa_tails"][variant_name]["p_value"]
        assert variant.decision == expected["spa_tails"][variant_name]["decision"]

    p_values = tuple(float.fromhex(variants[name].tail_result.p_value) for name in ("LOWER", "CONSISTENT", "UPPER"))
    assert p_values[0] <= p_values[1] <= p_values[2]
    if "expected_rc_decision" in case:
        assert reality_check.decision == case["expected_rc_decision"]
    if "expected_spa_consistent_decision" in case:
        assert variants["CONSISTENT"].decision == case["expected_spa_consistent_decision"]
    if case.get("expect_spa_p_below_rc"):
        assert p_values[1] < float.fromhex(reality_check.tail_result.p_value)
    if case.get("expect_inclusive_equalities"):
        assert expected["rc_equalities"] > 0
        assert expected["spa_equalities"]["CONSISTENT"] > 0
        assert reality_check.tail_result.exceedance_count == inputs["replication_count"]
        assert variants["CONSISTENT"].tail_result.exceedance_count == inputs["replication_count"]
    if case.get("expect_any_primary_ambiguous"):
        assert reality_check.decision == "AMBIGUOUS" or variants["CONSISTENT"].decision == "AMBIGUOUS"


def test_candidate_permutation_is_byte_identical_after_config_hash_sorting():
    cases = {case["name"]: case for case in _fixture_cases()}
    original = kernel.compute_reality_check_and_spa(**_case_inputs(cases["clearly_superior"]))
    permuted = kernel.compute_reality_check_and_spa(**_case_inputs(cases["clearly_superior_permuted"]))

    assert canonical_json_bytes(original[0]) == canonical_json_bytes(permuted[0])
    assert canonical_json_bytes(original[1]) == canonical_json_bytes(permuted[1])


def test_subnormal_fixture_has_positive_gamma_and_degenerate_omega_in_independent_reference():
    case = next(case for case in _fixture_cases() if case["name"] == "omega_squared_degeneracy")

    with pytest.raises(_ReferenceDegeneracy) as caught:
        _reference_compute(_case_inputs(case))

    assert caught.value.gamma_zero > 0.0
    assert caught.value.omega_squared <= caught.value.floor


def test_negative_zero_is_canonical_positive_zero_before_statistics_and_hashes():
    case = next(case for case in _fixture_cases() if case["name"] == "clearly_superior")
    positive_inputs = _case_inputs(case)
    negative_inputs = dict(positive_inputs)
    negative_candidates = list(positive_inputs["candidate_net_returns"])
    first_key, first_values = negative_candidates[0]
    negative_candidates[0] = (first_key, (-0.0,) + tuple(first_values[1:]))
    positive_candidates = list(positive_inputs["candidate_net_returns"])
    positive_candidates[0] = (first_key, (0.0,) + tuple(first_values[1:]))
    negative_inputs["candidate_net_returns"] = tuple(negative_candidates)
    positive_inputs["candidate_net_returns"] = tuple(positive_candidates)

    negative_result = kernel.compute_reality_check_and_spa(**negative_inputs)
    positive_result = kernel.compute_reality_check_and_spa(**positive_inputs)

    assert canonical_json_bytes(negative_result[0]) == canonical_json_bytes(positive_result[0])
    assert canonical_json_bytes(negative_result[1]) == canonical_json_bytes(positive_result[1])


def test_block_length_one_sets_all_kappa_lag_weights_to_zero():
    case = next(case for case in _fixture_cases() if case["name"] == "clearly_superior")
    inputs = _case_inputs(case)
    inputs["block_length"] = 1
    expected = _reference_compute(inputs)

    _, spa = kernel.compute_reality_check_and_spa(**inputs)

    assert expected["omega_squared"] == expected["gamma_zero"]
    _assert_float_hex_vector(spa.long_run_variances, expected["gamma_zero"])


def test_monte_carlo_equality_to_alpha_is_ambiguous():
    case = next(case for case in _fixture_cases() if case["name"] == "clearly_superior")
    inputs = _case_inputs(case)
    first_result, _ = kernel.compute_reality_check_and_spa(**inputs)
    equality_alpha = float.fromhex(first_result.tail_result.p_value)
    inputs["alpha"] = equality_alpha

    equality_result, _ = kernel.compute_reality_check_and_spa(**inputs)

    assert equality_result.tail_result.p_value == equality_alpha.hex()
    assert equality_result.tail_result.ambiguous is True
    assert equality_result.decision == "AMBIGUOUS"


def test_monte_carlo_radius_boundary_is_inclusively_ambiguous():
    tail, decision = kernel._tail_result(exceedance_count=4, replication_count=9, alpha=0.2)

    assert float.fromhex(tail.p_value) == 0.5
    assert float.fromhex(tail.conservative_radius) == 0.3
    assert abs(float.fromhex(tail.p_value) - 0.2) == float.fromhex(tail.conservative_radius)
    assert tail.ambiguous is True
    assert decision == "AMBIGUOUS"


def test_publishable_status_starts_at_ten_thousand_replications():
    case = next(case for case in _fixture_cases() if case["name"] == "clearly_superior")
    inputs = _case_inputs(case)
    inputs["replication_count"] = 10_000
    inputs["prefix_replication_counts"] = (10_000,)

    reality_check, spa = kernel.compute_reality_check_and_spa(**inputs)

    assert reality_check.run_status == spa.run_status == "PUBLISHABLE"


def test_larger_compute_run_reuses_every_smaller_stream_prefix():
    case = next(case for case in _fixture_cases() if case["name"] == "clearly_superior")
    short_inputs = _case_inputs(case)
    short_inputs["replication_count"] = 100
    short_inputs["prefix_replication_counts"] = (10, 100)
    long_inputs = _case_inputs(case)
    long_inputs["replication_count"] = 1000
    long_inputs["prefix_replication_counts"] = (10, 100, 1000)

    short_reality_check, _ = kernel.compute_reality_check_and_spa(**short_inputs)
    long_reality_check, _ = kernel.compute_reality_check_and_spa(**long_inputs)

    assert short_reality_check.prefix_hashes == long_reality_check.prefix_hashes[:2]


def _base_valid_inputs() -> dict[str, Any]:
    case = next(case for case in _fixture_cases() if case["name"] == "clearly_superior")
    inputs = _case_inputs(case)
    inputs["replication_count"] = 20
    inputs["prefix_replication_counts"] = (10, 20)
    return inputs


def _invalid_cases() -> tuple[tuple[str, dict[str, Any]], ...]:
    base = _base_valid_inputs()
    period_count = len(base["period_keys"])
    valid_column = base["candidate_net_returns"][0][1]
    cases: list[tuple[str, dict[str, Any]]] = []

    def add(name: str, **changes: Any) -> None:
        cases.append((name, {**base, **changes}))

    add("K below two", candidate_net_returns=(("only", valid_column),))
    add("ragged column", candidate_net_returns=(("a", valid_column), ("b", valid_column[:-1])))
    for name, value in (("NaN", math.nan), ("positive infinity", math.inf), ("negative infinity", -math.inf)):
        invalid = list(valid_column)
        invalid[3] = value
        add(name, candidate_net_returns=(("a", tuple(invalid)), ("b", valid_column)))
        invalid_benchmark = list(base["benchmark_net_returns"])
        invalid_benchmark[3] = value
        add(f"benchmark {name}", benchmark_net_returns=tuple(invalid_benchmark))
    bool_candidate = list(valid_column)
    bool_candidate[3] = True
    add("candidate bool return", candidate_net_returns=(("a", tuple(bool_candidate)), ("b", valid_column)))
    bool_benchmark = list(base["benchmark_net_returns"])
    bool_benchmark[3] = False
    add("benchmark bool return", benchmark_net_returns=tuple(bool_benchmark))
    huge_integer = 10**10_000
    overflow_candidate = list(valid_column)
    overflow_candidate[3] = huge_integer
    add("candidate conversion overflow", candidate_net_returns=(("a", tuple(overflow_candidate)), ("b", valid_column)))
    overflow_benchmark = list(base["benchmark_net_returns"])
    overflow_benchmark[3] = huge_integer
    add("benchmark conversion overflow", benchmark_net_returns=tuple(overflow_benchmark))
    add("empty keys", period_keys=())
    duplicate_keys = list(base["period_keys"])
    duplicate_keys[2] = duplicate_keys[1]
    add("duplicate keys", period_keys=tuple(duplicate_keys))
    reversed_keys = list(base["period_keys"])
    reversed_keys[1], reversed_keys[2] = reversed_keys[2], reversed_keys[1]
    add("non-monotone keys", period_keys=tuple(reversed_keys))
    add("misaligned keys", period_keys=base["period_keys"][:-1])
    duck_keys = list(base["period_keys"])
    duck_keys[3] = _DuckPeriodMapping(duck_keys[3])
    add("period key duck mapping", period_keys=tuple(duck_keys))
    subclass_keys = list(base["period_keys"])
    subclass_keys[3] = _PeriodKeySubclass(**subclass_keys[3].model_dump(mode="python"))
    add("period key subclass", period_keys=tuple(subclass_keys))
    zero_time = list(base["period_keys"])
    zero_time[0] = PeriodKey.model_construct(event_time=0, frequency_id="UTC_DAILY", label_horizon="P1D")
    add("zero event time", period_keys=tuple(zero_time))
    bool_time = list(base["period_keys"])
    bool_time[0] = PeriodKey.model_construct(event_time=True, frequency_id="UTC_DAILY", label_horizon="P1D")
    add("bool event time", period_keys=tuple(bool_time))
    mixed_frequency = list(base["period_keys"])
    mixed_frequency[3] = PeriodKey(event_time=mixed_frequency[3].event_time, frequency_id="UTC_WEEKLY", label_horizon="P1D")
    add("mixed frequency", period_keys=tuple(mixed_frequency))
    mixed_horizon = list(base["period_keys"])
    mixed_horizon[3] = PeriodKey(event_time=mixed_horizon[3].event_time, frequency_id="UTC_DAILY", label_horizon="P5D")
    add("mixed label horizon", period_keys=tuple(mixed_horizon))
    short_candidates = tuple((key, values[:19]) for key, values in base["candidate_net_returns"])
    add("T below twenty", candidate_net_returns=short_candidates, benchmark_net_returns=base["benchmark_net_returns"][:19], period_keys=base["period_keys"][:19], block_length=4)
    add("block below one", block_length=0)
    add("block above half T", block_length=period_count // 2 + 1)
    add("block bool", block_length=True)
    add("alpha zero", alpha=0.0)
    add("alpha one", alpha=1.0)
    add("alpha bool", alpha=True)
    add("seed negative", seed=-1)
    add("seed overflow", seed=1 << 64)
    add("seed bool", seed=False)
    add("replication bool", replication_count=True, prefix_replication_counts=(1,))
    add("empty prefix schedule", prefix_replication_counts=())
    add("non-positive prefix", prefix_replication_counts=(0, 20))
    add("prefix bool", prefix_replication_counts=(True, 20))
    add("duplicate prefix", prefix_replication_counts=(10, 10, 20))
    add("descending prefix", prefix_replication_counts=(15, 10, 20))
    add("prefix above B", prefix_replication_counts=(10, 21))
    add("terminal prefix differs from B", prefix_replication_counts=(10, 19))
    add("empty config hash", candidate_net_returns=(("", valid_column), ("b", valid_column)))
    add("non-NFC config hash", candidate_net_returns=(("e\u0301", valid_column), ("b", valid_column)))
    add("duplicate config hash", candidate_net_returns=(("duplicate", valid_column), ("duplicate", valid_column)))
    add("benchmark iterator", benchmark_net_returns=iter(base["benchmark_net_returns"]))
    add("candidate container iterator", candidate_net_returns=iter(base["candidate_net_returns"]))
    add("candidate return iterator", candidate_net_returns=(("a", iter(valid_column)), ("b", valid_column)))
    add("period keys iterator", period_keys=iter(base["period_keys"]))
    add("prefix iterator", prefix_replication_counts=iter(base["prefix_replication_counts"]))
    add("benchmark mapping sequence", benchmark_net_returns=_MappingSequence(base["benchmark_net_returns"]))
    add("candidate container mapping sequence", candidate_net_returns=_MappingSequence(base["candidate_net_returns"]))
    add(
        "candidate return mapping sequence",
        candidate_net_returns=(("a", _MappingSequence(valid_column)), ("b", valid_column)),
    )
    add(
        "candidate pair mapping sequence",
        candidate_net_returns=(_MappingSequence(("a", valid_column)), ("b", valid_column)),
    )
    add("period keys mapping sequence", period_keys=_MappingSequence(base["period_keys"]))
    add(
        "prefix mapping sequence",
        prefix_replication_counts=_MappingSequence(base["prefix_replication_counts"]),
    )
    maximum = sys.float_info.max
    add(
        "finite differential overflow",
        candidate_net_returns=(
            ("a", (maximum,) * period_count),
            ("b", tuple(maximum if index % 2 == 0 else maximum / 2.0 for index in range(period_count))),
        ),
        benchmark_net_returns=(-maximum,) * period_count,
    )
    add(
        "finite centered square overflow",
        candidate_net_returns=(
            ("a", tuple(maximum / 2.0 if index % 2 == 0 else -maximum / 2.0 for index in range(period_count))),
            ("b", tuple(maximum / 3.0 if index % 2 == 0 else -maximum / 3.0 for index in range(period_count))),
        ),
        benchmark_net_returns=(0.0,) * period_count,
    )
    return tuple(cases)


@pytest.mark.parametrize(("name", "inputs"), _invalid_cases(), ids=lambda value: value if isinstance(value, str) else None)
def test_invalid_kernel_inputs_raise_typed_fail_closed_error(name: str, inputs: Mapping[str, Any]):
    with pytest.raises(kernel.RealityCheckInputError) as caught:
        kernel.compute_reality_check_and_spa(**inputs)

    assert type(caught.value) is kernel.RealityCheckInputError


@pytest.mark.parametrize(
    ("period_count", "expected"),
    ((20, (1, 2, 4)), (27, (1, 3, 6)), (64, (2, 4, 8)), (125, (2, 5, 10))),
)
def test_block_length_grid_uses_integer_cube_root(period_count: int, expected: tuple[int, ...]):
    assert kernel.block_length_grid_v1(period_count) == expected


@pytest.mark.parametrize("period_count", (0, -1, 19, True, 20.0))
def test_block_length_grid_rejects_invalid_period_count(period_count: object):
    with pytest.raises(kernel.RealityCheckInputError) as caught:
        kernel.block_length_grid_v1(period_count)  # type: ignore[arg-type]

    assert type(caught.value) is kernel.RealityCheckInputError
