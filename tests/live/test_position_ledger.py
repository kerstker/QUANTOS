"""LiveLedger(live/execution/position_ledger.py):append-only 本地仓位账本。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.5。镜像 ForwardLedger/
PaperTrackStore 的 append-only 模式(同一文件多表共存,幂等 INSERT OR IGNORE)。
order_type/limit_price 惰性迁移:docs/superpowers/specs/2026-07-06-limit-order-execution-design.md §6。"""
import sqlite3

import pytest

from live.execution.position_ledger import LiveLedger, LiveLedgerError
from quantos.contracts.trading import OrderSide


@pytest.fixture()
def ledger(tmp_path):
    return LiveLedger(str(tmp_path / "live.db"), create=True)


def test_missing_db_fails_closed(tmp_path):
    with pytest.raises(LiveLedgerError):
        LiveLedger(str(tmp_path / "nonexistent.db"), create=False)


def test_close_releases_live_ledger_connection(tmp_path):
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    ledger.close()
    ledger.close()
    with pytest.raises(sqlite3.ProgrammingError):
        ledger.orders()


def test_record_submission_is_idempotent(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, strategy_id="crowd_contra", submitted_at=1000)
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, strategy_id="crowd_contra", submitted_at=1000)   # 重复提交
    orders = ledger.orders()
    assert len(orders) == 1


def test_update_status(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, strategy_id="crowd_contra", submitted_at=1000)
    ledger.update_status("c1", "FILLED", venue_order_id="999")
    o = ledger.orders()[0]
    assert o["status"] == "FILLED" and o["venue_order_id"] == "999"


def test_record_fill_folds_into_position_buy_positive_sell_negative(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, strategy_id="crowd_contra", submitted_at=1000)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 0.01, 60000.0, strategy_id="crowd_contra", filled_at=1001)
    ledger.record_submission("c2", "BTC", OrderSide.SELL, 0.004, strategy_id="crowd_contra", submitted_at=2000)
    ledger.record_fill("f2", "c2", "BTC", OrderSide.SELL, 0.004, 61000.0, strategy_id="crowd_contra", filled_at=2001)
    assert ledger.position("BTC") == pytest.approx(0.006)


def test_record_fill_is_idempotent(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, strategy_id="crowd_contra", submitted_at=1000)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 0.01, 60000.0, strategy_id="crowd_contra", filled_at=1001)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 0.01, 60000.0, strategy_id="crowd_contra", filled_at=1001)   # 重复回报
    assert ledger.position("BTC") == pytest.approx(0.01)


def test_position_zero_when_no_fills(ledger):
    assert ledger.position("GHOST") == 0.0


def test_all_positions_folds_across_instruments(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, strategy_id="crowd_contra", submitted_at=1000)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 0.01, 60000.0, strategy_id="crowd_contra", filled_at=1001)
    ledger.record_submission("c2", "ETH", OrderSide.SELL, 1.5, strategy_id="crowd_contra", submitted_at=1000)
    ledger.record_fill("f2", "c2", "ETH", OrderSide.SELL, 1.5, 3000.0, strategy_id="crowd_contra", filled_at=1001)
    assert ledger.all_positions() == {"BTC": pytest.approx(0.01), "ETH": pytest.approx(-1.5)}


def test_coexists_multiple_ledgers_same_file(tmp_path):
    path = str(tmp_path / "shared.db")
    l1 = LiveLedger(path, create=True)
    l1.record_submission("c1", "BTC", OrderSide.BUY, 0.01, strategy_id="crowd_contra", submitted_at=1000)
    l2 = LiveLedger(path, create=False)          # 第二个连接,同一文件,不需要 create
    assert len(l2.orders()) == 1


# --- order_type/limit_price(2026-07-06 战线②):双轨迁移——_SCHEMA(新库/preview 草稿账本)
# --- + 惰性 ALTER(存量真实测试网账本),批判必须修改 #7 ---

def _cols(path, table):
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


