"""Latest-days REST gap-fill for the Binance Data Vision publication lag (out-of-gate fetcher).

The Data Vision daily ZIPs (perp markPriceKlines + futures metrics) are published ~10-16h AFTER a
UTC day closes, so every day from ~00:00Z until the archive appears the forward-data stores miss the
freshest CLOSED day and `tools/data_quality_admission.py` BLOCKs the paper loops. Binance serves the
same data over public REST with NO publication lag:

    GET /fapi/v1/markPriceKlines?symbol=X&interval=1d&limit=N          (mark price daily closes)
    GET /futures/data/openInterestHist?symbol=X&period=5m&...          (OI + positioning, 5-min)
    GET /futures/data/globalLongShortAccountRatio?...  topLongShortPositionRatio?...  takerlongshortRatio?...

Endpoint semantics pinned against the local archive on 2026-07-26 (BTCUSDT, day 2026-07-24):
  * markPriceKlines rows are labelled by DAY START (openTime == day) — the close equals the archive
    ZIP row bit-for-bit. The trailing row is the current UNCLOSED day and is always dropped.
  * openInterestHist / globalLongShortAccountRatio / topLongShortPositionRatio label 5-min records
    by WINDOW END — the archive's end-of-day row (create_time 23:55) is the REST record with
    timestamp == day + 86_400_000 (00:00 next day).
  * takerlongshortRatio labels 5-min records by WINDOW START — the archive EOD row is the REST
    record with timestamp == day + 86_400_000 - 300_000 (23:55 same day).
  * REST rounds the ratio fields to 4 decimals where the archive CSV keeps full precision; OI and
    OI-value are exact. Gap-filled rows therefore carry REST precision until the archive replaces
    them. The period=1d variants of the metrics endpoints are deliberately NOT used: their
    timestamps are shifted one day vs the archive and their taker value is a full-day aggregate,
    not the archive's last-5-min print.

Precedence / provenance rule (archive is canonical):
  * Rows ingested here are placeholders for not-yet-archived days, marked
    source="REST_GAP_FILL" (OI record field) or in the mark cache's top-level "provenance" section.
  * Every refresh re-attempts placeholder days from the Data Vision archive FIRST; a published
    archive row always replaces the REST placeholder (and flips the marker to DATA_VISION_ARCHIVE).
    REST fill only writes days that are entirely ABSENT — it never overwrites an archive row.
  * Each filled day is archived into the raw archive as an immutable CANONICAL_JSON_UTF8 observation
    under the dedicated dataset ids `perp_mark_prices_rest_gap_fill` / `oi_metrics_rest_gap_fill`
    (never the archive dataset ids, so ZIP replay can never pick up a JSON payload).

PIT discipline: only CLOSED days are accepted (day < current UTC day); the current unclosed day's
partial bar is never ingested. knowledge_time=event_time per repo convention (an eod row is
knowable at the close of its day).

Read-only DATA only: stdlib urllib GET of public endpoints, no key, NO exchange/broker SDK, NO order
or transfer calls. Network/wall-clock live here (out of the gate); tests inject fake transports.
"""
from __future__ import annotations

import json
import math
import time

from services.data.raw_archive import archive_json_response
from services.data.binance_realtime_store import MAX_FUTURE_SKEW_MS
from tools.coingecko_fetch import _http_get_json
from tools.source_failures import source_failure_detail

_HOST = "https://fapi.binance.com"
_DAY_MS = 86_400_000
_FIVE_MIN_MS = 300_000
# The live futures/data REST family only retains roughly the last month; older days are the
# archive's job (a REST request for them simply returns empty, which we treat as NOT_FOUND).
REST_RETENTION_DAYS = 30

