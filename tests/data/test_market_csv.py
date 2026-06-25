"""真实市场数据离线 CSV 适配器:列映射/日期→event_time/失败关闭 + 端到端方法论。"""
import csv
import pytest
from quantos.contracts.research import AlphaState
from services.data.persistent_store import SqlitePITStore
from services.data.market_csv import ingest_market_csv, DEFAULT_COLUMN_MAP
from services.alpha.catalog import AlphaCatalog
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.intelligence.search_space import HypothesisSpace
from services.research.session import run_research_session


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


_STD_HEADER = ["Date", "Open", "High", "Low", "Close", "Volume"]


# ---------------- Task 2: 适配器单测 ----------------

def test_maps_real_format_into_store(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, _STD_HEADER, [
        ["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000000],
        ["2024-03-18", 100.5, 102.0, 100.0, 101.5, 1200000],   # 周末跳空(16/17)
    ])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_market_csv(str(p), store, instrument_id="AAA")
    assert rep.rows_read == 2 and rep.bars_ingested == 2 and rep.rejected == ()
    ets = [b.event_time for b in store.as_of("AAA", as_of_time=99999999)]
    assert ets == [20240315, 20240318]                          # event_time == YYYYMMDD
    assert rep.content_hash and rep.quality.passed


def test_custom_column_map_and_date_format(tmp_path):
    p = tmp_path / "bbb.csv"
    _write(p, ["d", "o", "h", "l", "c", "v"], [
        ["20240102", 10.0, 10.5, 9.8, 10.2, 5000],
    ])
    store = SqlitePITStore(":memory:", create=True)
    cmap = {"date": "d", "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"}
    rep = ingest_market_csv(str(p), store, instrument_id="BBB",
                            column_map=cmap, date_format="%Y%m%d")
    assert rep.bars_ingested == 1
    assert [b.event_time for b in store.as_of("BBB", as_of_time=99999999)] == [20240102]


def test_bad_rows_rejected_good_rows_kept(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, _STD_HEADER, [
        ["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000],    # ok
        ["not-a-date", 100.0, 101.0, 99.0, 100.5, 1000],    # 坏日期
        ["2024-03-18", 100.0, 90.0, 99.0, 100.5, 1000],     # high<low → OHLC 矛盾
    ])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_market_csv(str(p), store, instrument_id="AAA")
    assert rep.bars_ingested == 1 and len(rep.rejected) == 2     # 失败关闭,不崩
    classes = {r[1] for r in rep.rejected}
    assert "ValueError" in classes


def test_missing_mapped_column_rejected(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, ["Date", "Open", "High", "Low", "Close"], [      # 无 Volume 列
        ["2024-03-15", 100.0, 101.0, 99.0, 100.5],
    ])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_market_csv(str(p), store, instrument_id="AAA")
    assert rep.bars_ingested == 0 and len(rep.rejected) == 1     # 缺列 → 该行进 rejected


def test_reingest_idempotent_same_hash(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, _STD_HEADER, [["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    a = ingest_market_csv(str(p), store, instrument_id="AAA")
    b = ingest_market_csv(str(p), store, instrument_id="AAA")
    assert a.bars_ingested == 1 and b.bars_ingested == 0 and b.duplicates == 1
    assert a.content_hash == b.content_hash


def test_now_rejects_future_dates(tmp_path):
    p = tmp_path / "AAA.csv"
    _write(p, _STD_HEADER, [
        ["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000],
        ["2099-01-01", 100.0, 101.0, 99.0, 100.5, 1000],     # 未来
    ])
    store = SqlitePITStore(":memory:", create=True)
    rep = ingest_market_csv(str(p), store, instrument_id="AAA", now=20240601)
    assert rep.bars_ingested == 1 and len(rep.rejected) == 1
    assert rep.rejected[0][1] == "FutureDataError"


def test_bad_column_map_raises():
    store = SqlitePITStore(":memory:", create=True)
    with pytest.raises(ValueError):
        ingest_market_csv("nonexistent.csv", store, instrument_id="X",
                          column_map={"date": "Date"})           # 缺 OHLCV 键


def test_ambiguous_date_format_rejected():
    store = SqlitePITStore(":memory:", create=True)
    with pytest.raises(ValueError):
        ingest_market_csv("x.csv", store, instrument_id="X", date_format="%y-%m-%d")  # 两位年翻转
    with pytest.raises(ValueError):
        ingest_market_csv("x.csv", store, instrument_id="X", date_format="%Y-%m")     # 缺日


def test_explicit_knowledge_time_retains_restatement(tmp_path):
    # 同一交易日的厂商修正:用更晚的 knowledge_time 重接 → 两版 bitemporal 行都保留(审查 P1)
    p1 = tmp_path / "AAA_v1.csv"
    _write(p1, _STD_HEADER, [["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000]])
    p2 = tmp_path / "AAA_v2.csv"
    _write(p2, _STD_HEADER, [["2024-03-15", 100.0, 101.0, 99.0, 100.9, 1000]])  # 收盘被修正
    store = SqlitePITStore(":memory:", create=True)
    ingest_market_csv(str(p1), store, instrument_id="AAA", knowledge_time=20240315)
    rep2 = ingest_market_csv(str(p2), store, instrument_id="AAA", knowledge_time=20240320)
    assert rep2.bars_ingested == 1 and rep2.duplicates == 0            # 修正作为新行入库,非丢弃
    assert store.latest_as_of("AAA", 20240315).close == 100.5          # 修正前:只见原值(PIT)
    assert store.latest_as_of("AAA", 20240320).close == 100.9          # 修正后:见最新已知值


def test_default_knowledge_time_drops_restatement_as_duplicate(tmp_path):
    # 默认 kt=event_time:同日期修正 PK 相同 → 被去重丢弃(首见为准,文档化的 v1 行为)
    p = tmp_path / "AAA.csv"
    _write(p, _STD_HEADER, [["2024-03-15", 100.0, 101.0, 99.0, 100.5, 1000]])
    store = SqlitePITStore(":memory:", create=True)
    ingest_market_csv(str(p), store, instrument_id="AAA")
    p2 = tmp_path / "AAA2.csv"
    _write(p2, _STD_HEADER, [["2024-03-15", 100.0, 101.0, 99.0, 100.9, 1000]])
    rep = ingest_market_csv(str(p2), store, instrument_id="AAA")
    assert rep.bars_ingested == 0 and rep.duplicates == 1
    assert store.latest_as_of("AAA", 20240315).close == 100.5         # 首见为准


# ---------------- Task 3: 端到端方法论 ----------------

def _ingest_universe(tmp_path):
    # 3 个标的、同一段交易日(含周末跳空),真实格式;价量随标的/日期确定性变化。
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = SqlitePITStore(":memory:", create=True)
    days = ["2024-03-%02d" % d for d in range(4, 23) if d % 7 not in (0, 6)]  # 跳过周末
    for k, inst in enumerate(("AAA", "BBB", "CCC")):
        p = tmp_path / f"{inst}.csv"
        rows = []
        for j, d in enumerate(days):
            base = 10.0 + k + 0.1 * ((j * (k + 1)) % 5)       # 确定性的真实型价
            rows.append([d, base, base + 1.0, base - 0.5, base + 0.2 * ((j + k) % 3), 1000 + j])
        _write(p, _STD_HEADER, rows)
        rep = ingest_market_csv(str(p), store, instrument_id=inst)
        assert rep.bars_ingested == len(days) and rep.rejected == ()
    return store


def _event_times(store):
    return sorted({b.event_time for inst in store.keys()
                   for b in store.fetch_bars(inst, 0, 99999999)})


def test_end_to_end_real_format_runs_methodology(tmp_path):
    store = _ingest_universe(tmp_path)
    reg = ExperimentRegistry(SqliteExperimentStore(":memory:", create=True))
    space = HypothesisSpace(factors=("AAA",), operators=("momentum", "zscore"), windows=(3, 5))
    out = run_research_session(store, store.keys(), event_times=_event_times(store), fwd_horizon=1,
                               space=space, rounds=4, batch_size=2, timestamp=20240601, registry=reg)
    assert out["dataset_version"]                       # 真实数据内容派生版本
    assert out["n_evaluated"] >= 1                      # 方法论在真实格式数据上跑出评估
    cat = AlphaCatalog()
    reg.load_into(cat)
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())   # contained


def test_end_to_end_is_deterministic(tmp_path):
    a_store = _ingest_universe(tmp_path / "a")
    b_store = _ingest_universe(tmp_path / "b")
    kw = dict(fwd_horizon=1, space=HypothesisSpace(factors=("AAA",),
              operators=("momentum", "zscore"), windows=(3, 5)),
              rounds=4, batch_size=2, timestamp=20240601)
    a = run_research_session(a_store, a_store.keys(), event_times=_event_times(a_store), **kw)
    b = run_research_session(b_store, b_store.keys(), event_times=_event_times(b_store), **kw)
    assert a["dataset_version"] == b["dataset_version"] and a["arm_means"] == b["arm_means"]
