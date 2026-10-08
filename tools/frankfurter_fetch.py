"""Out-of-gate FX fetcher: ECB daily reference rates via Frankfurter (api.frankfurter.app) — KEYLESS, no
exchange/broker SDK, no order/transfer calls. Returns canonical PIT rows (one per currency per day) for the
shared `ingest_provider_rows` core, so the same offline research/gate machinery that runs on crypto runs on a
genuinely different asset class + regime (FX, daily back to 1999 across the GFC / 2015 CHF / COVID / 2022 hikes).

`close` = the USD price of one unit of the currency (= 1 / USD-base-rate), so a rising series = an appreciating
currency. ECB publishes a single reference rate per day, so open=high=low=close (no intraday); volume = 0 (FX
reference rates carry none — volume factors simply yield nothing, which is honest). Pure stdlib (urllib+json) +
arithmetic date math (no datetime/calendar, to satisfy the data-boundary import allowlist). Network lives ONLY
here (tools/, outside the governance scanner); the research core stays offline + deterministic.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

_BASE = "https://api.frankfurter.app"


def _http_get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (QUANTOS research/offline)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _ymd_to_ms(d: str) -> int:
    """'YYYY-MM-DD' -> UTC-midnight epoch ms. Pure arithmetic (proleptic Gregorian), no datetime/calendar."""
    y, m, day = (int(x) for x in d.split("-"))
    days = 0
    for yr in range(1970, y):
        days += 366 if _is_leap(yr) else 365
    mdays = [31, 29 if _is_leap(y) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    days += sum(mdays[: m - 1]) + (day - 1)
    return days * 86400 * 1000


def fetch_fx_rates(start: str, end: str, *, base: str = "USD", http_get=_http_get_json) -> dict:
    """Frankfurter time-series: {base, start_date, end_date, rates: {date: {ccy: rate}}}."""
    return http_get(f"{_BASE}/{start}..{end}?base={base}")


def parse_fx(payload: dict, *, base: str = "USD") -> list:
    """Canonical PIT rows from a Frankfurter time-series payload. close = 1/rate = USD price of the currency."""
    rates = payload.get("rates", {}) or {}
    rows: list = []
    for d in sorted(rates):
        et = _ymd_to_ms(d)
        for ccy, rate in rates[d].items():
            try:
                rate = float(rate)
            except (TypeError, ValueError):
                continue
            if rate <= 0:
                continue
            px = 1.0 / rate
            rows.append({"instrument_id": str(ccy), "event_time": et, "knowledge_time": et,
                         "open": px, "high": px, "low": px, "close": px, "volume": 0.0})
    return rows


def fetch_fx_rows(start: str, end: str, *, base: str = "USD", http_get=_http_get_json) -> list:
    return parse_fx(fetch_fx_rates(start, end, base=base, http_get=http_get), base=base)


def main() -> None:
    from services.data.persistent_store import SqlitePITStore
    from services.data.market_provider import ingest_provider_rows

    start = sys.argv[1] if len(sys.argv) > 1 else "1999-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else time.strftime("%Y-%m-%d", time.gmtime())
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(os.path.join("data", "cache", "fx.db"), create=True)
    rows = fetch_fx_rows(start, end)
    rep = ingest_provider_rows(rows, store, now=int(time.time() * 1000), dataset_id="frankfurter-fx",
                               time_unit="ms")
    print(f"FX fetch {start}..{end}: {len(rows)} rows -> data/cache/fx.db "
          f"(accepted {getattr(rep, 'accepted', '?')}, rejected {len(getattr(rep, 'rejected', []))})")


if __name__ == "__main__":
    main()