PROVENANCE_REST_GAP_FILL = "REST_GAP_FILL"
PROVENANCE_DATA_VISION_ARCHIVE = "DATA_VISION_ARCHIVE"
# Raw-archive dataset ids for REST gap-fill observations. Distinct from the Data Vision dataset ids
# ("perp_mark_prices" / "oi_metrics") so ZIP replay paths never see JSON payloads and vice versa.
REST_DATASET_MARK = "perp_mark_prices_rest_gap_fill"
REST_DATASET_OI = "oi_metrics_rest_gap_fill"
_REST_PROVIDER = "Binance USD-M futures public REST (gap-fill)"

# endpoint -> (record field, 5-min timestamp convention). "end": window-end labelling (eod row is
# timestamp == day + 1d); "start": window-start labelling (eod row is timestamp == day + 1d - 5m).
_OI_ENDPOINTS = (
    ("openInterestHist", "end"),
    ("globalLongShortAccountRatio", "end"),
    ("topLongShortPositionRatio", "end"),
    ("takerlongshortRatio", "start"),
)
_OI_FIELD_BY_ENDPOINT = {
    "openInterestHist": ("sumOpenInterest", "sumOpenInterestValue"),
    "globalLongShortAccountRatio": ("longShortRatio",),
    "topLongShortPositionRatio": ("longShortRatio",),
    "takerlongshortRatio": ("buySellRatio",),
}
_OI_RECORD_FIELDS = ("oi", "oi_value", "top_ls", "global_ls", "taker_ls")


class RestGapFillError(ValueError):
    pass


def _get(path: str, http_get=_http_get_json):
    return http_get(
        _HOST + path if path.startswith("/") else path,
        {"accept": "application/json"},
    )


def depth_snapshot_path(symbol: str, *, limit: int = 1000) -> str:
    """Return the public USD-M depth snapshot path for one canonical symbol."""
    normalized = str(symbol).upper()
    if (
        not normalized.endswith("USDT")
        or not normalized.isalnum()
        or normalized != str(symbol).upper()
        or not 1 <= int(limit) <= 1000
    ):
        raise RestGapFillError("depth snapshot symbol or limit is invalid")
    return f"/fapi/v1/depth?symbol={normalized}&limit={int(limit)}"


def _depth_levels(value, field: str) -> list[list[float]]:
    if not isinstance(value, list) or not value:
        raise RestGapFillError(f"depth snapshot {field} has no levels")
    output = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise RestGapFillError(f"depth snapshot {field} level is malformed")
        try:
            price = float(row[0])
            quantity = float(row[1])
        except (TypeError, ValueError) as exc:
            raise RestGapFillError(
                f"depth snapshot {field} level is not numeric"
            ) from exc
        if (
            not math.isfinite(price)
            or not math.isfinite(quantity)
            or price <= 0
            or quantity < 0
        ):
            raise RestGapFillError(f"depth snapshot {field} level is invalid")
        output.append([price, quantity])
    return output


def parse_depth_snapshot(payload, *, observed_at_ms: int) -> dict:
    """Validate a public depth snapshot used to recover a stream sequence gap."""
    if not isinstance(payload, dict) or not payload:
        raise RestGapFillError("empty depth snapshot result")
    try:
        update_id = int(payload["lastUpdateId"])
        event_time_ms = int(payload.get("E", observed_at_ms))
        transaction_time_ms = int(payload.get("T", event_time_ms))
    except (KeyError, TypeError, ValueError) as exc:
        raise RestGapFillError("depth snapshot identity is invalid") from exc
    if (
        update_id < 0
        or event_time_ms < 0
        or transaction_time_ms < 0
        or event_time_ms > int(observed_at_ms) + MAX_FUTURE_SKEW_MS
    ):
        raise RestGapFillError("depth snapshot identity is invalid")
    return {
        "last_update_id": update_id,
        "event_time_ms": event_time_ms,
        "transaction_time_ms": transaction_time_ms,
        "bids": _depth_levels(payload.get("bids"), "bids"),
        "asks": _depth_levels(payload.get("asks"), "asks"),
    }


