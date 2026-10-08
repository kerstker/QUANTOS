#!/usr/bin/env python3
"""Normalize a quarantined TongDaXin daily-bar intake into compact SQLite.

The source directory is never modified.  Daily ``.day`` records are decoded
from the documented 32-byte little-endian layout and written to a staging
database.  ``base.dbf`` is retained as a *current snapshot* only: it must not be
joined backwards as point-in-time fundamentals.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
from pathlib import Path
from typing import BinaryIO, Iterator, Sequence

try:  # POSIX advisory lock; unavailable on native Windows.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised by platform-boundary tests
    _fcntl = None  # type: ignore[assignment]

try:  # Native Windows byte-range lock; unavailable on POSIX.
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised by platform-boundary tests
    _msvcrt = None  # type: ignore[assignment]


DAY_RECORD = struct.Struct("<IIIIIfII")
# Kept as the default for the low-level decoder's backwards-compatible unit API.
# Production intake derives the capture date from the snapshot directory or an
# explicit CLI argument; it never relies on this constant for new snapshots.
CAPTURE_DATE = 20260830
SCHEMA_VERSION = 2
TECHNICAL_STATUS = "TECHNICAL_VALIDATION_ONLY"
CURRENT_ONLY = "CURRENT_CAPTURE_ONLY_NOT_HISTORICAL_PIT"
OPAQUE_ACTION_STATUS = "OPAQUE_ENCRYPTED_RECORDS_NOT_ADMITTED"
_CAPTURE_RE = re.compile(r"(?<!\d)(20\d{6})(?!\d)")
_TNF_LAYOUTS = ((360, 31, 18), (314, 23, 18))
_TNF_FILENAMES = {"sh": "shs.tnf", "sz": "szs.tnf", "bj": "bjs.tnf"}
_DISCOVERY_SKIP = {
    ".git", ".cache", ".venv", "venv", "node_modules", "__pycache__",
    "models", "checkpoints", "snap", "docker", "containerd",
}
_REQUIRED_SCHEMA_COLUMNS = {
    "metadata": {"key", "value_json"},
    "instruments": {
        "instrument", "market", "code", "listing_date", "first_date", "last_date",
        "bar_rows", "current_master_present", "source_path",
    },
    "daily_prices": {
        "instrument", "trade_date", "knowledge_time", "open", "high", "low", "close",
        "amount", "volume", "reserved",
    },
    "current_fundamental_snapshot": {
        "instrument", "snapshot_date", "listing_date", "payload_json", "authority_mode",
    },
    "source_captures": {
        "capture_date", "manifest_sha256", "source_root", "source_report_json",
    },
    "universe_snapshots": {
        "capture_date", "instrument", "market", "code", "authority_mode",
    },
    "security_master_snapshots": {
        "capture_date", "instrument", "market", "code", "name", "is_st", "authority_mode",
    },
    "fundamental_snapshots": {
        "capture_date", "instrument", "vendor_update_date", "listing_date", "payload_json",
        "authority_mode",
    },
    "industry_snapshots": {
        "capture_date", "instrument", "industry_code", "industry_name",
        "secondary_industry_code", "secondary_industry_name", "payload_json", "authority_mode",
    },
    "current_business_snapshots": {
        "capture_date", "instrument", "payload_json", "authority_mode",
    },
    "opaque_source_snapshots": {
        "capture_date", "source_name", "source_sha256", "source_bytes", "record_count",
        "parse_status", "reason",
    },
}
_MANIFEST_IDENTITY_FIELDS = ("manifest_sha256", "files", "bytes")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_sha256_manifest(path: Path) -> list[tuple[str, Path]]:
    root = path.parent
    rows: list[tuple[str, Path]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            digest, relative = raw.split(maxsplit=1)
        except ValueError as exc:
            raise ValueError(f"invalid SHA256SUMS line {line_number}") from exc
        relative = relative.removeprefix("*").removeprefix("./")
        unresolved = root / relative
        if unresolved.is_symlink():
            raise ValueError(f"manifest entry must not be a symlink: {relative}")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"manifest path escapes source root: {relative}") from exc
        if len(digest) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in digest):
            raise ValueError(f"invalid SHA-256 on line {line_number}")
        rows.append((digest.lower(), candidate))
    return rows


def verify_sha256_manifest(path: Path) -> dict[str, int | str]:
    rows = parse_sha256_manifest(path)
    listed = {candidate for _, candidate in rows}
    if len(listed) != len(rows):
        raise ValueError("SHA256SUMS contains duplicate paths")
    actual: set[Path] = set()
    for candidate in path.parent.rglob("*"):
        if candidate == path or not candidate.is_file():
            continue
        if candidate.is_symlink():
            raise ValueError(f"source contains a symlink: {candidate}")
        actual.add(candidate.resolve())
    unlisted = sorted(actual - listed)
    missing_from_tree = sorted(listed - actual)
    if unlisted:
        sample = ", ".join(str(item.relative_to(path.parent.resolve())) for item in unlisted[:3])
        raise ValueError(f"source files not covered by SHA256SUMS: {sample}")
    if missing_from_tree:
        sample = ", ".join(str(item) for item in missing_from_tree[:3])
        raise FileNotFoundError(f"manifest file missing: {sample}")
    total_bytes = 0
    for expected, candidate in rows:
        if not candidate.is_file():
            raise FileNotFoundError(f"manifest file missing: {candidate}")
        actual = _sha256(candidate)
        if actual != expected:
            raise ValueError(f"SHA-256 mismatch: {candidate}")
        total_bytes += candidate.stat().st_size
    return {
        "status": "PASS",
        "files": len(rows),
        "bytes": total_bytes,
        "manifest_sha256": _sha256(path),
    }


def _valid_yyyymmdd(value: int, *, label: str) -> int:
    try:
        dt.datetime.strptime(str(value), "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"invalid {label}: {value}") from exc
    return value


def infer_capture_date(source: Path, *, explicit: int | None = None) -> int:
    """Return an auditable capture date, never an mtime-derived approximation."""
    if explicit is not None:
        return _valid_yyyymmdd(int(explicit), label="capture date")
    matches = _CAPTURE_RE.findall(source.name)
    if not matches:
        raise ValueError("capture date is required when source directory has no YYYYMMDD suffix")
    return _valid_yyyymmdd(int(matches[-1]), label="capture date")


def read_dbf(path: Path) -> list[dict[str, str]]:
    """Read the simple dBase-III subset used by TongDaXin ``base.dbf``."""
    data = path.read_bytes()
    if len(data) < 33 or data[0] != 0x03:
        raise ValueError(f"unsupported DBF header: {path}")
    row_count = struct.unpack_from("<I", data, 4)[0]
    header_bytes = struct.unpack_from("<H", data, 8)[0]
    row_bytes = struct.unpack_from("<H", data, 10)[0]
    fields: list[tuple[str, int]] = []
    offset = 32
    while offset < header_bytes and data[offset] != 0x0D:
        descriptor = data[offset : offset + 32]
        name = descriptor[:11].split(b"\0", 1)[0].decode("ascii")
        fields.append((name, descriptor[16]))
        offset += 32
    if 1 + sum(length for _, length in fields) != row_bytes:
        raise ValueError("DBF record length does not match field descriptors")
    expected_bytes = header_bytes + row_count * row_bytes
    if len(data) not in {expected_bytes, expected_bytes + 1}:
        raise ValueError("DBF file length does not match header")

    rows: list[dict[str, str]] = []
    for index in range(row_count):
        record = data[header_bytes + index * row_bytes : header_bytes + (index + 1) * row_bytes]
        if record[:1] == b"*":
            continue
        cursor = 1
        row: dict[str, str] = {}
        for name, length in fields:
            raw = record[cursor : cursor + length]
            cursor += length
            row[name] = raw.rstrip(b"\0 ").decode("gb18030").strip()
        rows.append(row)
    return rows


def _decode_gb18030(raw: bytes, *, label: str) -> str:
    try:
        return raw.decode("gb18030").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid GB18030 text in {label}") from exc


def _is_st_name(name: str) -> bool:
    normalized = name.strip().upper().replace(" ", "")
    return normalized.startswith(("ST", "*ST", "S*ST", "SST"))


def read_tnf(path: Path, market: str) -> dict[str, dict[str, object]]:
    """Decode the security code/name subset of current TongDaXin ``*.tnf``.

    TongDaXin has shipped both 314-byte and 360-byte records.  Layout selection
    is exact-length based and every admitted stock code is validated; unknown
    layouts fail loudly rather than guessing offsets.
    """
    data = path.read_bytes()
    header_bytes = 50
    if len(data) < header_bytes:
        raise ValueError(f"truncated TNF header: {path}")
    layouts = [layout for layout in _TNF_LAYOUTS if (len(data) - header_bytes) % layout[0] == 0]
    if not layouts:
        raise ValueError(f"unsupported TNF record layout: {path}")
    selected: tuple[int, int, int] | None = None
    for layout in layouts:
        record_bytes, _, _ = layout
        sample_count = min(64, (len(data) - header_bytes) // record_bytes)
        samples = [data[header_bytes + i * record_bytes : header_bytes + i * record_bytes + 6]
                   for i in range(sample_count)]
        if samples and all(len(code) == 6 and code.isdigit() for code in samples):
            selected = layout
            break
    if selected is None:
        raise ValueError(f"TNF records do not begin with six-digit codes: {path}")

    record_bytes, name_offset, name_bytes = selected
    rows: dict[str, dict[str, object]] = {}
    record_count = (len(data) - header_bytes) // record_bytes
    for index in range(record_count):
        record = data[header_bytes + index * record_bytes : header_bytes + (index + 1) * record_bytes]
        raw_code = record[:6]
        if not raw_code.isdigit():
            raise ValueError(f"invalid TNF code at record {index}: {path}")
        code = raw_code.decode("ascii")
        if not is_stock_code(market, code):
            continue
        raw_name = record[name_offset : name_offset + name_bytes].split(b"\0", 1)[0]
        name = _decode_gb18030(raw_name, label=f"{path}:{index}")
        key = instrument_key(market, code)
        row = {
            "code": code,
            "instrument": key,
            "market": market,
            "name": name,
            "is_st": _is_st_name(name),
        }
        previous = rows.setdefault(key, row)
        if previous != row:
            raise ValueError(f"conflicting TNF security rows for {key}")
    return rows


def _hq_cache_path(source: Path) -> Path:
    for relative in ("metadata/hq_cache", "T0002/hq_cache", "hq_cache"):
        candidate = source / relative
        if candidate.is_dir():
            return candidate
    # Stable expected path keeps missing-file diagnostics deterministic.
    return source / "metadata" / "hq_cache"


def read_security_master(source: Path) -> dict[str, dict[str, object]]:
    hq = _hq_cache_path(source)
    rows: dict[str, dict[str, object]] = {}
    for market, filename in _TNF_FILENAMES.items():
        path = hq / filename
        if not path.is_file():
            continue
        for key, row in read_tnf(path, market).items():
            if key in rows and rows[key] != row:
                raise ValueError(f"duplicate security master row: {key}")
            rows[key] = row
    return rows


def _read_pipe_rows(path: Path, *, expected_fields: int) -> list[list[str]]:
    if not path.is_file():
        return []
    rows: list[list[str]] = []
    for line_number, raw in enumerate(path.read_text(encoding="gb18030").splitlines(), 1):
        if not raw.strip():
            continue
        fields = raw.split("|")
        if len(fields) != expected_fields:
            raise ValueError(f"unexpected field count in {path}:{line_number}")
        rows.append(fields)
    return rows


def read_industry_snapshots(source: Path) -> dict[str, dict[str, object]]:
    hq = _hq_cache_path(source)
    names: dict[str, str] = {}
    # tdxzs3 is the observed superset; tdxzs supplies compatible fallbacks.
    for filename in ("tdxzs3.cfg", "tdxzs.cfg"):
        for fields in _read_pipe_rows(hq / filename, expected_fields=6):
            code, name = fields[-1], fields[0]
            previous = names.setdefault(code, name)
            if previous != name:
                raise ValueError(f"conflicting current industry label for {code}")
    market_by_sc = {"0": "sz", "1": "sh", "2": "bj"}
    rows: dict[str, dict[str, object]] = {}
    for fields in _read_pipe_rows(hq / "tdxhy.cfg", expected_fields=6):
        sc, code, industry_code, unused_1, unused_2, secondary_code = fields
        if sc not in market_by_sc or not code.isdigit() or len(code) != 6:
            continue
        if unused_1 or unused_2:
            raise ValueError(f"unsupported non-empty tdxhy reserved fields for {sc}:{code}")
        market = market_by_sc[sc]
        if not is_stock_code(market, code):
            continue
        key = instrument_key(market, code)
        row = {
            "instrument": key,
            "industry_code": industry_code or None,
            "industry_name": names.get(industry_code),
            "secondary_industry_code": secondary_code or None,
            "secondary_industry_name": names.get(secondary_code),
            "payload": fields,
        }
        previous = rows.setdefault(key, row)
        if previous != row:
            raise ValueError(f"conflicting current industry row for {key}")
    return rows


def read_current_business_snapshots(source: Path) -> dict[str, list[str]]:
    hq = _hq_cache_path(source)
    market_by_sc = {"0": "sz", "1": "sh", "2": "bj"}
    rows: dict[str, list[str]] = {}
    for fields in _read_pipe_rows(hq / "specgpext.txt", expected_fields=11):
        sc, code = fields[:2]
        if sc not in market_by_sc or not code.isdigit() or len(code) != 6:
            continue
        market = market_by_sc[sc]
        if not is_stock_code(market, code):
            continue
        key = instrument_key(market, code)
        previous = rows.setdefault(key, fields)
        if previous != fields:
            raise ValueError(f"conflicting current business row for {key}")
    return rows


def inspect_gbbq(path: Path) -> dict[str, int | str]:
    """Validate the observed encrypted 29-byte record container without decoding it."""
    data = path.read_bytes()
    if len(data) < 4:
        raise ValueError(f"truncated gbbq header: {path}")
    record_count = struct.unpack_from("<I", data, 0)[0]
    record_bytes = 29
    if len(data) - 4 != record_count * record_bytes:
        raise ValueError(f"gbbq record count does not match file length: {path}")
    return {
        "status": OPAQUE_ACTION_STATUS,
        "record_count": record_count,
        "record_bytes": record_bytes,
        "bytes": len(data),
        "sha256": _sha256(path),
    }


def is_stock_code(market: str, code: str) -> bool:
    if market == "sh":
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    if market == "sz":
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if market == "bj":
        prefixes = (
            "430", "830", "831", "832", "833", "834", "835", "836",
            "837", "838", "839", "870", "871", "872", "873", "920",
        )
        return code.startswith(prefixes) and code != "920000"
    return False


def instrument_key(market: str, code: str) -> str:
    return f"{code}.{market.upper()}"


def iter_day_rows(
    path: Path,
    *,
    capture_date: int = CAPTURE_DATE,
) -> Iterator[tuple[int, float, float, float, float, float, int, int]]:
    capture_date = _valid_yyyymmdd(capture_date, label="capture date")
    size = path.stat().st_size
    if size % DAY_RECORD.size:
        raise ValueError(f"partial .day record: {path}")
    previous_date = 0
    with path.open("rb") as handle:
        while raw := handle.read(DAY_RECORD.size):
            date, open_i, high_i, low_i, close_i, amount, volume, reserved = DAY_RECORD.unpack(raw)
            try:
                dt.datetime.strptime(str(date), "%Y%m%d")
            except ValueError as exc:
                raise ValueError(f"invalid trading date {date} in {path}") from exc
            if date <= previous_date:
                raise ValueError(f"non-increasing trading date {date} in {path}")
            if date > capture_date:
                raise ValueError(f"trading date {date} is later than capture {capture_date} in {path}")
            if high_i < max(open_i, close_i, low_i) or low_i > min(open_i, close_i, high_i):
                raise ValueError(f"invalid OHLC relationship on {date} in {path}")
            if not math.isfinite(amount) or amount < 0 or volume < 0:
                raise ValueError(f"invalid amount/volume on {date} in {path}")
            previous_date = date
            yield (
                date,
                open_i / 100.0,
                high_i / 100.0,
                low_i / 100.0,
                close_i / 100.0,
                float(amount),
                int(volume),
                int(reserved),
            )


def _stock_day_files(source: Path) -> list[tuple[str, str, Path]]:
    rows: list[tuple[str, str, Path]] = []
    for path in source.glob("vipdoc/*/lday/*.day"):
        market = path.parts[-3].lower()
        stem = path.stem.lower()
        code = stem[2:] if stem.startswith(market) else stem
        if is_stock_code(market, code):
            rows.append((market, code, path))
    return sorted(rows)


def _metadata_rows(connection: sqlite3.Connection, values: dict[str, object]) -> None:
    connection.executemany(
        """INSERT INTO metadata(key,value_json) VALUES (?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
        ((key, json.dumps(value, ensure_ascii=False, sort_keys=True)) for key, value in values.items()),
    )


