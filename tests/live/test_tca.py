"""live/execution/tca.py —— 成交质量分析(TCA,2026-07-05)。设计:
docs/engineering/specs/2026-07-05-institutional-live-stack-design.md §③。

每笔确认成交记录"决策价 vs 实际成交均价"的带方向滑点(bps,买贵/卖贱为正=成本),
append-only 进账本 tca 表;纯函数汇总对照回测的成本假设(10bps)——"执行现实校验回测假设"
闭环的最后一块。
"""
import pytest

from live.contracts import VenueOrderResult
from live.execution.position_ledger import LiveLedger, LiveLedgerConflictError
from live.execution.tca import slippage_bps, tca_summary
from live.governance.mandate import AUTO
from quantos.contracts.trading import OrderSide
from services.monitoring.audit_log import GlobalAuditLog

from .test_pipeline import (_PRICES, _FakeVenue, _WIDE_MANDATE, _ctx_factory, _gate,
                            submit_live_batch)

_NOW = 1_700_000_000_000


@pytest.fixture()
def ledger(tmp_path):
    return LiveLedger(str(tmp_path / "live.db"), create=True)


# --- slippage_bps:带方向的滑点(纯函数) ---

def test_buy_above_decision_price_is_positive_cost():
    # 买单成交价高于决策价 = 付出了成本
    assert slippage_bps(OrderSide.BUY, decision_price=100.0, fill_price=100.1) == pytest.approx(10.0)


def test_sell_below_decision_price_is_positive_cost():
    # 卖单成交价低于决策价 = 付出了成本
    assert slippage_bps(OrderSide.SELL, decision_price=100.0, fill_price=99.9) == pytest.approx(10.0)


def test_price_improvement_is_negative():
    assert slippage_bps(OrderSide.BUY, decision_price=100.0, fill_price=99.9) == pytest.approx(-10.0)
    assert slippage_bps(OrderSide.SELL, decision_price=100.0, fill_price=100.1) == pytest.approx(-10.0)


def test_invalid_prices_return_none():
    # 决策价/成交价非正 -> 无法计算,诚实 None(不硬凑 0——0 是"零滑点"的真实断言)
    assert slippage_bps(OrderSide.BUY, decision_price=0.0, fill_price=100.0) is None
    assert slippage_bps(OrderSide.BUY, decision_price=100.0, fill_price=0.0) is None
    assert slippage_bps(OrderSide.BUY, decision_price=-1.0, fill_price=100.0) is None


# --- 账本 tca 表:append-only 读写 ---

def test_record_tca_round_trip_and_idempotent(ledger):
    ledger.record_tca("f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.BUY,
                      decision_price=60000.0, fill_price=60060.0, quantity=0.01,
                      strategy_id="crowd_contra", recorded_at=_NOW)
    ledger.record_tca("f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.BUY,
                      decision_price=60000.0, fill_price=60060.0, quantity=0.01,
                      strategy_id="crowd_contra", recorded_at=_NOW + 1)
    rows = ledger.tca_records()
    assert len(rows) == 1
    assert rows[0]["fill_id"] == "f1"
    assert rows[0]["decision_price"] == pytest.approx(60000.0)
    assert rows[0]["fill_price"] == pytest.approx(60060.0)
    assert rows[0]["slippage_bps"] == pytest.approx(10.0)      # (60060-60000)/60000*1e4


def test_record_tca_same_fill_id_different_payload_raises(ledger):
    ledger.record_tca(
        "f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.BUY,
        decision_price=60000.0, fill_price=60060.0, quantity=0.01,
        strategy_id="crowd_contra", recorded_at=_NOW,
    )
    with pytest.raises(LiveLedgerConflictError, match="TCA fill_id collision"):
        ledger.record_tca(
            "f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.BUY,
            decision_price=1.0, fill_price=2.0, quantity=9.9,
            strategy_id="crowd_contra", recorded_at=_NOW + 1,
        )


