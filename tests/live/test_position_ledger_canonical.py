"""LiveLedger 的 canonical OrderState 接线(2026-07-23 Phase 3 OMS):canonical_state 列、
快照转换校验、惰性迁移、幂等语义零回归。被测实现:live/execution/position_ledger.py +
live/execution/oms_state.py。"""
import sqlite3

import pytest

from live.execution.position_ledger import (LiveLedger, LiveLedgerConflictError)
from quantos.contracts.order import InvalidOrderTransition, OrderState
from quantos.contracts.trading import OrderSide


@pytest.fixture()
def ledger(tmp_path):
    return LiveLedger(str(tmp_path / "live.db"), create=True)


def _submit(ledger, cid, **kw):
    kw.setdefault("strategy_id", "crowd_contra")
    kw.setdefault("submitted_at", 1000)
    ledger.record_submission(cid, "BTC", OrderSide.BUY, 0.01, **kw)


def _row(ledger, cid="c1"):
    return {r["client_order_id"]: r for r in ledger.orders()}[cid]


# --- 全生命周期:canonical_state 逐步写对,orders() 读路径可加性暴露 ---

def test_canonical_state_tracked_across_full_lifecycle(ledger):
    _submit(ledger, "c1", status="PENDING_NEW")
    assert _row(ledger)["canonical_state"] == OrderState.RISK_APPROVED.value
    ledger.update_status("c1", "NEW", venue_order_id="v1")
    assert _row(ledger)["canonical_state"] == OrderState.SUBMITTED.value
    ledger.update_status("c1", "PARTIALLY_FILLED")
    assert _row(ledger)["canonical_state"] == OrderState.PARTIALLY_FILLED.value
    ledger.update_status("c1", "FILLED")
    row = _row(ledger)
    assert row["status"] == "FILLED"                       # raw venue 状态原样保留
    assert row["canonical_state"] == OrderState.FILLED.value
    assert row["venue_order_id"] == "v1"                   # 既有字段语义一字节不变


def test_record_submission_default_new_maps_to_submitted(ledger):
    _submit(ledger, "c1")
    assert _row(ledger)["canonical_state"] == OrderState.SUBMITTED.value


def test_fresh_insert_with_terminal_status_is_allowed(ledger):
    # 崩溃恢复/补记路径:订单第一次落库就是终态,没有"前一状态"可校验,必须能记。
    _submit(ledger, "c1", status="FILLED")
    _submit(ledger, "c2", status="CANCELED")
    assert _row(ledger)["canonical_state"] == OrderState.FILLED.value
    assert _row(ledger, "c2")["canonical_state"] == OrderState.CANCELLED.value


# --- 非法 canonical 转换:拒绝且不改库 ---

def test_terminal_rewrite_filled_to_new_rejected(ledger):
    _submit(ledger, "c1", status="PENDING_NEW")
    ledger.update_status("c1", "FILLED")
    with pytest.raises(InvalidOrderTransition):
        ledger.update_status("c1", "NEW")
    row = _row(ledger)                                      # 拒绝必须不改库
    assert row["status"] == "FILLED"
    assert row["canonical_state"] == OrderState.FILLED.value


def test_terminal_rewrite_canceled_to_filled_rejected(ledger):
    _submit(ledger, "c1", status="NEW")
    ledger.update_status("c1", "CANCELED")
    with pytest.raises(InvalidOrderTransition):
        ledger.update_status("c1", "FILLED")


def test_partially_filled_cannot_regress_to_unknown(ledger):
    # venue 的诚实"我不知道"(UNKNOWN→SUBMITTED)不许覆盖已知的部分成交——fail-closed,
    # 留给下一轮崩溃恢复回查,而不是静默丢掉信息。
    _submit(ledger, "c1", status="NEW")
    ledger.update_status("c1", "PARTIALLY_FILLED")
    with pytest.raises(InvalidOrderTransition):
        ledger.update_status("c1", "UNKNOWN")


def test_new_to_pending_new_regression_rejected(ledger):
    _submit(ledger, "c1", status="PENDING_NEW")
    ledger.update_status("c1", "NEW")
    with pytest.raises(InvalidOrderTransition):
        ledger.update_status("c1", "PENDING_NEW")


# --- 幂等语义零回归 ---

def test_same_status_rewrite_is_noop_not_error(ledger):
    _submit(ledger, "c1", status="NEW")
    ledger.update_status("c1", "NEW")                       # 同态重写(轮询重复确认)
    ledger.update_status("c1", "FILLED")
    ledger.update_status("c1", "FILLED")                    # 终态同态重写也必须是 no-op
    assert _row(ledger)["canonical_state"] == OrderState.FILLED.value


def test_record_submission_replay_same_payload_noop(ledger):
    _submit(ledger, "c1", status="PENDING_NEW")
    _submit(ledger, "c1", status="FILLED")                  # status 不在 immutable payload 里
    rows = ledger.orders()
    assert len(rows) == 1
    assert rows[0]["status"] == "PENDING_NEW"               # 旧语义:重放 no-op,不炸
    assert rows[0]["canonical_state"] == OrderState.RISK_APPROVED.value


def test_record_submission_conflicting_payload_still_raises(ledger):
    _submit(ledger, "c1")
    with pytest.raises(LiveLedgerConflictError, match="client_order_id collision"):
        ledger.record_submission("c1", "BTC", OrderSide.SELL, 0.01,
                                 strategy_id="crowd_contra", submitted_at=2000)


