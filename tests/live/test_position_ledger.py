"""LiveLedger(live/execution/position_ledger.py):append-only 本地仓位账本。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.5。镜像 ForwardLedger/
PaperTrackStore 的 append-only 模式(同一文件多表共存,幂等 INSERT OR IGNORE)。"""
import pytest

from live.execution.position_ledger import LiveLedger, LiveLedgerError
from quantos.contracts.trading import OrderSide


@pytest.fixture()
def ledger(tmp_path):
    return LiveLedger(str(tmp_path / "live.db"), create=True)


def test_missing_db_fails_closed(tmp_path):
    with pytest.raises(LiveLedgerError):
        LiveLedger(str(tmp_path / "nonexistent.db"), create=False)


def test_record_submission_is_idempotent(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, submitted_at=1000)
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, submitted_at=1000)   # 重复提交
    orders = ledger.orders()
    assert len(orders) == 1


def test_update_status(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, submitted_at=1000)
    ledger.update_status("c1", "FILLED", venue_order_id="999")
    o = ledger.orders()[0]
    assert o["status"] == "FILLED" and o["venue_order_id"] == "999"


def test_record_fill_folds_into_position_buy_positive_sell_negative(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, submitted_at=1000)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 0.01, 60000.0, filled_at=1001)
    ledger.record_submission("c2", "BTC", OrderSide.SELL, 0.004, submitted_at=2000)
    ledger.record_fill("f2", "c2", "BTC", OrderSide.SELL, 0.004, 61000.0, filled_at=2001)
    assert ledger.position("BTC") == pytest.approx(0.006)


def test_record_fill_is_idempotent(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, submitted_at=1000)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 0.01, 60000.0, filled_at=1001)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 0.01, 60000.0, filled_at=1001)   # 重复回报
    assert ledger.position("BTC") == pytest.approx(0.01)


def test_position_zero_when_no_fills(ledger):
    assert ledger.position("GHOST") == 0.0


def test_all_positions_folds_across_instruments(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.01, submitted_at=1000)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 0.01, 60000.0, filled_at=1001)
    ledger.record_submission("c2", "ETH", OrderSide.SELL, 1.5, submitted_at=1000)
    ledger.record_fill("f2", "c2", "ETH", OrderSide.SELL, 1.5, 3000.0, filled_at=1001)
    assert ledger.all_positions() == {"BTC": pytest.approx(0.01), "ETH": pytest.approx(-1.5)}


def test_coexists_multiple_ledgers_same_file(tmp_path):
    path = str(tmp_path / "shared.db")
    l1 = LiveLedger(path, create=True)
    l1.record_submission("c1", "BTC", OrderSide.BUY, 0.01, submitted_at=1000)
    l2 = LiveLedger(path, create=False)          # 第二个连接,同一文件,不需要 create
    assert len(l2.orders()) == 1
