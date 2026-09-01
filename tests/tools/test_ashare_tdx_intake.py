from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from pathlib import Path

import pytest

from tools import ashare_tdx_intake as intake_module
from tools.ashare_tdx_intake import (
    DAY_RECORD,
    discover_tdx_sources,
    infer_capture_date,
    inspect_gbbq,
    is_stock_code,
    iter_day_rows,
    parse_sha256_manifest,
    read_tnf,
    sync_tdx_intake,
    verify_sha256_manifest,
)


def test_stock_code_allowlist_excludes_indices_and_funds():
    assert is_stock_code("sh", "600000")
    assert is_stock_code("sh", "688001")
    assert is_stock_code("sz", "000001")
    assert is_stock_code("sz", "301001")
    assert is_stock_code("bj", "920001")
    assert not is_stock_code("sh", "000001")
    assert not is_stock_code("sh", "510300")
    assert not is_stock_code("sz", "399001")
    assert not is_stock_code("bj", "920000")


def test_day_decoder_uses_raw_amount_and_cent_prices(tmp_path: Path):
    path = tmp_path / "sh600000.day"
    path.write_bytes(
        DAY_RECORD.pack(20260827, 1001, 1100, 990, 1050, 123456.5, 7654, 1)
        + DAY_RECORD.pack(20260828, 1050, 1075, 1000, 1010, 234567.0, 8765, 0)
    )

    rows = list(iter_day_rows(path))

    assert rows[0] == (20260827, 10.01, 11.0, 9.9, 10.5, 123456.5, 7654, 1)
    assert rows[1][0] == 20260828


def test_day_decoder_rejects_partial_and_non_increasing_records(tmp_path: Path):
    partial = tmp_path / "partial.day"
    partial.write_bytes(b"x")
    with pytest.raises(ValueError, match="partial"):
        list(iter_day_rows(partial))

    duplicate = tmp_path / "duplicate.day"
    row = DAY_RECORD.pack(20260828, 1000, 1100, 900, 1000, 1.0, 1, 0)
    duplicate.write_bytes(row + row)
    with pytest.raises(ValueError, match="non-increasing"):
        list(iter_day_rows(duplicate))

    nonfinite = tmp_path / "nonfinite.day"
    nonfinite.write_bytes(DAY_RECORD.pack(20260828, 1000, 1000, 1000, 1000, float("nan"), 1, 0))
    with pytest.raises(ValueError, match="invalid amount"):
        list(iter_day_rows(nonfinite))


def test_sha_manifest_rejects_parent_escape(tmp_path: Path):
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{'0' * 64}  ../escape\n", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        parse_sha256_manifest(manifest)


def test_manifest_requires_complete_source_inventory(tmp_path: Path):
    admitted = tmp_path / "admitted.txt"
    admitted.write_text("ok", encoding="utf-8")
    digest = hashlib.sha256(admitted.read_bytes()).hexdigest()
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{digest}  admitted.txt\n", encoding="utf-8")

    assert verify_sha256_manifest(manifest)["files"] == 1

    (tmp_path / "not-in-manifest.txt").write_text("untracked", encoding="utf-8")
    with pytest.raises(ValueError, match="not covered by SHA256SUMS"):
        verify_sha256_manifest(manifest)


def test_capture_date_is_explicit_or_derived_from_snapshot_directory(tmp_path: Path):
    assert infer_capture_date(tmp_path / "tdx-windows-20260830") == 20260830
    assert infer_capture_date(tmp_path / "anything", explicit=20260901) == 20260901
    with pytest.raises(ValueError, match="capture date"):
        infer_capture_date(tmp_path / "undated")


def _write_tnf_rows(path: Path, rows: list[tuple[str, str]]) -> None:
    payload = bytearray(b"x" * 50)
    for code, name in rows:
        record = bytearray(360)
        record[:6] = code.encode("ascii")
        encoded = name.encode("gb18030")
        record[31 : 31 + len(encoded)] = encoded
        payload.extend(record)
    path.write_bytes(bytes(payload))


def _write_tnf(path: Path, code: str, name: str) -> None:
    _write_tnf_rows(path, [(code, name)])