def fetch_depth_snapshot(
    symbol: str,
    *,
    observed_at_ms: int,
    http_get=_http_get_json,
) -> dict:
    """Fetch one credential-free USD-M depth snapshot for gap recovery."""
    path = depth_snapshot_path(symbol)
    return parse_depth_snapshot(
        _get(path, http_get),
        observed_at_ms=int(observed_at_ms),
    )


def mark_klines_path(ticker: str, *, start_ms: int, limit: int) -> str:
    symbol = ticker.upper() + "USDT"
    return (
        f"/fapi/v1/markPriceKlines?symbol={symbol}&interval=1d"
        f"&startTime={int(start_ms)}&limit={int(limit)}"
    )


def _oi_endpoint_path(endpoint: str, ticker: str, *, start_ms: int, end_ms: int,
                      limit: int = 500) -> str:
    symbol = ticker.upper() + "USDT"
    return (
        f"/futures/data/{endpoint}?symbol={symbol}&period=5m"
        f"&startTime={int(start_ms)}&endTime={int(end_ms)}&limit={int(limit)}"
    )


def _valid_ohlc(open_price, high, low, close) -> bool:
    values = (open_price, high, low, close)
    if any(not math.isfinite(value) or value <= 0 for value in values):
        return False
    return high >= max(open_price, close, low) and low <= min(open_price, close, high)


