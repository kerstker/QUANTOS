from __future__ import annotations

import pytest

from services.data.fundamental_store import SqliteFundamentalStore
from tools import okx_borrow_fetch as borrow


NOW = 1_800_000_100_000
HOUR_MS = 3_600_000


def _summary_payload(*, amount: str = "") -> dict:
    return {
        "code": "0",
        "msg": "",
        "data": [
            {
                "ccy": "BTC",
                "avgRate": "0.015",
                "preRate": "0.012",
                "estRate": "0.013",
                "avgAmt": amount,
                "avgAmtUsd": "",
            },
            {
                "ccy": "ETH",
                "avgRate": "0.025",
                "preRate": "0.022",
                "estRate": "0.023",
                "avgAmt": "",
                "avgAmtUsd": "",
            },
        ],
    }


def _history_payload(*, ccy: str = "BTC", amount: str = "") -> dict:
    return {
        "code": "0",
        "msg": "",
        "data": [
            {
                "ccy": ccy,
                "rate": "0.012",
                "lendingRate": "0.010",
                "amt": amount,
                "ts": str(NOW - HOUR_MS),
            },
            {
                "ccy": ccy,
                "rate": "0.011",
                "lendingRate": "0.009",
                "amt": "",
                "ts": str(NOW - 2 * HOUR_MS),
            },
        ],
    }


def test_parse_public_summary_keeps_rates_and_never_infers_availability() -> None:
    payload = _summary_payload()
    payload["data"].append(
        {
            "ccy": "OUTSIDE-PINNED-GRAMMAR",
            "avgRate": "1",
            "preRate": "1",
            "estRate": "1",
            "avgAmt": "",
            "avgAmtUsd": "",
        }
    )
    rows = borrow.parse_public_summary(
        payload, observed_at_ms=NOW, expected_currencies=("BTC", "ETH")
    )

    assert [row["currency"] for row in rows] == ["BTC", "ETH"]
    assert rows[0]["annual_average_borrow_rate_24h"] == 0.015
    assert rows[0]["annual_previous_borrow_rate"] == 0.012
    assert rows[0]["annual_estimated_next_borrow_rate"] == 0.013
    assert rows[0]["borrow_availability_amount"] is None
    assert rows[0]["borrow_availability_status"] == (
        "UNAVAILABLE_PUBLIC_AMOUNT_FIELDS_DEPRECATED"
    )
    assert rows[0]["credentials_accessed"] is False
    assert rows[0]["order_capability"] is False


def test_parse_history_is_source_timed_but_known_only_at_fetch_time() -> None:
    rows = borrow.parse_public_history(
        _history_payload(), expected_currency="BTC", observed_at_ms=NOW
    )

    assert [row["event_time_ms"] for row in rows] == [
        NOW - 2 * HOUR_MS,
        NOW - HOUR_MS,
    ]
    assert all(row["knowledge_time_ms"] == NOW for row in rows)
    assert rows[-1]["annual_borrow_rate"] == 0.012
    assert rows[-1]["annual_lending_rate"] == 0.010
    assert rows[-1]["borrow_availability_amount"] is None


def test_parser_fails_closed_on_changed_amount_contract_or_future_history() -> None:
    with pytest.raises(borrow.OkxBorrowFetchError, match="deprecated amount"):
        borrow.parse_public_summary(
            _summary_payload(amount="100"),
            observed_at_ms=NOW,
            expected_currencies=("BTC", "ETH"),
        )
    with pytest.raises(borrow.OkxBorrowFetchError, match="deprecated amount"):
        borrow.parse_public_history(
            _history_payload(amount="100"),
            expected_currency="BTC",
            observed_at_ms=NOW,
        )
    future = _history_payload()
    future["data"][0]["ts"] = str(NOW + HOUR_MS)
    with pytest.raises(borrow.OkxBorrowFetchError, match="future event"):
        borrow.parse_public_history(
            future, expected_currency="BTC", observed_at_ms=NOW
        )


def test_snapshot_and_forward_vintages_keep_history_and_summary_separate(tmp_path) -> None:
    summary = borrow.parse_public_summary(
        _summary_payload(), observed_at_ms=NOW, expected_currencies=("BTC", "ETH")
    )
    history = {
        "BTC": borrow.parse_public_history(
            _history_payload(), expected_currency="BTC", observed_at_ms=NOW
        ),
        "ETH": borrow.parse_public_history(
            _history_payload(ccy="ETH"), expected_currency="ETH", observed_at_ms=NOW
        ),
    }
    snapshot = borrow.build_snapshot(
        summary,
        history,
        observed_at_ms=NOW,
        requested_currencies=("BTC", "ETH"),
        raw_artifacts=("a" * 64, "b" * 64, "c" * 64),
    )
    assert snapshot["provenance"] == "REAL_OKX_PUBLIC_BORROW_RATES"
    assert snapshot["formal_forward_credit_start_ms"] == NOW
    assert snapshot["borrow_availability"] == (
        "UNAVAILABLE_PUBLIC_AMOUNT_FIELDS_DEPRECATED"
    )
    assert snapshot["promotion_eligible"] is False

    db_path = tmp_path / "okx_borrow_vintages.db"
    assert borrow.record_forward_vintages(snapshot, str(db_path)) == 10
    assert borrow.record_forward_vintages(snapshot, str(db_path)) == 0

    store = SqliteFundamentalStore(str(db_path))
    latest = store.latest_as_of(
        borrow.SETTLED_BORROW_FEATURE_ID, "BTC", NOW
    )
    assert latest is not None
    assert (latest.event_time, latest.knowledge_time, latest.value) == (
        NOW - HOUR_MS,
        NOW,
        0.012,
    )
    assert store.latest_as_of("okx_borrow_availability", "BTC", NOW) is None
    store.close()


def test_missing_requested_currency_is_explicit_not_silently_dropped() -> None:
    rows = borrow.parse_public_summary(
        _summary_payload(),
        observed_at_ms=NOW,
        expected_currencies=("BTC", "ETH", "SOL"),
    )
    by_ccy = {row["currency"]: row for row in rows}
    assert by_ccy["SOL"]["rate_status"] == "MISSING_PUBLIC_SUMMARY"
    assert by_ccy["SOL"]["annual_average_borrow_rate_24h"] is None
