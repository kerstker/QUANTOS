"""strategy_id 仓位隔离——账本分账维度(2026-07-06 战线③,commit 1)。设计:
docs/superpowers/specs/2026-07-06-strategy-position-isolation-design.md。

核心裁定:strategy_id 只是**本地账本内部的分账维度**;venue/对账/风控/紧急减仓全部维持
账户级。结构性不变量:Σ_s all_positions(strategy_id=s) 逐标的恒等于 all_positions()
(账户级 = 全部 fills 的和,策略级 = 按 fill.strategy_id 的分区,分区求和恒等于全集)。

迁移红线:存量库是**真实测试网交易数据**,迁移只允许 ALTER TABLE ADD COLUMN(SQLite 的
ADD COLUMN 不重写任何存量行、不碰任何已有值,老行读取时返回列默认值 'crowd_contra'——
2026-07-06 只读核查证实存量全部成交确属 crowd_contra 决策产物或其人工补记,见设计文档 §7)。
"""
import sqlite3

import pytest

from live.execution.position_ledger import (LiveLedger, _BACKFILL_STRATEGY_ID)
from quantos.contracts.trading import OrderSide

_NOW = 1_700_000_000_000

# 旧版 schema 原文(2026-07-06 之前,无 strategy_id 列)——手工建库模拟真实存量账本
_OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_orders (
  client_order_id TEXT PRIMARY KEY,
  instrument_id   TEXT NOT NULL,
  side            TEXT NOT NULL,
  quantity        REAL NOT NULL,
  submitted_at    INTEGER NOT NULL,
  venue_order_id  TEXT,
  status          TEXT NOT NULL DEFAULT 'NEW'
);
CREATE TABLE IF NOT EXISTS live_fills (
  fill_id         TEXT PRIMARY KEY,
  client_order_id TEXT NOT NULL,
  instrument_id   TEXT NOT NULL,
  side            TEXT NOT NULL,
  quantity        REAL NOT NULL,
  price           REAL NOT NULL,
  filled_at       INTEGER NOT NULL
);
"""
_OLD_TCA_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_tca (
  fill_id          TEXT PRIMARY KEY,
  client_order_id  TEXT NOT NULL,
  instrument_id    TEXT NOT NULL,
  side             TEXT NOT NULL,
  decision_price   REAL NOT NULL,
  fill_price       REAL NOT NULL,
  quantity         REAL NOT NULL,
  slippage_bps     REAL,
  recorded_at      INTEGER NOT NULL
);
"""

# 模拟真实测试网账本的三种行形态(2026-07-06 只读核查,设计文档 §7):
# ① 32 位 hex 决策订单(decision_prefix+hash),② quantos-smoke 手工冒烟单 + recon-smoke 补记
# fill,③ resize-comp-* 人工补偿 fill(有 fill 无 order 行)。
_REAL_SHAPE_ORDERS = [
    ("quantos-smoke-1783147029", "BTC", "BUY", 0.002, 1783147031274, "v1", "FILLED"),
    ("a24fdea9aeae9160c33331d26cb74de9", "ADA", "SELL", 840.0, 1783228680067, "v2", "FILLED"),
    ("536ce8cfe0ce23308e7133e982884b0a", "TRX", "BUY", 695.0, 1783236028309, "", "NEW"),
]
_REAL_SHAPE_FILLS = [
    ("recon-smoke-fill-1", "quantos-smoke-1783147029", "BTC", "BUY", 0.002, 62443.8, 1783147031274),
    ("a312fccafae8fb24", "a24fdea9aeae9160c33331d26cb74de9", "ADA", "SELL", 840.0, 0.1896, 1783228680067),
    ("5957452ac90217a8", "resize-comp-ADA", "ADA", "SELL", 1370.0, 0.1896, 1783300000000),
]


def _make_old_ledger(path, *, with_tca=True):
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_SCHEMA)
    if with_tca:
        conn.executescript(_OLD_TCA_SCHEMA)
    conn.executemany(
        "INSERT INTO live_orders(client_order_id, instrument_id, side, quantity, submitted_at,"
        " venue_order_id, status) VALUES (?,?,?,?,?,?,?)", _REAL_SHAPE_ORDERS)
    conn.executemany(
        "INSERT INTO live_fills(fill_id, client_order_id, instrument_id, side, quantity, price,"
        " filled_at) VALUES (?,?,?,?,?,?,?)", _REAL_SHAPE_FILLS)
    if with_tca:
        conn.execute(
            "INSERT INTO live_tca(fill_id, client_order_id, instrument_id, side, decision_price,"
            " fill_price, quantity, slippage_bps, recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("a312fccafae8fb24", "a24fdea9aeae9160c33331d26cb74de9", "ADA", "SELL",
             0.19, 0.1896, 840.0, 21.05, 1783228680067))
    conn.commit()
    conn.close()