def discover_tdx_sources(roots: Sequence[Path], *, max_depth: int = 12) -> list[dict[str, object]]:
    """Find local TongDaXin roots without following symlinks or scanning forever."""
    found: dict[Path, dict[str, object]] = {}
    for raw_root in roots:
        root = raw_root.expanduser().resolve()
        if not root.is_dir():
            continue
        for directory, dirnames, _ in os.walk(root, followlinks=False):
            here = Path(directory)
            try:
                depth = len(here.relative_to(root).parts)
            except ValueError:
                continue
            dirnames[:] = [
                name for name in dirnames
                if name not in _DISCOVERY_SKIP and not (here / name).is_symlink()
            ]
            if depth >= max_depth:
                dirnames[:] = []
            if here.name.lower() != "vipdoc":
                continue
            source = here.parent.resolve()
            by_market = {
                market: sum(1 for _ in (here / market / "lday").glob("*.day"))
                for market in ("bj", "sh", "sz")
            }
            if not sum(by_market.values()):
                continue
            hq = _hq_cache_path(source)
            found[source] = {
                "source_root": str(source),
                "vipdoc": str(here.resolve()),
                "hq_cache": str(hq.resolve()) if hq.is_dir() else None,
                "day_files": sum(by_market.values()),
                "markets": by_market,
                "has_base_dbf": (hq / "base.dbf").is_file(),
                "has_security_master": any((hq / name).is_file() for name in ("shs.tnf", "szs.tnf", "bjs.tnf")),
                "has_current_industry": (hq / "tdxhy.cfg").is_file(),
                "has_corporate_action_container": (hq / "gbbq").is_file(),
                "has_sha256_manifest": (source / "SHA256SUMS").is_file(),
            }
            dirnames[:] = []
    return [found[key] for key in sorted(found, key=str)]


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS metadata(
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS instruments(
            instrument TEXT PRIMARY KEY,
            market TEXT NOT NULL,
            code TEXT NOT NULL,
            listing_date INTEGER,
            first_date INTEGER NOT NULL,
            last_date INTEGER NOT NULL,
            bar_rows INTEGER NOT NULL,
            current_master_present INTEGER NOT NULL,
            source_path TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_prices(
            instrument TEXT NOT NULL,
            trade_date INTEGER NOT NULL,
            knowledge_time INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            amount REAL NOT NULL,
            volume INTEGER NOT NULL,
            reserved INTEGER NOT NULL,
            PRIMARY KEY(instrument, trade_date)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS current_fundamental_snapshot(
            instrument TEXT PRIMARY KEY,
            snapshot_date INTEGER,
            listing_date INTEGER,
            payload_json TEXT NOT NULL,
            authority_mode TEXT NOT NULL
            CHECK(authority_mode='CURRENT_SNAPSHOT_ONLY_NOT_PIT')
        );
        CREATE TABLE IF NOT EXISTS source_captures(
            capture_date INTEGER PRIMARY KEY,
            manifest_sha256 TEXT NOT NULL UNIQUE,
            source_root TEXT NOT NULL,
            source_report_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS universe_snapshots(
            capture_date INTEGER NOT NULL,
            instrument TEXT NOT NULL,
            market TEXT NOT NULL,
            code TEXT NOT NULL,
            authority_mode TEXT NOT NULL CHECK(authority_mode='{CURRENT_ONLY}'),
            PRIMARY KEY(capture_date,instrument)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS security_master_snapshots(
            capture_date INTEGER NOT NULL,
            instrument TEXT NOT NULL,
            market TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            is_st INTEGER,
            authority_mode TEXT NOT NULL CHECK(authority_mode='{CURRENT_ONLY}'),
            PRIMARY KEY(capture_date,instrument)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS fundamental_snapshots(
            capture_date INTEGER NOT NULL,
            instrument TEXT NOT NULL,
            vendor_update_date INTEGER,
            listing_date INTEGER,
            payload_json TEXT NOT NULL,
            authority_mode TEXT NOT NULL CHECK(authority_mode='{CURRENT_ONLY}'),
            PRIMARY KEY(capture_date,instrument)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS industry_snapshots(
            capture_date INTEGER NOT NULL,
            instrument TEXT NOT NULL,
            industry_code TEXT,
            industry_name TEXT,
            secondary_industry_code TEXT,
            secondary_industry_name TEXT,
            payload_json TEXT NOT NULL,
            authority_mode TEXT NOT NULL CHECK(authority_mode='{CURRENT_ONLY}'),
            PRIMARY KEY(capture_date,instrument)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS current_business_snapshots(
            capture_date INTEGER NOT NULL,
            instrument TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            authority_mode TEXT NOT NULL CHECK(authority_mode='{CURRENT_ONLY}'),
            PRIMARY KEY(capture_date,instrument)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS opaque_source_snapshots(
            capture_date INTEGER NOT NULL,
            source_name TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            source_bytes INTEGER NOT NULL,
            record_count INTEGER,
            parse_status TEXT NOT NULL,
            reason TEXT NOT NULL,
            PRIMARY KEY(capture_date,source_name)
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS daily_prices_date_idx
            ON daily_prices(trade_date,instrument);
        CREATE INDEX IF NOT EXISTS universe_snapshots_instrument_idx
            ON universe_snapshots(instrument,capture_date);
        PRAGMA user_version={SCHEMA_VERSION};
        """
    )


def _optional_date(raw: str) -> int | None:
    if not raw or not raw.isdigit() or len(raw) != 8:
        return None
    try:
        return _valid_yyyymmdd(int(raw), label="metadata date")
    except ValueError:
        return None


def _manifest_identity(report: dict[str, object]) -> tuple[object, ...]:
    return tuple(report.get(field) for field in _MANIFEST_IDENTITY_FIELDS)


def _require_contract_int(report: dict, key: str, *, minimum: int = 0) -> int:
    value = report.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"existing capture contract has invalid {key}")
    return value


def _validate_capture_contract(
    report: object,
    *,
    capture_date: int,
    manifest_sha256: str,
    source_root: str,
    current_verification: dict[str, object],
) -> dict:
    if not isinstance(report, dict):
        raise ValueError("existing capture contract must be a JSON object")
    expected_values = {
        "schema_version": SCHEMA_VERSION,
        "evidence_status": TECHNICAL_STATUS,
        "source_kind": "TONGDAXIN_LOCAL_DAILY_AND_CURRENT_METADATA",
        "capture_date": capture_date,
        "capture_status": "INGESTED",
        "daily_price_semantics": "RAW_UNADJUSTED_CLOSE_KNOWN_AFTER_CLOSE_T",
        "security_master_semantics": CURRENT_ONLY,
        "st_semantics": CURRENT_ONLY,
        "fundamental_semantics": CURRENT_ONLY,
        "industry_semantics": CURRENT_ONLY,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
    }
    for key, expected in expected_values.items():
        if report.get(key) != expected:
            raise ValueError(f"existing capture contract has invalid {key}")
    if not source_root or report.get("source_root") != source_root:
        raise ValueError("existing capture contract has invalid source_root")

    verification = report.get("sha256_verification")
    if not isinstance(verification, dict):
        raise ValueError("existing capture contract has invalid sha256_verification")
    if verification.get("manifest_sha256") != manifest_sha256:
        raise ValueError("existing capture contract manifest does not match source_captures")
    stored_files = _require_contract_int(verification, "files", minimum=1)
    current_files = current_verification.get("files")
    if stored_files != current_files:
        raise ValueError("existing capture contract file count does not match current manifest")
    stored_bytes = verification.get("bytes")
    current_bytes = current_verification.get("bytes")
    if stored_bytes is not None:
        if isinstance(stored_bytes, bool) or not isinstance(stored_bytes, int) or stored_bytes < 0:
            raise ValueError("existing capture contract has invalid manifest byte count")
        if current_bytes is not None and stored_bytes != current_bytes:
            raise ValueError("existing capture contract byte count does not match current manifest")
    pre_publish = report.get("pre_publish_sha256_verification")
    if pre_publish is not None:
        if not isinstance(pre_publish, dict) or _manifest_identity(pre_publish) != _manifest_identity(
            verification
        ):
            raise ValueError("existing capture contract has inconsistent pre-publish verification")

    for key in (
        "instruments", "rows", "rows_inserted", "rows_total", "first_date", "last_date",
        "current_master_absent", "current_security_names", "current_st_instruments",
        "current_fundamental_rows", "current_industry_rows", "current_business_rows",
        "added_since_prior_capture", "absent_since_prior_capture",
    ):
        _require_contract_int(report, key)
    if report["instruments"] < 1 or report["rows"] < 1 or report["rows_total"] < 1:
        raise ValueError("existing capture contract has empty core data counts")
    if report["first_date"] > report["last_date"] or report["last_date"] > capture_date:
        raise ValueError("existing capture contract has invalid data date range")
    by_market = report.get("instruments_by_market")
    if not isinstance(by_market, dict) or set(by_market) != {"sh", "sz", "bj"}:
        raise ValueError("existing capture contract has invalid instruments_by_market")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in by_market.values()
    ) or sum(by_market.values()) != report["instruments"]:
        raise ValueError("existing capture contract has inconsistent market counts")
    if report["current_master_absent"] + report["current_fundamental_rows"] != report[
        "instruments"
    ]:
        raise ValueError("existing capture contract has inconsistent current master coverage")
    for key in (
        "current_security_names", "current_st_instruments", "current_fundamental_rows",
        "current_industry_rows", "current_business_rows",
    ):
        if report[key] > report["instruments"]:
            raise ValueError(f"existing capture contract has impossible {key}")

    contract_version = report.get("capture_contract_version", 1)
    if contract_version not in {1, 2}:
        raise ValueError("existing capture contract has invalid capture_contract_version")
    if contract_version == 2:
        if report.get("current_universe_authority") != "TONGDAXIN_TNF_CURRENT_CAPTURE":
            raise ValueError("existing capture contract has invalid current_universe_authority")
        if report.get(
            "daily_coverage_semantics"
        ) != "HISTORICAL_BAR_FILES_NOT_CURRENT_MEMBERSHIP_AUTHORITY":
            raise ValueError("existing capture contract has invalid daily_coverage_semantics")
        if report.get("instruments_semantics") != "CURRENT_TNF_UNIVERSE_COUNT":
            raise ValueError("existing capture contract has invalid instruments_semantics")
        if report.get(
            "instrument_current_master_present_semantics"
        ) != "TNF_CURRENT_UNIVERSE_MEMBERSHIP_FOR_INSTRUMENTS_WITH_DAILY_HISTORY":
            raise ValueError(
                "existing capture contract has invalid instrument current-master semantics"
            )
        if report.get(
            "current_master_absent_semantics"
        ) != "TNF_CURRENT_UNIVERSE_WITHOUT_BASE_DBF_ROW":
            raise ValueError("existing capture contract has invalid current master absence semantics")
        for key in ("daily_instruments", "daily_only_instruments", "current_universe_without_daily"):
            _require_contract_int(report, key)
        daily_by_market = report.get("daily_instruments_by_market")
        if not isinstance(daily_by_market, dict) or set(daily_by_market) != {"sh", "sz", "bj"}:
            raise ValueError("existing capture contract has invalid daily_instruments_by_market")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in daily_by_market.values()
        ) or sum(daily_by_market.values()) != report["daily_instruments"]:
            raise ValueError("existing capture contract has inconsistent daily market counts")
        if report["daily_only_instruments"] > report["daily_instruments"] or report[
            "current_universe_without_daily"
        ] > report["instruments"]:
            raise ValueError("existing capture contract has impossible universe coverage counts")
        if report["daily_instruments"] - report["daily_only_instruments"] != report[
            "instruments"
        ] - report["current_universe_without_daily"]:
            raise ValueError("existing capture contract has inconsistent universe overlap")
        authority_markets = report.get("current_universe_authority_markets")
        missing_authority_markets = report.get("current_universe_missing_authority_markets")
        if not isinstance(authority_markets, list) or not isinstance(
            missing_authority_markets, list
        ):
            raise ValueError("existing capture contract has invalid TNF authority market evidence")
        if set(authority_markets) | set(missing_authority_markets) != {"sh", "sz", "bj"} or set(
            authority_markets
        ) & set(missing_authority_markets):
            raise ValueError("existing capture contract has inconsistent TNF authority markets")
        expected_scope = (
            "ALL_SUPPORTED_STOCK_MARKETS"
            if not missing_authority_markets
            else "PARTIAL_MARKETS_EXPLICIT"
        )
        if report.get("current_universe_scope") != expected_scope:
            raise ValueError("existing capture contract has invalid current_universe_scope")
    return report


def _validate_existing_core(
    connection: sqlite3.Connection,
    *,
    capture_date: int,
    report: dict,
) -> None:
    universe_by_market = {market: count for market, count in connection.execute(
        "SELECT market,COUNT(*) FROM universe_snapshots WHERE capture_date=? GROUP BY market",
        (capture_date,),
    )}
    actual_by_market = {market: int(universe_by_market.get(market, 0)) for market in ("sh", "sz", "bj")}
    if actual_by_market != report["instruments_by_market"]:
        raise ValueError("existing capture core data has inconsistent universe market counts")
    universe_rows = sum(actual_by_market.values())
    if universe_rows != report["instruments"]:
        raise ValueError("existing capture core data has inconsistent universe count")

    missing_security_rows = connection.execute(
        """SELECT COUNT(*)
             FROM universe_snapshots AS universe
             LEFT JOIN security_master_snapshots AS security
               ON security.capture_date=universe.capture_date
              AND security.instrument=universe.instrument
            WHERE universe.capture_date=? AND security.instrument IS NULL""",
        (capture_date,),
    ).fetchone()[0]
    security_rows = connection.execute(
        "SELECT COUNT(*) FROM security_master_snapshots WHERE capture_date=?", (capture_date,)
    ).fetchone()[0]
    if missing_security_rows or security_rows != universe_rows:
        raise ValueError("existing capture core data has inconsistent security master snapshot")
    names_and_st = connection.execute(
        """SELECT
               SUM(CASE WHEN name IS NOT NULL AND name<>'' THEN 1 ELSE 0 END),
               SUM(CASE WHEN is_st=1 THEN 1 ELSE 0 END)
             FROM security_master_snapshots WHERE capture_date=?""",
        (capture_date,),
    ).fetchone()
    if tuple(int(value or 0) for value in names_and_st) != (
        report["current_security_names"], report["current_st_instruments"]
    ):
        raise ValueError("existing capture core data has inconsistent security master attributes")

    capture_snapshot_counts = {
        "fundamental_snapshots": report["current_fundamental_rows"],
        "industry_snapshots": report["current_industry_rows"],
        "current_business_snapshots": report["current_business_rows"],
    }
    for table, expected_count in capture_snapshot_counts.items():
        orphan_rows = connection.execute(
            f"""SELECT COUNT(*) FROM {table} AS snapshot
                 LEFT JOIN universe_snapshots AS universe
                   ON universe.capture_date=snapshot.capture_date
                  AND universe.instrument=snapshot.instrument
                WHERE snapshot.capture_date=? AND universe.instrument IS NULL""",
            (capture_date,),
        ).fetchone()[0]
        if orphan_rows:
            raise ValueError(f"existing capture core data has orphan rows in {table}")
        actual_count = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE capture_date=?", (capture_date,)
        ).fetchone()[0]
        if actual_count != expected_count:
            raise ValueError(f"existing capture core data has inconsistent row count in {table}")

    orphan_prices = connection.execute(
        """SELECT COUNT(*) FROM daily_prices AS prices
             LEFT JOIN instruments USING(instrument)
            WHERE instruments.instrument IS NULL"""
    ).fetchone()[0]
    if orphan_prices:
        raise ValueError("existing capture core data has orphan daily prices")
    inconsistent_instruments = connection.execute(
        """SELECT COUNT(*) FROM (
               SELECT instruments.instrument,
                      instruments.bar_rows,
                      instruments.first_date,
                      instruments.last_date,
                      COUNT(daily_prices.trade_date) AS actual_rows,
                      MIN(daily_prices.trade_date) AS actual_first,
                      MAX(daily_prices.trade_date) AS actual_last
                 FROM instruments
                 LEFT JOIN daily_prices USING(instrument)
                GROUP BY instruments.instrument
               HAVING actual_rows=0
                   OR bar_rows<>actual_rows
                   OR first_date<>actual_first
                   OR last_date<>actual_last
           )"""
    ).fetchone()[0]
    if inconsistent_instruments:
        raise ValueError("existing capture core data has inconsistent instrument bar summaries")
    invalid_prices = connection.execute(
        """SELECT COUNT(*) FROM daily_prices
            WHERE knowledge_time<>trade_date
               OR high<open OR high<close OR high<low
               OR low>open OR low>close OR low>high
               OR amount<0 OR volume<0"""
    ).fetchone()[0]
    if invalid_prices:
        raise ValueError("existing capture core data has invalid daily price rows")

    total_rows = connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
    latest_capture = connection.execute("SELECT MAX(capture_date) FROM source_captures").fetchone()[0]
    if total_rows < report["rows_total"] or (
        latest_capture == capture_date and total_rows != report["rows_total"]
    ):
        raise ValueError("existing capture core data has inconsistent total row count")
    latest_trade_date = connection.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()[0]
    if latest_trade_date is None or latest_capture is None or latest_trade_date > latest_capture:
        raise ValueError("existing capture core data extends beyond the latest capture")
    stale_current_flags = connection.execute(
        """SELECT COUNT(*)
             FROM instruments
             LEFT JOIN universe_snapshots AS current_universe
               ON current_universe.capture_date=?
              AND current_universe.instrument=instruments.instrument
            WHERE instruments.current_master_present NOT IN (0,1)
               OR (current_universe.instrument IS NULL AND instruments.current_master_present<>0)
               OR (current_universe.instrument IS NOT NULL AND instruments.current_master_present<>1)""",
        (latest_capture,),
    ).fetchone()[0]
    if stale_current_flags:
        raise ValueError("existing capture core data has stale current master flags")
    stale_current_fundamentals = connection.execute(
        """SELECT COUNT(*)
             FROM current_fundamental_snapshot AS fundamental
             LEFT JOIN universe_snapshots AS current_universe
               ON current_universe.capture_date=?
              AND current_universe.instrument=fundamental.instrument
            WHERE current_universe.instrument IS NULL""",
        (latest_capture,),
    ).fetchone()[0]
    if stale_current_fundamentals:
        raise ValueError("existing capture core data has stale current fundamental snapshots")
    if latest_capture == capture_date:
        current_fundamental_rows = connection.execute(
            "SELECT COUNT(*) FROM current_fundamental_snapshot"
        ).fetchone()[0]
        if current_fundamental_rows != report["current_fundamental_rows"]:
            raise ValueError("existing capture core data has inconsistent current fundamentals")

    metadata_rows = {
        key: json.loads(value_json)
        for key, value_json in connection.execute(
            "SELECT key,value_json FROM metadata WHERE key IN ('schema_version','capture_date')"
        )
    }
    if metadata_rows.get("schema_version") != SCHEMA_VERSION or metadata_rows.get(
        "capture_date"
    ) != latest_capture:
        raise ValueError("existing capture core metadata does not describe the latest capture")


def _existing_capture(
    output: Path,
    capture_date: int,
    current_verification: dict[str, object],
) -> dict | None:
    if not output.is_file():
        return None
    try:
        with sqlite3.connect(f"{output.as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
            if quick_check != ["ok"]:
                detail = "; ".join(quick_check[:3])
                raise RuntimeError(f"existing staging SQLite quick_check failed: {detail}")
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "source_captures" not in tables:
                if user_version >= SCHEMA_VERSION:
                    raise ValueError("existing schema v2 is missing required table source_captures")
                return None
            if user_version != SCHEMA_VERSION:
                raise ValueError(
                    f"existing capture schema version is {user_version}, expected {SCHEMA_VERSION}"
                )
            missing_tables = sorted(set(_REQUIRED_SCHEMA_COLUMNS) - tables)
            if missing_tables:
                raise ValueError(
                    "existing schema v2 is missing required tables: " + ", ".join(missing_tables)
                )
            for table, required_columns in _REQUIRED_SCHEMA_COLUMNS.items():
                actual_columns = {
                    row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
                }
                missing_columns = sorted(required_columns - actual_columns)
                if missing_columns:
                    raise ValueError(
                        f"existing schema v2 table {table} is missing columns: "
                        + ", ".join(missing_columns)
                    )

            row = connection.execute(
                """SELECT manifest_sha256,source_root,source_report_json
                     FROM source_captures WHERE capture_date=?""",
                (capture_date,),
            ).fetchone()
            if row is None:
                return None
            manifest_sha256 = str(current_verification.get("manifest_sha256", ""))
            if row[0] != manifest_sha256:
                raise ValueError(
                    f"capture date {capture_date} already exists with a different manifest; "
                    "use a new immutable capture directory/date"
                )
            try:
                raw_report = json.loads(row[2])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("existing capture contract is not valid JSON") from exc
            report = _validate_capture_contract(
                raw_report,
                capture_date=capture_date,
                manifest_sha256=manifest_sha256,
                source_root=row[1],
                current_verification=current_verification,
            )
            _validate_existing_core(connection, capture_date=capture_date, report=report)
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"existing staging SQLite integrity validation failed: {exc}") from exc
    return {
        **report,
        "sqlite_quick_check": quick_check[0],
        "existing_capture_validation": "PASS",
    }


def _verify_source_unchanged(
    manifest_path: Path,
    initial_verification: dict[str, object],
) -> dict[str, object]:
    final_verification: dict[str, object] = verify_sha256_manifest(manifest_path)
    if _manifest_identity(final_verification) != _manifest_identity(initial_verification):
        raise ValueError(
            "TDX source changed during intake: pre-publish manifest/files/bytes differ "
            "from initial verification"
        )
    return final_verification


def _copy_database_for_atomic_update(output: Path, temporary: Path) -> sqlite3.Connection:
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        if output.is_file():
            with sqlite3.connect(f"{output.as_uri()}?mode=ro", uri=True) as source_connection:
                source_connection.backup(connection)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA temp_store=MEMORY")
        return connection
    except BaseException:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise


def _fsync_directory(path: Path) -> None:
    # Windows does not expose portable directory handles/fsync. os.replace is
    # still atomic there; POSIX additionally flushes the directory entry.
    if os.name == "nt":
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextmanager
def _exclusive_file_lock(handle: BinaryIO) -> Iterator[None]:
    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
        try:
            yield
        finally:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        return
    if _msvcrt is not None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        _msvcrt.locking(handle.fileno(), _msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
        return
    raise RuntimeError("atomic intake requires a POSIX fcntl or Windows msvcrt file lock")


def _atomic_replace(
    temporary: Path,
    output: Path,
    *,
    previous_mode: int | None,
    previous_owner: tuple[int, int] | None,
) -> None:
    if previous_mode is not None:
        os.chmod(temporary, previous_mode)
    if previous_owner is not None:
        current = temporary.stat()
        if (current.st_uid, current.st_gid) != previous_owner:
            chown = getattr(os, "chown", None)
            if chown is not None:
                chown(temporary, *previous_owner)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, output)
    _fsync_directory(output.parent)


def _sync_tdx_intake_locked(
    source: Path,
    output: Path,
    *,
    verify_hashes: bool,
    capture_date: int | None,
    allow_existing: bool,
) -> dict:
    source = source.resolve()
    output = output.resolve()
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("output database must be outside the immutable source directory")
    if output.exists() and not allow_existing:
        raise FileExistsError(f"refusing to overwrite staging database: {output}")
    capture = infer_capture_date(source, explicit=capture_date)
    manifest_path = source / "SHA256SUMS"
    hq = _hq_cache_path(source)
    base_path = hq / "base.dbf"
    if not manifest_path.is_file() or not base_path.is_file():
        raise FileNotFoundError("TDX intake is missing SHA256SUMS or hq_cache/base.dbf")
    verification = (
        verify_sha256_manifest(manifest_path)
        if verify_hashes
        else {
            "status": "NOT_RERUN",
            "files": len(parse_sha256_manifest(manifest_path)),
            "bytes": None,
            "manifest_sha256": _sha256(manifest_path),
        }
    )
    manifest_sha256 = str(verification["manifest_sha256"])
    existing_report = _existing_capture(output, capture, verification) if allow_existing else None
    if existing_report is not None:
        return {
            **existing_report,
            "capture_status": "ALREADY_INGESTED",
            "rows_inserted": 0,
            "output": str(output),
            "output_bytes": output.stat().st_size,
            "output_sha256": _sha256(output),
        }

    files = _stock_day_files(source)
    if not files:
        raise ValueError("no stock .day files found")
    daily_markets = {market for market, _, _ in files}
    missing_tnf = [
        _TNF_FILENAMES[market]
        for market in sorted(daily_markets)
        if not (hq / _TNF_FILENAMES[market]).is_file()
    ]
    if missing_tnf:
        raise ValueError(
            "authoritative TNF current security master is missing for daily-bar markets: "
            + ", ".join(missing_tnf)
        )

    master_rows = read_dbf(base_path)
    market_by_sc = {"0": "sz", "1": "sh", "2": "bj"}
    current_master: dict[tuple[str, str], dict[str, str]] = {}
    for row in master_rows:
        if row.get("SC") not in market_by_sc:
            continue
        key = (market_by_sc[row["SC"]], row.get("GPDM", ""))
        previous = current_master.setdefault(key, row)
        if previous != row:
            raise ValueError(f"conflicting base.dbf rows for {key[0]}:{key[1]}")
    security_master = read_security_master(source)
    if not security_master:
        raise ValueError("authoritative TNF current security master is empty")
    security_markets = {str(row["market"]) for row in security_master.values()}
    uncovered_markets = sorted(daily_markets - security_markets)
    if uncovered_markets:
        raise ValueError(
            "authoritative TNF current security master has no stocks for daily-bar markets: "
            + ", ".join(uncovered_markets)
        )
    current_universe = set(security_master)
    industries = read_industry_snapshots(source)
    businesses = read_current_business_snapshots(source)
    gbbq_path = hq / "gbbq"
    gbbq = inspect_gbbq(gbbq_path) if gbbq_path.is_file() else None
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    previous_stat = output.stat() if output.exists() else None
    previous_mode = previous_stat.st_mode & 0o777 if previous_stat is not None else None
    previous_owner = (previous_stat.st_uid, previous_stat.st_gid) if previous_stat is not None else None
    connection = _copy_database_for_atomic_update(output, temporary)
    try:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        latest_capture_row = connection.execute(
            "SELECT MAX(capture_date) FROM source_captures"
        ).fetchone()
        latest_capture = latest_capture_row[0] if latest_capture_row else None
        if latest_capture is not None and capture <= int(latest_capture):
            # Exact same-capture/same-manifest replays returned before the
            # atomic-copy transaction.  Everything else must advance the
            # capture clock monotonically: accepting an older snapshot would
            # overwrite current_* tables and metadata with stale authority.
            raise ValueError(
                f"out-of-order capture {capture} does not advance latest capture "
                f"{latest_capture}"
            )
        source_rows = 0
        inserted_rows = 0
        first_date = 99999999
        last_date = 0
        daily_by_market: dict[str, int] = {"sh": 0, "sz": 0, "bj": 0}
        daily_instruments: set[str] = set()
        prior_capture = int(latest_capture) if latest_capture is not None else None
        prior_universe = (
            {
                row[0] for row in connection.execute(
                    "SELECT instrument FROM universe_snapshots WHERE capture_date=?", (prior_capture,)
                )
            }
            if prior_capture is not None else set()
        )
        # These two objects describe only the newest capture. Rebuild them in
        # the same transaction so a symbol removed from the authoritative TNF
        # master cannot retain a stale current flag or fundamental payload.
        connection.execute("UPDATE instruments SET current_master_present=0")
        connection.execute("DELETE FROM current_fundamental_snapshot")
        for market, code, path in files:
            key = instrument_key(market, code)
            decoded = list(iter_day_rows(path, capture_date=capture))
            if not decoded:
                continue
            daily_instruments.add(key)
            master = current_master.get((market, code))
            listing_raw = (master or {}).get("SSDATE", "")
            listing_date = _optional_date(listing_raw)
            existing = {
                row[0]: row
                for row in connection.execute(
                    """SELECT trade_date,knowledge_time,open,high,low,close,amount,volume,reserved
                       FROM daily_prices WHERE instrument=?""",
                    (key,),
                )
            }
            new_rows: list[tuple[object, ...]] = []
            for row in decoded:
                expected = (row[0], row[0], *row[1:])
                previous = existing.get(row[0])
                if previous is not None and previous != expected:
                    raise ValueError(f"historical bar conflict for {key} on {row[0]}")
                if previous is None:
                    new_rows.append((key, *expected))
            connection.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?)", new_rows)
            inserted_rows += len(new_rows)
            all_dates = set(existing) | {row[0] for row in decoded}
            connection.execute(
                """INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(instrument) DO UPDATE SET
                     listing_date=COALESCE(excluded.listing_date,instruments.listing_date),
                     first_date=MIN(instruments.first_date,excluded.first_date),
                     last_date=MAX(instruments.last_date,excluded.last_date),
                     bar_rows=excluded.bar_rows,
                     current_master_present=excluded.current_master_present,
                     source_path=excluded.source_path""",
                (
                    key,
                    market,
                    code,
                    listing_date,
                    min(all_dates),
                    max(all_dates),
                    len(all_dates),
                    int(key in current_universe),
                    str(path.relative_to(source)),
                ),
            )
            source_rows += len(decoded)
            first_date = min(first_date, decoded[0][0])
            last_date = max(last_date, decoded[-1][0])
            daily_by_market[market] += 1

        if not daily_instruments:
            raise ValueError("no non-empty stock .day files found")

        current_by_market: dict[str, int] = {"sh": 0, "sz": 0, "bj": 0}
        for key in sorted(current_universe):
            security = security_master[key]
            market = str(security["market"])
            code = str(security["code"])
            master = current_master.get((market, code))
            listing_raw = (master or {}).get("SSDATE", "")
            listing_date = _optional_date(listing_raw)
            connection.execute(
                "INSERT INTO universe_snapshots VALUES (?,?,?,?,?)",
                (capture, key, market, code, CURRENT_ONLY),
            )
            connection.execute(
                "INSERT INTO security_master_snapshots VALUES (?,?,?,?,?,?,?)",
                (
                    capture,
                    key,
                    market,
                    code,
                    security.get("name"),
                    int(bool(security["is_st"])),
                    CURRENT_ONLY,
                ),
            )
            connection.execute(
                """UPDATE instruments
                      SET current_master_present=1,
                          listing_date=COALESCE(?,listing_date)
                    WHERE instrument=?""",
                (listing_date, key),
            )
            if master is not None:
                snapshot_raw = master.get("GXRQ", "")
                vendor_update_date = _optional_date(snapshot_raw)
                payload_json = json.dumps(master, ensure_ascii=False, sort_keys=True)
                connection.execute(
                    "INSERT INTO current_fundamental_snapshot VALUES (?,?,?,?,?)",
                    (
                        key,
                        vendor_update_date,
                        listing_date,
                        payload_json,
                        "CURRENT_SNAPSHOT_ONLY_NOT_PIT",
                    ),
                )
                connection.execute(
                    "INSERT INTO fundamental_snapshots VALUES (?,?,?,?,?,?)",
                    (capture, key, vendor_update_date, listing_date, payload_json, CURRENT_ONLY),
                )
            industry = industries.get(key)
            if industry is not None:
                connection.execute(
                    "INSERT INTO industry_snapshots VALUES (?,?,?,?,?,?,?,?)",
                    (
                        capture,
                        key,
                        industry["industry_code"],
                        industry["industry_name"],
                        industry["secondary_industry_code"],
                        industry["secondary_industry_name"],
                        json.dumps(industry["payload"], ensure_ascii=False),
                        CURRENT_ONLY,
                    ),
                )
            business = businesses.get(key)
            if business is not None:
                connection.execute(
                    "INSERT INTO current_business_snapshots VALUES (?,?,?,?)",
                    (capture, key, json.dumps(business, ensure_ascii=False), CURRENT_ONLY),
                )
            current_by_market[market] += 1

        if gbbq is not None:
            connection.execute(
                "INSERT INTO opaque_source_snapshots VALUES (?,?,?,?,?,?,?)",
                (
                    capture,
                    "gbbq",
                    gbbq["sha256"],
                    gbbq["bytes"],
                    gbbq["record_count"],
                    gbbq["status"],
                    "TongDaXin client container is encrypted; no event or knowledge dates admitted",
                ),
            )
        total_database_rows = connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        current_names = sum(1 for key in current_universe if security_master[key].get("name"))
        current_st = sum(1 for key in current_universe if bool(security_master[key]["is_st"]))
        industry_rows = sum(1 for key in current_universe if key in industries)
        fundamental_rows = sum(
            1 for key in current_universe
            if (str(security_master[key]["market"]), str(security_master[key]["code"]))
            in current_master
        )
        business_rows = sum(1 for key in current_universe if key in businesses)
        absent_master = len(current_universe) - fundamental_rows
        daily_only_instruments = daily_instruments - current_universe
        current_without_daily = current_universe - daily_instruments
        added_instruments = sorted(current_universe - prior_universe) if prior_capture else []
        absent_since_prior = sorted(prior_universe - current_universe) if prior_capture else []
        source_contract = {
            "schema_version": SCHEMA_VERSION,
            "capture_contract_version": 2,
            "evidence_status": TECHNICAL_STATUS,
            "source_kind": "TONGDAXIN_LOCAL_DAILY_AND_CURRENT_METADATA",
            "source_root": str(source),
            "capture_date": capture,
            "capture_status": "INGESTED",
            "sha256_verification": verification,
            "daily_price_semantics": "RAW_UNADJUSTED_CLOSE_KNOWN_AFTER_CLOSE_T",
            "daily_coverage_semantics": "HISTORICAL_BAR_FILES_NOT_CURRENT_MEMBERSHIP_AUTHORITY",
            "instruments_semantics": "CURRENT_TNF_UNIVERSE_COUNT",
            "instrument_current_master_present_semantics": (
                "TNF_CURRENT_UNIVERSE_MEMBERSHIP_FOR_INSTRUMENTS_WITH_DAILY_HISTORY"
            ),
            "current_universe_authority": "TONGDAXIN_TNF_CURRENT_CAPTURE",
            "current_universe_authority_markets": sorted(security_markets),
            "current_universe_missing_authority_markets": sorted(
                set(_TNF_FILENAMES) - security_markets
            ),
            "current_universe_scope": (
                "ALL_SUPPORTED_STOCK_MARKETS"
                if security_markets == set(_TNF_FILENAMES)
                else "PARTIAL_MARKETS_EXPLICIT"
            ),
            "security_master_semantics": CURRENT_ONLY,
            "st_semantics": CURRENT_ONLY,
            "fundamental_semantics": CURRENT_ONLY,
            "historical_universe_semantics": "CAPTURE_SNAPSHOTS_ONLY_FROM_FIRST_INGEST_FORWARD",
            "industry_semantics": CURRENT_ONLY,
            "event_semantics": (
                OPAQUE_ACTION_STATUS if gbbq is not None else "CORPORATE_ACTION_SOURCE_MISSING"
            ),
            "capital_flow_semantics": "DAILY_AMOUNT_ONLY_NO_ORDER_SIZE_OR_INVESTOR_CLASS",
            "instruments": len(current_universe),
            "instruments_by_market": current_by_market,
            "daily_instruments": len(daily_instruments),
            "daily_instruments_by_market": daily_by_market,
            "daily_only_instruments": len(daily_only_instruments),
            "current_universe_without_daily": len(current_without_daily),
            "rows": source_rows,
            "rows_inserted": inserted_rows,
            "rows_total": total_database_rows,
            "first_date": first_date,
            "last_date": last_date,
            "current_master_absent": absent_master,
            "current_master_absent_semantics": "TNF_CURRENT_UNIVERSE_WITHOUT_BASE_DBF_ROW",
            "current_security_names": current_names,
            "current_st_instruments": current_st,
            "current_fundamental_rows": fundamental_rows,
            "current_industry_rows": industry_rows,
            "current_business_rows": business_rows,
            "corporate_action_container": gbbq,
            "prior_capture_date": prior_capture,
            "added_since_prior_capture": len(added_instruments),
            "absent_since_prior_capture": len(absent_since_prior),
            "absence_semantics": "NOT_A_DELISTING_ASSERTION_REQUIRES_INDEPENDENT_CONFIRMATION",
            "promotion_allowed": False,
            "paper_persistence_allowed": False,
        }
        if verify_hashes:
            source_contract["pre_publish_sha256_verification"] = _verify_source_unchanged(
                manifest_path,
                verification,
            )
        _metadata_rows(connection, source_contract)
        connection.execute(
            "INSERT INTO source_captures VALUES (?,?,?,?)",
            (
                capture,
                manifest_sha256,
                str(source),
                json.dumps(source_contract, ensure_ascii=False, sort_keys=True),
            ),
        )
        connection.commit()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"staging SQLite quick_check failed: {quick_check}")
    except BaseException:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise
    else:
        connection.close()
    _atomic_replace(
        temporary,
        output,
        previous_mode=previous_mode,
        previous_owner=previous_owner,
    )
    report = {
        **source_contract,
        "sqlite_quick_check": "ok",
        "output": str(output),
        "output_bytes": output.stat().st_size,
        "output_sha256": _sha256(output),
    }
    return report


def _sync_tdx_intake(
    source: Path,
    output: Path,
    *,
    verify_hashes: bool,
    capture_date: int | None,
    allow_existing: bool,
) -> dict:
    resolved_source = source.expanduser().resolve()
    resolved_output = output.expanduser().resolve()
    try:
        resolved_output.relative_to(resolved_source)
    except ValueError:
        pass
    else:
        raise ValueError("output database must be outside the immutable source directory")
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = resolved_output.with_name(f".{resolved_output.name}.intake.lock")
    with lock_path.open("a+b") as lock_handle:
        with _exclusive_file_lock(lock_handle):
            return _sync_tdx_intake_locked(
                resolved_source,
                resolved_output,
                verify_hashes=verify_hashes,
                capture_date=capture_date,
                allow_existing=allow_existing,
            )


def _write_json_atomic(path: Path, value: object) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def normalize_tdx_intake(
    source: Path,
    output: Path,
    *,
    verify_hashes: bool = True,
    capture_date: int | None = None,
) -> dict:
    return _sync_tdx_intake(
        source,
        output,
        verify_hashes=verify_hashes,
        capture_date=capture_date,
        allow_existing=False,
    )


def sync_tdx_intake(
    source: Path,
    output: Path,
    *,
    verify_hashes: bool = True,
    capture_date: int | None = None,
) -> dict:
    """Atomically append a new immutable capture or return an idempotent replay."""
    return _sync_tdx_intake(
        source,
        output,
        verify_hashes=verify_hashes,
        capture_date=capture_date,
        allow_existing=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--capture-date", type=int)
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--skip-hash-verification", action="store_true")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--discover-root", action="append", default=[], type=Path)
    parser.add_argument("--max-discovery-depth", type=int, default=12)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.discover_only:
        roots = args.discover_root or [
            Path("/home/kerstker/quantos-data-intake"),
            Path("/mnt"),
            Path("/media"),
            Path("/run/media"),
        ]
        candidates = discover_tdx_sources(roots, max_depth=args.max_discovery_depth)
        report = {
            "mode": "ASHARE_TDX_LOCAL_DISCOVERY",
            "roots": [str(root) for root in roots],
            "candidates": candidates,
            "candidate_count": len(candidates),
        }
        if args.report:
            _write_json_atomic(args.report, report)
        print(f"ASHARE_TDX_DISCOVERY_PASS candidates={len(candidates)}")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    if args.source is None or args.output is None or args.report is None:
        raise SystemExit("--source, --output and --report are required unless --discover-only is used")
    intake = sync_tdx_intake if args.incremental else normalize_tdx_intake
    report = intake(
        args.source,
        args.output,
        verify_hashes=not args.skip_hash_verification,
        capture_date=args.capture_date,
    )
    _write_json_atomic(args.report, report)
    print(
        "ASHARE_TDX_INTAKE_PASS "
        f"capture={report['capture_date']} capture_status={report['capture_status']} "
        f"instruments={report['instruments']} source_rows={report['rows']} "
        f"inserted={report['rows_inserted']} total={report['rows_total']} "
        f"first={report['first_date']} last={report['last_date']} "
        f"status={report['evidence_status']}"
    )
    print(f"database={args.output}")
    print(f"report={args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
