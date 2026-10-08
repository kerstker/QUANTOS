"""OKX public borrow-rate collector (keyless, read-only, no account surface).

The two official public Savings endpoints expose current annual borrowing-rate
summaries and settled hourly borrowing/lending rates.  Their amount fields are
deprecated and currently empty.  This collector therefore records borrow cost
only and keeps borrow availability explicitly unavailable; a future non-empty
amount response fails closed until its semantics receive a separate review.

Retrospective hourly rows retain their source event time but enter the
bitemporal forward-vintage store with the actual fetch time as knowledge time.
No downloaded history is back-dated into formal forward evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from quantos.contracts.feature import FeatureValue
from services.data.fundamental_store import SqliteFundamentalStore
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.cache_io import atomic_write_json


HOST = "https://www.okx.com"
SUMMARY_ENDPOINT = f"{HOST}/api/v5/finance/savings/lending-rate-summary"
HISTORY_ENDPOINT = f"{HOST}/api/v5/finance/savings/lending-rate-history"
SOURCE_NAME = "OKX public Savings borrow-rate API"
DEFAULT_OUTPUT = os.path.join("data", "cache", "okx_public_borrow_rates.json")
DEFAULT_VINTAGE_DB = os.path.join("data", "cache", "okx_borrow_vintages.db")
DEFAULT_CURRENCIES = (
    "ADA", "ATOM", "AVAX", "BCH", "BNB", "BTC", "DOGE", "DOT",
    "ETH", "LINK", "LTC", "SOL", "UNI", "XRP",
)
AVERAGE_BORROW_FEATURE_ID = "okx_borrow_average_24h_annual"
PREVIOUS_BORROW_FEATURE_ID = "okx_borrow_previous_annual"
ESTIMATED_BORROW_FEATURE_ID = "okx_borrow_estimated_next_annual"
SETTLED_BORROW_FEATURE_ID = "okx_borrow_settled_annual"
SETTLED_LENDING_FEATURE_ID = "okx_lending_settled_annual"
_CURRENCY = re.compile(r"[A-Z0-9]{1,20}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class OkxBorrowFetchError(RuntimeError):
    """The public response cannot satisfy the reviewed borrow-rate contract."""


def _number(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OkxBorrowFetchError(f"invalid {field}") from exc
    if not math.isfinite(result) or result < 0.0:
        raise OkxBorrowFetchError(f"invalid {field}")
    return result


def _currency(value: object) -> str:
    result = str(value).strip().upper()
    if _CURRENCY.fullmatch(result) is None:
        raise OkxBorrowFetchError("invalid currency")
    return result


def _response_rows(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        raise OkxBorrowFetchError("OKX response is not an object")
    if payload.get("code") != "0" or payload.get("msg") not in (None, ""):
        raise OkxBorrowFetchError("OKX public endpoint returned an error")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise OkxBorrowFetchError("OKX response data is not a list")
    if not all(isinstance(row, dict) for row in rows):
        raise OkxBorrowFetchError("OKX response contains a non-object row")
    return rows


def _require_deprecated_amounts_empty(row: dict, fields: tuple[str, ...]) -> None:
    if any(row.get(field) not in (None, "") for field in fields):
        raise OkxBorrowFetchError(
            "deprecated amount field became populated; availability semantics require review"
        )


def parse_public_summary(
    payload: object,
    *,
    observed_at_ms: int,
    expected_currencies=DEFAULT_CURRENCIES,
) -> list[dict]:
    """Validate current public rates without inferring a lendable amount."""
    observed = int(observed_at_ms)
    if observed < 0:
        raise ValueError("observed_at_ms must be non-negative")
    expected = tuple(dict.fromkeys(_currency(value) for value in expected_currencies))
    if not expected:
        raise ValueError("expected_currencies must not be empty")
    expected_set = frozenset(expected)
    by_currency: dict[str, dict] = {}
    for raw in _response_rows(payload):
        raw_currency = str(raw.get("ccy", "")).strip().upper()
        # OKX may list product tokens whose naming grammar is outside the
        # pinned research universe. They are irrelevant to this contract and
        # must not make an otherwise valid pinned cross-section unavailable.
        if raw_currency not in expected_set:
            continue
        currency = _currency(raw_currency)
        if currency in by_currency:
            raise OkxBorrowFetchError(f"duplicate summary currency: {currency}")
        _require_deprecated_amounts_empty(raw, ("avgAmt", "avgAmtUsd"))
        by_currency[currency] = {
            "schema_version": 1,
            "venue": "okx",
            "currency": currency,
            "event_time_ms": observed,
            "knowledge_time_ms": observed,
            "observed_at_ms": observed,
            "rate_status": "AVAILABLE",
            "annual_average_borrow_rate_24h": _number(raw.get("avgRate"), "avgRate"),
            "annual_previous_borrow_rate": _number(raw.get("preRate"), "preRate"),
            "annual_estimated_next_borrow_rate": _number(raw.get("estRate"), "estRate"),
            "borrow_availability_amount": None,
            "borrow_availability_status": (
                "UNAVAILABLE_PUBLIC_AMOUNT_FIELDS_DEPRECATED"
            ),
            "credentials_accessed": False,
            "account_data_accessed": False,
            "order_capability": False,
            "source_uri": SUMMARY_ENDPOINT,
        }
    output = []
    for currency in expected:
        if currency in by_currency:
            output.append(by_currency[currency])
            continue
        output.append(
            {
                "schema_version": 1,
                "venue": "okx",
                "currency": currency,
                "event_time_ms": observed,
                "knowledge_time_ms": observed,
                "observed_at_ms": observed,
                "rate_status": "MISSING_PUBLIC_SUMMARY",
                "annual_average_borrow_rate_24h": None,
                "annual_previous_borrow_rate": None,
                "annual_estimated_next_borrow_rate": None,
                "borrow_availability_amount": None,
                "borrow_availability_status": (
                    "UNAVAILABLE_PUBLIC_AMOUNT_FIELDS_DEPRECATED"
                ),
                "credentials_accessed": False,
                "account_data_accessed": False,
                "order_capability": False,
                "source_uri": SUMMARY_ENDPOINT,
            }
        )
    return output


def parse_public_history(
    payload: object,
    *,
    expected_currency: str,
    observed_at_ms: int,
) -> list[dict]:
    """Validate settled hourly rates with source time and fetch knowledge time."""
    expected = _currency(expected_currency)
    observed = int(observed_at_ms)
    if observed < 0:
        raise ValueError("observed_at_ms must be non-negative")
    by_time: dict[int, dict] = {}
    for raw in _response_rows(payload):
        currency = _currency(raw.get("ccy"))
        if currency != expected:
            raise OkxBorrowFetchError(
                f"history currency mismatch: expected {expected}, received {currency}"
            )
        _require_deprecated_amounts_empty(raw, ("amt",))
        try:
            event_time = int(raw["ts"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise OkxBorrowFetchError("invalid history timestamp") from exc
        if event_time < 0 or event_time > observed:
            raise OkxBorrowFetchError("history contains a future event")
        row = {
            "schema_version": 1,
            "venue": "okx",
            "currency": currency,
            "event_time_ms": event_time,
            "knowledge_time_ms": observed,
            "observed_at_ms": observed,
            "annual_borrow_rate": _number(raw.get("rate"), "rate"),
            "annual_lending_rate": _number(raw.get("lendingRate"), "lendingRate"),
            "borrow_availability_amount": None,
            "borrow_availability_status": (
                "UNAVAILABLE_PUBLIC_AMOUNT_FIELDS_DEPRECATED"
            ),
            "credentials_accessed": False,
            "account_data_accessed": False,
            "order_capability": False,
            "source_uri": HISTORY_ENDPOINT,
        }
        existing = by_time.get(event_time)
        if existing is not None and existing != row:
            raise OkxBorrowFetchError("conflicting duplicate history timestamp")
        by_time[event_time] = row
    return [by_time[timestamp] for timestamp in sorted(by_time)]


def build_snapshot(
    summary_rows,
    history_by_currency,
    *,
    observed_at_ms: int,
    requested_currencies=DEFAULT_CURRENCIES,
    raw_artifacts=(),
) -> dict:
    requested = tuple(dict.fromkeys(_currency(value) for value in requested_currencies))
    summary = tuple(summary_rows)
    if tuple(row.get("currency") for row in summary) != requested:
        raise OkxBorrowFetchError("summary rows do not match requested currency order")
    history = {}
    for currency in requested:
        rows = tuple(history_by_currency.get(currency, ()))
        if any(row.get("currency") != currency for row in rows):
            raise OkxBorrowFetchError("history rows do not match requested currency")
        history[currency] = list(rows)
    artifacts = tuple(str(value) for value in raw_artifacts)
    if any(_SHA256.fullmatch(value) is None for value in artifacts):
        raise OkxBorrowFetchError("invalid raw archive digest")
    available = sum(row.get("rate_status") == "AVAILABLE" for row in summary)
    history_count = sum(len(rows) for rows in history.values())
    observed = int(observed_at_ms)
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "source_uris": [SUMMARY_ENDPOINT, HISTORY_ENDPOINT],
        "provenance": "REAL_OKX_PUBLIC_BORROW_RATES",
        "availability": "REAL_RECENT_PLUS_FORWARD",
        "revision_policy": (
            "SETTLED_SOURCE_TIMES_IMPORTED_AT_ACTUAL_FETCH_KNOWLEDGE_TIME"
        ),
        "formal_forward_credit_start_ms": observed,
        "historical_rows_pit_before_fetch": False,
        "promotion_eligible": False,
        "borrow_availability": "UNAVAILABLE_PUBLIC_AMOUNT_FIELDS_DEPRECATED",
        "credentials_accessed": False,
        "account_data_accessed": False,
        "order_capability": False,
        "observed_at_ms": observed,
        "requested_currencies": list(requested),
        "coverage": {
            "requested_currency_count": len(requested),
            "summary_available_count": available,
            "summary_missing_count": len(requested) - available,
            "history_row_count": history_count,
        },
        "raw_artifact_sha256": list(artifacts),
        "summary": list(summary),
        "history": history,
    }


def _feature_values(snapshot: dict) -> tuple[FeatureValue, ...]:
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("provenance") != "REAL_OKX_PUBLIC_BORROW_RATES"
        or snapshot.get("promotion_eligible") is not False
        or snapshot.get("borrow_availability")
        != "UNAVAILABLE_PUBLIC_AMOUNT_FIELDS_DEPRECATED"
    ):
        raise OkxBorrowFetchError("unexpected borrow snapshot contract")
    try:
        observed = int(snapshot["observed_at_ms"])
        requested = tuple(snapshot["requested_currencies"])
        summary = tuple(snapshot["summary"])
        history = snapshot["history"]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise OkxBorrowFetchError("invalid borrow snapshot contract") from exc
    if tuple(row.get("currency") for row in summary) != requested:
        raise OkxBorrowFetchError("invalid borrow snapshot currency order")
    values = []
    summary_features = (
        (AVERAGE_BORROW_FEATURE_ID, "annual_average_borrow_rate_24h"),
        (PREVIOUS_BORROW_FEATURE_ID, "annual_previous_borrow_rate"),
        (ESTIMATED_BORROW_FEATURE_ID, "annual_estimated_next_borrow_rate"),
    )
    for row in summary:
        currency = _currency(row.get("currency"))
        if int(row.get("knowledge_time_ms", -1)) != observed:
            raise OkxBorrowFetchError("summary knowledge time mismatch")
        for feature_id, field in summary_features:
            raw_value = row.get(field)
            if raw_value is None:
                continue
            values.append(
                FeatureValue(
                    feature_id=feature_id,
                    instrument_id=currency,
                    event_time=observed,
                    knowledge_time=observed,
                    value=_number(raw_value, field),
                )
            )
        rows = tuple(history.get(currency, ()))
        if not rows:
            continue
        latest = max(rows, key=lambda item: int(item["event_time_ms"]))
        event_time = int(latest["event_time_ms"])
        if event_time > observed or int(latest.get("knowledge_time_ms", -1)) != observed:
            raise OkxBorrowFetchError("history timing mismatch")
        values.extend(
            (
                FeatureValue(
                    feature_id=SETTLED_BORROW_FEATURE_ID,
                    instrument_id=currency,
                    event_time=event_time,
                    knowledge_time=observed,
                    value=_number(latest.get("annual_borrow_rate"), "annual_borrow_rate"),
                ),
                FeatureValue(
                    feature_id=SETTLED_LENDING_FEATURE_ID,
                    instrument_id=currency,
                    event_time=event_time,
                    knowledge_time=observed,
                    value=_number(latest.get("annual_lending_rate"), "annual_lending_rate"),
                ),
            )
        )
    return tuple(values)


def record_forward_vintages(snapshot: dict, db_path: str = DEFAULT_VINTAGE_DB) -> int:
    values = _feature_values(snapshot)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    store = SqliteFundamentalStore(db_path, create=True)
    try:
        return int(store.add_many(values))
    finally:
        store.close()


def _http_get_json(url: str, headers: dict):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _request_json(
    url: str,
    *,
    http_get=_http_get_json,
    max_attempts: int = 3,
    retry_sleep: float = 1.0,
):
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    headers = {
        "accept": "application/json",
        "user-agent": "QUANTOS-L2-Public-Research/1.0",
    }
    for attempt in range(max_attempts):
        try:
            return http_get(url, headers)
        except urllib.error.HTTPError:
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            if attempt + 1 >= max_attempts:
                raise
            if retry_sleep:
                time.sleep(retry_sleep * (2**attempt))
    raise AssertionError("unreachable retry loop")


def fetch_public_borrow_rates(
    currencies=DEFAULT_CURRENCIES,
    *,
    history_limit: int = 100,
    request_sleep: float = 0.4,
    http_get=_http_get_json,
) -> tuple[object, dict[str, object]]:
    requested = tuple(dict.fromkeys(_currency(value) for value in currencies))
    if not requested:
        raise ValueError("currencies must not be empty")
    if history_limit < 1 or history_limit > 100:
        raise ValueError("history_limit must be in [1, 100]")
    summary = _request_json(SUMMARY_ENDPOINT, http_get=http_get)
    history = {}
    for index, currency in enumerate(requested):
        if index and request_sleep:
            time.sleep(request_sleep)
        query = urllib.parse.urlencode({"ccy": currency, "limit": history_limit})
        history[currency] = _request_json(
            f"{HISTORY_ENDPOINT}?{query}", http_get=http_get
        )
    return summary, history


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description="Fetch public OKX borrow rates")
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    parser.add_argument("--archive-root", default=DEFAULT_RAW_ARCHIVE)
    parser.add_argument("--vintage-db", default=DEFAULT_VINTAGE_DB)
    parser.add_argument("--currencies", default=",".join(DEFAULT_CURRENCIES))
    parser.add_argument("--history-limit", type=int, default=100)
    parser.add_argument("--request-sleep", type=float, default=0.4)
    args = parser.parse_args(argv)
    requested = tuple(
        _currency(value) for value in str(args.currencies).split(",") if value.strip()
    )
    observed_at_ms = int(time.time() * 1000)
    raw_summary, raw_history = fetch_public_borrow_rates(
        requested,
        history_limit=args.history_limit,
        request_sleep=args.request_sleep,
    )
    summary = parse_public_summary(
        raw_summary,
        observed_at_ms=observed_at_ms,
        expected_currencies=requested,
    )
    history = {
        currency: parse_public_history(
            raw_history[currency],
            expected_currency=currency,
            observed_at_ms=observed_at_ms,
        )
        for currency in requested
    }
    artifacts = []
    if args.archive_root:
        summary_archive = archive_json_response(
            root=args.archive_root,
            payload=raw_summary,
            source_uri=SUMMARY_ENDPOINT,
            provider=SOURCE_NAME,
            dataset_id="okx_public_borrow_rate_summary",
            instrument_id="ALL_CURRENCIES",
            observed_at_ms=observed_at_ms,
            parse_diagnostics={
                "status": "ACCEPTED",
                "input_rows": len(_response_rows(raw_summary)),
                "accepted_rows": sum(row["rate_status"] == "AVAILABLE" for row in summary),
                "rejected_rows": 0,
                "duplicate_rows": 0,
            },
        )
        artifacts.append(summary_archive["artifact_sha256"])
        for currency in requested:
            source_uri = (
                f"{HISTORY_ENDPOINT}?"
                + urllib.parse.urlencode({"ccy": currency, "limit": args.history_limit})
            )
            archived = archive_json_response(
                root=args.archive_root,
                payload=raw_history[currency],
                source_uri=source_uri,
                provider=SOURCE_NAME,
                dataset_id="okx_public_borrow_rate_history_1h",
                instrument_id=currency,
                observed_at_ms=observed_at_ms,
                parse_diagnostics={
                    "status": "ACCEPTED",
                    "input_rows": len(_response_rows(raw_history[currency])),
                    "accepted_rows": len(history[currency]),
                    "rejected_rows": 0,
                    "duplicate_rows": 0,
                },
            )
            artifacts.append(archived["artifact_sha256"])
    snapshot = build_snapshot(
        summary,
        history,
        observed_at_ms=observed_at_ms,
        requested_currencies=requested,
        raw_artifacts=artifacts,
    )
    atomic_write_json(args.out, snapshot, indent=2)
    added = record_forward_vintages(snapshot, args.vintage_db)
    print(
        "OKX public borrow rates: "
        f"currencies={len(requested)}, history_rows={snapshot['coverage']['history_row_count']}, "
        f"forward_rows_added={added} -> {args.out}",
        flush=True,
    )
    return snapshot


if __name__ == "__main__":
    main()


__all__ = [
    "AVERAGE_BORROW_FEATURE_ID",
    "DEFAULT_CURRENCIES",
    "ESTIMATED_BORROW_FEATURE_ID",
    "OkxBorrowFetchError",
    "PREVIOUS_BORROW_FEATURE_ID",
    "SETTLED_BORROW_FEATURE_ID",
    "SETTLED_LENDING_FEATURE_ID",
    "build_snapshot",
    "fetch_public_borrow_rates",
    "parse_public_history",
    "parse_public_summary",
    "record_forward_vintages",
]