def test_current_tnf_decoder_and_gbbq_structural_audit(tmp_path: Path):
    tnf = tmp_path / "shs.tnf"
    _write_tnf(tnf, "600000", "*ST测试")
    rows = read_tnf(tnf, "sh")
    assert rows == {
        "600000.SH": {
            "code": "600000",
            "instrument": "600000.SH",
            "market": "sh",
            "name": "*ST测试",
            "is_st": True,
        }
    }

    gbbq = tmp_path / "gbbq"
    gbbq.write_bytes(struct.pack("<I", 2) + b"a" * 58)
    assert inspect_gbbq(gbbq) == {
        "status": "OPAQUE_ENCRYPTED_RECORDS_NOT_ADMITTED",
        "record_count": 2,
        "record_bytes": 29,
        "bytes": 62,
        "sha256": hashlib.sha256(gbbq.read_bytes()).hexdigest(),
    }
    gbbq.write_bytes(struct.pack("<I", 2) + b"a")
    with pytest.raises(ValueError, match="record count"):
        inspect_gbbq(gbbq)


def test_discovery_finds_vipdoc_source_without_following_unrelated_tree(tmp_path: Path):
    source = tmp_path / "client" / "tdx-windows-20260901"
    day = source / "vipdoc" / "sh" / "lday" / "sh600000.day"
    day.parent.mkdir(parents=True)
    day.write_bytes(DAY_RECORD.pack(20260901, 1000, 1000, 1000, 1000, 1.0, 1, 0))
    metadata = source / "metadata" / "hq_cache"
    metadata.mkdir(parents=True)
    (metadata / "base.dbf").write_bytes(b"placeholder")

    rows = discover_tdx_sources([tmp_path])

    assert len(rows) == 1
    assert rows[0]["source_root"] == str(source.resolve())
    assert rows[0]["day_files"] == 1
    assert rows[0]["markets"] == {"bj": 0, "sh": 1, "sz": 0}
    assert rows[0]["has_base_dbf"] is True


def _write_dbf(path: Path, rows: list[dict[str, str]]) -> None:
    fields = [("SC", 1), ("GPDM", 6), ("GXRQ", 8), ("SSDATE", 8)]
    header_bytes = 32 + 32 * len(fields) + 1
    row_bytes = 1 + sum(width for _, width in fields)
    header = bytearray(header_bytes)
    header[0] = 0x03
    struct.pack_into("<I", header, 4, len(rows))
    struct.pack_into("<H", header, 8, header_bytes)
    struct.pack_into("<H", header, 10, row_bytes)
    for index, (name, width) in enumerate(fields):
        offset = 32 + index * 32
        header[offset : offset + len(name)] = name.encode("ascii")
        header[offset + 11] = ord("C")
        header[offset + 16] = width
    header[-1] = 0x0D
    body = bytearray()
    for row in rows:
        body.extend(b" ")
        for name, width in fields:
            body.extend(row.get(name, "").encode("ascii").ljust(width)[:width])
    path.write_bytes(bytes(header + body) + b"\x1a")


def _rewrite_manifest(source: Path) -> None:
    manifest_rows = []
    for candidate in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = candidate.relative_to(source).as_posix()
        if relative == "SHA256SUMS":
            continue
        manifest_rows.append(f"{hashlib.sha256(candidate.read_bytes()).hexdigest()}  {relative}")
    (source / "SHA256SUMS").write_text("\n".join(manifest_rows) + "\n", encoding="utf-8")