def parse_mark_klines(payload, *, now_ms: int) -> dict:
    """Pure: a markPriceKlines JSON array -> {closed_day_ms: close}, strictly validated.

    Mirrors parse_mark_price_zip's checks (day-bound open/close times, positive finite OHLC,
    ordering) and DROPS the current unclosed day's partial bar (PIT)."""
    today = (int(now_ms) // _DAY_MS) * _DAY_MS
    out = {}
    if not isinstance(payload, list):
        raise RestGapFillError("markPriceKlines response must be a JSON array")
    for row in payload:
        if not (isinstance(row, (list, tuple)) and len(row) >= 7):
            raise RestGapFillError("markPriceKlines row has too few columns")
        try:
            open_time = int(row[0])
            open_price, high, low, close = (float(row[i]) for i in range(1, 5))
            close_time = int(row[6])
        except (TypeError, ValueError) as exc:
            raise RestGapFillError("markPriceKlines row has invalid numeric fields") from exc
        if open_time % _DAY_MS != 0 or close_time != open_time + _DAY_MS - 1:
            raise RestGapFillError("markPriceKlines row does not close a UTC day")
        if open_time >= today:
            continue                                    # current unclosed day — never ingested
        if not _valid_ohlc(open_price, high, low, close):
            raise RestGapFillError("markPriceKlines OHLC is invalid")
        out[open_time] = close
    return out


def parse_mark_rest_day(payload: bytes, day: int) -> float:
    """Replay parser for one archived REST gap-fill mark-price observation (the raw kline row)."""
    try:
        row = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RestGapFillError(f"archived REST mark row is unreadable: {exc}") from exc
    parsed = parse_mark_klines([row], now_ms=int(day) + _DAY_MS)
    if int(day) not in parsed:
        raise RestGapFillError(f"archived REST mark row does not cover day {day}")
    return parsed[int(day)]


def _positive_finite(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0


def _eod_row(rows, *, day: int, convention: str):
    """Select the end-of-day 5-min record for `day`, mirroring the archive's last-row-of-day rule.

    Window-end labelled endpoints: the eod record is the max timestamp in (day, day+1d].
    Window-start labelled endpoints: the eod record is the max timestamp in [day, day+1d)."""
    if not isinstance(rows, list):
        raise RestGapFillError("metrics endpoint response must be a JSON array")
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = int(row.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if convention == "end" and day < ts <= day + _DAY_MS:
            candidates.append((ts, row))
        elif convention == "start" and day <= ts < day + _DAY_MS:
            candidates.append((ts, row))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def build_oi_record(rows_by_endpoint: dict, *, day: int) -> dict:
    """Pure: {endpoint: raw eod row} -> the canonical 5-field record, strictly validated.

    All four endpoints must contribute a row and every field must be positive finite — a partial
    day is rejected wholesale (fail-closed on shape, exactly like the archive parse)."""
    record = {}
    for endpoint, _convention in _OI_ENDPOINTS:
        row = rows_by_endpoint.get(endpoint)
        if row is None:
            raise RestGapFillError(f"no end-of-day {endpoint} row for day {day}")
        for raw_field in _OI_FIELD_BY_ENDPOINT[endpoint]:
            value = row.get(raw_field)
            if not _positive_finite(value):
                raise RestGapFillError(
                    f"{endpoint}.{raw_field} is not a positive finite number"
                )
        if endpoint == "openInterestHist":
            record["oi"] = float(row["sumOpenInterest"])
            record["oi_value"] = float(row["sumOpenInterestValue"])
        elif endpoint == "globalLongShortAccountRatio":
            record["global_ls"] = float(row["longShortRatio"])
        elif endpoint == "topLongShortPositionRatio":
            record["top_ls"] = float(row["longShortRatio"])
        else:
            record["taker_ls"] = float(row["buySellRatio"])
    if tuple(sorted(record)) != tuple(sorted(_OI_RECORD_FIELDS)):
        raise RestGapFillError("OI record fields are incomplete")
    return record


def parse_oi_rest_day(payload: bytes, day: int) -> dict:
    """Replay parser for one archived REST gap-fill OI observation ({endpoint: raw eod row})."""
    try:
        rows = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RestGapFillError(f"archived REST OI rows are unreadable: {exc}") from exc
    if not isinstance(rows, dict):
        raise RestGapFillError("archived REST OI payload must be an object")
    return build_oi_record(rows, day=int(day))


def _archive_day(*, archive_root, observed_at_ms, dataset_id, ticker, day,
                 payload, source_uri, input_rows, extra_diagnostics=None) -> None:
    diagnostics = {
        "status": "ACCEPTED", "input_rows": int(input_rows),
        "accepted_rows": 1, "rejected_rows": 0, "duplicate_rows": 0,
        "event_time_ms": int(day),
    }
    diagnostics.update(extra_diagnostics or {})
    archive_json_response(
        root=archive_root,
        payload=payload,
        source_uri=source_uri,
        provider=_REST_PROVIDER,
        dataset_id=dataset_id,
        instrument_id=ticker,
        observed_at_ms=int(observed_at_ms),
        parse_diagnostics=diagnostics,
    )


def fillable_days(days, *, now_ms: int) -> list[int]:
    """The requested days that are CLOSED (day < current UTC day) and inside REST retention.

    This is the single PIT gate for gap-fill: the current unclosed day and days beyond REST
    retention are never even requested."""
    today = (int(now_ms) // _DAY_MS) * _DAY_MS
    floor = today - REST_RETENTION_DAYS * _DAY_MS
    out = []
    for day in days:
        day = int(day)
        if day % _DAY_MS != 0:
            raise RestGapFillError(f"requested day {day} is not UTC-day aligned")
        if floor <= day < today:
            out.append(day)
    return sorted(set(out))


def fetch_mark_days_rest(ticker: str, days, *, now_ms: int, http_get=_http_get_json,
                         archive_root=None, observed_at_ms=None, failure_sink=None) -> dict:
    """Fetch the requested CLOSED days of USD-M mark-price daily closes via REST (no lag).

    One klines request covers the whole window; each accepted day is archived as its own immutable
    observation (raw kline row) so lineage replay works per event day. Fail-soft per ticker: a
    request/parse failure is recorded in `failure_sink` and the ticker simply stays unfilled."""
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    wanted = fillable_days(days, now_ms=now_ms)
    if not wanted:
        return {}
    path = mark_klines_path(ticker, start_ms=wanted[0],
                            limit=len(wanted) + 2)
    try:
        payload = _get(path, http_get)
        parsed = parse_mark_klines(payload, now_ms=now_ms)
    except Exception as exc:
        if failure_sink is not None:
            failure_sink.append(source_failure_detail(
                ticker, exc, phase="rest_gap_fill_mark"))
        return {}
    raw_by_day = {}
    for row in payload:
        try:
            raw_by_day[int(row[0])] = row
        except (TypeError, ValueError, IndexError):
            continue
    out = {}
    for day in wanted:
        if day not in parsed:
            if failure_sink is not None:
                failure_sink.append({
                    "ticker": ticker, "kind": "NOT_FOUND",
                    "error_type": "RestGapFillMissing",
                    "message": "REST markPriceKlines has no closed bar for day",
                    "day_ms": day, "phase": "rest_gap_fill_mark",
                })
            continue
        if archive_root is not None and day in raw_by_day:
            _archive_day(
                archive_root=archive_root, observed_at_ms=observed_at_ms,
                dataset_id=REST_DATASET_MARK, ticker=ticker, day=day,
                payload=raw_by_day[day], source_uri=_HOST + path,
                input_rows=len(payload),
            )
        out[day] = parsed[day]
    return out


def fetch_oi_days_rest(ticker: str, days, *, now_ms: int, http_get=_http_get_json,
                       archive_root=None, observed_at_ms=None, failure_sink=None,
                       sleep: float = 0.1) -> dict:
    """Fetch the requested CLOSED days of OI/positioning eod metrics via REST (no lag).

    Per day, one request per metrics endpoint (period=5m, that day's window); the eod row selection
    mirrors the archive's last-row-of-day semantics per endpoint timestamp convention (see module
    docstring). A day missing ANY endpoint row or with a non-positive field is skipped wholesale
    (recorded as NOT_FOUND/QUALITY in `failure_sink`), never partially written."""
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    wanted = fillable_days(days, now_ms=now_ms)
    out = {}
    for day in wanted:
        rows_by_endpoint = {}
        source_uris = {}
        failure = None
        for endpoint, convention in _OI_ENDPOINTS:
            end_ms = day + _DAY_MS if convention == "end" else day + _DAY_MS - 1
            path = _oi_endpoint_path(endpoint, ticker, start_ms=day, end_ms=end_ms)
            try:
                payload = _get(path, http_get)
                row = _eod_row(payload, day=day, convention=convention)
            except Exception as exc:
                failure = source_failure_detail(
                    ticker, exc, day_ms=day, phase="rest_gap_fill_oi")
                break
            if row is None:
                failure = {
                    "ticker": ticker, "kind": "NOT_FOUND",
                    "error_type": "RestGapFillMissing",
                    "message": f"REST {endpoint} has no end-of-day row for day",
                    "day_ms": day, "phase": "rest_gap_fill_oi",
                }
                break
            rows_by_endpoint[endpoint] = row
            source_uris[endpoint] = _HOST + path
            if sleep:
                time.sleep(sleep)
        if failure is not None:
            if failure_sink is not None:
                failure_sink.append(failure)
            continue
        try:
            record = build_oi_record(rows_by_endpoint, day=day)
        except Exception as exc:
            if failure_sink is not None:
                failure_sink.append(source_failure_detail(
                    ticker, exc, day_ms=day, phase="rest_gap_fill_oi"))
            continue
        if archive_root is not None:
            _archive_day(
                archive_root=archive_root, observed_at_ms=observed_at_ms,
                dataset_id=REST_DATASET_OI, ticker=ticker, day=day,
                payload=rows_by_endpoint,
                source_uri=source_uris[_OI_ENDPOINTS[0][0]],
                input_rows=len(_OI_ENDPOINTS),
                extra_diagnostics={
                    "source_uris": [source_uris[endpoint]
                                    for endpoint, _c in _OI_ENDPOINTS],
                },
            )
        out[day] = record
    return out
