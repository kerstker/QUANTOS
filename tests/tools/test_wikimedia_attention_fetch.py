import urllib.error

import pytest

from services.data.fundamental_store import SqliteFundamentalStore
from tools import wikimedia_attention_fetch as attention


DAY_MS = 86_400_000
T0 = 1_700_006_400_000


def _item(article: str, day_ms: int, views: object) -> dict:
    from datetime import datetime, timezone

    timestamp = datetime.fromtimestamp(day_ms / 1000, timezone.utc).strftime(
        "%Y%m%d00"
    )
    return {
        "project": "en.wikipedia",
        "article": article,
        "granularity": "daily",
        "timestamp": timestamp,
        "access": "all-access",
        "agent": "user",
        "views": views,
    }


def test_parse_pageviews_keeps_completed_positive_user_rows() -> None:
    diagnostics = {}
    payload = {
        "items": [
            _item("Bitcoin", T0, 100),
            _item("Bitcoin", T0 + DAY_MS, 120),
            _item("Bitcoin", T0 + 2 * DAY_MS, 999),
            _item("Bitcoin", T0 - DAY_MS, 0),
            {**_item("Bitcoin", T0 - 2 * DAY_MS, 5), "agent": "spider"},
        ]
    }

    result = attention.parse_pageviews(
        payload,
        expected_article="Bitcoin",
        now_ms=T0 + 2 * DAY_MS,
        diagnostics=diagnostics,
    )

    assert result == {T0: 100.0, T0 + DAY_MS: 120.0}
    assert diagnostics == {
        "status": "ACCEPTED",
        "input_rows": 5,
        "accepted_rows": 2,
        "rejected_rows": 3,
        "duplicate_rows": 0,
    }


def test_fetch_uses_encoded_article_and_user_only_endpoint() -> None:
    calls = []

    def fake_get(url, headers):
        calls.append((url, headers))
        return {"items": [_item("Bitcoin_Cash", T0, 100)]}

    payload = attention.fetch_article_pageviews(
        article="Bitcoin Cash",
        start_day="20231115",
        end_day="20231116",
        http_get=fake_get,
        retry_sleep=0,
    )

    assert payload["items"][0]["views"] == 100
    assert calls[0][0].endswith(
        "/en.wikipedia.org/all-access/user/Bitcoin%20Cash/daily/20231115/20231116"
    )
    assert "User-Agent" in calls[0][1]


def test_fetch_honors_retry_after_for_rate_limit(monkeypatch) -> None:
    attempts = []
    sleeps = []

    def fake_get(url, _headers):
        attempts.append(url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(
                url, 429, "Too Many Requests", {"Retry-After": "7"}, None
            )
        return {"items": [_item("Bitcoin", T0, 100)]}

    monkeypatch.setattr(attention.time, "sleep", sleeps.append)
    result = attention.fetch_article_pageviews(
        article="Bitcoin",
        start_day="20231115",
        end_day="20231116",
        http_get=fake_get,
        max_attempts=2,
        retry_sleep=1.0,
    )

    assert result["items"][0]["views"] == 100
    assert len(attempts) == 2
    assert sleeps == [7.0]


def test_fetch_does_not_retry_non_transient_http_error(monkeypatch) -> None:
    sleeps = []

    def fake_get(url, _headers):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(attention.time, "sleep", sleeps.append)
    with pytest.raises(urllib.error.HTTPError) as captured:
        attention.fetch_article_pageviews(
            article="Bitcoin",
            start_day="20231115",
            end_day="20231116",
            http_get=fake_get,
            max_attempts=3,
        )

    assert captured.value.code == 404
    assert sleeps == []


def test_snapshot_is_diagnostic_only_and_forward_rows_use_common_day() -> None:
    snapshot = attention.build_snapshot(
        {
            "BTC": {T0: 100.0, T0 + DAY_MS: 110.0},
            "ETH": {T0: 50.0, T0 + DAY_MS: 55.0, T0 + 2 * DAY_MS: 60.0},
        },
        article_map={"BTC": "Bitcoin", "ETH": "Ethereum"},
        fetched_at_ms=T0 + 3 * DAY_MS,
    )

    assert snapshot["provenance"] == "REAL_WIKIMEDIA_USER_PAGEVIEW_HISTORY"
    assert snapshot["revision_policy"] == "RETROSPECTIVE_API_NO_CAPTURED_VINTAGES"
    assert snapshot["pit_admissible_for_promotion"] is False
    assert snapshot["promotion_eligible"] is False
    assert snapshot["credentials_accessed"] is False
    assert snapshot["order_capability"] is False

    values = attention.forward_vintage_values(snapshot)
    assert [
        (row.instrument_id, row.event_time, row.knowledge_time, row.value)
        for row in values
    ] == [
        ("BTC", T0 + DAY_MS, T0 + 3 * DAY_MS, 110.0),
        ("ETH", T0 + DAY_MS, T0 + 3 * DAY_MS, 55.0),
    ]


def test_forward_cross_section_is_atomic_and_idempotent(tmp_path) -> None:
    snapshot = attention.build_snapshot(
        {"BTC": {T0: 100.0}, "ETH": {T0: 50.0}},
        article_map={"BTC": "Bitcoin", "ETH": "Ethereum"},
        fetched_at_ms=T0 + DAY_MS,
    )
    db_path = tmp_path / "attention_vintages.db"

    assert attention.record_forward_vintages(snapshot, str(db_path)) == 2
    assert attention.record_forward_vintages(snapshot, str(db_path)) == 0

    store = SqliteFundamentalStore(str(db_path))
    btc = store.latest_as_of(
        attention.FORWARD_FEATURE_ID, "BTC", T0 + DAY_MS
    )
    eth = store.latest_as_of(
        attention.FORWARD_FEATURE_ID, "ETH", T0 + DAY_MS
    )
    assert btc is not None and btc.value == 100.0
    assert eth is not None and eth.value == 50.0
    store.close()


def test_no_common_completed_day_fails_closed() -> None:
    snapshot = attention.build_snapshot(
        {"BTC": {T0: 100.0}, "ETH": {T0 + DAY_MS: 50.0}},
        article_map={"BTC": "Bitcoin", "ETH": "Ethereum"},
        fetched_at_ms=T0 + 2 * DAY_MS,
    )

    with pytest.raises(attention.WikimediaAttentionError, match="common completed"):
        attention.forward_vintage_values(snapshot)


def test_noncanonical_snapshot_cannot_enter_forward_store() -> None:
    snapshot = attention.build_snapshot(
        {"BTC": {T0: 100.0}},
        article_map={"BTC": "Bitcoin"},
        fetched_at_ms=T0 + DAY_MS,
    )
    snapshot["pit_admissible_for_promotion"] = True

    with pytest.raises(attention.WikimediaAttentionError, match="snapshot contract"):
        attention.forward_vintage_values(snapshot)


def test_corrupt_snapshot_observation_fails_closed() -> None:
    snapshot = attention.build_snapshot(
        {"BTC": {T0: 100.0}},
        article_map={"BTC": "Bitcoin"},
        fetched_at_ms=T0 + DAY_MS,
    )
    snapshot["series"]["BTC"][str(T0)] = -1.0

    with pytest.raises(attention.WikimediaAttentionError, match="invalid attention series"):
        attention.forward_vintage_values(snapshot)
