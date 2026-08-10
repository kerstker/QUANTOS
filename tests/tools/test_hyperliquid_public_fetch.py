import pytest

from tools import hyperliquid_public_fetch as hp


def _funding_row(hour: int, *, coin: str = "BTC", rate: float = 0.0001,
                 premium: float = 0.0002) -> dict:
    return {
        "coin": coin,
        "fundingRate": str(rate),
        "premium": str(premium),
        "time": hour * hp.HOUR_MS + 123,
    }


def test_parse_meta_contexts_keeps_public_market_fields_only():
    payload = [
        {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
        [
            {
                "markPx": "100.5", "oraclePx": "100", "midPx": "100.4",
                "funding": "0.00001", "premium": "0.00002",
                "openInterest": "42", "dayNtlVlm": "123456",
            },
            {
                "markPx": "50.5", "oraclePx": "50", "midPx": "50.4",
                "funding": "-0.00001", "premium": "-0.00002",
                "openInterest": "21", "dayNtlVlm": "654321",
            },
        ],
    ]

    rows = hp.parse_meta_and_asset_contexts(payload, observed_at_ms=1_700_000_000_000)

    assert [row["instrument_id"] for row in rows] == ["BTC", "ETH"]
    assert rows[0]["venue"] == "hyperliquid_perp"
    assert rows[0]["knowledge_time_ms"] == 1_700_000_000_000
    assert rows[0]["funding_rate"] == pytest.approx(0.00001)
    assert rows[0]["open_interest"] == pytest.approx(42)
    assert "user" not in rows[0] and "account" not in rows[0]


def test_parse_meta_contexts_rejects_shape_mismatch():
    with pytest.raises(hp.HyperliquidFetchError, match="length mismatch"):
        hp.parse_meta_and_asset_contexts(
            [{"universe": [{"name": "BTC"}]}, []], observed_at_ms=1
        )


def test_fetch_funding_history_paginates_and_deduplicates():
    first = [_funding_row(hour) for hour in range(500)]
    second = [_funding_row(499), _funding_row(500), _funding_row(501)]
    calls = []

    def fake_post(_url, payload, _headers):
        calls.append(dict(payload))
        return first if len(calls) == 1 else second

    rows = hp.fetch_funding_history(
        "BTC", start_ms=0, end_ms=502 * hp.HOUR_MS,
        http_post=fake_post, sleep=0, max_pages=3,
    )

    assert len(rows) == 502
    assert rows[0]["time_ms"] == 123
    assert rows[-1]["time_ms"] == 501 * hp.HOUR_MS + 123
    assert calls[1]["startTime"] == first[-1]["time"] + 1
    assert all(call["type"] == "fundingHistory" for call in calls)


def test_daily_aggregation_requires_24_distinct_completed_hours():
    complete = [_funding_row(hour) for hour in range(24)]
    incomplete = [_funding_row(24 + hour) for hour in range(23)]
    current = [_funding_row(48 + hour) for hour in range(24)]
    parsed = hp.parse_funding_rows(complete + incomplete + current, expected_coin="BTC")

    out = hp.aggregate_daily_funding(parsed, now_ms=48 * hp.HOUR_MS)

    assert list(out) == [0]
    assert out[0]["observation_count"] == 24
    assert out[0]["funding_sum"] == pytest.approx(0.0024)
    assert out[0]["premium_mean"] == pytest.approx(0.0002)


def test_parse_predicted_fundings_keeps_venue_interval_contract():
    payload = [["BTC", [
        ["BinPerp", {"fundingRate": "0.0001", "nextFundingTime": 2000,
                     "fundingIntervalHours": 8}],
        ["HlPerp", {"fundingRate": "0.00002", "nextFundingTime": 1000,
                    "fundingIntervalHours": 1}],
        ["BybitPerp", {"fundingRate": "0.0001", "nextFundingTime": 2000,
                       "fundingIntervalHours": 8}],
    ]]]

    rows = hp.parse_predicted_fundings(payload, observed_at_ms=500)

    assert len(rows) == 3
    assert {row["venue"] for row in rows} == {
        "binance_perp", "hyperliquid_perp", "bybit_perp",
    }
    assert next(row for row in rows if row["venue"] == "hyperliquid_perp")[
        "funding_interval_hours"
    ] == 1


def test_parse_predicted_fundings_does_not_invent_missing_interval():
    rows = hp.parse_predicted_fundings(
        [["BTC", [["BinPerp", {
            "fundingRate": "0.0001", "nextFundingTime": 2000,
        }]]]],
        observed_at_ms=500,
    )

    assert rows[0]["funding_interval_hours"] is None


def test_parse_predicted_fundings_omits_unavailable_zero_time_placeholder():
    rows = hp.parse_predicted_fundings(
        [["NEIROETH", [["BinPerp", {
            "fundingRate": "0.0", "nextFundingTime": 0,
        }]]]],
        observed_at_ms=500,
    )

    assert rows == []


def test_build_snapshot_marks_history_as_public_pit_event_data():
    daily = {
        "BTC": {0: {"funding_sum": 0.001, "premium_mean": 0.002,
                    "observation_count": 24}},
    }
    snapshot = hp.build_snapshot(
        daily_by_coin=daily, current_rows=[], predicted_rows=[],
        fetched_at_ms=1_700_000_000_000,
    )

    assert snapshot["provenance"] == "REAL_HYPERLIQUID_PUBLIC_MARKET_HISTORY"
    assert snapshot["pit_admissible_for_research"] is True
    assert snapshot["credentials_accessed"] is False
    assert snapshot["account_data_accessed"] is False
    assert snapshot["order_capability"] is False
    assert snapshot["assets"]["BTC"]["complete_days"] == 1


def test_merge_snapshot_extends_history_without_rewriting_old_days():
    previous = hp.build_snapshot(
        daily_by_coin={"BTC": {0: {
            "funding_sum": 0.001, "premium_mean": 0.002, "observation_count": 24,
        }}}, current_rows=[], predicted_rows=[], fetched_at_ms=100,
    )
    current = hp.build_snapshot(
        daily_by_coin={"BTC": {hp.DAY_MS: {
            "funding_sum": 0.003, "premium_mean": 0.004, "observation_count": 24,
        }}}, current_rows=[{"instrument_id": "BTC"}],
        predicted_rows=[{"instrument_id": "BTC"}], fetched_at_ms=200,
    )

    merged = hp.merge_snapshots(previous, current)

    assert list(merged["series"]["BTC"]) == ["0", str(hp.DAY_MS)]
    assert merged["fetched_at_ms"] == 200
    assert merged["current_asset_contexts"] == [{"instrument_id": "BTC"}]


def test_merge_snapshot_rejects_changed_immutable_settlement_day():
    previous = hp.build_snapshot(
        daily_by_coin={"BTC": {0: {
            "funding_sum": 0.001, "premium_mean": 0.002, "observation_count": 24,
        }}}, current_rows=[], predicted_rows=[], fetched_at_ms=100,
    )
    changed = hp.build_snapshot(
        daily_by_coin={"BTC": {0: {
            "funding_sum": 0.999, "premium_mean": 0.002, "observation_count": 24,
        }}}, current_rows=[], predicted_rows=[], fetched_at_ms=200,
    )

    with pytest.raises(hp.HyperliquidFetchError, match="historical conflict"):
        hp.merge_snapshots(previous, changed)
