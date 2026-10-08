from __future__ import annotations

from types import SimpleNamespace

import pytest

from quantos.contracts.data import Bar
from services.data.available_time import fetch_bars_for_event_decisions
from services.data.persistent_store import SqlitePITStore


DAY_MS = 86_400_000


def _bar(
    event_time: int,
    knowledge_time: int,
    close: float,
    *,
    instrument_id: str = "BTC",
) -> Bar:
    return Bar(
        instrument_id=instrument_id,
        event_time=event_time,
        knowledge_time=knowledge_time,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
    )


def test_mixed_cache_selects_corrected_close_vintage_and_rejects_legacy() -> None:
    store = SqlitePITStore(":memory:", create=True)
    store.add("BTC", _bar(0, 0, 100.0))
    store.add("BTC", _bar(0, DAY_MS, 101.0))
    store.add("BTC", _bar(DAY_MS, DAY_MS, 102.0))
    store.add("BTC", _bar(DAY_MS, 2 * DAY_MS, 103.0))

    rows = fetch_bars_for_event_decisions(
        store,
        "BTC",
        0,
        DAY_MS,
        decision_lag=DAY_MS,
        minimum_knowledge_lag=DAY_MS,
    )

    assert [(row.event_time, row.knowledge_time, row.close) for row in rows] == [
        (0, DAY_MS, 101.0),
        (DAY_MS, 2 * DAY_MS, 103.0),
    ]


def test_legacy_only_event_fails_closed() -> None:
    store = SqlitePITStore(":memory:", create=True)
    store.add("BTC", _bar(0, 0, 100.0))

    rows = fetch_bars_for_event_decisions(
        store,
        "BTC",
        0,
        0,
        decision_lag=DAY_MS,
        minimum_knowledge_lag=DAY_MS,
    )

    assert rows == []


def test_vintage_announced_after_decision_fails_closed() -> None:
    store = SqlitePITStore(":memory:", create=True)
    store.add("BTC", _bar(0, DAY_MS + 1, 100.0))

    rows = fetch_bars_for_event_decisions(
        store,
        "BTC",
        0,
        0,
        decision_lag=DAY_MS,
        minimum_knowledge_lag=DAY_MS,
    )

    assert rows == []


class _StaticStore:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def fetch_bars(self, instrument_id: str, start: int, end: int) -> list[object]:
        return list(self._rows)


def test_returned_bar_instrument_identity_must_match_request() -> None:
    store = _StaticStore([_bar(0, DAY_MS, 2000.0, instrument_id="ETH")])

    with pytest.raises(ValueError, match="instrument identity"):
        fetch_bars_for_event_decisions(
            store,
            "BTC",
            0,
            0,
            decision_lag=DAY_MS,
            minimum_knowledge_lag=DAY_MS,
        )


def test_store_cannot_return_a_bar_outside_the_requested_window() -> None:
    store = _StaticStore([_bar(2, 2, 100.0)])

    with pytest.raises(ValueError, match="outside the requested event window"):
        fetch_bars_for_event_decisions(
            store,
            "BTC",
            0,
            1,
            decision_lag=1,
            minimum_knowledge_lag=0,
        )


@pytest.mark.parametrize("reverse", (False, True))
def test_equal_temporal_identity_with_changed_content_fails_closed(
    reverse: bool,
) -> None:
    rows = [_bar(0, DAY_MS, 11.0), _bar(0, DAY_MS, 21.0)]
    if reverse:
        rows.reverse()

    with pytest.raises(ValueError, match="temporal identity conflict"):
        fetch_bars_for_event_decisions(
            _StaticStore(rows),
            "BTC",
            0,
            0,
            decision_lag=DAY_MS,
            minimum_knowledge_lag=DAY_MS,
        )


def test_exact_temporal_replay_is_order_independent_and_idempotent() -> None:
    bar = _bar(0, DAY_MS, 11.0)
    rows = fetch_bars_for_event_decisions(
        _StaticStore([bar, bar.model_copy(deep=True)]),
        "BTC",
        0,
        0,
        decision_lag=DAY_MS,
        minimum_knowledge_lag=DAY_MS,
    )

    assert [(row.event_time, row.knowledge_time, row.close) for row in rows] == [
        (0, DAY_MS, 11.0)
    ]


@pytest.mark.parametrize(
    ("event_time", "knowledge_time", "message"),
    (
        (0.0, 1, "bar.event_time"),
        (False, 1, "bar.event_time"),
        (0, "1", "bar.knowledge_time"),
        (-1, 0, "bar.event_time"),
        (2, 1, "knowledge_time cannot precede"),
    ),
)
def test_returned_bar_time_fields_fail_closed_without_coercion(
    event_time: object, knowledge_time: object, message: str
) -> None:
    store = _StaticStore(
        [
            SimpleNamespace(
                instrument_id="BTC",
                event_time=event_time,
                knowledge_time=knowledge_time,
            )
        ]
    )

    with pytest.raises(ValueError, match=message):
        fetch_bars_for_event_decisions(
            store,
            "BTC",
            0,
            10,
            decision_lag=10,
            minimum_knowledge_lag=0,
        )


@pytest.mark.parametrize(
    "kwargs",
    (
        {"decision_lag": True, "minimum_knowledge_lag": 0},
        {"decision_lag": -1, "minimum_knowledge_lag": 0},
        {"decision_lag": 1, "minimum_knowledge_lag": 2},
    ),
)
def test_invalid_clock_policy_is_rejected(kwargs: dict) -> None:
    store = SqlitePITStore(":memory:", create=True)
    with pytest.raises(ValueError):
        fetch_bars_for_event_decisions(store, "BTC", 0, 1, **kwargs)
