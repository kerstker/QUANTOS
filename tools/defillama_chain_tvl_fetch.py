"""DefiLlama Free API chain-TVL history (public, keyless, research input).

Only the documented free host is used.  The chain-to-traded-asset mapping is
pinned explicitly and verified against `/v2/chains` before histories are read.
Completed UTC days are archived with observation time; no missing value is
filled and no credential, account, broker, or order surface exists here.
"""
from __future__ import annotations

import argparse
import math
import os
import time
import urllib.error
import urllib.parse

from quantos.contracts.feature import FeatureValue
from services.data.fundamental_store import SqliteFundamentalStore
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json


DAY_MS = 86_400_000
HOST = "https://api.llama.fi"
CATALOG_ENDPOINT = "/v2/chains"
HISTORY_ENDPOINT = "/v2/historicalChainTvl"
DEFAULT_OUTPUT = os.path.join("data", "cache", "defillama_chain_tvl.json")
DEFAULT_VINTAGE_DB = os.path.join(
    "data", "cache", "defillama_tvl_vintages.db"
)
SOURCE_NAME = "DefiLlama Free API chain TVL"
SOURCE_BOUNDARY = "DefiLlama Free API / no authentication"
FORWARD_TVL_FEATURE_ID = "defillama_chain_tvl_forward_vintage"
MIN_FORWARD_VINTAGE_INSTRUMENTS = 10

# Fixed from the official chain catalog before historical values were fetched.
# Each asset is unique and exists in the local Binance PIT research universe.
CHAIN_ASSET_MAP = (
    ("Ethereum", "ETH"),
    ("BSC", "BNB"),
    ("Solana", "SOL"),
    ("Bitcoin", "BTC"),
    ("Avalanche", "AVAX"),
    ("OP Mainnet", "OP"),
    ("Stellar", "XLM"),
    ("Near", "NEAR"),
    ("Cardano", "ADA"),
    ("Aptos", "APT"),
    ("XRPL", "XRP"),
    ("ICP", "ICP"),
    ("Injective", "INJ"),
    ("Bitcoincash", "BCH"),
    ("Doge", "DOGE"),
    ("Litecoin", "LTC"),
    ("CosmosHub", "ATOM"),
    ("EthereumClassic", "ETC"),
    ("Filecoin", "FIL"),
)


class DefiLlamaFetchError(RuntimeError):
    """The free source cannot satisfy the pinned data contract."""


