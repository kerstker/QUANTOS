"""Multi-asset market-data ingestion seam (asset-class agnostic, OFFLINE).

A `MarketDataProvider` yields CANONICAL bar rows; `ingest_provider_rows` routes them
through the shared fail-closed ingestion core into the PIT store. The provider that
actually talks to the network (e.g. tools/coingecko_fetch for crypto) lives OUT of the
gate; this seam is pure/offline and reused identically for crypto, equities, etc.

Canonical row: {instrument_id, event_time, knowledge_time, open, high, low, close, volume}.
event_time/knowledge_time are integers on a single monotonic scale per store/run
(crypto: epoch-ms; daily equities: YYYYMMDD). Bitemporal knowledge_time >= event_time is
enforced by the Bar contract; here knowledge_time is supplied per row (a bar is knowable
at its CLOSE, so providers set knowledge_time = bar_open_ts + interval).
"""
from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .file_ingest import IngestionReport, _ingest_records


@runtime_checkable
class MarketDataProvider(Protocol):
    """Any real-data source (crypto/equity/future...). Implementations fetch read-only
    market data and return canonical bar rows; they NEVER trade or touch credentials
    beyond a read-only data key. The network lives in the implementation, off the gate."""
    asset_class: str

    def fetch_ohlc(self, instrument_id: str, *, days: int, vs_currency: str = "usd") -> list:
        ...


def ingest_provider_rows(rows: Sequence[dict], store, *, now=None,
                         dataset_id: str = "market", version: str = "") -> IngestionReport:
    """Ingest canonical provider rows into the PIT store via the shared fail-closed core
    (normalize/validate OHLC + bitemporal, dedup, content_hash, quality). Bad rows are
    recorded in `rejected`, never crashed. Asset-class agnostic."""
    def parse(raw):
        kt = int(raw["knowledge_time"])
        canonical = {
            "instrument_id": raw["instrument_id"],
            "event_time": int(raw["event_time"]),
            "open": float(raw["open"]),
            "high": float(raw["high"]),
            "low": float(raw["low"]),
            "close": float(raw["close"]),
            "volume": float(raw.get("volume", 0.0)),
        }
        return canonical, kt

    return _ingest_records(list(enumerate(rows)), parse, store, now=now,
                           dataset_id=dataset_id, version=version)
