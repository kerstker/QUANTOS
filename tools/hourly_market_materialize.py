"""Deterministic, local-only materialization of attested hourly market panels."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
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
_ALLOWED_INTERVALS = (3600, 14400)
_KINDS = ("bar", "funding", "open_interest", "universe")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_INT = (1 << 63) - 1


@dataclass(frozen=True)
class MaterializationReceipt:
    manifests: tuple[Path, ...]
    manifest_digests: tuple[str, ...]


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bounded_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_INT:
        raise ValueError(f"{name} must be a bounded non-negative integer")
    return value


def _strict_digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    return value


def _input_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("invalid input path")
    from pathlib import PurePosixPath
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != value:
        raise ValueError("invalid input path")
    return value


def _required(row: dict[str, Any], names: Iterable[str]) -> None:
    missing = [name for name in names if name not in row]
    if missing:
        raise ValueError(f"missing required fields: {missing}")


def _record(row: dict[str, Any], kind: str) -> Any:
    if row.get("schema_version") != INPUT_SCHEMA:
        raise ValueError("unsupported input schema_version")
    if kind not in _KINDS or row.get("kind") != kind:
        raise ValueError("unknown kind")
    _strict_digest(row.get("source_digest"), "source_digest")
    common = {"schema_version", "kind", "source_digest"}
    if kind == "bar":
        fields = ("instrument", "event_time", "knowledge_time", "open", "high", "low", "close", "quote_volume", "interval_seconds")
        _required(row, fields)
        if set(row) != common | set(fields) or row["interval_seconds"] not in (60, 300):
            raise ValueError("invalid bar envelope")
        return HourlyBar(*(row[k] for k in fields[:-1]))
    if kind == "funding":
        fields = ("instrument", "event_time", "knowledge_time", "rate")
        _required(row, fields)
        if set(row) != common | set(fields):
            raise ValueError("invalid funding envelope")
        return HourlyFunding(*(row[k] for k in fields))
    if kind == "open_interest":
        fields = ("instrument", "event_time", "knowledge_time", "value")
        _required(row, fields)
        if set(row) != common | set(fields):
            raise ValueError("invalid open_interest envelope")
        return HourlyOpenInterest(*(row[k] for k in fields))
    fields = ("instrument", "event_time", "knowledge_time", "quote_volume", "spread_bps", "listed_at", "data_coverage")
    _required(row, fields)
    if set(row) != common | set(fields):
        raise ValueError("invalid universe envelope")
    return UniverseObservation(*(row[k] for k in fields))


def _read_raw(root: Path, as_of: int) -> tuple[list[Any], list[UniverseObservation], list[str], list[dict[str, Any]], dict[str, int]]:
    if not root.is_dir():
        raise ValueError("raw_root must be a directory")
    values: list[Any] = []
    universes: list[UniverseObservation] = []
    source_digests: list[str] = []
    inputs: list[dict[str, Any]] = []
    bar_intervals: dict[str, int] = {}
    seen: set[tuple[str, str, int, int]] = set()
    files = sorted(root.rglob("*.jsonl"))
    if not files:
        raise ValueError("raw_root contains no JSONL input")
    counts = {kind: 0 for kind in _KINDS}
    for path in files:
        raw = path.read_bytes()
        file_digest = _sha(raw)
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError(f"invalid UTF-8 at {path}") from exc
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_no}") from exc
            if not isinstance(row, dict):
                raise ValueError("input record must be an object")
            kind = row.get("kind")
            value = _record(row, kind)
            if value.event_time > as_of or value.knowledge_time > as_of:
                raise ValueError("future event or knowledge time")
            key = (kind, value.instrument, value.event_time, value.knowledge_time)
            if key in seen:
                raise ValueError("duplicate point-in-time record")
            seen.add(key)
            counts[kind] += 1
            source_digests.append(row["source_digest"])
            if kind == "bar":
                interval = row["interval_seconds"]
                prior = bar_intervals.get(value.instrument)
                if prior is not None and prior != interval:
                    raise ValueError("inconsistent base interval for instrument")
                bar_intervals[value.instrument] = interval
                values.append(value)
            elif kind == "universe":
                universes.append(value)
            else:
                values.append(value)
        inputs.append({"path": path.relative_to(root).as_posix(), "sha256": file_digest})
    if any(counts[kind] == 0 for kind in _KINDS):
        raise ValueError("raw input must contain nonempty bar, funding, open_interest, and universe sets")
    bars = sorted((r for r in values if isinstance(r, HourlyBar)), key=lambda r: (r.instrument, r.event_time))
    for instrument in bar_intervals:
        points = [r.event_time for r in bars if r.instrument == instrument]
        base = bar_intervals[instrument]
        if any(b - a != base for a, b in zip(points, points[1:])):
            raise ValueError("bar event_time cadence has a gap")
    return values, universes, sorted(set(source_digests)), inputs, bar_intervals


def _series(rows: list[Any], interval: int, bar_intervals: dict[str, int]) -> tuple[list[HourlyBar], list[HourlyFunding], list[HourlyOpenInterest]]:
    bars = sorted((r for r in rows if isinstance(r, HourlyBar)), key=lambda r: (r.instrument, r.event_time))
    for instrument, base in sorted(bar_intervals.items()):
        points = [r.event_time for r in bars if r.instrument == instrument]
        bucket_size = interval // base
        if interval % base or not points or len(points) < bucket_size:
            raise ValueError("insufficient bars for complete output bucket")
        for start in range(0, len(points), bucket_size):
            group = points[start:start + bucket_size]
            if len(group) != bucket_size or any(b - a != base for a, b in zip(group, group[1:])):
                raise ValueError("partial or non-contiguous output bucket")
            if group[0] % interval or group[-1] // interval != group[0] // interval:
                raise ValueError("bars do not form complete aligned output buckets")
    aggregated = list(aggregate_bars(bars, interval_seconds=interval))

    def latest(cls: type) -> list[Any]:
        grouped: dict[tuple[str, int], list[Any]] = {}
        for row in rows:
            if isinstance(row, cls):
                grouped.setdefault((row.instrument, row.event_time // interval), []).append(row)
        result = []
        for (instrument, bucket), group in sorted(grouped.items()):
            result.append(cls(instrument, bucket * interval, max(x.knowledge_time for x in group),
                              getattr(max(group, key=lambda x: (x.event_time, x.knowledge_time)), "rate", None)
                              if cls is HourlyFunding else getattr(max(group, key=lambda x: (x.event_time, x.knowledge_time)), "value")))
        return result
    return aggregated, latest(HourlyFunding), latest(HourlyOpenInterest)


@contextmanager
def _advisory_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise FileExistsError(f"artifact target is locked: {path}") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _publish(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.parent / f".{path.name}.lock"
    with _advisory_lock(lock):
        if path.exists():
            if path.read_bytes() != data:
                raise FileExistsError(f"immutable artifact conflict: {path}")
            return
        temp_name = ""
        fd, temp_name = tempfile.mkstemp(prefix=".materialize-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                if path.read_bytes() != data:
                    raise FileExistsError(f"immutable artifact conflict: {path}")
            else:
                os.replace(temp_name, path)
                temp_name = ""
        finally:
            if temp_name and os.path.exists(temp_name):
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


def _validate_intervals(intervals: Iterable[int]) -> tuple[int, ...]:
    values = tuple(intervals)
    if not values or len(set(values)) != len(values) or any(isinstance(v, bool) or v not in _ALLOWED_INTERVALS for v in values):
        raise ValueError(f"intervals must be unique values from {_ALLOWED_INTERVALS}")
    return values


def materialize_hourly_market(*, raw_root: Path, output_root: Path, as_of: int, policy: UniversePolicy,
                              intervals: Iterable[int] = (3600, 14400)) -> MaterializationReceipt:
    if isinstance(as_of, bool) or not isinstance(as_of, int) or as_of < 0:
        raise ValueError("as_of must be a non-negative integer")
    intervals = _validate_intervals(intervals)
    rows, observations, source_digests, inputs, bar_intervals = _read_raw(Path(raw_root), as_of)
    universe = select_hourly_universe(observations, as_of=as_of, policy=policy)
    selected = set(universe.instruments)
    bar_instruments = {row.instrument for row in rows if isinstance(row, HourlyBar)}
    funding_instruments = {row.instrument for row in rows if isinstance(row, HourlyFunding)}
    oi_instruments = {row.instrument for row in rows if isinstance(row, HourlyOpenInterest)}
    if not selected <= bar_instruments or not selected <= funding_instruments or not selected <= oi_instruments:
        raise ValueError("each selected instrument requires bars, funding, and open_interest")
    manifests: list[Path] = []
    digests: list[str] = []
    for interval in intervals:
        bars, funding, oi = _series(rows, interval, bar_intervals)
        panel = HourlyMarketPanel("hourly-market-v1", as_of, tuple(bars), tuple(funding), tuple(oi), _sha(_canonical(source_digests)))
        validate_panel(panel)
        records = _rows(panel, interval, universe.instruments)
        if not records:
            raise ValueError("no materialized records")
        data = b"".join(_canonical(record) for record in records)
        partition = Path(output_root) / "hourly_market" / datetime.fromtimestamp(as_of, tz=timezone.utc).strftime("%Y-%m-%d") / f"interval-{interval}"
        _publish(partition / "data.jsonl", data)
        manifest_payload = {"schema_version": OUTPUT_SCHEMA, "as_of": as_of, "interval_seconds": interval,
                            "input_files": inputs, "policy": {k: getattr(policy, k) for k in ("min_size", "max_size", "min_listing_age_seconds", "max_spread_bps", "min_data_coverage")},
                            "complete": True, "source_digests": source_digests, "selected_universe_instruments": list(universe.instruments),
                            "record_counts": {"total": len(records), "bar": sum(r["kind"] == "bar" for r in records), "funding": sum(r["kind"] == "funding" for r in records), "open_interest": sum(r["kind"] == "open_interest" for r in records)},
                            "output_sha256": _sha(data), "manifest_digest": ""}
        manifest_payload["manifest_digest"] = _sha(_canonical({k: v for k, v in manifest_payload.items() if k != "manifest_digest"}))
        manifest = partition / "manifest.json"
        _publish(manifest, _canonical(manifest_payload))
        manifests.append(manifest)
        digests.append(manifest_payload["manifest_digest"])
    return MaterializationReceipt(tuple(manifests), tuple(digests))


def load_materialized_partition(partition: Path, *, raw_root: Path | None = None) -> list[dict[str, Any]]:
    partition = Path(partition)
    manifest_path, data_path = partition / "manifest.json", partition / "data.jsonl"
    if not manifest_path.is_file() or not data_path.is_file():
        raise ValueError("incomplete materialized partition")
    manifest_bytes = manifest_path.read_bytes()
    try:
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid manifest JSON") from exc
    required = {"schema_version", "as_of", "interval_seconds", "input_files", "policy", "complete", "source_digests", "selected_universe_instruments", "record_counts", "output_sha256", "manifest_digest"}
    if not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != OUTPUT_SCHEMA or payload["complete"] is not True:
        raise ValueError("invalid manifest shape")
    if manifest_bytes != _canonical(payload):
        raise ValueError("manifest is not canonical JSON")
    interval = payload["interval_seconds"]
    if isinstance(payload["as_of"], bool) or not isinstance(payload["as_of"], int) or payload["as_of"] < 0 or interval not in _ALLOWED_INTERVALS:
        raise ValueError("invalid manifest time or interval")
    if not isinstance(payload["selected_universe_instruments"], list) or not payload["selected_universe_instruments"] or len(set(payload["selected_universe_instruments"])) != len(payload["selected_universe_instruments"]) or any(not isinstance(x, str) or not x.strip() for x in payload["selected_universe_instruments"]):
        raise ValueError("invalid selected universe")
    policy_keys = {"min_size", "max_size", "min_listing_age_seconds", "max_spread_bps", "min_data_coverage"}
    if not isinstance(payload["policy"], dict) or set(payload["policy"]) != policy_keys:
        raise ValueError("invalid policy")
    try:
        policy = UniversePolicy(**payload["policy"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid policy") from exc
    if len(payload["selected_universe_instruments"]) < policy.min_size or len(payload["selected_universe_instruments"]) > policy.max_size:
        raise ValueError("invalid selected universe size")
    input_files = payload["input_files"]
    if not isinstance(input_files, list) or not input_files:
        raise ValueError("invalid input files")
    input_paths: list[str] = []
    for item in input_files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("invalid input files")
        input_paths.append(_input_path(item["path"]))
        _strict_digest(item["sha256"], "input file sha256")
    if len(set(input_paths)) != len(input_paths):
        raise ValueError("duplicate input file path")
    if not isinstance(payload["source_digests"], list) or not payload["source_digests"] or payload["source_digests"] != sorted(set(payload["source_digests"])):
        raise ValueError("invalid source digests")
    for digest in payload["source_digests"]:
        _strict_digest(digest, "source_digest")
    counts = payload["record_counts"]
    if not isinstance(counts, dict) or set(counts) != {"total", "bar", "funding", "open_interest"} or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts.values()) or counts["total"] != counts["bar"] + counts["funding"] + counts["open_interest"]:
        raise ValueError("invalid record counts")
    if payload["manifest_digest"] != _sha(_canonical({k: v for k, v in payload.items() if k != "manifest_digest"})):
        raise ValueError("manifest digest mismatch")
    data_bytes = data_path.read_bytes()
    if payload["output_sha256"] != _sha(data_bytes):
        raise ValueError("output digest mismatch")
    universe = set(payload["selected_universe_instruments"])
    expected_keys = {"schema_version", "kind", "interval_seconds", "instrument", "event_time", "knowledge_time", "open", "high", "low", "close", "quote_volume"}
    rows: list[dict[str, Any]] = []
    try:
        for line in data_bytes.decode("utf-8").splitlines():
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("schema_version") != OUTPUT_SCHEMA or row.get("interval_seconds") != interval or row.get("instrument") not in universe:
                raise ValueError("invalid output row")
            kind = row.get("kind")
            keys = expected_keys if kind == "bar" else ({"schema_version", "kind", "interval_seconds", "instrument", "event_time", "knowledge_time", "rate"} if kind == "funding" else {"schema_version", "kind", "interval_seconds", "instrument", "event_time", "knowledge_time", "value"})
            if kind not in {"bar", "funding", "open_interest"} or set(row) != keys:
                raise ValueError("invalid output row schema")
            if any(isinstance(row[k], bool) or not isinstance(row[k], int) for k in ("event_time", "knowledge_time")) or row["event_time"] > payload["as_of"] or row["knowledge_time"] > payload["as_of"]:
                raise ValueError("invalid output row time")
            if kind == "bar":
                HourlyBar(*(row[k] for k in ("instrument", "event_time", "knowledge_time", "open", "high", "low", "close", "quote_volume")))
            elif kind == "funding":
                HourlyFunding(*(row[k] for k in ("instrument", "event_time", "knowledge_time", "rate")))
            else:
                HourlyOpenInterest(*(row[k] for k in ("instrument", "event_time", "knowledge_time", "value")))
            rows.append(row)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("invalid materialized data") from exc
    if data_bytes != b"".join(_canonical(row) for row in rows):
        raise ValueError("materialized data is not canonical JSONL")
    if len(rows) != counts["total"] or {k: sum(r["kind"] == k for r in rows) for k in ("bar", "funding", "open_interest")} != {k: counts[k] for k in ("bar", "funding", "open_interest")}:
        raise ValueError("record count mismatch")
    if raw_root is not None:
        root = Path(raw_root).resolve()
        for item in input_files:
            source = (root / item["path"]).resolve()
            try:
                source.relative_to(root)
            except ValueError as exc:
                raise ValueError("input path escapes raw_root") from exc
            if not source.is_file() or _sha(source.read_bytes()) != item["sha256"]:
                raise ValueError("input file digest mismatch")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--as-of", required=True, type=int)
    parser.add_argument("--interval-seconds", action="append", type=int)
    parser.add_argument("--min-size", type=int, default=30)
    parser.add_argument("--max-size", type=int, default=80)
    parser.add_argument("--min-listing-age-seconds", type=int, default=0)
    parser.add_argument("--max-spread-bps", type=float, default=UniversePolicy().max_spread_bps)
    parser.add_argument("--min-data-coverage", type=float, default=0.0)
    args = parser.parse_args(argv)
    intervals = tuple(args.interval_seconds) if args.interval_seconds else (3600, 14400)
    policy = UniversePolicy(min_size=args.min_size, max_size=args.max_size,
                            min_listing_age_seconds=args.min_listing_age_seconds,
                            max_spread_bps=args.max_spread_bps,
                            min_data_coverage=args.min_data_coverage)
    materialize_hourly_market(raw_root=Path(args.raw_root), output_root=Path(args.output_root), as_of=args.as_of, policy=policy, intervals=intervals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
