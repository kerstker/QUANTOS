"""live/execution/slicer.py —— TWAP 切单(2026-07-05)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md §⑥。

诚实定位:$3-5k 周度调仓单子极小,现阶段默认不启用(`submit_live_batch(slicer=None)`);
这是为规模化预留的**已测试**机制,不是摆设代码。关键性质:
① 纯函数、确定性——子单 client_order_id = "{parent}-s{i}",幂等可恢复(重试同一母单产生
   同一批子单 ID,交易所天然去重);
② 子单数量之和 == 母单数量(不多不少,浮点容差内);
③ 子单逐个走完整确认流程(轮询终态 + 超时撤单),任一子单未确认成交即中止剩余子单
   (已成交的如实记账)——宁可少执行,不可盲目继续。
"""
import pytest

from live.contracts import LiveOrderRequest
from live.execution.position_ledger import LiveLedger
from live.execution.slicer import plan_slices
from live.governance.mandate import AUTO
from live.pipeline import submit_live_batch
from quantos.contracts.trading import OrderSide
from services.monitoring.audit_log import GlobalAuditLog

from test_pipeline import _PRICES, _FakeVenue, _WIDE_MANDATE, _ctx_factory, _gate

_NOW = 1_700_000_000_000


@pytest.fixture()
def ledger(tmp_path):
    return LiveLedger(str(tmp_path / "live.db"), create=True)


def _req(qty, inst="BTC", side=OrderSide.BUY, cid="parent1"):
    return LiveOrderRequest(instrument_id=inst, side=side, quantity=qty, client_order_id=cid)


# --- plan_slices:纯函数切分 ---

def test_equal_slices_preserve_total_quantity():
    slices = plan_slices(_req(1.0), n_slices=4)
    assert len(slices) == 4
    assert sum(s.quantity for s in slices) == pytest.approx(1.0)
    assert all(s.instrument_id == "BTC" and s.side == OrderSide.BUY for s in slices)


def test_slice_ids_are_deterministic_and_derived_from_parent():
    a = plan_slices(_req(1.0), n_slices=3)
    b = plan_slices(_req(1.0), n_slices=3)
    assert [s.client_order_id for s in a] == [s.client_order_id for s in b]   # 幂等重试同 ID
    assert [s.client_order_id for s in a] == ["parent1-s0", "parent1-s1", "parent1-s2"]


def test_max_child_notional_caps_each_slice():
    # 母单 $60000(1 BTC @ 60000),子单名义额上限 $25000 -> 至少 3 片,每片 <= 上限
    slices = plan_slices(_req(1.0), max_child_notional=25_000.0, price=60_000.0)
    assert len(slices) == 3
    for s in slices:
        assert s.quantity * 60_000.0 <= 25_000.0 + 1e-6
    assert sum(s.quantity for s in slices) == pytest.approx(1.0)


def test_single_slice_when_under_cap_returns_parent_as_is():
    slices = plan_slices(_req(0.01), max_child_notional=25_000.0, price=60_000.0)
    assert len(slices) == 1
    assert slices[0].quantity == pytest.approx(0.01)
    assert slices[0].client_order_id == "parent1"              # 不切就不改 ID,幂等语义不变


def test_reduce_only_flag_propagates_to_children():
    parent = LiveOrderRequest(instrument_id="BTC", side=OrderSide.SELL, quantity=1.0,
                              client_order_id="p", reduce_only=True)
    assert all(s.reduce_only for s in plan_slices(parent, n_slices=3))


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        plan_slices(_req(1.0), n_slices=0)
    with pytest.raises(ValueError):
        plan_slices(_req(1.0))                                 # 必须给 n_slices 或名义额上限
    with pytest.raises(ValueError):
        plan_slices(_req(1.0), max_child_notional=1000.0)      # 名义额上限模式必须给价格


# --- 流水线集成:slicer= 可选参数 ---

def test_pipeline_with_slicer_submits_children_and_books_all_fills(ledger):
    venue = _FakeVenue()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=GlobalAuditLog(), strategy_id="crowd_contra",
        decided_ts=_NOW, now=_NOW,
        slicer=lambda req: plan_slices(req, n_slices=4))
    assert result.tier == AUTO
    assert len(venue.calls) == 4                               # 4 个子单逐个提交
    assert len(result.submitted) == 4
    assert ledger.position("BTC") == pytest.approx(5.0)        # 总量不变(0.5*600000/60000)
    assert len({c.client_order_id for c in venue.calls}) == 4  # 确定性且互不相同


def test_pipeline_without_slicer_behavior_unchanged(ledger):
    venue = _FakeVenue()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=GlobalAuditLog(), strategy_id="crowd_contra",
        decided_ts=_NOW, now=_NOW)
    assert len(venue.calls) == 1                               # 默认不切,行为与之前完全一致


def test_slice_failure_aborts_remaining_slices_but_books_completed_ones(ledger):
    # 第 2 个子单起 venue 真失败 -> 已成交的第 1 片如实记账,剩余子单不再盲目提交
    class _FailsAfterFirst(_FakeVenue):
        def place_order(self, order):
            if len(self.calls) >= 1:
                self.calls.append(order)
                raise RuntimeError("venue down mid-slicing")
            return super().place_order(order)

        def get_order(self, instrument_id, client_order_id):
            # 失败的子单在交易所侧也查不到(真失败,不是响应丢失)
            r = super().get_order(instrument_id, client_order_id)
            return r

    venue = _FailsAfterFirst()
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW,
        slicer=lambda req: plan_slices(req, n_slices=4))
    assert len(result.submitted) == 1                          # 只有第 1 片成交
    assert len(result.submission_errors) == 1                  # 第 2 片真失败
    assert len(venue.calls) == 2                               # 第 3/4 片从未提交(中止)
    assert ledger.position("BTC") == pytest.approx(5.0 / 4)    # 已成交部分如实入账
    assert "LIVE_SLICING_ABORTED" in [e.action for e in audit.events]


def test_step_size_rounds_children_and_never_exceeds_parent_total():
    # 切分在交易所过滤器之后——子单必须自己落回步长网格,余量并入最后一片,总量绝不超过母单
    slices = plan_slices(_req(0.01), n_slices=3, step_size=0.001)
    assert all(abs(s.quantity / 0.001 - round(s.quantity / 0.001)) < 1e-9 for s in slices)
    assert sum(s.quantity for s in slices) <= 0.01 + 1e-12
    assert sum(s.quantity for s in slices) == pytest.approx(0.01)   # 0.003+0.003+0.004
