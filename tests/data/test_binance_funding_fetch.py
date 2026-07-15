import json
import math

import pytest

from services.data.raw_archive import summarize_archive
from tools.binance_funding_fetch import (
    FundingDataError,
    fetch_daily_funding,
    validate_daily_funding,
)

_DAY = 86_400_000
_HOUR = 3_600_000
_NOW = 10 * _DAY


def _http(payload):
    def get(_url, _headers=None):
        return payload

    return get


def test_fetch_daily_funding_sums_valid_observations():
    day = 8 * _DAY
    payload = [
        {"fundingTime": day, "fundingRate": "0.001"},
        {"fundingTime": day + 8 * _HOUR, "fundingRate": "-0.0002"},
        {"fundingTime": day + 16 * _HOUR, "fundingRate": "0.0003"},
    ]

    out = fetch_daily_funding("BTC", days=5, http_get=_http(payload), now_ms=_NOW)

    assert out == {day: pytest.approx(0.0011)}


def test_fetch_daily_funding_archives_decoded_response_with_parse_counts(tmp_path):
    day = 8 * _DAY
    payload = [
        {"fundingTime": day, "fundingRate": "0.001"},
        {"fundingTime": day + 8 * _HOUR, "fundingRate": "-0.0002"},
    ]

    out = fetch_daily_funding(
        "BTC",
        days=5,
        http_get=_http(payload),
        now_ms=_NOW,
        archive_root=str(tmp_path),
        observed_at_ms=_NOW,
    )

    assert out == {day: pytest.approx(0.0008)}
    lineage = summarize_archive(str(tmp_path))
    assert lineage["status"] == "PASS"
    assert lineage["datasets"] == {"funding": 1}
    assert lineage["representations"] == ["CANONICAL_JSON_UTF8"]
    observation = json.loads(next(tmp_path.glob("observations/sha256/*/*.json")).read_text())
    assert observation["parse_diagnostics"] == {
        "accepted_rows": 2,
        "duplicate_rows": 0,
        "input_rows": 2,
        "rejected_rows": 0,
        "status": "ACCEPTED",
    }


@pytest.mark.parametrize(
    "bad_item",
    [
        {"fundingTime": 8 * _DAY},
        {"fundingRate": "0.001"},
        {"fundingTime": "bad", "fundingRate": "0.001"},
        {"fundingTime": 8 * _DAY, "fundingRate": "nan"},
    ],
)
def test_fetch_daily_funding_rejects_malformed_observation(bad_item):
    with pytest.raises(FundingDataError, match="observation"):
        fetch_daily_funding(
            "BTC", days=5, http_get=_http([bad_item]), now_ms=_NOW
        )


def test_fetch_daily_funding_rejects_duplicate_or_out_of_order_timestamp():
    day = 8 * _DAY
    payload = [
        {"fundingTime": day + 8 * _HOUR, "fundingRate": "0.001"},
        {"fundingTime": day, "fundingRate": "0.001"},
    ]

    with pytest.raises(FundingDataError, match="strictly increasing"):
        fetch_daily_funding("BTC", days=5, http_get=_http(payload), now_ms=_NOW)


def test_validate_daily_funding_rejects_gap_and_nonfinite_value():
    with pytest.raises(FundingDataError, match="completeness"):
        validate_daily_funding(
            "BTC",
            {7 * _DAY: 0.001, 9 * _DAY: 0.002},
            expected_end=9 * _DAY,
        )

    with pytest.raises(FundingDataError, match="non-finite"):
        validate_daily_funding(
            "BTC", {9 * _DAY: math.inf}, expected_end=9 * _DAY
        )


def test_validate_daily_funding_rejects_off_grid_or_post_boundary_day():
    with pytest.raises(FundingDataError, match="UTC-day grid"):
        validate_daily_funding(
            "BTC", {9 * _DAY + 1: 0.001}, expected_end=9 * _DAY
        )

    with pytest.raises(FundingDataError, match="expected end"):
        validate_daily_funding(
            "BTC", {9 * _DAY: 0.001, 10 * _DAY: 0.001}, expected_end=9 * _DAY
        )
