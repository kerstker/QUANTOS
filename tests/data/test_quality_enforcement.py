"""Rank #22 — data quality is now machine-ENFORCED (was computed-but-never-checked).

Two mechanisms: (1) an opt-in `strict=` ingestion gate that fail-closed raises when a
single-instrument dataset's quality report does not pass; (2) a cross-instrument volume-unit
consistency check that formalizes the scenario-research heuristic (the crypto_daily.db bug:
EOS ~$20k vs BTC ~$3.7Q, ~17 orders of magnitude) into a reusable, tested guard."""
import os

import pytest

from services.data.file_ingest import FatalIngestionError, ingest_bars_csv
from services.data.persistent_store import SqlitePITStore
from services.data.quality import check_volume_units


# ---- (2) cross-instrument volume-unit consistency ----

def test_consistent_volume_units_pass():
    check = check_volume_units({"BTC": 3.0e9, "ETH": 1.2e9, "SOL": 4.0e8}, max_ratio=1e6)
    assert check.passed is True


def test_mixed_volume_units_fail():
    # BTC $3.7Q vs EOS $20k -> ratio ~1.8e14 >> 1e6 -> UNRELIABLE (the crypto_daily.db bug)
    check = check_volume_units({"BTC": 3.7e15, "EOS": 2.0e4}, max_ratio=1e6)
    assert check.passed is False
    assert "ratio" in check.detail


def test_volume_units_ignore_nonpositive_and_thin_cross_section():
    assert check_volume_units({"BTC": 3e9, "ZERO": 0.0}, max_ratio=1e6).passed is True   # 0 ignored
    assert check_volume_units({"BTC": 3e9}, max_ratio=1e6).passed is True                # <2 -> trivially ok


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


def test_strict_ingestion_raises_on_bad_quality(tmp_path):
    store = SqlitePITStore(os.path.join(str(tmp_path), "s.db"), create=True)
    with pytest.raises(FatalIngestionError):
        ingest_bars_csv(_csv(tmp_path, _DUP), store, dataset_id="BTC", strict=True)


def test_non_strict_default_does_not_raise(tmp_path):
    store = SqlitePITStore(os.path.join(str(tmp_path), "s.db"), create=True)
    rep = ingest_bars_csv(_csv(tmp_path, _DUP), store, dataset_id="BTC")   # default strict=False
    assert rep.quality.passed is False                                    # quality still reported
    assert rep.rows_read == 2                                             # but no raise (zero regression)


def test_strict_passes_clean_dataset(tmp_path):
    store = SqlitePITStore(os.path.join(str(tmp_path), "s.db"), create=True)
    rep = ingest_bars_csv(_csv(tmp_path, _GOOD), store, dataset_id="BTC", strict=True)
    assert rep.bars_ingested == 2 and rep.quality.passed is True
