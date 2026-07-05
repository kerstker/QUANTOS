"""live/governance/review_queue.py —— NEEDS_HUMAN_REVIEW 持久化队列(2026-07-05,机构审计
P1:"标记 NEEDS_HUMAN_REVIEW 后无人能操作")。

背景:mandate 判 NEEDS_HUMAN_REVIEW 后,`result.pending_review` 只被拿去 `len()` 计数,
具体是哪些订单、为什么被拦、从未持久化——进程一退出就彻底丢失,人类除了编辑全局 mandate
文件这把钝器之外无处可看、无处可管。本模块只做**持久化 + 记录人工决定**(append-only,
镜像既有 ledger/audit 的 sqlite 模式)——**不做**"批准后自动重新提交"(那会绕过 mandate 对
这批订单的新鲜度前提,是需要独立设计的后续工作,诚实标注非本次范围)。
"""
import pytest

from live.governance.review_queue import ReviewQueue
from live.contracts import LiveOrderRequest
from quantos.contracts.trading import OrderSide

_NOW = 1_700_000_000_000


@pytest.fixture()
def queue(tmp_path):
    return ReviewQueue(str(tmp_path / "review.db"), create=True)


def _order(inst="BTC", side=OrderSide.BUY, qty=0.01, cid="c1"):
    return LiveOrderRequest(instrument_id=inst, side=side, quantity=qty, client_order_id=cid)


def test_record_batch_then_list_pending(queue):
    queue.record_batch("binance", decided_ts=_NOW, orders=[_order()],
                       reasons=("single_order_notional_exceeded:BTC",), recorded_at=_NOW)
    pending = queue.pending()
    assert len(pending) == 1
    assert pending[0]["venue"] == "binance"
    assert pending[0]["instrument_id"] == "BTC"
    assert pending[0]["status"] == "PENDING"
    assert "single_order_notional_exceeded" in pending[0]["reasons"]


def test_record_batch_persists_one_row_per_order(queue):
    queue.record_batch("binance", decided_ts=_NOW, orders=[_order(inst="BTC"), _order(inst="ETH", cid="c2")],
                       reasons=("gross_notional_exceeded",), recorded_at=_NOW)
    assert len(queue.pending()) == 2


def test_mark_reviewed_removes_from_pending_and_records_decision(queue):
    queue.record_batch("binance", decided_ts=_NOW, orders=[_order()], reasons=("x",), recorded_at=_NOW)
    row_id = queue.pending()[0]["id"]
    queue.mark_reviewed(row_id, status="APPROVED", reviewer="alice", at=_NOW + 1000, note="looks fine")
    assert queue.pending() == []
    all_rows = queue.all_records()
    assert all_rows[0]["status"] == "APPROVED"
    assert all_rows[0]["reviewer"] == "alice"
    assert all_rows[0]["note"] == "looks fine"


def test_mark_reviewed_rejects_invalid_status(queue):
    queue.record_batch("binance", decided_ts=_NOW, orders=[_order()], reasons=("x",), recorded_at=_NOW)
    row_id = queue.pending()[0]["id"]
    with pytest.raises(ValueError):
        queue.mark_reviewed(row_id, status="MAYBE", reviewer="alice", at=_NOW)


def test_idempotent_batch_recording_does_not_duplicate(queue):
    # 同一 (venue, decided_ts, client_order_id) 重复记录(如驱动器重试同一周期)不该重复入队
    o = _order()
    queue.record_batch("binance", decided_ts=_NOW, orders=[o], reasons=("x",), recorded_at=_NOW)
    queue.record_batch("binance", decided_ts=_NOW, orders=[o], reasons=("x",), recorded_at=_NOW + 500)
    assert len(queue.pending()) == 1


def test_pending_filters_by_venue_when_multiple(queue):
    queue.record_batch("binance", decided_ts=_NOW, orders=[_order(cid="c1")], reasons=("x",), recorded_at=_NOW)
    queue.record_batch("bybit_demo", decided_ts=_NOW, orders=[_order(cid="c2")], reasons=("y",), recorded_at=_NOW)
    assert len(queue.pending()) == 2
    assert len(queue.pending(venue="binance")) == 1
