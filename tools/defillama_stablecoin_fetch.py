"""DefiLlama global stablecoin-supply history (public, keyless, read-only).

The provider exposes retrospective history without vintages.  Historical rows
are therefore diagnostic only.  The latest completed observation is also
written to a bitemporal forward-vintage store with the actual fetch time as its
knowledge time; only that accumulating series can become PIT evidence later.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request

from quantos.contracts.feature import FeatureValue
from services.data.fundamental_store import SqliteFundamentalStore
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.cache_io import atomic_write_json


DAY_MS = 86_400_000
ENDPOINT = "https://stablecoins.llama.fi/stablecoincharts/all"
SOURCE_NAME = "DefiLlama global stablecoin chart"
DEFAULT_OUTPUT = os.path.join("data", "cache", "defillama_stablecoin_supply.json")
DEFAULT_VINTAGE_DB = os.path.join("data", "cache", "defillama_stablecoin_vintages.db")
FORWARD_FEATURE_ID = "defillama_global_stablecoin_supply_forward_vintage"
FORWARD_INSTRUMENT_ID = "GLOBAL_USD_STABLECOINS"


class DefiLlamaStablecoinError(RuntimeError):
    pass


def _http_get_json(url: str, headers: dict):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def parse_stablecoin_chart(payload, *, now_ms: int, diagnostics=None) -> dict[int, float]:
    current_day = (int(now_ms) // DAY_MS) * DAY_MS
    source_rows = payload if isinstance(payload, list) else []
    values: dict[int, float] = {}
    accepted = rejected = duplicates = 0
    for raw in source_rows:
        try:
            timestamp = int(raw["date"]) * 1000
            day = (timestamp // DAY_MS) * DAY_MS
            value = float(raw["totalCirculatingUSD"]["peggedUSD"])
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected += 1
            continue
        if day < 0 or day >= current_day or not math.isfinite(value) or value <= 0.0:
            rejected += 1
            continue
        if day in values:
            duplicates += 1
        else:
            accepted += 1
        values[day] = value
    if diagnostics is not None:
        diagnostics.update({
            "status": "ACCEPTED", "input_rows": len(source_rows),
            "accepted_rows": accepted, "rejected_rows": rejected,
            "duplicate_rows": duplicates,
        })
    return dict(sorted(values.items()))


def fetch_chart(*, http_get=_http_get_json, max_attempts: int = 3,
                retry_sleep: float = 1.0):
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    headers = {"accept": "application/json", "user-agent": "QUANTOS-L2-Public-Research/1.0"}
    for attempt in range(max_attempts):
        try:
            return http_get(ENDPOINT, headers)
        except urllib.error.HTTPError:
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            if attempt + 1 >= max_attempts:
                raise
            if retry_sleep:
                time.sleep(retry_sleep * (2 ** attempt))
    raise AssertionError("unreachable retry loop")


def build_snapshot(series: dict[int, float], *, fetched_at_ms: int) -> dict:
    timestamps = sorted(series)
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "source_uri": ENDPOINT,
        "provenance": "REAL_DEFILLAMA_GLOBAL_STABLECOIN_HISTORY",
        "metric": "totalCirculatingUSD.peggedUSD",
        "revision_policy": "RETROSPECTIVE_REVISABLE_NO_VINTAGES",
        "knowledge_time_semantics": (
            "fetched_at_ms is the only defensible knowledge time for imported history; "
            "event dates are retained for retrospective association diagnostics only"
        ),
        "pit_admissible_for_promotion": False,
        "promotion_eligible": False,
        "credentials_accessed": False,
        "account_data_accessed": False,
        "order_capability": False,
        "fetched_at_ms": int(fetched_at_ms),
        "coverage": {
            "days": len(series),
            "first_event_time_ms": timestamps[0] if timestamps else None,
            "last_event_time_ms": timestamps[-1] if timestamps else None,
        },
        "series": {str(timestamp): float(value) for timestamp, value in sorted(series.items())},
    }


def forward_vintage_value(snapshot: dict) -> FeatureValue:
    if not isinstance(snapshot, dict) or (
        snapshot.get("provenance") != "REAL_DEFILLAMA_GLOBAL_STABLECOIN_HISTORY"
        or snapshot.get("revision_policy") != "RETROSPECTIVE_REVISABLE_NO_VINTAGES"
        or snapshot.get("pit_admissible_for_promotion") is not False
    ):
        raise DefiLlamaStablecoinError("unexpected stablecoin snapshot contract")
    try:
        fetched_at_ms = int(snapshot["fetched_at_ms"])
        series = {int(key): float(value) for key, value in snapshot["series"].items()}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DefiLlamaStablecoinError("invalid stablecoin snapshot contract") from exc
    eligible = [
        (timestamp, value) for timestamp, value in series.items()
        if timestamp >= 0 and timestamp < (fetched_at_ms // DAY_MS) * DAY_MS
        and math.isfinite(value) and value > 0.0
    ]
    if not eligible:
        raise DefiLlamaStablecoinError("stablecoin snapshot has no completed observation")
    event_time, value = max(eligible)
    return FeatureValue(
        feature_id=FORWARD_FEATURE_ID,
        instrument_id=FORWARD_INSTRUMENT_ID,
        event_time=event_time,
        knowledge_time=fetched_at_ms,
        value=value,
    )


def record_forward_vintage(snapshot: dict, db_path: str = DEFAULT_VINTAGE_DB) -> int:
    row = forward_vintage_value(snapshot)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    store = SqliteFundamentalStore(db_path, create=True)
    try:
        return int(store.add(row))
    finally:
        store.close()


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description="Fetch public global stablecoin supply history")
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--vintage-db", default=DEFAULT_VINTAGE_DB)
    args = parser.parse_args(argv)
    fetched_at_ms = int(time.time() * 1000)
    raw = fetch_chart()
    diagnostics = {}
    series = parse_stablecoin_chart(raw, now_ms=fetched_at_ms, diagnostics=diagnostics)
    if not series:
        raise DefiLlamaStablecoinError("stablecoin history is empty after validation")
    if args.archive_root:
        archive_json_response(
            root=args.archive_root, payload=raw, source_uri=ENDPOINT,
            provider=SOURCE_NAME, dataset_id="global_stablecoin_supply_1d",
            instrument_id=FORWARD_INSTRUMENT_ID, observed_at_ms=fetched_at_ms,
            parse_diagnostics=diagnostics,
        )
    snapshot = build_snapshot(series, fetched_at_ms=fetched_at_ms)
    atomic_write_json(args.out, snapshot, indent=2)
    added = record_forward_vintage(snapshot, args.vintage_db)
    print(
        f"DefiLlama stablecoin supply: days={len(series)}, forward_rows_added={added} -> {args.out}",
        flush=True,
    )
    return snapshot


if __name__ == "__main__":
    main()


__all__ = [
    "DefiLlamaStablecoinError", "FORWARD_FEATURE_ID", "FORWARD_INSTRUMENT_ID",
    "build_snapshot", "fetch_chart", "forward_vintage_value", "parse_stablecoin_chart",
    "record_forward_vintage",
]

