import pytest
import sqlite3
from services.research.paper_trading.store import PaperTrackStore, PaperTrackStoreError


def _store():
    return PaperTrackStore(":memory:", create=True)


def test_read_only_store_never_creates_or_mutates_authority(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(PaperTrackStoreError):
        PaperTrackStore(str(missing), read_only=True)
    assert not missing.exists()

    path = tmp_path / "authority.db"
    writer = PaperTrackStore(str(path), create=True)
    writer.close()
    reader = PaperTrackStore(str(path), read_only=True)
    try:
        assert reader._conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            reader._conn.execute("CREATE TABLE forbidden(value INTEGER)")
    finally:
        reader.close()


def test_read_only_store_rejects_create_and_memory():
    with pytest.raises(PaperTrackStoreError):
        PaperTrackStore(":memory:", read_only=True)
    with pytest.raises(PaperTrackStoreError):
        PaperTrackStore(":memory:", create=True, read_only=True)


def test_append_is_idempotent_by_period():
    s = _store()
    s.append_periods("a1", [(1000, 0.01, 0.05, "OOS_TAIL"), (2000, -0.02, 0.04, "OOS_TAIL")])
    s.append_periods("a1", [(2000, -0.02, 0.04, "OOS_TAIL"), (3000, 0.03, 0.06, "OOS_TAIL")])  # 2000 dup
    assert [p["as_of_ts"] for p in s.periods("a1")] == [1000, 2000, 3000]   # dup ignored, new appended


def test_recompute_summary_is_correct():
    s = _store()
    s.append_periods("a1", [(1000, 0.01, 0.05, "OOS_TAIL"), (2000, 0.02, 0.07, "OOS_TAIL")])
    s.recompute_summary("a1", backtest_sharpe=2.0)
    t = s.track("a1")
    assert t["n_periods"] == 2 and t["start_ts"] == 1000 and t["last_ts"] == 2000
    assert t["paper_ic"] == pytest.approx(0.06, abs=1e-6)          # mean(0.05, 0.07)
    assert t["cum_return"] == pytest.approx((1.01 * 1.02 - 1.0), abs=1e-6)
    assert t["backtest_sharpe"] == 2.0
    # perf_gap = (bt - paper)/bt = consistency.performance_gap (store rounds the summary to 4 dp)
    from services.research.backtest_engine.metrics import sharpe
    from services.research.paper_trading.domain.consistency import performance_gap
    assert t["perf_gap"] == pytest.approx(performance_gap(2.0, sharpe([0.01, 0.02])), abs=1e-4)


def test_missing_db_fails_closed():
    with pytest.raises(PaperTrackStoreError):
        PaperTrackStore("nonexistent-paper-track.db", create=False)   # no schema -> raises


def test_null_ic_handled_and_track_none_when_absent():
    s = _store()
    assert s.track("ghost") is None
    s.append_periods("a2", [(1000, 0.01, None, "OOS_TAIL")])
    s.recompute_summary("a2", backtest_sharpe=None)
    t = s.track("a2")
    assert t["paper_ic"] is None and t["perf_gap"] is None          # no IC, no bt -> honest None
