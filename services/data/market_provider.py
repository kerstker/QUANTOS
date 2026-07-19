"""Multi-asset market-data ingestion seam (asset-class agnostic, OFFLINE).

A `MarketDataProvider` yields CANONICAL bar rows; `ingest_provider_rows` routes them
through the shared fail-closed ingestion core into the PIT store. The provider that
actually talks to the network (e.g. tools/coingecko_fetch for crypto) lives OUT of the
gate; this seam is pure/offline and reused identically for crypto, equities, etc.

Canonical row: {instrument_id, event_time, knowledge_time, open, high, low, close, volume}.
event_time/knowledge_time are integers on a single monotonic scale per store/run
(crypto: epoch-ms; daily equities: YYYYMMDD). Bitemporal knowledge_time >= event_time is
enforced by the Bar contract. **Each provider supplies knowledge_time in ITS OWN scale**
— a bar is knowable at its CLOSE, so crypto sets kt = open_ts + interval, while a daily
equity sets kt = its session-close timestamp. The seam does NOT bake any kt rule in.

Time-scale isolation: pass `time_unit` to fail-closed-reject event_times of the wrong
magnitude (e.g. a YYYYMMDD value fed into an ms store), so crypto-ms and equity-YYYYMMDD
data cannot silently co-mingle in one store/run (review: enforce, don't just document).
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from .file_ingest import IngestionReport, _ingest_records

# plausible integer ranges per time unit — used to fail-closed on a scale mismatch
_SCALE_RANGES = {
    "ms": (10 ** 11, 10 ** 14),          # epoch milliseconds (~2001..5138)
    "yyyymmdd": (10 ** 6, 10 ** 8),      # YYYYMMDD daily ints
}


@runtime_checkable
class MarketDataProvider(Protocol):
    """Any real-data source (crypto/equity/future...). Implementations fetch read-only
    market data and return canonical bar rows; they NEVER trade or touch credentials
    beyond a read-only data key. The network lives in the implementation, off the gate."""
    asset_class: str

    def fetch_ohlc(self, instrument_id: str, *, days: int, vs_currency: str = "usd") -> list:
        ...


def ingest_provider_rows(rows: Sequence[dict], store, *, now=None,
                         dataset_id: str = "market", version: str = "",
                         time_unit: Optional[str] = None, strict: bool = False,
                         expected_interval: Optional[int] = None,
                         expected_start: Optional[int] = None,
                         expected_end: Optional[int] = None) -> IngestionReport:
    """Ingest canonical provider rows into the PIT store via the shared fail-closed core
    (normalize/validate OHLC + bitemporal, dedup, content_hash, quality). Bad rows are
    recorded in `rejected`, never crashed. Asset-class agnostic.

    `now` arms the future-data guard (reject any bar whose knowledge_time > now) — pass the
    fetch wall-clock for live data so a not-yet-closed bar is rejected. `time_unit`
    ('ms'|'yyyymmdd') fail-closed-rejects event_times of the wrong magnitude, preventing a
    cross-scale mix in one store."""
    rng = _SCALE_RANGES.get(time_unit) if time_unit else None
    if time_unit and rng is None:
        raise ValueError(f"unknown time_unit {time_unit!r} (expected one of {sorted(_SCALE_RANGES)})")

    def parse(raw):
        kt = int(raw["knowledge_time"])
        et = int(raw["event_time"])
        if rng is not None and not (rng[0] <= et <= rng[1]):
            raise ValueError(f"event_time {et} out of range for time_unit {time_unit!r}")
        canonical = {
            "instrument_id": raw["instrument_id"],
            "event_time": et,
            "open": float(raw["open"]),
            "high": float(raw["high"]),
            "low": float(raw["low"]),
            "close": float(raw["close"]),
            "volume": float(raw.get("volume", 0.0)),
        }
        return canonical, kt

    return _ingest_records(list(enumerate(rows)), parse, store, now=now,
                           dataset_id=dataset_id, version=version, strict=strict,
                           expected_interval=expected_interval,
                           expected_start=expected_start, expected_end=expected_end)