def _source_snapshot(tmp_path: Path, capture: int, day_rows: list[bytes], *, close_name: str) -> Path:
    source = tmp_path / f"tdx-windows-{capture}"
    day = source / "vipdoc" / "sh" / "lday" / "sh600000.day"
    day.parent.mkdir(parents=True)
    day.write_bytes(b"".join(day_rows))
    hq = source / "metadata" / "hq_cache"
    hq.mkdir(parents=True)
    _write_dbf(
        hq / "base.dbf",
        [{"SC": "1", "GPDM": "600000", "GXRQ": str(capture), "SSDATE": "19991110"}],
    )
    _write_tnf(hq / "shs.tnf", "600000", close_name)
    (hq / "tdxhy.cfg").write_text("1|600000|T1001|||X500102\n", encoding="gb18030")
    (hq / "tdxzs3.cfg").write_text(
        "银行|880001|1|1|1|T1001\n股份制银行|880002|1|1|1|X500102\n",
        encoding="gb18030",
    )
    (hq / "specgpext.txt").write_text(
        "1|600000|银行业务|1|2|3|4|5|6|7|\n", encoding="gb18030"
    )
    (hq / "gbbq").write_bytes(struct.pack("<I", 1) + b"z" * 29)
    inventory = source / "SOURCE_INVENTORY.txt"
    inventory.write_text("fixture\n", encoding="utf-8")
    _rewrite_manifest(source)
    return source


def _day(date: int, close: int = 1000) -> bytes:
    return DAY_RECORD.pack(date, close, close, close, close, 100.0, 10, 0)


def test_incremental_sync_is_idempotent_append_only_and_preserves_capture_snapshots(tmp_path: Path):
    first = _source_snapshot(tmp_path, 20260830, [_day(20260828)], close_name="浦发银行")
    output = tmp_path / "ashare_tdx_pit.sqlite"

    report1 = sync_tdx_intake(first, output)
    report1_repeat = sync_tdx_intake(first, output)
    second = _source_snapshot(
        tmp_path,
        20260901,
        [_day(20260828), _day(20260901, 1010)],
        close_name="*ST浦发",
    )
    report2 = sync_tdx_intake(second, output)

    assert report1["rows_inserted"] == 1
    assert report1_repeat["rows_inserted"] == 0
    assert report1_repeat["capture_status"] == "ALREADY_INGESTED"
    assert report1_repeat["sqlite_quick_check"] == "ok"
    assert report1_repeat["existing_capture_validation"] == "PASS"
    assert report1["pre_publish_sha256_verification"] == report1["sha256_verification"]
    assert report2["rows_inserted"] == 1
    assert report2["rows_total"] == 2
    with sqlite3.connect(output) as connection:
        assert connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM source_captures").fetchone()[0] == 2
        assert connection.execute(
            "SELECT capture_date,name,is_st FROM security_master_snapshots ORDER BY capture_date"
        ).fetchall() == [(20260830, "浦发银行", 0), (20260901, "*ST浦发", 1)]
        assert connection.execute(
            "SELECT industry_name,secondary_industry_name FROM industry_snapshots "
            "WHERE capture_date=20260901"
        ).fetchone() == ("银行", "股份制银行")
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM current_business_snapshots WHERE capture_date=20260901"
            ).fetchone()[0]
        )
        assert payload[2] == "银行业务"


