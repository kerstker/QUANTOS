"""Deterministic, local-only materialization of attested hourly market panels."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from services.data.hourly_market import (
    HourlyBar, HourlyFunding, HourlyMarketPanel, HourlyOpenInterest,
    aggregate_bars, validate_panel,
)
from services.data.hourly_universe import (
    UniverseObservation, UniversePolicy, select_hourly_universe,
)

INPUT_SCHEMA = "hourly-market-input-v1"
OUTPUT_SCHEMA = "hourly-market-materialized-v1"
_INTERVALS = (3600, 14400)
_KINDS = {"bar", "funding", "open_interest", "universe"}


@dataclass(frozen=True)
class MaterializationReceipt:
    manifests: tuple[Path, ...]
    manifest_digests: tuple[str, ...]


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _required(row: dict[str, Any], names: Iterable[str]) -> None:
    missing = [name for name in names if name not in row]
    if missing:
        raise ValueError(f"missing required fields: {missing}")


def _record(row: dict[str, Any], kind: str) -> Any:
    if row.get("schema_version") != INPUT_SCHEMA:
        raise ValueError("unsupported input schema_version")
    if row.get("kind") != kind or kind not in _KINDS:
        raise ValueError("unknown kind")
    if not isinstance(row.get("source_digest"), str) or not row["source_digest"].strip():
        raise ValueError("source_digest is required")
    if kind == "bar":
        _required(row, ("instrument", "event_time", "knowledge_time", "open", "high", "low", "close", "quote_volume", "interval_seconds"))
        if row["interval_seconds"] not in (60, 300):
            raise ValueError("input bars must be 1m or 5m")
        return HourlyBar(*(row[k] for k in ("instrument", "event_time", "knowledge_time", "open", "high", "low", "close", "quote_volume")))
    if kind == "funding":
        _required(row, ("instrument", "event_time", "knowledge_time", "rate"))
        return HourlyFunding(*(row[k] for k in ("instrument", "event_time", "knowledge_time", "rate")))
    if kind == "open_interest":
        _required(row, ("instrument", "event_time", "knowledge_time", "value"))
        return HourlyOpenInterest(*(row[k] for k in ("instrument", "event_time", "knowledge_time", "value")))
    _required(row, ("instrument", "event_time", "knowledge_time", "quote_volume", "spread_bps", "listed_at", "data_coverage"))
    return UniverseObservation(*(row[k] for k in ("instrument", "event_time", "knowledge_time", "quote_volume", "spread_bps", "listed_at", "data_coverage")))


def _read_raw(root: Path, as_of: int) -> tuple[list[Any], list[UniverseObservation], list[str], list[dict[str, Any]]]:
    if not root.is_dir():
        raise ValueError("raw_root must be a directory")
    values: list[Any] = []
    universes: list[UniverseObservation] = []
    source_digests: list[str] = []
    inputs: list[dict[str, Any]] = []
    files = sorted(root.rglob("*.jsonl"))
    if not files:
        raise ValueError("raw_root contains no JSONL input")
    for path in files:
        raw = path.read_bytes()
        file_digest = _sha(raw)
        for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_no}") from exc
            if not isinstance(row, dict):
                raise ValueError("input record must be an object")
            kind = row.get("kind")
            value = _record(row, kind)
            if value.event_time > as_of or value.knowledge_time > as_of:
                raise ValueError("future event or knowledge time")
            if kind == "universe":
                universes.append(value)
            else:
                values.append(value)
            source_digests.append(row["source_digest"])
        inputs.append({"path": path.relative_to(root).as_posix(), "sha256": file_digest})
    return values, universes, sorted(set(source_digests)), inputs


def _series(rows: list[Any], interval: int) -> tuple[list[HourlyBar], list[HourlyFunding], list[HourlyOpenInterest]]:
    bars = sorted((r for r in rows if isinstance(r, HourlyBar)), key=lambda r: (r.instrument, r.event_time))
    aggregated = aggregate_bars(bars, interval_seconds=interval)
    def latest(cls: type) -> list[Any]:
        grouped: dict[tuple[str, int], list[Any]] = {}
        for row in rows:
            if isinstance(row, cls):
                grouped.setdefault((row.instrument, row.event_time // interval), []).append(row)
        result = []
        for key, group in sorted(grouped.items()):
            row = max(group, key=lambda x: (x.event_time, x.knowledge_time))
            if cls is HourlyFunding:
                result.append(HourlyFunding(row.instrument, key[1] * interval, max(x.knowledge_time for x in group), row.rate))
            else:
                result.append(HourlyOpenInterest(row.instrument, key[1] * interval, max(x.knowledge_time for x in group), row.value))
        return result
    return list(aggregated), latest(HourlyFunding), latest(HourlyOpenInterest)


def _publish(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"immutable artifact conflict: {path}")
        return
    fd, temp_name = tempfile.mkstemp(prefix=".materialize-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            if path.read_bytes() != data:
                raise FileExistsError(f"immutable artifact conflict: {path}")
            os.unlink(temp_name)
        else:
            os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _rows(panel: HourlyMarketPanel, interval: int, universe: tuple[str, ...]) -> list[dict[str, Any]]:
    output = []
    for kind, values in (("bar", panel.bars), ("funding", panel.funding), ("open_interest", panel.open_interest)):
        for row in values:
            if row.instrument in universe:
                payload = {"schema_version": OUTPUT_SCHEMA, "kind": kind, "interval_seconds": interval,
                           "instrument": row.instrument, "event_time": row.event_time, "knowledge_time": row.knowledge_time}
                fields = ("open", "high", "low", "close", "quote_volume") if kind == "bar" else (("rate",) if kind == "funding" else ("value",))
                payload.update({k: getattr(row, k) for k in fields})
                output.append(payload)
    return sorted(output, key=lambda x: (x["event_time"], x["instrument"], x["kind"]))


def materialize_hourly_market(*, raw_root: Path, output_root: Path, as_of: int, policy: UniversePolicy) -> MaterializationReceipt:
    if isinstance(as_of, bool) or not isinstance(as_of, int) or as_of < 0:
        raise ValueError("as_of must be a non-negative integer")
    rows, observations, source_digests, inputs = _read_raw(Path(raw_root), as_of)
    universe = select_hourly_universe(observations, as_of=as_of, policy=policy)
    manifests: list[Path] = []
    digests: list[str] = []
    for interval in _INTERVALS:
        bars, funding, oi = _series(rows, interval)
        panel = HourlyMarketPanel("hourly-market-v1", as_of, tuple(bars), tuple(funding), tuple(oi), _sha(_canonical(source_digests)))
        validate_panel(panel)
        records = _rows(panel, interval, universe.instruments)
        if not records:
            raise ValueError("no materialized records")
        data = b"".join(_canonical(record) for record in records)
        partition = Path(output_root) / "hourly_market" / datetime.fromtimestamp(as_of, tz=timezone.utc).strftime("%Y-%m-%d") / f"interval-{interval}"
        data_path = partition / "data.jsonl"
        _publish(data_path, data)
        manifest_payload = {"schema_version": OUTPUT_SCHEMA, "as_of": as_of, "interval_seconds": interval,
                            "input_files": inputs, "policy": {k: getattr(policy, k) for k in ("min_size", "max_size", "min_listing_age_seconds", "max_spread_bps", "min_data_coverage")},
                            "source_digests": source_digests, "selected_universe_instruments": list(universe.instruments),
                            "record_counts": {"total": len(records), "bar": sum(r["kind"] == "bar" for r in records), "funding": sum(r["kind"] == "funding" for r in records), "open_interest": sum(r["kind"] == "open_interest" for r in records)},
                            "output_sha256": _sha(data), "manifest_digest": ""}
        manifest_payload["manifest_digest"] = _sha(_canonical({k: v for k, v in manifest_payload.items() if k != "manifest_digest"}))
        manifest = partition / "manifest.json"
        _publish(manifest, _canonical(manifest_payload))
        manifests.append(manifest)
        digests.append(manifest_payload["manifest_digest"])
    return MaterializationReceipt(tuple(manifests), tuple(digests))


def load_materialized_partition(partition: Path) -> list[dict[str, Any]]:
    partition = Path(partition)
    manifest = partition / "manifest.json"
    data_path = partition / "data.jsonl"
    if not manifest.is_file() or not data_path.is_file():
        raise ValueError("incomplete materialized partition")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    expected = payload.get("manifest_digest")
    actual = _sha(_canonical({k: v for k, v in payload.items() if k != "manifest_digest"}))
    if expected != actual or payload.get("output_sha256") != _sha(data_path.read_bytes()):
        raise ValueError("materialized partition digest mismatch")
    return [json.loads(line) for line in data_path.read_text(encoding="utf-8").splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--as-of", required=True, type=int)
    parser.add_argument("--min-size", type=int, default=30)
    parser.add_argument("--max-size", type=int, default=80)
    args = parser.parse_args(argv)
    materialize_hourly_market(raw_root=Path(args.raw_root), output_root=Path(args.output_root), as_of=args.as_of,
                              policy=UniversePolicy(min_size=args.min_size, max_size=args.max_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
