import csv
import pytest
from services.data.persistent_store import DataStoreError, SqlitePITStore
from services.data.file_ingest import FatalIngestionError, ingest_bars_csv

# bitemporal invariant: knowledge_time >= event_time
_HEADER = ["instrument_id", "event_time", "knowledge_time", "open", "high", "low", "close", "volume"]


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(_HEADER)
        w.writerows(rows)


def test_ingests_valid_rows_and_persists(tmp_path):
    p = tmp_path / "bars.csv"
    _write_csv(p, [["X", 10, 10, 100, 101, 99, 100, 1000],
                   ["X", 20, 20, 101, 102, 100, 101, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_bars_csv(str(p), store)
    assert rep.rows_read == 2 and rep.bars_ingested == 2 and rep.duplicates == 0
    assert [b.event_time for b in store.as_of("X", as_of_time=60)] == [10, 20]
    assert rep.content_hash and rep.quality.passed


def test_future_and_malformed_rows_rejected_not_crash(tmp_path):
    p = tmp_path / "bars.csv"
    _write_csv(p, [["X", 10, 10, 100, 101, 99, 100, 1000],     # ok
                   ["X", 999, 999, 100, 101, 99, 100, 1000],   # future event (now=100)
                   ["X", 30, 30, 100, 90, 99, 100, 1000]])     # high<low -> invalid OHLC
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_bars_csv(str(p), store, now=100)
    assert rep.bars_ingested == 1 and len(rep.rejected) == 2
    reasons = {r[1] for r in rep.rejected}
    assert "FutureDataError" in reasons


def test_reingest_is_idempotent_same_hash(tmp_path):
    p = tmp_path / "bars.csv"
    _write_csv(p, [["X", 10, 10, 100, 101, 99, 100, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    a = ingest_bars_csv(str(p), store)
    b = ingest_bars_csv(str(p), store)
    assert a.bars_ingested == 1 and b.bars_ingested == 0 and b.duplicates == 1
    assert a.content_hash == b.content_hash
    assert len(store.as_of("X", as_of_time=60)) == 1


def test_reingest_same_identity_with_changed_content_is_not_a_duplicate(tmp_path):
    first = tmp_path / "first.csv"
    changed = tmp_path / "changed.csv"
    _write_csv(first, [["X", 10, 10, 100, 101, 99, 100, 1000]])
    _write_csv(changed, [["X", 10, 10, 200, 201, 199, 200, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    ingest_bars_csv(str(first), store)

    with pytest.raises(DataStoreError, match="identity conflict"):
        ingest_bars_csv(str(changed), store)

    assert store.latest_as_of("X", as_of_time=60).close == 100.0


def test_strict_reingest_content_conflict_fails_and_preserves_store(tmp_path):
    first = tmp_path / "first-strict.csv"
    changed = tmp_path / "changed-strict.csv"
    _write_csv(first, [["X", 10, 10, 100, 101, 99, 100, 1000]])
    _write_csv(changed, [["X", 10, 10, 200, 201, 199, 200, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    ingest_bars_csv(str(first), store, strict=True)

    with pytest.raises(FatalIngestionError, match="atomic write failed"):
        ingest_bars_csv(str(changed), store, strict=True)

    assert store.latest_as_of("X", as_of_time=60).close == 100.0


def test_future_knowledge_time_rejected(tmp_path):
    # event_time is in the past but the bar is "known" in the future -> not knowable yet
    p = tmp_path / "bars.csv"
    _write_csv(p, [["X", 10, 200, 100, 101, 99, 100, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_bars_csv(str(p), store, now=100)
    assert rep.bars_ingested == 0 and len(rep.rejected) == 1
    assert rep.rejected[0][1] == "FutureDataError"


def test_missing_knowledge_time_without_default_raises(tmp_path):
    p = tmp_path / "bars.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["instrument_id", "event_time", "open", "high", "low", "close", "volume"])
        w.writerow(["X", 10, 100, 101, 99, 100, 1000])
    store = SqlitePITStore(":memory:", create=True)
    with pytest.raises(ValueError):
        ingest_bars_csv(str(p), store)
