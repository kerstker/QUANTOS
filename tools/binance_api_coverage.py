"""Emit a deterministic, machine-readable Binance USD-M market coverage report."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from services.data.binance_api_coverage import assess_coverage
from tools.cache_io import atomic_write_json


DEFAULT_CONTRACT = os.path.join(
    "config", "binance_usdm_market_data_coverage.json"
)
DEFAULT_STATUS = os.path.join(
    "data", "cache", "binance_api_coverage_status.json"
)


def build_report(
    *,
    contract_path: str = DEFAULT_CONTRACT,
    generated_at_ms: int | None = None,
) -> dict:
    with open(contract_path, encoding="utf-8") as handle:
        contract = json.load(handle)
    report = assess_coverage(contract)
    report["generated_at_ms"] = (
        int(time.time() * 1_000)
        if generated_at_ms is None
        else int(generated_at_ms)
    )
    report["contract_path"] = contract_path
    return report


def main() -> int:
    report = build_report()
    Path(DEFAULT_STATUS).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(DEFAULT_STATUS, report, indent=2)
    print(
        "binance api coverage: "
        f"{report['accounted_endpoint_count']}/"
        f"{report['catalog_endpoint_count']} accounted, "
        f"{report['gap_count']} gaps"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
