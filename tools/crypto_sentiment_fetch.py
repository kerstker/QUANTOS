"""Fetch the Crypto Fear & Greed Index (alternative.me) — a GENUINELY DIFFERENT KIND of data than the
price / volume / funding / positioning the program has already mined out: a market-wide SENTIMENT series.

Free, no key, read-only DATA only (no exchange/broker SDK, no order/transfer path — governance-pinned by
tests/governance/test_crypto_data_boundary.py). Daily, multi-year (2018-present), spanning the 2018 bear,
2021 bull, 2022 bear and 2024-25 cycle — a real multi-regime sample.

HONESTY CAVEAT (carried into the research): the index is PARTLY price-derived — alternative.me documents its
components as ~25% volatility + ~25% market momentum/volume + 15% social + 15% surveys (paused) + 10%
dominance + 10% trends. So ~50-65% is a repackaging of price/volatility; only the social/dominance/trends
slice is orthogonal to price. It is therefore NOT a clean independent signal — treat any edge with that in
mind. Writes data/cache/crypto_fear_greed.json {_meta, data:{utc_midnight_ms: value0-100}}.
"""
from __future__ import annotations

import json
import os
import urllib.request

_OUT = os.path.join("data", "cache", "crypto_fear_greed.json")
_URL = "https://api.alternative.me/fng/?limit=0&format=json"
_DAY_MS = 86_400_000


def fetch() -> dict:
    req = urllib.request.Request(_URL, headers={"User-Agent": "Mozilla/5.0 (research; read-only)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = payload.get("data", [])
    series = {}
    for row in rows:
        try:
            # snap the publish timestamp to the UTC-midnight day grid (PIT: the value is known end-of-day)
            ts = (int(row["timestamp"]) // 86_400) * _DAY_MS
            series[str(ts)] = int(row["value"])
        except (KeyError, ValueError, TypeError):
            continue
    return series


def main() -> None:
    series = fetch()
    times = sorted(int(t) for t in series)
    out = {
        "_meta": {
            "source": "alternative.me Fear & Greed Index",
            "kind": "MARKET_SENTIMENT",
            "n": len(series),
            "start_ms": times[0] if times else None,
            "through_ms": times[-1] if times else None,
            "caveat": "partly price-derived (~25% volatility + ~25% momentum/volume); not orthogonal to price",
        },
        "data": series,
    }
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(out, open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"fetched {len(series)} daily Fear&Greed points -> {_OUT}")


if __name__ == "__main__":
    main()
