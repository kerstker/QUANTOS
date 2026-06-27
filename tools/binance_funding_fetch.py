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

import time

from tools.coingecko_fetch import _http_get_json

_FAPI = "https://fapi.binance.com"
_DAY_MS = 86_400_000


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


def fetch_daily_funding(ticker: str = "BTC", *, days: int = 2200, http_get=_http_get_json, now_ms=None) -> dict:
    """Public funding history -> {day_bucket_ms: daily-SUMMED fundingRate}. PIT-safe: a day's funding is
    realized that day (knowable at its close). No key. Empty dict if the perp is absent. Pages backward."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    symbol = ticker.upper() + "USDT"
    by_day = {}
    # Binance fundingRate returns earliest-first from startTime -> page FORWARD from (now - `days`).
    start = max(0, now_ms - days * _DAY_MS)
    for _ in range(20):
        batch = _get(f"/fapi/v1/fundingRate?symbol={symbol}&limit=1000&startTime={int(start)}", http_get)
        if not isinstance(batch, list) or not batch:
            break
        for it in batch:
            try:
                ts, rate = int(it["fundingTime"]), float(it["fundingRate"])
            except (KeyError, TypeError, ValueError):
                continue
            bucket = (ts // _DAY_MS) * _DAY_MS
            by_day[bucket] = by_day.get(bucket, 0.0) + rate     # sum the day's 8h fundings
        last = int(batch[-1]["fundingTime"])
        if len(batch) < 1000 or last >= now_ms:
            break
        start = last + 1                                        # next page starts after this batch's newest
        time.sleep(0.2)
    today = (now_ms // _DAY_MS) * _DAY_MS
    return {b: v for b, v in by_day.items() if b < today}       # drop the live (today) day


def main() -> None:
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    f = fetch_daily_funding(tk, days=2200)
    if not f:
        print(f"{tk}: no funding history"); return
    days = sorted(f)
    span = (time.strftime("%Y-%m-%d", time.gmtime(days[0] / 1000)) + " .. " +
            time.strftime("%Y-%m-%d", time.gmtime(days[-1] / 1000)))
    vals = list(f.values())
    print(f"{tk}USDT funding: {len(f)} days [{span}]  daily-sum range {min(vals):.5f}..{max(vals):.5f}")


if __name__ == "__main__":
    main()