def test_incremental_sync_uses_tnf_universe_and_clears_removed_current_state(tmp_path: Path):
    first = _source_snapshot(tmp_path, 20260830, [_day(20260828)], close_name="浦发银行")
    first_hq = first / "metadata" / "hq_cache"
    second_day = first / "vipdoc" / "sh" / "lday" / "sh600001.day"
    second_day.write_bytes(_day(20260828, 2000))
    (first / "vipdoc" / "sh" / "lday" / "sh600002.day").write_bytes(
        _day(20260828, 3000)
    )
    _write_tnf_rows(
        first_hq / "shs.tnf",
        [("600000", "浦发银行"), ("600001", "邯郸钢铁"), ("600002", "齐鲁石化")],
    )
    _write_dbf(
        first_hq / "base.dbf",
        [
            {"SC": "1", "GPDM": "600000", "GXRQ": "20260830", "SSDATE": "19991110"},
            {"SC": "1", "GPDM": "600001", "GXRQ": "20260830", "SSDATE": "19980122"},
            {"SC": "1", "GPDM": "600002", "GXRQ": "20260830", "SSDATE": "19980408"},
        ],
    )
    _rewrite_manifest(first)
    output = tmp_path / "ashare_tdx_pit.sqlite"
    first_report = sync_tdx_intake(first, output)

    second = _source_snapshot(
        tmp_path,
        20260901,
        [_day(20260828), _day(20260901, 1010)],
        close_name="浦发银行",
    )
    second_hq = second / "metadata" / "hq_cache"
    # 600001 keeps an old .day file and residual base row; 600002 keeps only
    # historical rows in the database. The authoritative TNF removes both,
    # exercising both the daily-loop update and transaction-wide flag reset.
    (second / "vipdoc" / "sh" / "lday" / "sh600001.day").write_bytes(
        _day(20260828, 2000)
    )
    _write_dbf(
        second_hq / "base.dbf",
        [
            {"SC": "1", "GPDM": "600000", "GXRQ": "20260901", "SSDATE": "19991110"},
            {"SC": "1", "GPDM": "600001", "GXRQ": "20260830", "SSDATE": "19980122"},
            {"SC": "1", "GPDM": "600002", "GXRQ": "20260830", "SSDATE": "19980408"},
        ],
    )
    _rewrite_manifest(second)
    second_report = sync_tdx_intake(second, output)

    assert first_report["instruments"] == 3
    assert first_report["daily_instruments"] == 3
    assert first_report["current_fundamental_rows"] == 3
    assert second_report["current_universe_authority"] == "TONGDAXIN_TNF_CURRENT_CAPTURE"
    assert second_report["current_universe_authority_markets"] == ["sh"]
    assert second_report["current_universe_missing_authority_markets"] == ["bj", "sz"]
    assert second_report["current_universe_scope"] == "PARTIAL_MARKETS_EXPLICIT"
    assert second_report["instruments"] == 1
    assert second_report["daily_instruments"] == 2
    assert second_report["daily_only_instruments"] == 1
    assert second_report["current_universe_without_daily"] == 0
    assert second_report["absent_since_prior_capture"] == 2
    assert second_report["current_fundamental_rows"] == 1
    with sqlite3.connect(output) as connection:
        assert connection.execute(
            "SELECT instrument,current_master_present FROM instruments ORDER BY instrument"
        ).fetchall() == [("600000.SH", 1), ("600001.SH", 0), ("600002.SH", 0)]
        assert connection.execute(
            "SELECT instrument FROM current_fundamental_snapshot ORDER BY instrument"
        ).fetchall() == [("600000.SH",)]
        assert connection.execute(
            "SELECT capture_date,instrument FROM universe_snapshots ORDER BY capture_date,instrument"
        ).fetchall() == [
            (20260830, "600000.SH"),
            (20260830, "600001.SH"),
            (20260830, "600002.SH"),
            (20260901, "600000.SH"),
        ]
        assert connection.execute(
            "SELECT capture_date,instrument FROM fundamental_snapshots "
            "ORDER BY capture_date,instrument"
        ).fetchall() == [
            (20260830, "600000.SH"),
            (20260830, "600001.SH"),
            (20260830, "600002.SH"),
            (20260901, "600000.SH"),
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE instrument='600001.SH'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE instrument='600002.SH'"
        ).fetchone() == (1,)


def test_current_tnf_universe_can_explicitly_exceed_daily_coverage(tmp_path: Path):
    source = _source_snapshot(tmp_path, 20260830, [_day(20260828)], close_name="浦发银行")
    hq = source / "metadata" / "hq_cache"
    _write_tnf_rows(
        hq / "shs.tnf",
        [("600000", "浦发银行"), ("600001", "邯郸钢铁")],
    )
    _write_dbf(
        hq / "base.dbf",
        [
            {"SC": "1", "GPDM": "600000", "GXRQ": "20260830", "SSDATE": "19991110"},
            {"SC": "1", "GPDM": "600001", "GXRQ": "20260830", "SSDATE": "19980122"},
        ],
    )
    _rewrite_manifest(source)
    output = tmp_path / "ashare_tdx_pit.sqlite"

    report = sync_tdx_intake(source, output)

    assert report["instruments"] == 2
    assert report["daily_instruments"] == 1
    assert report["daily_only_instruments"] == 0
    assert report["current_universe_without_daily"] == 1
    assert report["current_fundamental_rows"] == 2
    with sqlite3.connect(output) as connection:
        assert connection.execute(
            "SELECT instrument FROM universe_snapshots ORDER BY instrument"
        ).fetchall() == [("600000.SH",), ("600001.SH",)]
        assert connection.execute(
            "SELECT instrument FROM instruments ORDER BY instrument"
        ).fetchall() == [("600000.SH",)]
        assert connection.execute(
            "SELECT instrument FROM current_fundamental_snapshot ORDER BY instrument"
        ).fetchall() == [("600000.SH",), ("600001.SH",)]


