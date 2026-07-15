"""Out-of-gate, READ-ONLY PUBLIC funding-rate fetcher (Binance perpetuals) — an ALT-DATA dimension.

Funding rate is NOT price or volume: it is the periodic payment between perpetual-future longs and shorts,
i.e. a direct read of LEVERAGE / positioning imbalance — an alpha dimension orthogonal to price. Binance's
PUBLIC fundingRate history needs no key, no account, and exposes no order capability: read-only DATA only
(stdlib urllib GET via the shared helper; imports NO exchange/broker SDK and makes NO order/account call —
governance boundary test pins exactly that). Funding posts every 8h; we aggregate to a DAILY SUM (the carry
of holding the position one day) snapped to the UTC day grid. Network lives HERE (out of the gate). There is
intentionally no function here that trades; do not add one.
"""
from __future__ import annotations

import math
import time

from services.data.quality import build_ingestion_report
from services.data.raw_archive import (DEFAULT_RAW_ARCHIVE,
                                       archive_json_response)
from tools.coingecko_fetch import _http_get_json

_FAPI = "https://fapi.binance.com"
_DAY_MS = 86_400_000


class FundingDataError(ValueError):
    """The public response or derived daily series cannot support research evidence."""


def validate_daily_funding(ticker: str, values: dict, *, expected_end=None,
                           expected_start=None):
    """Validate finite daily values and a gap-free UTC-day series."""
    if not isinstance(values, dict):
        raise FundingDataError(f"funding {ticker}: values must be an object")
    try:
        times = [int(day) for day in values]
    except (TypeError, ValueError) as exc:
        raise FundingDataError(f"funding {ticker}: invalid day key") from exc
    off_grid = sorted(day for day in times if day % _DAY_MS != 0)
    if off_grid:
        raise FundingDataError(
            f"funding {ticker}: timestamps are not on the UTC-day grid: {off_grid[:3]}"
        )
    if expected_end is not None:
        beyond = sorted(day for day in times if day > int(expected_end))
        if beyond:
            raise FundingDataError(
                f"funding {ticker}: timestamps after expected end {expected_end}: {beyond[:3]}"
            )
    for day, value in values.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value))):
            raise FundingDataError(
                f"funding {ticker}: non-finite funding rate at {day}"
            )
    report = build_ingestion_report(
        f"funding-{ticker}",
        {ticker: sorted(times)},
        expected_interval=_DAY_MS,
        expected_start=expected_start,
        expected_end=expected_end,
    )
    if not report.passed:
        failed = [check.name for check in report.checks if not check.passed]
        raise FundingDataError(
            f"funding {ticker}: failed quality checks {failed}"
        )
    return report


def _get(path: str, http_get=_http_get_json):
    return http_get(_FAPI + path, {"accept": "application/json"})


def fetch_perp_symbols(http_get=_http_get_json) -> list:
    """Public exchangeInfo -> base tickers of TRADING USDT-margined PERPETUAL contracts."""
    info = _get("/fapi/v1/exchangeInfo", http_get)
    out = []
    for s in (info.get("symbols", []) if isinstance(info, dict) else []):
        if (s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"):
            base = s.get("baseAsset")
            if base and base not in out:
                out.append(base)
    return out


def fetch_daily_funding(ticker: str = "BTC", *, days: int = 2200,
                        http_get=_http_get_json, now_ms=None,
                        archive_root=None, observed_at_ms=None) -> dict:
    """Public funding history -> {day_bucket_ms: daily-SUMMED fundingRate}. PIT-safe: a day's funding is
    realized that day (knowable at its close). No key. Empty dict if the perp is absent. Pages backward."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    symbol = ticker.upper() + "USDT"
    by_day = {}
    last_observation_ts = None
    # Binance fundingRate returns earliest-first from startTime -> page FORWARD from (now - `days`).
    start = max(0, now_ms - days * _DAY_MS)
    for _ in range(20):
        path = f"/fapi/v1/fundingRate?symbol={symbol}&limit=1000&startTime={int(start)}"
        batch = _get(path, http_get)
        source_uri = _FAPI + path

        def archive(status, *, accepted_rows=0, rejected_rows=0,
                    duplicate_rows=0, error_type=None):
            if archive_root is None:
                return
            diagnostics = {
                "status": status,
                "input_rows": len(batch) if isinstance(batch, list) else 0,
                "accepted_rows": accepted_rows,
                "rejected_rows": rejected_rows,
                "duplicate_rows": duplicate_rows,
            }
            if error_type is not None:
                diagnostics["error_type"] = error_type
            archive_json_response(
                root=archive_root,
                payload=batch,
                source_uri=source_uri,
                provider="Binance USD-M perpetual funding history",
                dataset_id="funding",
                instrument_id=ticker,
                observed_at_ms=int(observed_at_ms),
                parse_diagnostics=diagnostics,
            )

        if not isinstance(batch, list):
            archive("REJECTED", rejected_rows=1, error_type="FundingDataError")
            raise FundingDataError(
                f"funding {ticker}: expected a list response, got {type(batch).__name__}"
            )
        if not batch:
            archive("ACCEPTED")
            break
        parsed = []
        for index, it in enumerate(batch):
            try:
                ts, rate = int(it["fundingTime"]), float(it["fundingRate"])
            except (KeyError, TypeError, ValueError) as exc:
                archive(
                    "REJECTED", accepted_rows=len(parsed), rejected_rows=1,
                    error_type="FundingDataError",
                )
                raise FundingDataError(
                    f"funding {ticker}: malformed observation at index {index}"
                ) from exc
            if ts < 0 or ts > now_ms or not math.isfinite(rate):
                archive(
                    "REJECTED", accepted_rows=len(parsed), rejected_rows=1,
                    error_type="FundingDataError",
                )
                raise FundingDataError(
                    f"funding {ticker}: invalid observation at index {index}"
                )
            if last_observation_ts is not None and ts <= last_observation_ts:
                archive(
                    "REJECTED",
                    accepted_rows=len(parsed),
                    rejected_rows=1,
                    duplicate_rows=1 if ts == last_observation_ts else 0,
                    error_type="FundingDataError",
                )
                raise FundingDataError(
                    f"funding {ticker}: observation timestamps must be strictly increasing"
                )
            last_observation_ts = ts
            parsed.append((ts, rate))
        archive("ACCEPTED", accepted_rows=len(parsed))
        for ts, rate in parsed:
            bucket = (ts // _DAY_MS) * _DAY_MS
            by_day[bucket] = by_day.get(bucket, 0.0) + rate     # sum the day's 8h fundings
        last = parsed[-1][0]
        if len(batch) < 1000 or last >= now_ms:
            break
        start = last + 1                                        # next page starts after this batch's newest
        time.sleep(0.2)
    today = (now_ms // _DAY_MS) * _DAY_MS
    return {b: v for b, v in by_day.items() if b < today}       # drop the live (today) day


def main() -> None:
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    now_ms = int(time.time() * 1000)
    f = fetch_daily_funding(
        tk,
        days=2200,
        now_ms=now_ms,
        archive_root=DEFAULT_RAW_ARCHIVE,
        observed_at_ms=now_ms,
    )
    if not f:
        print(f"{tk}: no funding history"); return
    days = sorted(f)
    span = (time.strftime("%Y-%m-%d", time.gmtime(days[0] / 1000)) + " .. " +
            time.strftime("%Y-%m-%d", time.gmtime(days[-1] / 1000)))
    vals = list(f.values())
    print(f"{tk}USDT funding: {len(f)} days [{span}]  daily-sum range {min(vals):.5f}..{max(vals):.5f}")


if __name__ == "__main__":
    main()
