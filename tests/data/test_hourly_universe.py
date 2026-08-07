from dataclasses import FrozenInstanceError, is_dataclass
from math import inf, nan

import pytest

from services.data.hourly_universe import (
    HourlyUniverse,
    UniverseAdmissionError,
    UniverseObservation,
    UniversePolicy,
    select_hourly_universe,
)


def obs(instrument="BTCUSDT", *, event=100, knowledge=110, volume=100.0,
        spread=5.0, listed=0, coverage=1.0):
    return UniverseObservation(instrument, event, knowledge, volume, spread, listed, coverage)


def policy(**kwargs):
    values = dict(min_size=1, max_size=3, min_listing_age_seconds=0,
                  max_spread_bps=10.0, min_data_coverage=0.8)
    values.update(kwargs)
    return UniversePolicy(**values)


def test_point_in_time_latest_is_causal_and_updates_event_first():
    rows = [
        obs("A", event=100, knowledge=101, volume=1),
        obs("A", event=100, knowledge=105, volume=9),
        obs("A", event=102, knowledge=103, volume=7),
        obs("A", event=102, knowledge=120, volume=99),  # future knowledge
        obs("B", event=101, knowledge=102, volume=8),
        obs("B", event=103, knowledge=104, volume=6),  # future event at as_of=102
    ]
    result = select_hourly_universe(rows, as_of=102, policy=policy())
    assert result.instruments == ("B", "A")
    chosen = {row.instrument: row for row in result.observations}
    assert chosen["A"].event_time == 100
    assert chosen["A"].knowledge_time == 101
    assert chosen["A"].quote_volume == 1
    assert chosen["B"].quote_volume == 8


def test_result_is_input_order_independent_and_sorted_with_tie_break():
    rows = [obs("Z", volume=10), obs("A", volume=10), obs("M", volume=20)]
    first = select_hourly_universe(rows, as_of=200, policy=policy(max_size=3))
    second = select_hourly_universe(reversed(rows), as_of=200, policy=policy(max_size=3))
    assert first == second
    assert first.instruments == ("M", "A", "Z")


def test_policy_boundaries_and_max_size():
    rows = [obs("A", knowledge=100, listed=0, volume=10, spread=10, coverage=0.8),
            obs("B", knowledge=100, volume=20), obs("C", knowledge=100, volume=30)]
    result = select_hourly_universe(rows, as_of=100, policy=policy(max_size=2))
    assert result.instruments == ("C", "B")
    assert select_hourly_universe([obs("A", knowledge=100, listed=0)], as_of=100,
                                  policy=policy(min_listing_age_seconds=100)).instruments == ("A",)


def test_admission_shortfall_is_specialized_and_fail_closed():
    with pytest.raises(UniverseAdmissionError):
        select_hourly_universe([obs("A")], as_of=200, policy=policy(min_size=2))


@pytest.mark.parametrize("field,value", [
    ("event_time", True), ("knowledge_time", False), ("quote_volume", nan),
    ("spread_bps", inf), ("listed_at", -1), ("data_coverage", -0.1),
])
def test_observation_rejects_invalid_values(field, value):
    values = dict(instrument="A", event_time=100, knowledge_time=110,
                  quote_volume=1.0, spread_bps=1.0, listed_at=0, data_coverage=1.0)
    values[field] = value
    with pytest.raises(ValueError):
        UniverseObservation(**values)


def test_observation_rejects_bad_relationships_and_blank_instrument():
    with pytest.raises(ValueError):
        UniverseObservation(" ", 100, 100, 1, 1, 0, 1)
    with pytest.raises(ValueError):
        UniverseObservation("A", 100, 99, 1, 1, 0, 1)
    with pytest.raises(ValueError):
        UniverseObservation("A", 100, 100, 1, 1, 101, 1)
    with pytest.raises(ValueError):
        UniverseObservation("A", 100, 100, 1, 1, 0, 1.1)


def test_duplicate_key_is_idempotent_but_conflict_fails_closed():
    duplicate = obs("A")
    assert select_hourly_universe([duplicate, duplicate], as_of=200, policy=policy()).instruments == ("A",)
    with pytest.raises(ValueError):
        select_hourly_universe([duplicate, obs("A", volume=2)], as_of=200, policy=policy())


@pytest.mark.parametrize("kwargs", [
    dict(min_size=0), dict(min_size=2, max_size=1), dict(max_size=81),
    dict(min_listing_age_seconds=-1), dict(max_spread_bps=-1),
    dict(min_data_coverage=2), dict(min_size=True),
])
def test_policy_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        UniversePolicy(**kwargs)


def test_types_are_frozen_slots_and_selection_is_deterministic():
    assert is_dataclass(UniverseObservation) and is_dataclass(UniversePolicy)
    assert is_dataclass(HourlyUniverse)
    assert all(hasattr(cls, "__slots__") for cls in (UniverseObservation, UniversePolicy, HourlyUniverse))
    result = select_hourly_universe([obs("A")], as_of=200, policy=policy())
    with pytest.raises((FrozenInstanceError, AttributeError)):
        result.instruments = ()