def parse_tvl_rows(rows, *, now_ms: int, start_ms: int, diagnostics=None) -> dict[int, float]:
    current_day = (int(now_ms) // DAY_MS) * DAY_MS
    values: dict[int, float] = {}
    accepted_rows = 0
    rejected_rows = 0
    duplicate_rows = 0
    source_rows = rows if isinstance(rows, list) else []
    for row in source_rows:
        try:
            timestamp = int(row["date"]) * 1000
            day = (timestamp // DAY_MS) * DAY_MS
            value = float(row["tvl"])
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected_rows += 1
            continue
        if (
            day < int(start_ms)
            or day >= current_day
            or not math.isfinite(value)
            or value <= 0
        ):
            rejected_rows += 1
            continue
        if day in values:
            duplicate_rows += 1
        else:
            accepted_rows += 1
        values[day] = value
    if diagnostics is not None:
        diagnostics.update(
            {
                "status": "ACCEPTED",
                "input_rows": len(source_rows),
                "accepted_rows": accepted_rows,
                "rejected_rows": rejected_rows,
                "duplicate_rows": duplicate_rows,
            }
        )
    return dict(sorted(values.items()))


def _request(url: str, *, http_get, max_attempts: int, retry_sleep: float):
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    for attempt in range(max_attempts):
        try:
            return http_get(url, {"accept": "application/json"})
        except urllib.error.HTTPError:
            # Policy/rate-limit responses are never amplified by retry.
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            if attempt + 1 >= max_attempts:
                raise
            if retry_sleep:
                time.sleep(retry_sleep * (2**attempt))
    raise AssertionError("unreachable retry loop")


def _validate_mapping(chain_asset_map) -> tuple[tuple[str, str], ...]:
    rows = tuple(
        (str(chain).strip(), str(asset).strip().upper())
        for chain, asset in chain_asset_map
        if str(chain).strip() and str(asset).strip()
    )
    if not rows:
        raise ValueError("chain_asset_map must not be empty")
    if len({chain for chain, _asset in rows}) != len(rows):
        raise ValueError("chain names must be unique")
    if len({asset for _chain, asset in rows}) != len(rows):
        raise ValueError("asset symbols must be unique")
    return rows


def fetch_history(
    *,
    chain_asset_map=CHAIN_ASSET_MAP,
    days: int = 2400,
    now_ms: int | None = None,
    http_get=_http_get_json,
    sleep: float = 0.25,
    retry_sleep: float = 1.0,
    max_attempts: int = 3,
    archive_root=None,
    observed_at_ms: int | None = None,
) -> dict[str, dict]:
    if days <= 0:
        raise ValueError("days must be positive")
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    mapping = _validate_mapping(chain_asset_map)
    catalog_url = f"{HOST}{CATALOG_ENDPOINT}"
    catalog = _request(
        catalog_url,
        http_get=http_get,
        max_attempts=max_attempts,
        retry_sleep=retry_sleep,
    )
    if not isinstance(catalog, list):
        raise DefiLlamaFetchError("chain catalog is not a list")
    catalog_map = {
        str(row.get("name", "")): str(row.get("tokenSymbol", "")).upper()
        for row in catalog
        if isinstance(row, dict)
    }
    mismatch = [
        f"{chain}:{asset}!={catalog_map.get(chain)}"
        for chain, asset in mapping
        if catalog_map.get(chain) != asset
    ]
    if mismatch:
        raise DefiLlamaFetchError("chain catalog mismatch: " + ", ".join(mismatch))
    if archive_root is not None:
        archive_json_response(
            root=archive_root,
            payload=catalog,
            source_uri=catalog_url,
            provider=SOURCE_NAME,
            dataset_id="chain_catalog",
            instrument_id="ALL",
            observed_at_ms=int(observed_at_ms),
            parse_diagnostics={
                "status": "ACCEPTED",
                "input_rows": len(catalog),
                "accepted_rows": len(mapping),
                "rejected_rows": 0,
                "duplicate_rows": 0,
            },
        )

    start_ms = max(0, int(now_ms) - int(days) * DAY_MS)
    result: dict[str, dict] = {}
    for index, (chain, asset) in enumerate(mapping):
        encoded_chain = urllib.parse.quote(chain, safe="")
        url = f"{HOST}{HISTORY_ENDPOINT}/{encoded_chain}"
        rows = _request(
            url,
            http_get=http_get,
            max_attempts=max_attempts,
            retry_sleep=retry_sleep,
        )
        if not isinstance(rows, list):
            raise DefiLlamaFetchError(f"history for {chain} is not a list")
        diagnostics = {}
        values = parse_tvl_rows(
            rows, now_ms=now_ms, start_ms=start_ms, diagnostics=diagnostics
        )
        result[asset] = {"chain": chain, "tvl": values}
        if archive_root is not None:
            archive_json_response(
                root=archive_root,
                payload=rows,
                source_uri=url,
                provider=SOURCE_NAME,
                dataset_id="historical_chain_tvl_1d",
                instrument_id=asset,
                observed_at_ms=int(observed_at_ms),
                parse_diagnostics=diagnostics,
            )
        if sleep and index + 1 < len(mapping):
            time.sleep(sleep)
    return dict(sorted(result.items()))


def _utc_date(timestamp: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(timestamp / 1000))


def build_snapshot(series, *, fetched_at_ms: int) -> dict:
    assets = {}
    serialized = {}
    for asset, row in sorted(series.items()):
        chain = str(row.get("chain", ""))
        values = row.get("tvl") if isinstance(row.get("tvl"), dict) else {}
        timestamps = sorted(values)
        assets[asset] = {
            "chain": chain,
            "days": len(values),
            "first_date": _utc_date(timestamps[0]) if timestamps else None,
            "last_date": _utc_date(timestamps[-1]) if timestamps else None,
        }
        serialized[asset] = {
            "chain": chain,
            "tvl": {str(timestamp): value for timestamp, value in sorted(values.items())},
        }
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "source_uri": f"{HOST}{HISTORY_ENDPOINT}/{{chain}}",
        "provenance": "REAL_DEFILLAMA_FREE_CHAIN_TVL_HISTORY",
        "source_boundary": SOURCE_BOUNDARY,
        "knowledge_time_semantics": (
            "fetched_at_ms is the knowledge time for every imported historical row; "
            "the provider exposes no historical vintages, so event_time alone is not "
            "PIT-admissible and no missing value is imputed"
        ),
        "revision_policy": "RETROSPECTIVE_REVISABLE_NO_VINTAGES",
        "pit_admissible_for_promotion": False,
        "fetched_at_ms": int(fetched_at_ms),
        "fetched_at_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(fetched_at_ms / 1000)
        ),
        "assets": assets,
        "series": serialized,
    }