def test_tca_records_on_legacy_ledger_without_table_is_empty_and_lazy_creates(tmp_path):
    # 老账本(建表时还没有 tca 表)必须能平滑迁移:读 -> 空(不炸),写 -> 惰性建表
    import sqlite3
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE live_orders (client_order_id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL,
        side TEXT NOT NULL, quantity REAL NOT NULL, submitted_at INTEGER NOT NULL,
        venue_order_id TEXT, status TEXT NOT NULL DEFAULT 'NEW');
      CREATE TABLE live_fills (fill_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL,
        instrument_id TEXT NOT NULL, side TEXT NOT NULL, quantity REAL NOT NULL,
        price REAL NOT NULL, filled_at INTEGER NOT NULL);
    """)
    conn.commit()
    conn.close()
    legacy = LiveLedger(path)                                   # create=False,老库原样打开
    assert legacy.tca_records() == []
    legacy.record_tca("f1", client_order_id="c1", instrument_id="BTC", side=OrderSide.SELL,
                      decision_price=100.0, fill_price=99.0, quantity=1.0,
                      strategy_id="crowd_contra", recorded_at=_NOW)
    assert len(legacy.tca_records()) == 1


# --- tca_summary:汇总(纯函数) ---

def test_summary_aggregates_and_compares_to_assumed_cost():
    records = [
        {"instrument_id": "BTC", "slippage_bps": 10.0, "decision_price": 100.0,
         "fill_price": 100.1, "quantity": 2.0},                 # 名义额 ~200.2
        {"instrument_id": "ETH", "slippage_bps": -5.0, "decision_price": 50.0,
         "fill_price": 49.975, "quantity": 4.0},                # 名义额 ~199.9
    ]
    s = tca_summary(records, assumed_cost_bps=10.0)
    assert s["n_fills"] == 2
    assert s["mean_slippage_bps"] == pytest.approx(2.5)
    assert s["median_slippage_bps"] == pytest.approx(2.5)
    assert s["worst_slippage_bps"] == pytest.approx(10.0)
    # 名义额加权 ≈ (10*200.2 + (-5)*199.9) / 400.1 ≈ 2.506
    assert s["notional_weighted_slippage_bps"] == pytest.approx(
        (10.0 * 200.2 - 5.0 * 199.9) / (200.2 + 199.9), rel=1e-6)
    assert s["assumed_cost_bps"] == 10.0
    assert set(s["by_instrument"]) == {"BTC", "ETH"}
    assert s["by_instrument"]["BTC"]["mean_slippage_bps"] == pytest.approx(10.0)
    assert s["by_instrument"]["BTC"]["n_fills"] == 1


def test_summary_empty_records_is_honest():
    s = tca_summary([], assumed_cost_bps=10.0)
    assert s["n_fills"] == 0
    assert s["mean_slippage_bps"] is None
    assert s["notional_weighted_slippage_bps"] is None
    assert s["by_instrument"] == {}


# --- 流水线集成:确认成交后自动落 TCA ---

class _SlippageVenue(_FakeVenue):
    """成交价比决策价差 10bps 的 venue(买贵卖贱)——模拟真实市场冲击。"""

    def place_order(self, order):
        ack = super().place_order(order)
        cur = self._orders[order.client_order_id]
        if cur.status == "FILLED":
            worse = 1.0001 if order.side == OrderSide.BUY else 0.9999
            self._orders[order.client_order_id] = VenueOrderResult(
                venue_order_id=cur.venue_order_id, client_order_id=cur.client_order_id,
                instrument_id=cur.instrument_id, status="FILLED", filled_qty=cur.filled_qty,
                avg_price=_PRICES[order.instrument_id] * worse)
        return ack


def test_pipeline_records_tca_for_confirmed_fills(ledger):
    venue = _SlippageVenue()
    result = submit_live_batch(
        weights={"BTC": 0.5, "ETH": -0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=GlobalAuditLog(), strategy_id="crowd_contra",
        decided_ts=_NOW, now=_NOW)
    assert result.tier == AUTO and len(result.submitted) == 2
    rows = ledger.tca_records()
    assert len(rows) == 2
    by_inst = {r["instrument_id"]: r for r in rows}
    assert by_inst["BTC"]["slippage_bps"] == pytest.approx(1.0, abs=0.01)   # 买贵 1bp
    assert by_inst["ETH"]["slippage_bps"] == pytest.approx(1.0, abs=0.01)   # 卖贱 1bp
    assert by_inst["BTC"]["decision_price"] == pytest.approx(_PRICES["BTC"])


def test_pipeline_tca_write_failure_does_not_break_fill_booking(ledger):
    class _TcaBreaks(LiveLedger):
        def record_tca(self, *a, **kw):
            raise RuntimeError("simulated tca write failure")

    broken = _TcaBreaks(ledger_path_for(ledger), create=True)
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=broken, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert len(result.submitted) == 1
    assert result.ledger_write_errors == ()                    # 成交记账不受 TCA 写失败影响
    assert broken.position("BTC") == pytest.approx(5.0)
    assert "LIVE_TCA_WRITE_FAILED" in [e.action for e in audit.events]


def ledger_path_for(ledger):
    # sqlite 连接的数据库文件路径(仅测试用)
    row = ledger._conn.execute("PRAGMA database_list").fetchone()
    return row["file"]