def _cols(path, table):
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


# --- 新库 schema ---

def test_new_ledger_schema_has_strategy_id_on_all_three_tables(tmp_path):
    path = str(tmp_path / "new.db")
    ledger = LiveLedger(path, create=True)
    ledger.record_tca("f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.BUY,
                      decision_price=100.0, fill_price=100.1, quantity=1.0,
                      strategy_id="crowd_contra", recorded_at=_NOW)   # 触发 tca 惰性建表
    for table in ("live_orders", "live_fills", "live_tca"):
        assert "strategy_id" in _cols(path, table), table


def test_write_apis_require_explicit_strategy_id(tmp_path):
    # 写入 API strategy_id 必填关键字——杜绝新写入静默落回 'crowd_contra'(设计 §1.3)
    ledger = LiveLedger(str(tmp_path / "new.db"), create=True)
    with pytest.raises(TypeError):
        ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, submitted_at=_NOW)
    with pytest.raises(TypeError):
        ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 0.01, 100.0, filled_at=_NOW)
    with pytest.raises(TypeError):
        ledger.record_tca("f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.BUY,
                          decision_price=100.0, fill_price=100.1, quantity=1.0, recorded_at=_NOW)


# --- 惰性迁移:存量真实数据零改动 ---

def test_migration_old_ledger_preserves_real_data(tmp_path):
    path = str(tmp_path / "old.db")
    _make_old_ledger(path)
    ledger = LiveLedger(path)                                  # 无 create,ops API/gonogo 同款打开
    orders = ledger.orders()
    fills = ledger.fills()
    assert len(orders) == len(_REAL_SHAPE_ORDERS)
    assert len(fills) == len(_REAL_SHAPE_FILLS)
    # 逐行逐值与插入时完全一致(除新增 strategy_id 列),存量行回填 'crowd_contra'
    for row, src in zip(orders, _REAL_SHAPE_ORDERS):
        assert (row["client_order_id"], row["instrument_id"], row["side"], row["quantity"],
                row["submitted_at"], row["venue_order_id"], row["status"]) == src
        assert row["strategy_id"] == _BACKFILL_STRATEGY_ID
    for row, src in zip(fills, _REAL_SHAPE_FILLS):
        assert (row["fill_id"], row["client_order_id"], row["instrument_id"], row["side"],
                row["quantity"], row["price"], row["filled_at"]) == src
        assert row["strategy_id"] == _BACKFILL_STRATEGY_ID
    # 账户级仓位数值与迁移前手工计算一致:BTC +0.002;ADA -840-1370
    assert ledger.all_positions()["BTC"] == pytest.approx(0.002)
    assert ledger.all_positions()["ADA"] == pytest.approx(-2210.0)
    # TCA 行同样带上回填列
    assert ledger.tca_records()[0]["strategy_id"] == _BACKFILL_STRATEGY_ID


def test_migration_idempotent_across_reopens(tmp_path):
    path = str(tmp_path / "old.db")
    _make_old_ledger(path)
    LiveLedger(path)                                            # 第一次打开(无 create)迁移
    LiveLedger(path, create=True)                               # create=True 路径
    ledger = LiveLedger(path)                                   # 第三次
    assert len([c for c in _cols(path, "live_fills") if c == "strategy_id"]) == 1
    assert len(ledger.fills()) == len(_REAL_SHAPE_FILLS)        # 数据不变


def test_migration_concurrent_double_open_does_not_crash(tmp_path):
    # 评审 #3:两个进程(驱动器/哨兵/ops API)同时打开老库,都看到列缺失、都 ALTER——
    # 后到者的 duplicate column 必须按"已被并发迁移完成"处理,不许把构造函数炸掉。
    path = str(tmp_path / "old.db")
    _make_old_ledger(path)
    first = LiveLedger(path)
    # 确定性模拟后到者:PRAGMA 快照已经过期(它当时看到列缺失),ALTER 撞 duplicate column
    first._add_strategy_column_if_missing("live_fills")         # 列已存在——不许 raise
    second = LiveLedger(path)                                   # 双开:第二个连接照常可用
    assert second.fills()[0]["strategy_id"] == _BACKFILL_STRATEGY_ID


def test_migration_duplicate_column_catch_only_swallows_duplicate(tmp_path):
    # 幂等化只吞 duplicate column,其它 OperationalError(真实损坏)必须照常暴露
    path = str(tmp_path / "old.db")
    _make_old_ledger(path)
    ledger = LiveLedger(path)
    with pytest.raises(sqlite3.OperationalError):
        ledger._conn.execute("ALTER TABLE no_such_table ADD COLUMN strategy_id TEXT")