def forward_vintage_values(
    snapshot: dict, *, min_instruments: int = MIN_FORWARD_VINTAGE_INSTRUMENTS
) -> tuple[FeatureValue, ...]:
    """Project one honestly knowable TVL cross-section from a fresh snapshot.

    The provider's retrospective history is never backfilled.  Only the latest
    completed day with sufficient breadth is recorded, with the actual fetch
    time as knowledge_time.  Later observations of the same event day become
    retained restatements through the bitemporal primary key.
    """
    if min_instruments <= 0:
        raise ValueError("min_instruments must be positive")
    if not isinstance(snapshot, dict) or (
        snapshot.get("source") != SOURCE_NAME
        or snapshot.get("provenance")
        != "REAL_DEFILLAMA_FREE_CHAIN_TVL_HISTORY"
        or snapshot.get("revision_policy")
        != "RETROSPECTIVE_REVISABLE_NO_VINTAGES"
        or snapshot.get("pit_admissible_for_promotion") is not False
    ):
        raise DefiLlamaFetchError("unexpected DefiLlama snapshot contract")
    try:
        fetched_at_ms = int(snapshot["fetched_at_ms"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DefiLlamaFetchError("invalid snapshot fetch time") from exc
    if fetched_at_ms <= 0:
        raise DefiLlamaFetchError("invalid snapshot fetch time")
    current_day = (fetched_at_ms // DAY_MS) * DAY_MS
    series = snapshot.get("series")
    if not isinstance(series, dict):
        raise DefiLlamaFetchError("unexpected DefiLlama snapshot contract")

    by_day: dict[int, dict[str, float]] = {}
    for raw_asset, row in series.items():
        asset = str(raw_asset).strip().upper()
        values = row.get("tvl") if isinstance(row, dict) else None
        if not asset or not isinstance(values, dict):
            continue
        for raw_day, raw_value in values.items():
            try:
                day = int(raw_day)
                value = float(raw_value)
            except (TypeError, ValueError, OverflowError):
                continue
            if (
                day < 0
                or day % DAY_MS != 0
                or day >= current_day
                or not math.isfinite(value)
                or value <= 0
            ):
                continue
            by_day.setdefault(day, {})[asset] = value

    eligible_days = [
        day for day, values in by_day.items() if len(values) >= min_instruments
    ]
    if not eligible_days:
        raise DefiLlamaFetchError(
            "forward vintage breadth below " f"{min_instruments} instruments"
        )
    event_time = max(eligible_days)
    return tuple(
        FeatureValue(
            feature_id=FORWARD_TVL_FEATURE_ID,
            instrument_id=asset,
            event_time=event_time,
            knowledge_time=fetched_at_ms,
            value=value,
        )
        for asset, value in sorted(by_day[event_time].items())
    )


def record_forward_vintage(
    snapshot: dict,
    db_path: str = DEFAULT_VINTAGE_DB,
    *,
    min_instruments: int = MIN_FORWARD_VINTAGE_INSTRUMENTS,
) -> int:
    values = forward_vintage_values(snapshot, min_instruments=min_instruments)
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    store = SqliteFundamentalStore(db_path, create=True)
    try:
        return store.add_many(values)
    finally:
        store.close()


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description="Fetch DefiLlama Free API completed daily chain TVL history"
    )
    parser.add_argument("--days", type=int, default=2400)
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--vintage-db", default=DEFAULT_VINTAGE_DB)
    parser.add_argument("--sleep", type=float, default=0.25)
    args = parser.parse_args(argv)
    now_ms = int(time.time() * 1000)
    series = fetch_history(
        days=args.days,
        now_ms=now_ms,
        sleep=args.sleep,
        archive_root=args.archive_root,
        observed_at_ms=now_ms,
    )
    snapshot = build_snapshot(series, fetched_at_ms=now_ms)
    atomic_write_json(args.out, snapshot, indent=2)
    vintage_rows = record_forward_vintage(snapshot, args.vintage_db)
    for asset, meta in sorted(snapshot["assets"].items()):
        print(f"{asset}: chain={meta['chain']} tvl_days={meta['days']}", flush=True)
    print(f"snapshot -> {args.out}", flush=True)
    print(
        f"forward vintage rows added={vintage_rows} -> {args.vintage_db}",
        flush=True,
    )
    return snapshot


if __name__ == "__main__":
    main()


__all__ = [
    "CHAIN_ASSET_MAP",
    "DEFAULT_VINTAGE_DB",
    "DefiLlamaFetchError",
    "FORWARD_TVL_FEATURE_ID",
    "MIN_FORWARD_VINTAGE_INSTRUMENTS",
    "build_snapshot",
    "fetch_history",
    "forward_vintage_values",
    "parse_tvl_rows",
    "record_forward_vintage",
]
