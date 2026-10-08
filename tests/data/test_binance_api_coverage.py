import json
from pathlib import Path

import pytest

from services.data.binance_api_coverage import (
    BinanceApiCoverageError,
    assess_coverage,
)
from tools.binance_api_coverage import build_report


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "config" / "binance_usdm_market_data_coverage.json"


def test_checked_in_catalog_accounts_for_all_official_market_endpoints():
    report = build_report(
        contract_path=str(CONTRACT),
        generated_at_ms=1_800_000_000_000,
    )

    assert report["catalog_endpoint_count"] == 34
    assert report["accounted_endpoint_count"] == 34
    assert report["load_bearing_endpoint_count"] == 23
    assert report["gap_count"] == 0
    assert report["gaps"] == []
    assert report["safety"] == {
        "public_market_data_only": True,
        "credentials_loaded": False,
        "account_endpoints": False,
        "trade_endpoints": False,
        "order_submission": False,
    }


def test_catalog_count_and_duplicate_paths_fail_closed():
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    payload["catalog_endpoint_count"] = 35
    with pytest.raises(BinanceApiCoverageError, match="count mismatch"):
        assess_coverage(payload)

    payload["catalog_endpoint_count"] = 34
    payload["capabilities"][1]["path"] = payload["capabilities"][0]["path"]
    with pytest.raises(BinanceApiCoverageError, match="duplicate"):
        assess_coverage(payload)


def test_load_bearing_capability_requires_implementation():
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    item = next(
        row for row in payload["capabilities"] if row["status"] == "LOAD_BEARING"
    )
    item["implementation"] = []
    with pytest.raises(BinanceApiCoverageError, match="lacks implementation"):
        assess_coverage(payload)