# 战线③(2026-07-06)之后、本战线之前的真实存量 schema:已有 strategy_id,尚无
# order_type/limit_price——模拟真实测试网账本升级前的样子。
_PRE_LIMIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_orders (
  client_order_id TEXT PRIMARY KEY,
  instrument_id   TEXT NOT NULL,
  side            TEXT NOT NULL,
  quantity        REAL NOT NULL,
  submitted_at    INTEGER NOT NULL,
  venue_order_id  TEXT,
  status          TEXT NOT NULL DEFAULT 'NEW',
  strategy_id     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_fills (
  fill_id         TEXT PRIMARY KEY,
  client_order_id TEXT NOT NULL,
  instrument_id   TEXT NOT NULL,
  side            TEXT NOT NULL,
  quantity        REAL NOT NULL,
  price           REAL NOT NULL,
  filled_at       INTEGER NOT NULL,
  strategy_id     TEXT NOT NULL
);
"""


def _make_pre_limit_ledger(path):
    conn = sqlite3.connect(path)
    conn.executescript(_PRE_LIMIT_SCHEMA)
    conn.execute(
        "INSERT INTO live_orders(client_order_id, instrument_id, side, quantity, submitted_at,"
        " venue_order_id, status, strategy_id) VALUES (?,?,?,?,?,?,?,?)",
        ("c-old", "BTC", "BUY", 0.002, 1783147031274, "v1", "FILLED", "crowd_contra"))
    conn.commit()
    conn.close()


def test_new_ledger_schema_has_order_type_and_limit_price_columns(tmp_path):
    # _SCHEMA 那一半(新库/preview 一次性草稿账本 create=True 路径)直接带列,不依赖 ALTER
    path = str(tmp_path / "new.db")
    LiveLedger(path, create=True)
    cols = _cols(path, "live_orders")
    assert {"order_type", "limit_price"} <= cols


def test_record_submission_defaults_to_market_order_type_and_null_limit_price(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, strategy_id="crowd_contra",
                             submitted_at=1000)
    row = ledger.orders()[0]
    assert row["order_type"] == "MARKET" and row["limit_price"] is None


def test_record_submission_stores_limit_order_type_and_price(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, strategy_id="crowd_contra",
                             submitted_at=1000, order_type="LIMIT", limit_price=60123.4)
    row = ledger.orders()[0]
    assert row["order_type"] == "LIMIT" and row["limit_price"] == pytest.approx(60123.4)


def test_migration_old_ledger_without_order_type_columns_opens_and_preserves_data(tmp_path):
    # 惰性 ALTER 那一半(存量真实测试网账本):打开不炸,存量行读回 order_type/limit_price
    # 为 NULL(诚实——当时只有 MARKET,没有回填假值)。
    path = str(tmp_path / "old.db")
    _make_pre_limit_ledger(path)
    ledger = LiveLedger(path)                    # 无 create,镜像 ops API/gonogo 的打开方式
    cols = _cols(path, "live_orders")
    assert {"order_type", "limit_price"} <= cols
    row = ledger.orders()[0]
    assert row["client_order_id"] == "c-old"
    assert row["order_type"] is None and row["limit_price"] is None
    assert ledger.position("BTC") == 0.0          # 未受影响的既有数据路径(无 fill)


def test_migration_order_type_columns_idempotent_across_reopens(tmp_path):
    path = str(tmp_path / "old.db")
    _make_pre_limit_ledger(path)
    LiveLedger(path)                              # 第一次打开(无 create)迁移
    LiveLedger(path, create=True)                 # create=True 路径
    ledger = LiveLedger(path)                      # 第三次
    assert len([c for c in _cols(path, "live_orders") if c == "order_type"]) == 1
    assert len(ledger.orders()) == 1               # 数据不变


def test_migration_order_type_columns_concurrent_double_open_does_not_crash(tmp_path):
    # 两个进程同时打开老库都看到列缺失、都 ALTER——后到者的 duplicate column 必须按
    # "已被并发迁移完成"处理,不许把构造函数炸掉(镜像 strategy_id 迁移的同款并发防御)。
    path = str(tmp_path / "old.db")
    _make_pre_limit_ledger(path)
    first = LiveLedger(path)
    first._add_order_type_columns_if_missing()    # 列已存在——不许 raise
    second = LiveLedger(path)                      # 双开:第二个连接照常可用
    assert second.orders()[0]["client_order_id"] == "c-old"


def test_new_writes_after_migration_get_real_values_not_backfill(tmp_path):
    # 迁移后的新写入必须显式带值(MARKET 默认/LIMIT 真实价),不是 SQL 端 DEFAULT 回填
    # (与 strategy_id 的"存量回填 vs 新写入必须显式"同一纪律)。
    path = str(tmp_path / "old.db")
    _make_pre_limit_ledger(path)
    ledger = LiveLedger(path)
    ledger.record_submission("c-new", "ETH", OrderSide.SELL, 1.0, strategy_id="crowd_contra",
                             submitted_at=2000, order_type="LIMIT", limit_price=3000.5)
    rows = {r["client_order_id"]: r for r in ledger.orders()}
    assert rows["c-old"]["order_type"] is None            # 存量行仍然 NULL
    assert rows["c-new"]["order_type"] == "LIMIT"
    assert rows["c-new"]["limit_price"] == pytest.approx(3000.5)
