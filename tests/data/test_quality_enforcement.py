"""Data quality enforcement, including conservative cross-instrument volume dispersion.

Two mechanisms: (1) an opt-in `strict=` ingestion gate that fail-closed raises when a
single-instrument dataset's quality report does not pass; (2) a dispersion guard that withholds
capacity estimates when liquidity inputs span an implausibly broad range. Dispersion alone does
not prove mixed units; explicit SourceSpec volume semantics own that contract."""
import os

import pytest

from services.data.file_ingest import FatalIngestionError, ingest_bars_csv
from services.data.persistent_store import SqlitePITStore
from services.data.quality import (VolumeDispersionError, assert_volume_dispersion,
                                   check_volume_dispersion)


# ---- (2) cross-instrument volume dispersion ----

def test_moderate_volume_dispersion_passes():
    check = check_volume_dispersion({"BTC": 3.0e9, "ETH": 1.2e9, "SOL": 4.0e8}, max_ratio=1e6)
    assert check.passed is True


def test_extreme_volume_dispersion_fails_without_claiming_unit_mismatch():
    check = check_volume_dispersion({"BTC": 3.7e15, "EOS": 2.0e4}, max_ratio=1e6)
    assert check.passed is False
    assert check.name == "volume_dispersion"
    assert "ratio" in check.detail


def test_volume_dispersion_ignores_nonpositive_and_thin_cross_section():
    assert check_volume_dispersion({"BTC": 3e9, "ZERO": 0.0}, max_ratio=1e6).passed is True
    assert check_volume_dispersion({"BTC": 3e9}, max_ratio=1e6).passed is True


# ---- (2b) fail-closed dispersion assertion for capacity/ADV consumers ----

def test_assert_volume_dispersion_passes_moderate_values():
    check = assert_volume_dispersion({"BTC": 3.0e9, "ETH": 1.2e9, "SOL": 4.0e8}, max_ratio=1e6)
    assert check.passed is True and check.name == "volume_dispersion"


def test_assert_volume_dispersion_raises_on_extreme_values():
    with pytest.raises(VolumeDispersionError):
        assert_volume_dispersion({"BTC": 3.7e15, "EOS": 2.0e4}, max_ratio=1e6)


def test_assert_volume_dispersion_thin_cross_section_and_zero_ok():
    assert_volume_dispersion({"BTC": 3e9}, max_ratio=1e6)
    assert_volume_dispersion({"BTC": 3e9, "Z": 0.0}, max_ratio=1e6)


def test_assert_volume_dispersion_error_carries_ratio():
    with pytest.raises(VolumeDispersionError) as exc:
        assert_volume_dispersion({"BTC": 3.7e15, "EOS": 2.0e4}, max_ratio=1e6)
    assert "ratio" in str(exc.value)


# ---- (1) strict ingestion gate (single-instrument datasets) ----

def _csv(tmp_path, rows):
    p = os.path.join(str(tmp_path), "bars.csv")
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write("instrument_id,event_time,knowledge_time,open,high,low,close,volume\n")
        for r in rows:
            fh.write(",".join(str(x) for x in r) + "\n")
    return p


_GOOD = [("BTC", 20240101, 20240101, 100, 101, 99, 100, 5),
         ("BTC", 20240102, 20240102, 100, 102, 99, 101, 6)]
_DUP = [("BTC", 20240101, 20240101, 100, 101, 99, 100, 5),
        ("BTC", 20240101, 20240101, 100, 102, 99, 101, 6)]   # duplicate event_time -> quality fails
_OOO = [("BTC", 20240102, 20240102, 100, 102, 99, 101, 6),
        ("BTC", 20240101, 20240101, 100, 101, 99, 100, 5)]   # event_time goes backwards -> out-of-order


def test_strict_ingestion_raises_on_bad_quality(tmp_path):
    store = SqlitePITStore(os.path.join(str(tmp_path), "s.db"), create=True)
    with pytest.raises(FatalIngestionError):
        ingest_bars_csv(_csv(tmp_path, _DUP), store, dataset_id="BTC", strict=True)
    assert store.as_of("BTC", as_of_time=99999999) == []


def test_non_strict_default_does_not_raise(tmp_path):
    store = SqlitePITStore(os.path.join(str(tmp_path), "s.db"), create=True)
    rep = ingest_bars_csv(_csv(tmp_path, _DUP), store, dataset_id="BTC")   # default strict=False
    assert rep.quality.passed is False                                    # quality still reported
    assert rep.rows_read == 2                                             # but no raise (zero regression)


def test_strict_passes_clean_dataset(tmp_path):
    store = SqlitePITStore(os.path.join(str(tmp_path), "s.db"), create=True)
    rep = ingest_bars_csv(_csv(tmp_path, _GOOD), store, dataset_id="BTC", strict=True)
    assert rep.bars_ingested == 2 and rep.quality.passed is True


# ---- (1b) out-of-order detection at ingestion (audit P2-1: was a dead check) ----

def test_out_of_order_detected_at_ingestion(tmp_path):
    """Regression (audit P2-1): _ingest_records sorted event_times before the quality
    check, so count_out_of_order saw an already-sorted series and was structurally always
    0 — a dead check. The report must detect out-of-order arrival on the ORIGINAL order."""
    store = SqlitePITStore(os.path.join(str(tmp_path), "s.db"), create=True)
    rep = ingest_bars_csv(_csv(tmp_path, _OOO), store, dataset_id="BTC")   # default strict=False
    assert rep.quality.out_of_order_count >= 1
    assert rep.quality.passed is False


def test_strict_ingestion_raises_on_out_of_order(tmp_path):
    store = SqlitePITStore(os.path.join(str(tmp_path), "s.db"), create=True)
    with pytest.raises(FatalIngestionError):
        ingest_bars_csv(_csv(tmp_path, _OOO), store, dataset_id="BTC", strict=True)
    assert store.as_of("BTC", as_of_time=99999999) == []