def test_intake_fails_closed_without_authoritative_tnf_for_daily_market(tmp_path: Path):
    source = _source_snapshot(tmp_path, 20260830, [_day(20260828)], close_name="浦发银行")
    (source / "metadata" / "hq_cache" / "shs.tnf").unlink()
    _rewrite_manifest(source)
    output = tmp_path / "ashare_tdx_pit.sqlite"

    with pytest.raises(ValueError, match="authoritative TNF current security master is missing"):
        sync_tdx_intake(source, output)

    assert not output.exists()


def test_incremental_sync_rejects_historical_bar_revision_without_touching_target(tmp_path: Path):
    first = _source_snapshot(tmp_path, 20260830, [_day(20260828)], close_name="浦发银行")
    output = tmp_path / "ashare_tdx_pit.sqlite"
    sync_tdx_intake(first, output)
    before = hashlib.sha256(output.read_bytes()).hexdigest()
    revised = _source_snapshot(tmp_path, 20260901, [_day(20260828, 999)], close_name="浦发银行")

    with pytest.raises(ValueError, match="historical bar conflict"):
        sync_tdx_intake(revised, output)

    assert hashlib.sha256(output.read_bytes()).hexdigest() == before


def test_incremental_sync_rejects_out_of_order_capture_without_rewinding_current_state(
    tmp_path: Path,
):
    first = _source_snapshot(
        tmp_path, 20260901, [_day(20260901)], close_name="浦发银行"
    )
    output = tmp_path / "ashare_tdx_pit.sqlite"
    sync_tdx_intake(first, output)
    latest = _source_snapshot(
        tmp_path,
        20260902,
        [_day(20260901), _day(20260902, 1010)],
        close_name="*ST浦发",
    )
    sync_tdx_intake(latest, output)
    before = hashlib.sha256(output.read_bytes()).hexdigest()
    older = _source_snapshot(
        tmp_path,
        20260831,
        [_day(20260831, 990)],
        close_name="旧快照",
    )

    with pytest.raises(ValueError, match="out-of-order capture"):
        sync_tdx_intake(older, output)

    assert hashlib.sha256(output.read_bytes()).hexdigest() == before
    with sqlite3.connect(output) as connection:
        assert connection.execute(
            "SELECT MAX(capture_date) FROM source_captures"
        ).fetchone() == (20260902,)
        assert connection.execute(
            "SELECT name FROM security_master_snapshots "
            "WHERE capture_date=20260902"
        ).fetchone() == ("*ST浦发",)
        metadata_capture = json.loads(
            connection.execute(
                "SELECT value_json FROM metadata WHERE key='capture_date'"
            ).fetchone()[0]
        )
        assert metadata_capture == 20260902


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("schema", "schema version"),
        ("required_table", "missing required tables"),
        ("capture_contract", "invalid promotion_allowed"),
        ("core_data", "inconsistent instrument bar summaries"),
    ],
)
def test_existing_capture_reuse_fails_closed_on_database_tampering(
    tmp_path: Path,
    tamper: str,
    message: str,
):
    source = _source_snapshot(tmp_path, 20260830, [_day(20260828)], close_name="浦发银行")
    output = tmp_path / "ashare_tdx_pit.sqlite"
    sync_tdx_intake(source, output)
    with sqlite3.connect(output) as connection:
        if tamper == "schema":
            connection.execute("PRAGMA user_version=1")
        elif tamper == "required_table":
            connection.execute("DROP TABLE industry_snapshots")
        elif tamper == "capture_contract":
            encoded = connection.execute(
                "SELECT source_report_json FROM source_captures WHERE capture_date=20260830"
            ).fetchone()[0]
            report = json.loads(encoded)
            report["promotion_allowed"] = True
            connection.execute(
                "UPDATE source_captures SET source_report_json=? WHERE capture_date=20260830",
                (json.dumps(report, sort_keys=True),),
            )
        elif tamper == "core_data":
            connection.execute("UPDATE instruments SET bar_rows=bar_rows+1")
        else:  # pragma: no cover - parametrization is closed above
            raise AssertionError(tamper)
        connection.commit()
    before = hashlib.sha256(output.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match=message):
        sync_tdx_intake(source, output)

    assert hashlib.sha256(output.read_bytes()).hexdigest() == before


