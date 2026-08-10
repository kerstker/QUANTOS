from __future__ import annotations

from datetime import datetime, timezone

from services.research.calendar_basis import CalendarBasisPeriod
from services.research.funding_settlement import HOUR_MS
from tools.calendar_basis_research import (
    evaluate_pressure,
    fetch_quarterly_history,
    quarter_delivery_times,
)


def _ms(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def test_quarter_schedule_uses_last_friday_0800_and_includes_next_delivery() -> None:
    assert quarter_delivery_times(
        _ms(2024, 1, 1),
        _ms(2024, 8, 10),
    ) == (
        _ms(2024, 3, 29, 8),
        _ms(2024, 6, 28, 8),
        _ms(2024, 9, 27, 8),
    )


def test_fetch_keeps_only_completed_monday_mark_and_binds_contract_metadata() -> None:
    delivery = _ms(2024, 6, 28, 8)
    monday_open = _ms(2024, 6, 3)
    payload = [
        [monday_open, "100", "102", "99", "101", "0", monday_open + HOUR_MS - 1],
        [monday_open + HOUR_MS, "101", "103", "100", "102", "0", monday_open + 2 * HOUR_MS - 1],
    ]
    calls: list[str] = []

    def http_get(url: str):
        calls.append(url)
        return payload

    history = fetch_quarterly_history(
        start_ms=_ms(2024, 6, 1),
        end_ms=_ms(2024, 6, 10),
        underlyings=("BTCUSDT", "ETHUSDT"),
        delivery_times=(delivery,),
        http_get=http_get,
        archive_root=None,
        observed_at_ms=_ms(2024, 6, 10),
        max_workers=1,
    )

    assert sorted(history) == ["BTCUSDT_240628", "ETHUSDT_240628"]
    row = history["BTCUSDT_240628"][0]
    assert row.underlying_id == "BTCUSDT"
    assert row.delivery_time_ms == delivery
    assert row.decision_time_ms == monday_open + HOUR_MS
    assert row.decision_mark_price == 101.0
    assert len(calls) == 2


def _period(index: int, *, reverse: bool = False) -> CalendarBasisPeriod:
    sign = -1.0 if reverse else 1.0
    edge = 0.006 if index % 2 == 0 else 0.010
    return CalendarBasisPeriod(
        instruments=("BTCUSDT", "BTC_Q", "ETHUSDT", "ETH_Q"),
        signals=(sign, -sign, sign, -sign),
        target_weights=(0.25 * sign, -0.25 * sign, 0.25 * sign, -0.25 * sign),
        forward_returns=(edge, -edge, edge, -edge),
        event_time=index,
    )


def test_pressure_gate_requires_all_frozen_hurdles() -> None:
    passing = evaluate_pressure(
        {
            "basis_convergence": tuple(_period(index) for index in range(104)),
            "basis_divergence_control": tuple(
                _period(index, reverse=True) for index in range(104)
            ),
        }
    )
    assert passing["passes_all"] is True
    assert all(passing["hurdles"].values())

    failing = evaluate_pressure(
        {
            "basis_convergence": tuple(
                CalendarBasisPeriod(
                    instruments=("BTCUSDT", "BTC_Q", "ETHUSDT", "ETH_Q"),
                    signals=(1.0, -1.0, 1.0, -1.0),
                    target_weights=(0.25, -0.25, 0.25, -0.25),
                    forward_returns=(0.0, 0.0, 0.0, 0.0),
                    event_time=index,
                )
                for index in range(104)
            ),
            "basis_divergence_control": tuple(
                _period(index, reverse=True) for index in range(104)
            ),
        }
    )
    assert failing["passes_all"] is False
    assert failing["hurdles"]["base_sharpe_above_2_70"] is False

