#!/usr/bin/env python3
"""Normalize a quarantined TongDaXin daily-bar intake into compact SQLite.

The source directory is never modified.  Daily ``.day`` records are decoded
from the documented 32-byte little-endian layout and written to a staging
database.  ``base.dbf`` is retained as a *current snapshot* only: it must not be
joined backwards as point-in-time fundamentals.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import struct
from pathlib import Path
from typing import Iterable, Iterator, Sequence


DAY_RECORD = struct.Struct("<IIIIIfII")
CAPTURE_DATE = 20260830
SCHEMA_VERSION = 1
TECHNICAL_STATUS = "TECHNICAL_VALIDATION_ONLY"


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
        candidate = (root / relative).resolve()
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


def iter_day_rows(path: Path) -> Iterator[tuple[int, float, float, float, float, float, int, int]]:
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
            if date > CAPTURE_DATE:
                raise ValueError(f"future trading date {date} in {path}")
            if high_i < max(open_i, close_i, low_i) or low_i > min(open_i, close_i, high_i):
                raise ValueError(f"invalid OHLC relationship on {date} in {path}")
            if amount < 0 or volume < 0:
                raise ValueError(f"negative amount/volume on {date} in {path}")
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
        "INSERT INTO metadata(key,value_json) VALUES (?,?)",
        ((key, json.dumps(value, ensure_ascii=False, sort_keys=True)) for key, value in values.items()),
    )


def normalize_tdx_intake(source: Path, output: Path, *, verify_hashes: bool = True) -> dict:
    source = source.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite staging database: {output}")
    manifest_path = source / "SHA256SUMS"
    base_path = source / "metadata" / "hq_cache" / "base.dbf"
    if not manifest_path.is_file() or not base_path.is_file():
        raise FileNotFoundError("TDX intake is missing SHA256SUMS or metadata/hq_cache/base.dbf")
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
    master_rows = read_dbf(base_path)
    market_by_sc = {"0": "sz", "1": "sh", "2": "bj"}
    current_master = {
        (market_by_sc[row["SC"]], row["GPDM"]): row
        for row in master_rows
        if row.get("SC") in market_by_sc
    }
    files = _stock_day_files(source)
    if not files:
        raise ValueError("no stock .day files found")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA temp_store=MEMORY;
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
            CREATE TABLE instruments(
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
            CREATE TABLE daily_prices(
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
            CREATE TABLE current_fundamental_snapshot(
                instrument TEXT PRIMARY KEY,
                snapshot_date INTEGER,
                listing_date INTEGER,
                payload_json TEXT NOT NULL,
                authority_mode TEXT NOT NULL CHECK(authority_mode='CURRENT_SNAPSHOT_ONLY_NOT_PIT')
            );
            """
        )
        total_rows = 0
        first_date = 99999999
        last_date = 0
        absent_master = 0
        by_market: dict[str, int] = {"sh": 0, "sz": 0, "bj": 0}
        for market, code, path in files:
            key = instrument_key(market, code)
            decoded = list(iter_day_rows(path))
            if not decoded:
                continue
            master = current_master.get((market, code))
            if master is None:
                absent_master += 1
            listing_raw = (master or {}).get("SSDATE", "")
            listing_date = int(listing_raw) if listing_raw.isdigit() else None
            connection.executemany(
                "INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?)",
                ((key, row[0], row[0], *row[1:]) for row in decoded),
            )
            connection.execute(
                "INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    market,
                    code,
                    listing_date,
                    decoded[0][0],
                    decoded[-1][0],
                    len(decoded),
                    int(master is not None),
                    str(path.relative_to(source)),
                ),
            )
            if master is not None:
                snapshot_raw = master.get("GXRQ", "")
                snapshot_date = int(snapshot_raw) if snapshot_raw.isdigit() else None
                connection.execute(
                    "INSERT INTO current_fundamental_snapshot VALUES (?,?,?,?,?)",
                    (
                        key,
                        snapshot_date,
                        listing_date,
                        json.dumps(master, ensure_ascii=False, sort_keys=True),
                        "CURRENT_SNAPSHOT_ONLY_NOT_PIT",
                    ),
                )
            total_rows += len(decoded)
            first_date = min(first_date, decoded[0][0])
            last_date = max(last_date, decoded[-1][0])
            by_market[market] += 1

        source_contract = {
            "schema_version": SCHEMA_VERSION,
            "evidence_status": TECHNICAL_STATUS,
            "source_kind": "TONGDAXIN_LOCAL_DAILY_AND_CURRENT_METADATA",
            "source_root": str(source),
            "sha256_verification": verification,
            "daily_price_semantics": "RAW_UNADJUSTED_CLOSE_KNOWN_AFTER_CLOSE_T",
            "fundamental_semantics": "CURRENT_SNAPSHOT_ONLY_NOT_HISTORICAL_PIT",
            "historical_universe_semantics": "CURRENT_MASTER_ONLY_SURVIVOR_BIAS_PRESENT",
            "industry_semantics": "CURRENT_CONFIG_WITHOUT_EFFECTIVE_DATE_HISTORY",
            "event_semantics": "CORPORATE_ACTION_FILE_PRESENT_BUT_KNOWLEDGE_TIME_NOT_ADMITTED",
            "capital_flow_semantics": "DAILY_AMOUNT_ONLY_NO_ORDER_SIZE_OR_INVESTOR_CLASS",
            "instruments": sum(by_market.values()),
            "instruments_by_market": by_market,
            "rows": total_rows,
            "first_date": first_date,
            "last_date": last_date,
            "current_master_absent": absent_master,
            "promotion_allowed": False,
            "paper_persistence_allowed": False,
        }
        _metadata_rows(connection, source_contract)
        connection.execute("CREATE INDEX daily_prices_date_idx ON daily_prices(trade_date,instrument)")
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
    os.replace(temporary, output)
    report = {
        **source_contract,
        "sqlite_quick_check": "ok",
        "output": str(output),
        "output_bytes": output.stat().st_size,
        "output_sha256": _sha256(output),
    }
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--skip-hash-verification", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = normalize_tdx_intake(
        args.source,
        args.output,
        verify_hashes=not args.skip_hash_verification,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "ASHARE_TDX_INTAKE_PASS "
        f"instruments={report['instruments']} rows={report['rows']} "
        f"first={report['first_date']} last={report['last_date']} "
        f"status={report['evidence_status']}"
    )
    print(f"database={args.output}")
    print(f"report={args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