def test_existing_capture_reuse_requires_successful_read_only_quick_check(tmp_path: Path):
    source = _source_snapshot(tmp_path, 20260830, [_day(20260828)], close_name="浦发银行")
    output = tmp_path / "ashare_tdx_pit.sqlite"
    sync_tdx_intake(source, output)
    database = output.read_bytes()
    page_size = int.from_bytes(database[16:18], "big") or 65536
    assert len(database) > page_size
    output.write_bytes(database[:-page_size])
    before = hashlib.sha256(output.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="SQLite integrity"):
        sync_tdx_intake(source, output)

    assert hashlib.sha256(output.read_bytes()).hexdigest() == before


def test_intake_reverifies_complete_manifest_before_publish(monkeypatch, tmp_path: Path):
    source = _source_snapshot(tmp_path, 20260830, [_day(20260828)], close_name="浦发银行")
    output = tmp_path / "ashare_tdx_pit.sqlite"
    original = intake_module._stock_day_files
    mutated = False

    def mutate_after_initial_verification(root: Path):
        nonlocal mutated
        rows = original(root)
        if not mutated:
            mutated = True
            (root / "LATE_SOURCE_FILE.txt").write_text("arrived during intake\n", encoding="utf-8")
            _rewrite_manifest(root)
        return rows

    monkeypatch.setattr(intake_module, "_stock_day_files", mutate_after_initial_verification)

    with pytest.raises(ValueError, match="source changed during intake"):
        sync_tdx_intake(source, output)

    assert mutated is True
    assert not output.exists()


def test_intake_never_writes_output_inside_immutable_source(tmp_path: Path):
    source = tmp_path / "tdx-windows-20260830"
    source.mkdir()
    output = source / "derived.sqlite"

    with pytest.raises(ValueError, match="outside the immutable source"):
        sync_tdx_intake(source, output)

    assert not output.exists()


def test_discovery_and_decoders_do_not_require_a_platform_lock(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(intake_module, "_fcntl", None)
    monkeypatch.setattr(intake_module, "_msvcrt", None)
    source = tmp_path / "tdx-windows-20260901"
    day = source / "vipdoc" / "sh" / "lday" / "sh600000.day"
    day.parent.mkdir(parents=True)
    day.write_bytes(_day(20260901))
    tnf = tmp_path / "shs.tnf"
    _write_tnf(tnf, "600000", "浦发银行")

    assert discover_tdx_sources([tmp_path])[0]["day_files"] == 1
    assert read_tnf(tnf, "sh")["600000.SH"]["name"] == "浦发银行"
    lock = tmp_path / "write.lock"
    with lock.open("a+b") as handle:
        with pytest.raises(RuntimeError, match="requires a POSIX fcntl or Windows msvcrt"):
            with intake_module._exclusive_file_lock(handle):
                pass


def test_windows_msvcrt_lock_fallback_is_balanced(monkeypatch, tmp_path: Path):
    calls: list[tuple[int, int, int]] = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd: int, mode: int, count: int) -> None:
            calls.append((fd, mode, count))

    monkeypatch.setattr(intake_module, "_fcntl", None)
    monkeypatch.setattr(intake_module, "_msvcrt", FakeMsvcrt)
    lock = tmp_path / "windows.lock"
    with lock.open("a+b") as handle:
        with intake_module._exclusive_file_lock(handle):
            assert handle.read(1) == b"\0"

    assert [mode for _, mode, _ in calls] == [FakeMsvcrt.LK_LOCK, FakeMsvcrt.LK_UNLCK]
    assert all(count == 1 for _, _, count in calls)
