"""Declarative multi-source market-data adapter + registry (OFFLINE seam).

Adding a new data source should be a DECLARATION, not a new parser. A `SourceSpec` says how a
source's raw tabular records map to the canonical bar fields (instrument_id, event_time,
OHLC, volume), on which time scale, and how long after the bar opens its close is knowable
(`knowledge_lag`). `normalize_records` then turns raw records into the canonical rows the
existing fail-closed ingestion seam (`market_provider.ingest_provider_rows`) already accepts —
so a richer feed (real volume, another exchange, equities OHLCV, a higher-tier crypto plan)
plugs in with a spec, no core changes.

This layer is pure and offline: it never touches the network or credentials (that stays
out-of-gate in tools/). It only normalizes already-fetched records. The `register_source`
registry lets the platform enumerate which sources are configured.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional, Sequence

from quantos.contracts.data import VolumeSemantics, VolumeUnit

# canonical bar fields a SourceSpec.field_map MUST cover (volume optional -> defaults to 0)
_REQUIRED = ("event_time", "open", "high", "low", "close")


@dataclass(frozen=True)
class SourceSpec:
    """How to map ONE source's raw records to canonical bars.

    field_map: canonical_field -> raw_key, covering at least _REQUIRED (+ "volume" and
      optionally "instrument_id"). time_unit: 'ms' | 'yyyymmdd' (handed to the ingest guard).
      knowledge_lag: added to event_time to get knowledge_time (a bar is knowable at CLOSE).
      instrument_id: set when the source is single-instrument and its rows carry no id column."""
    name: str
    asset_class: str
    time_unit: str
    field_map: dict
    knowledge_lag: int = 0
    instrument_id: Optional[str] = None
    volume_unit: Optional[VolumeUnit] = None
    volume_semantics: Optional[VolumeSemantics] = None

    def __post_init__(self):
        missing = [k for k in _REQUIRED if k not in self.field_map]
        if missing:
            raise ValueError(f"SourceSpec.field_map missing required keys: {missing}")
        if "instrument_id" not in self.field_map and self.instrument_id is None:
            raise ValueError("SourceSpec needs an instrument_id field_map entry OR a fixed instrument_id")
        if self.knowledge_lag < 0:
            raise ValueError("knowledge_lag must be >= 0")
        has_volume = "volume" in self.field_map
        if has_volume and self.volume_unit is None:
            raise ValueError("SourceSpec with volume must declare volume_unit")
        if has_volume and self.volume_semantics is None:
            raise ValueError("SourceSpec with volume must declare volume_semantics")
        if not has_volume and (self.volume_unit is not None or self.volume_semantics is not None):
            raise ValueError("SourceSpec without volume cannot declare volume semantics")


def quote_notional_volume(*, close: float, volume: float, volume_unit: VolumeUnit) -> float:
    """Convert declared volume to quote/USD notional without double scaling."""
    close = float(close)
    volume = float(volume)
    if not math.isfinite(close) or close <= 0:
        raise ValueError("close must be finite and positive")
    if not math.isfinite(volume) or volume < 0:
        raise ValueError("volume must be finite and non-negative")
    if volume_unit == VolumeUnit.BASE_ASSET:
        return close * volume
    if volume_unit == VolumeUnit.QUOTE_USD:
        return volume
    raise ValueError(f"cannot convert {volume_unit.value} volume to quote notional without metadata")


def normalize_records(raw_records: Sequence[dict], spec: SourceSpec) -> list:
    """Map raw records -> canonical bar rows (instrument_id, event_time, knowledge_time, OHLC,
    volume). Fail-closed: a record missing a mapped field, or a non-numeric value, raises a
    clear ValueError (a spec/data mismatch is a real error, not a silently dropped row)."""
    fm = spec.field_map
    out = []
    for i, rec in enumerate(raw_records):
        try:
            inst = spec.instrument_id if spec.instrument_id is not None else str(rec[fm["instrument_id"]])
            et = int(rec[fm["event_time"]])
            row = {
                "instrument_id": inst,
                "event_time": et,
                "knowledge_time": et + int(spec.knowledge_lag),
                "open": float(rec[fm["open"]]),
                "high": float(rec[fm["high"]]),
                "low": float(rec[fm["low"]]),
                "close": float(rec[fm["close"]]),
                "volume": float(rec[fm["volume"]]) if "volume" in fm else 0.0,
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"source {spec.name!r} record {i} does not match field_map: {exc}") from exc
        out.append(row)
    return out


# ---- registry: which sources the platform knows about (declarative, no network) ----------
_REGISTRY: dict = {}


def register_source(spec: SourceSpec) -> SourceSpec:
    """Register a configured source. Duplicate names fail closed (no silent overwrite)."""
    if spec.name in _REGISTRY:
        raise ValueError(f"data source already registered: {spec.name!r}")
    _REGISTRY[spec.name] = spec
    return spec


def get_source(name: str) -> SourceSpec:
    if name not in _REGISTRY:
        raise KeyError(f"no such data source: {name!r}")
    return _REGISTRY[name]


def list_sources() -> list:
    return sorted(_REGISTRY)