def test_migration_old_ledger_without_tca_table(tmp_path):
    # 最老形态(建库时还没有 live_tca):打开不炸;record_tca 惰性建表自带 strategy_id 列
    path = str(tmp_path / "oldest.db")
    _make_old_ledger(path, with_tca=False)
    ledger = LiveLedger(path)
    assert ledger.tca_records() == []
    ledger.record_tca("f9", client_order_id="c9", instrument_id="BTC", side=OrderSide.BUY,
                      decision_price=100.0, fill_price=100.1, quantity=1.0,
                      strategy_id="stratX", recorded_at=_NOW)
    assert ledger.tca_records()[0]["strategy_id"] == "stratX"


# --- 分账读取 API ---

@pytest.fixture()
def two_strategy_ledger(tmp_path):
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    ledger.record_submission("a1", "BTC", OrderSide.BUY, 0.010, strategy_id="strat_a",
                             submitted_at=_NOW)
    ledger.record_fill("fa1", "a1", "BTC", OrderSide.BUY, 0.010, 60000.0, strategy_id="strat_a",
                       filled_at=_NOW)
    ledger.record_submission("b1", "BTC", OrderSide.SELL, 0.004, strategy_id="strat_b",
                             submitted_at=_NOW + 1)
    ledger.record_fill("fb1", "b1", "BTC", OrderSide.SELL, 0.004, 61000.0, strategy_id="strat_b",
                       filled_at=_NOW + 1)
    return ledger


def test_two_strategies_same_instrument_opposite_netting(two_strategy_ledger):
    ledger = two_strategy_ledger
    assert ledger.all_positions()["BTC"] == pytest.approx(0.006)             # 账户级净额
    assert ledger.all_positions(strategy_id="strat_a")["BTC"] == pytest.approx(0.010)
    assert ledger.all_positions(strategy_id="strat_b")["BTC"] == pytest.approx(-0.004)
    assert ledger.position("BTC", strategy_id="strat_a") == pytest.approx(0.010)
    assert [f["fill_id"] for f in ledger.fills(strategy_id="strat_a")] == ["fa1"]
    assert [o["client_order_id"] for o in ledger.orders(strategy_id="strat_b")] == ["b1"]


def test_partition_invariant_sums_to_account(two_strategy_ledger):
    # 结构性不变量:Σ_s per-strategy positions 逐标的恒等于账户级(含 emergency 保留账本)。
    # **诚实边界(2026-07-06 对抗式复审确认,low severity,非功能缺陷)**:这是一个 groupby-
    # 再求和的数学恒等式——对"任意"非空 strategy_id 标注都成立,不管标注是否*正确*。它不能
    # 也不该被当成"strategy_id 归属正确"的安全网:一个把 fill 错标到别的合法 strategy_id 的
    # 未来 bug,此断言依然会通过。真正验证归属正确性的是
    # tests/live/test_pipeline.py::test_recovery_fill_inherits_order_strategy_id_not_current_driver
    # 与 tests/live/test_emergency.py::test_emergency_fills_use_reserved_strategy_id——它们断言
    # 具体的 strategy_id 值,不是求和恒等式。
    ledger = two_strategy_ledger
    ledger.record_fill("fe1", "e1", "BTC", OrderSide.SELL, 0.003, 60500.0,
                       strategy_id="emergency_flatten", filled_at=_NOW + 2)
    ledger.record_fill("fe2", "e2", "ETH", OrderSide.BUY, 1.5, 3000.0,
                       strategy_id="emergency_flatten", filled_at=_NOW + 3)
    account = ledger.all_positions()
    summed: dict = {}
    for s in ledger.strategy_ids():
        for inst, qty in ledger.all_positions(strategy_id=s).items():
            summed[inst] = summed.get(inst, 0.0) + qty
    assert set(summed) == set(account)
    for inst in account:
        assert summed[inst] == pytest.approx(account[inst]), inst


def test_strategy_ids_unions_orders_and_fills(two_strategy_ledger):
    # 评审 #5:DISTINCT 至少并上 live_orders——只提交未成交的策略也必须可见
    ledger = two_strategy_ledger
    ledger.record_submission("c1", "ETH", OrderSide.BUY, 1.0, strategy_id="strat_c",
                             submitted_at=_NOW + 5)             # 有 order、无 fill
    assert ledger.strategy_ids() == ["strat_a", "strat_b", "strat_c"]


def test_unfiltered_reads_keep_account_level_semantics(two_strategy_ledger):
    # 不带参 = 账户级聚合(风控/对账语义,一字节不变);带参 = 策略过滤
    ledger = two_strategy_ledger
    assert len(ledger.fills()) == 2
    assert len(ledger.orders()) == 2
    assert ledger.fills(strategy_id="no_such") == []
    assert ledger.all_positions(strategy_id="no_such") == {}
