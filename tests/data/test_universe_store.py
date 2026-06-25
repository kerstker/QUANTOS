"""持久化 PIT universe store + BYO-CSV 接入:往返、多区间、fail-closed、坏行拒绝。"""
import csv
import pytest
from services.data.universe import PITUniverse
from services.data.universe_store import SqliteUniverseStore, ingest_universe_csv
from services.data.persistent_store import DataStoreError


def test_roundtrip_and_readd():
    u = PITUniverse()
    u.add_membership("AAA", "2024-01-01", "2024-03-31")
    u.add_membership("AAA", "2024-06-01")        # 退市后重纳入,仍是成员
    u.add_membership("BBB", "2024-01-01")
    s = SqliteUniverseStore(":memory:", create=True)
    assert s.save(u) == 3
    u2 = PITUniverse()
    s.load_into(u2)
    assert u2.is_member_as_of("AAA", "2024-02-15")
    assert not u2.is_member_as_of("AAA", "2024-04-15")    # 区间外
    assert u2.is_member_as_of("AAA", "2024-07-01")        # 重纳入后
    assert u2.members_as_of("2024-02-15") == {"AAA", "BBB"}
    assert s.members_as_of("2024-04-15") == {"BBB"}


def test_save_is_idempotent():
    u = PITUniverse()
    u.add_membership("AAA", "2024-01-01")
    s = SqliteUniverseStore(":memory:", create=True)
    assert s.save(u) == 1
    assert s.save(u) == 0                                  # PK 去重


def test_fail_closed_uninitialized():
    s = SqliteUniverseStore(":memory:")                   # 未 create
    with pytest.raises(DataStoreError):
        s.members_as_of("2024-01-01")


def test_add_membership_rejects_bad_dates():
    s = SqliteUniverseStore(":memory:", create=True)
    with pytest.raises(ValueError):
        s.add_membership("AAA", "not-a-date")
    with pytest.raises(ValueError):
        s.add_membership("AAA", "2024-06-30", "2024-01-01")   # end < start


def test_ingest_csv_good_and_bad(tmp_path):
    p = tmp_path / "u.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["instrument_id", "start_date", "end_date"])
        w.writerow(["AAA", "2024-01-01", ""])
        w.writerow(["BBB", "2024-01-01", "2024-06-30"])
        w.writerow(["CCC", "not-a-date", ""])                 # 坏 ISO
        w.writerow(["DDD", "2024-06-30", "2024-01-01"])       # end<start
    s = SqliteUniverseStore(":memory:", create=True)
    rep = ingest_universe_csv(str(p), s)
    assert rep.rows_read == 4 and rep.added == 2 and len(rep.rejected) == 2
    assert s.members_as_of("2024-03-01") == {"AAA", "BBB"}
    assert s.members_as_of("2024-08-01") == {"AAA"}           # BBB 已退出
