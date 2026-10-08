import json
from pathlib import Path

import pytest

from services.data.binance_stream_coverage import (
    BinanceStreamCoverageError,
    assess_stream_coverage,
)
from tools.binance_stream_coverage import build_report


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "config" / "binance_usdm_ws_stream_coverage.json"


def test_checked_in_contract_accounts_for_all_official_streams():
    report = build_report(
        contract_path=str(CONTRACT),
        generated_at_ms=1_800_000_000_000,
    )

    assert report["catalog_stream_count"] == 20
    assert report["accounted_stream_count"] == 20
    assert report["load_bearing_stream_count"] == 8
    assert report["gap_count"] == 0
    assert report["status_counts"] == {
        "GAP": 0,
        "LOAD_BEARING": 8,
        "NOT_APPLICABLE": 3,
        "REDUNDANT": 9,
    }
    assert report["safety"]["private_streams"] is False
    assert report["safety"]["order_submission"] is False


def test_stream_count_and_duplicate_identity_fail_closed():
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    payload["catalog_stream_count"] = 21
    with pytest.raises(BinanceStreamCoverageError, match="count mismatch"):
        assess_stream_coverage(payload)

    payload["catalog_stream_count"] = 20
    payload["capabilities"][1]["stream"] = payload["capabilities"][0]["stream"]
    payload["capabilities"][1]["route"] = payload["capabilities"][0]["route"]
    with pytest.raises(BinanceStreamCoverageError, match="duplicate"):
        assess_stream_coverage(payload)


def test_load_bearing_stream_requires_implementation():
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    item = next(
        row for row in payload["capabilities"] if row["status"] == "LOAD_BEARING"
    )
    item["implementation"] = []
    with pytest.raises(BinanceStreamCoverageError, match="lacks implementation"):
        assess_stream_coverage(payload)
