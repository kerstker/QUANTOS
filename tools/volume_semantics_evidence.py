"""Generate local evidence for the crypto daily volume-semantics root-cause correction."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import statistics
import time

from services.data.adapters import quote_notional_volume
from services.data.quality import check_volume_dispersion
from tools.coingecko_volume_fetch import MC_SPEC


_DB = os.path.join("data", "cache", "crypto_daily.db")
_OUT = os.path.join("evidence", "volume_semantics_baseline.json")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_volume_semantics_evidence(db_path: str) -> dict:
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)
    connection = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT key, event_time, payload_json FROM bars ORDER BY key, event_time"
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise ValueError("crypto daily store has no bars")

    quote_by_instrument: dict[str, list[float]] = {}
    legacy_by_instrument: dict[str, list[float]] = {}
    event_times = []
    for key, event_time, payload_json in rows:
        payload = json.loads(payload_json)
        if payload.get("instrument_id") != key:
            raise ValueError(f"bar key/payload mismatch: {key}")
        close = float(payload["close"])
        volume = float(payload["volume"])
        if not math.isfinite(close) or close <= 0 or not math.isfinite(volume) or volume < 0:
            raise ValueError(f"non-finite or invalid OHLCV row: {key}@{event_time}")
        if volume > 0:
            quote_by_instrument.setdefault(key, []).append(
                quote_notional_volume(
                    close=close, volume=volume, volume_unit=MC_SPEC.volume_unit,
                )
            )
            legacy_by_instrument.setdefault(key, []).append(close * volume)
        event_times.append(int(event_time))

    quote_medians = {
        key: statistics.median(values) for key, values in quote_by_instrument.items()
    }
    legacy_medians = {
        key: statistics.median(values) for key, values in legacy_by_instrument.items()
    }
    dispersion = check_volume_dispersion(quote_medians, max_ratio=1e6)
    return {
        "schema_version": 1,
        "verdict": "PASS_LOCAL_SEMANTICS",
        "data_provenance": "REAL_LOCAL_ARTIFACT_WITH_CODE_CONTRACT",
        "artifact": {
            "path": db_path.replace("\\", "/"),
            "sha256": _sha256(db_path),
            "rows": len(rows),
            "instruments": len({row[0] for row in rows}),
            "event_time_min": min(event_times),
            "event_time_max": max(event_times),
        },
        "source_contract": {
            "source": MC_SPEC.name,
            "raw_field": "total_volumes",
            "volume_unit": MC_SPEC.volume_unit.value,
            "volume_semantics": MC_SPEC.volume_semantics.value,
        },
        "corrected_quote_adv": {
            "median_by_instrument": dict(sorted(quote_medians.items())),
            "max_min_ratio": max(quote_medians.values()) / min(quote_medians.values()),
            "dispersion_check": dispersion.model_dump(mode="json"),
        },
        "root_cause": {
            "legacy_formula": "close * quote_usd_volume",
            "correct_formula": "quote_usd_volume",
            "legacy_median_max_min_ratio": (
                max(legacy_medians.values()) / min(legacy_medians.values())
            ),
        },
        "assurance_boundary": (
            "Source code and the current artifact hash establish local semantics; historical "
            "CoinGecko wire responses were not archived and remain a P1-8 lineage gap."
        ),
    }


def main() -> None:
    evidence = build_volume_semantics_evidence(_DB)
    from tools.scenario_tail_research import _trend_capacity

    evidence["trend_capacity_recompute"] = _trend_capacity()
    evidence["generated_at_ms"] = int(time.time() * 1000)
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    with open(_OUT, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"volume semantics evidence: {evidence['verdict']} -> {_OUT}")


if __name__ == "__main__":
    main()
