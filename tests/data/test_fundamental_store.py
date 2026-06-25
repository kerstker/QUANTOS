"""PIT 基本面 store(复用 FeatureValue):防泄漏 as_of、重述保留、fail-closed、坏行拒绝。"""
import csv
import pytest
from quantos.contracts.feature import FeatureValue
from services.data.fundamental_store import SqliteFundamentalStore, ingest_fundamentals_csv
from services.data.persistent_store import DataStoreError


def _fv(metric, inst, et, kt, val):
    return FeatureValue(feature_id=metric, instrument_id=inst, event_time=et, knowledge_time=kt, value=val)


def test_pit_as_of_and_restatement():
    s = SqliteFundamentalStore(":memory:", create=True)
    assert s.add(_fv("eps", "AAA", 20240331, 20240420, 1.0))     # Q1 期末,4/20 公告
    assert s.add(_fv("eps", "AAA", 20240331, 20240520, 1.2))     # 重述,5/20 公告(保留)
    assert s.latest_as_of("eps", "AAA", 20240419) is None        # 公告前不可知(防泄漏)
    assert s.latest_as_of("eps", "AAA", 20240430).value == 1.0   # 首次公告后、重述前
    assert s.latest_as_of("eps", "AAA", 20240601).value == 1.2   # 重述后取最新可知
    assert len(s.as_of("eps", "AAA", 20240601)) == 2             # 两版都保留


def test_pk_dedup():
    s = SqliteFundamentalStore(":memory:", create=True)
    assert s.add(_fv("eps", "AAA", 20240331, 20240420, 1.0))
    assert s.add(_fv("eps", "AAA", 20240331, 20240420, 1.0)) is False


def test_fail_closed_uninitialized():
    s = SqliteFundamentalStore(":memory:")
    with pytest.raises(DataStoreError):
        s.latest_as_of("eps", "AAA", 20240601)


def test_ingest_good_and_bad(tmp_path):
    p = tmp_path / "f.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["instrument_id", "metric", "event_time", "knowledge_time", "value"])
        w.writerow(["AAA", "eps", 20240331, 20240420, 1.5])
        w.writerow(["BBB", "eps", 20240331, 20240419, 2.0])
        w.writerow(["CCC", "eps", 20240331, 20240301, 1.0])      # kt<et 泄漏 → 拒
        w.writerow(["DDD", "eps", 20240331, 20240420, "bad"])    # value 非数 → 拒
        w.writerow(["EEE", "eps", 20240331, 20240420, "inf"])    # 非有限 → 拒(审查 P2)
    s = SqliteFundamentalStore(":memory:", create=True)
    rep = ingest_fundamentals_csv(str(p), s)
    assert rep.rows_read == 5 and rep.added == 2 and len(rep.rejected) == 3
    assert s.latest_as_of("eps", "AAA", 20240601).value == 1.5