# --- 未知 venue 状态 fail-closed ---

def test_record_submission_unknown_status_raises(ledger):
    with pytest.raises(ValueError, match="unknown venue order status"):
        _submit(ledger, "c1", status="SOME_FUTURE_VENUE_STATUS")
    assert ledger.orders() == []                            # 写前就拒,不落半个行


def test_update_status_unknown_status_raises_and_preserves_row(ledger):
    _submit(ledger, "c1", status="NEW")
    with pytest.raises(ValueError, match="unknown venue order status"):
        ledger.update_status("c1", "SOME_FUTURE_VENUE_STATUS")
    assert _row(ledger)["status"] == "NEW"


# --- 惰性迁移:迁移前的存量行(canonical_state NULL)行为正确 ---

# 复刻 test_position_ledger.py 的存量真实测试网账本 schema(有 strategy_id、无
# order_type/limit_price、无 canonical_state),模拟升级前的老库。
_PRE_CANONICAL_SCHEMA = """
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


def _make_pre_canonical_ledger(path):
    conn = sqlite3.connect(path)
    conn.executescript(_PRE_CANONICAL_SCHEMA)
    conn.execute(
        "INSERT INTO live_orders(client_order_id, instrument_id, side, quantity, submitted_at,"
        " venue_order_id, status, strategy_id) VALUES (?,?,?,?,?,?,?,?)",
        ("c-open", "BTC", "BUY", 0.002, 1783147031274, "v1", "NEW", "crowd_contra"))
    conn.execute(
        "INSERT INTO live_orders(client_order_id, instrument_id, side, quantity, submitted_at,"
        " venue_order_id, status, strategy_id) VALUES (?,?,?,?,?,?,?,?)",
        ("c-done", "ETH", "SELL", 1.0, 1783147031274, "v2", "FILLED", "crowd_contra"))
    conn.commit()
    conn.close()


def _cols(path, table):
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_new_ledger_schema_has_canonical_state_column(tmp_path):
    path = str(tmp_path / "new.db")
    LiveLedger(path, create=True)
    assert "canonical_state" in _cols(path, "live_orders")


def test_migration_adds_column_and_preserves_pre_migration_rows(tmp_path):
    path = str(tmp_path / "old.db")
    _make_pre_canonical_ledger(path)
    ledger = LiveLedger(path)                               # 无 create,镜像 ops API 的打开方式
    assert "canonical_state" in _cols(path, "live_orders")
    rows = {r["client_order_id"]: r for r in ledger.orders()}
    assert rows["c-open"]["canonical_state"] is None        # 存量行 NULL = 诚实,不回填
    assert rows["c-done"]["canonical_state"] is None
    assert rows["c-done"]["status"] == "FILLED"             # 数据零改动


def test_pre_migration_row_transitions_via_raw_status_fallback(tmp_path):
    path = str(tmp_path / "old.db")
    _make_pre_canonical_ledger(path)
    ledger = LiveLedger(path)
    ledger.update_status("c-open", "PARTIALLY_FILLED")      # NULL → 从 raw "NEW" 现推 SUBMITTED
    row = _row(ledger, "c-open")
    assert row["canonical_state"] == OrderState.PARTIALLY_FILLED.value
    ledger.update_status("c-open", "FILLED")                # 之后用已存的 canonical 值
    assert _row(ledger, "c-open")["canonical_state"] == OrderState.FILLED.value


def test_pre_migration_terminal_row_rewrite_still_rejected(tmp_path):
    path = str(tmp_path / "old.db")
    _make_pre_canonical_ledger(path)
    ledger = LiveLedger(path)
    with pytest.raises(InvalidOrderTransition):             # raw "FILLED" → FILLED 终态,改写拒绝
        ledger.update_status("c-done", "NEW")
    assert _row(ledger, "c-done")["status"] == "FILLED"


def test_migration_idempotent_across_reopens(tmp_path):
    path = str(tmp_path / "old.db")
    _make_pre_canonical_ledger(path)
    LiveLedger(path)
    LiveLedger(path, create=True)
    ledger = LiveLedger(path)
    assert len([c for c in _cols(path, "live_orders") if c == "canonical_state"]) == 1
    assert len(ledger.orders()) == 2                        # 数据不变


def test_migration_concurrent_double_open_does_not_crash(tmp_path):
    # 镜像 strategy_id/order_type 迁移的并发防御:两进程同时看到列缺失、同时 ALTER,
    # 后到者的 duplicate column 必须按"已被并发迁移完成"吞掉。
    path = str(tmp_path / "old.db")
    _make_pre_canonical_ledger(path)
    first = LiveLedger(path)
    first._add_column_if_missing("live_orders", "canonical_state", "TEXT")   # 列已存在——不许 raise
    second = LiveLedger(path)
    assert len(second.orders()) == 2


def test_new_writes_after_migration_get_real_canonical_not_null(tmp_path):
    path = str(tmp_path / "old.db")
    _make_pre_canonical_ledger(path)
    ledger = LiveLedger(path)
    _submit(ledger, "c-new", status="PENDING_NEW")
    rows = {r["client_order_id"]: r for r in ledger.orders()}
    assert rows["c-open"]["canonical_state"] is None        # 存量行仍然 NULL
    assert rows["c-new"]["canonical_state"] == OrderState.RISK_APPROVED.value
