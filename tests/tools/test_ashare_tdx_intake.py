from __future__ import annotations

import hashlib
import sqlite3
import struct
from pathlib import Path

import pytest

from tools.ashare_tdx_intake import (
    DAY_RECORD,
    is_stock_code,
    iter_day_rows,
    parse_sha256_manifest,
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


def test_sha_manifest_rejects_parent_escape(tmp_path: Path):
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{'0' * 64}  ../escape\n", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        parse_sha256_manifest(manifest)
