"""CFTC Traders in Financial Futures public-data fetcher (keyless/read-only).

Only the official Socrata Futures-Only dataset is queried.  The two canonical
CME contracts are retained; micro and discontinued duplicates are deliberately
excluded.  Historical report rows are assigned a conservative availability
time of report date + 7 days, rather than pretending Tuesday positions were
known before publication.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
import time
import urllib.parse
import urllib.request

from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.cache_io import atomic_write_json


ENDPOINT = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
SOURCE_NAME = "CFTC Traders in Financial Futures - Futures Only"
DEFAULT_OUTPUT = os.path.join("data", "cache", "cftc_tff_crypto.json")
KNOWLEDGE_LAG_DAYS = 7
CONTRACTS = {
    "BTC": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    "ETH": "ETHER CASH SETTLED - CHICAGO MERCANTILE EXCHANGE",
}
_FIELDS = (
    "id", "market_and_exchange_names", "report_date_as_yyyy_mm_dd",
    "open_interest_all", "pct_of_oi_lev_money_long", "pct_of_oi_lev_money_short",
    "pct_of_oi_asset_mgr_long", "pct_of_oi_asset_mgr_short",
    "pct_of_oi_dealer_long_all", "pct_of_oi_dealer_short_all",
    "pct_of_oi_other_rept_long", "pct_of_oi_other_rept_short",
    "pct_of_oi_nonrept_long_all", "pct_of_oi_nonrept_short_all",
)


class CftcFetchError(RuntimeError):
    pass


def iso_date_to_ms(value: str) -> int:
    date = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(date.timestamp() * 1000)


def _number(value, field: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CftcFetchError(f"invalid {field}") from exc
    if not math.isfinite(number) or (positive and number <= 0.0):
        raise CftcFetchError(f"invalid {field}")
    return number


def parse_tff_rows(payload, *, instrument: str, observed_at_ms: int) -> list[dict]:
    instrument = instrument.strip().upper()
    if instrument not in CONTRACTS:
        raise ValueError(f"unsupported CFTC instrument: {instrument}")
    if not isinstance(payload, list):
        raise CftcFetchError("CFTC response must be a list")
    expected_market = CONTRACTS[instrument]
    by_event: dict[int, dict] = {}
    for raw in payload:
        if not isinstance(raw, dict):
            raise CftcFetchError("CFTC row must be an object")
        if raw.get("market_and_exchange_names") != expected_market:
            raise CftcFetchError("CFTC contract mismatch")
        try:
            event_time = iso_date_to_ms(str(raw["report_date_as_yyyy_mm_dd"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CftcFetchError("invalid report date") from exc
        knowledge_time = event_time + KNOWLEDGE_LAG_DAYS * 86_400_000
        if knowledge_time > int(observed_at_ms):
            continue
        lev_long = _number(raw.get("pct_of_oi_lev_money_long"), "leveraged long")
        lev_short = _number(raw.get("pct_of_oi_lev_money_short"), "leveraged short")
        asset_long = _number(raw.get("pct_of_oi_asset_mgr_long"), "asset-manager long")
        asset_short = _number(raw.get("pct_of_oi_asset_mgr_short"), "asset-manager short")
        dealer_long = _number(raw.get("pct_of_oi_dealer_long_all"), "dealer long")
        dealer_short = _number(raw.get("pct_of_oi_dealer_short_all"), "dealer short")
        other_long = _number(raw.get("pct_of_oi_other_rept_long"), "other-reportable long")
        other_short = _number(raw.get("pct_of_oi_other_rept_short"), "other-reportable short")
        nonrep_long = _number(raw.get("pct_of_oi_nonrept_long_all"), "non-reportable long")
        nonrep_short = _number(raw.get("pct_of_oi_nonrept_short_all"), "non-reportable short")
        normalized = {
            "schema_version": 1,
            "instrument_id": instrument,
            "contract": expected_market,
            "source_row_id": str(raw.get("id", "")),
            "event_time_ms": event_time,
            "knowledge_time_ms": knowledge_time,
            "observed_at_ms": int(observed_at_ms),
            "open_interest_contracts": _number(raw.get("open_interest_all"), "open interest", positive=True),
            "leveraged_money_net_pct": lev_long - lev_short,
            "asset_manager_net_pct": asset_long - asset_short,
            "dealer_net_pct": dealer_long - dealer_short,
            "other_reportable_net_pct": other_long - other_short,
            "nonreportable_net_pct": nonrep_long - nonrep_short,
            "source_uri": ENDPOINT,
        }
        existing = by_event.get(event_time)
        if existing is not None and existing != normalized:
            raise CftcFetchError("conflicting duplicate report date")
        by_event[event_time] = normalized
    return [by_event[event_time] for event_time in sorted(by_event)]


def _http_get_json(url: str, headers: dict):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _query_url(market: str, *, limit: int, offset: int) -> str:
    query = {
        "$limit": str(limit),
        "$offset": str(offset),
        "$select": ",".join(_FIELDS),
        "$where": f"market_and_exchange_names='{market.replace(chr(39), chr(39) * 2)}'",
        "$order": "report_date_as_yyyy_mm_dd ASC,id ASC",
    }
    return ENDPOINT + "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)


def fetch_contract_history(
    instrument: str,
    *,
    http_get=_http_get_json,
    page_size: int = 500,
    max_pages: int = 20,
    sleep: float = 0.25,
    archive_root=None,
    observed_at_ms: int | None = None,
) -> list[dict]:
    instrument = instrument.strip().upper()
    if instrument not in CONTRACTS:
        raise ValueError(f"unsupported CFTC instrument: {instrument}")
    if page_size <= 0 or max_pages <= 0:
        raise ValueError("page_size and max_pages must be positive")
    if observed_at_ms is None:
        observed_at_ms = int(time.time() * 1000)
    headers = {"accept": "application/json", "user-agent": "QUANTOS-L2-Public-Research/1.0"}
    by_event: dict[int, dict] = {}
    for page_index in range(max_pages):
        offset = page_index * page_size
        url = _query_url(CONTRACTS[instrument], limit=page_size, offset=offset)
        raw = http_get(url, headers)
        parsed = parse_tff_rows(raw, instrument=instrument, observed_at_ms=observed_at_ms)
        before = len(by_event)
        for row in parsed:
            by_event[row["event_time_ms"]] = row
        if archive_root is not None:
            archive_json_response(
                root=archive_root,
                payload=raw,
                source_uri=url,
                provider=SOURCE_NAME,
                dataset_id="cftc_tff_futures_only_crypto",
                instrument_id=instrument,
                observed_at_ms=int(observed_at_ms),
                parse_diagnostics={
                    "status": "ACCEPTED", "input_rows": len(raw),
                    "accepted_rows": len(parsed),
                    "rejected_rows": len(raw) - len(parsed),
                    "duplicate_rows": len(parsed) - (len(by_event) - before),
                    "page_index": page_index,
                },
            )
        if not isinstance(raw, list) or len(raw) < page_size:
            break
        if len(by_event) == before:
            break
        if sleep:
            time.sleep(sleep)
    else:
        raise CftcFetchError("CFTC pagination exceeded max_pages")
    return [by_event[event_time] for event_time in sorted(by_event)]


def build_snapshot(*, rows_by_instrument: dict[str, list[dict]], fetched_at_ms: int) -> dict:
    series = {instrument: list(rows) for instrument, rows in sorted(rows_by_instrument.items())}
    coverage = {}
    for instrument, rows in series.items():
        coverage[instrument] = {
            "rows": len(rows),
            "first_event_time_ms": rows[0]["event_time_ms"] if rows else None,
            "last_event_time_ms": rows[-1]["event_time_ms"] if rows else None,
            "last_knowledge_time_ms": rows[-1]["knowledge_time_ms"] if rows else None,
        }
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "source_uri": ENDPOINT,
        "provenance": "REAL_CFTC_TFF_FUTURES_ONLY",
        "contracts": dict(CONTRACTS),
        "knowledge_time_policy": "REPORT_DATE_PLUS_7_CALENDAR_DAYS_CONSERVATIVE",
        "knowledge_time_note": (
            "CFTC normally publishes Tuesday positions later in the week; research waits a full "
            "seven calendar days and enters only on the first strictly later market close."
        ),
        "pit_admissible_for_research": True,
        "promotion_eligible": False,
        "credentials_accessed": False,
        "account_data_accessed": False,
        "order_capability": False,
        "fetched_at_ms": int(fetched_at_ms),
        "coverage": coverage,
        "series": series,
    }


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description="Fetch official public CFTC crypto TFF history")
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--sleep", type=float, default=0.25)
    args = parser.parse_args(argv)
    observed_at_ms = int(time.time() * 1000)
    rows_by_instrument = {
        instrument: fetch_contract_history(
            instrument, sleep=args.sleep, archive_root=args.archive_root,
            observed_at_ms=observed_at_ms,
        )
        for instrument in CONTRACTS
    }
    snapshot = build_snapshot(
        rows_by_instrument=rows_by_instrument, fetched_at_ms=observed_at_ms
    )
    atomic_write_json(args.out, snapshot, indent=2)
    print(
        "CFTC TFF public fetch: "
        + ", ".join(f"{instrument}={len(rows)}" for instrument, rows in rows_by_instrument.items())
        + f" -> {args.out}",
        flush=True,
    )
    return snapshot


if __name__ == "__main__":
    main()


__all__ = [
    "CONTRACTS", "CftcFetchError", "build_snapshot", "fetch_contract_history",
    "iso_date_to_ms", "parse_tff_rows",
]
