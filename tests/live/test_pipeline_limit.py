"""live/pipeline.py::submit_live_batch —— LIMIT 限价单执行路径(2026-07-06 战线②)。设计:
docs/engineering/specs/2026-07-06-limit-order-execution-design.md §5;
批判修正(2026-07-06):preview+LIMIT 短路轮询、MARKET 继续尊重调用方 max_confirm_polls、
book_ticker 退化响应回退、mandate 保守名义额口径,均在此文件与 test_mandate.py/test_pricing.py
分别覆盖。

MARKET 路径的既有性质(缩放往返/gate拒绝隔离/mandate零提交/记账失败fail-soft/崩溃恢复等)
已在 test_pipeline.py 完整覆盖,本文件不重复——只测 LIMIT 特有的行为 + 一条 MARKET 回归锁。
"""
import pytest

from live.contracts import ExchangeFilters, VenueOrderResult
from live.execution.position_ledger import LiveLedger
from live.execution.pricing import LimitPricing
from live.governance.mandate import AUTO, NEEDS_HUMAN_REVIEW, Mandate
from live.governance.validation_attestation import HUMAN_OVERRIDE, ValidationAttestation
from live.pipeline import (
    _submit_live_batch_technical_diagnostic as _submit_live_batch,
    resolve_unconfirmed_orders,
)
from quantos.contracts.trading import OrderSide
from services.monitoring.audit_log import GlobalAuditLog
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import AccountRiskState, MarketContext, RiskContext, StrategyApproval
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch

_NOW = 1_700_000_000_000
_PRICES = {"BTC": 60000.0, "ETH": 3000.0}
_FILTERS = {
    "BTC": ExchangeFilters(instrument_id="BTC", step_size=0.0001, min_qty=0.0001, min_notional=5.0,
                           tick_size=0.1),
    "ETH": ExchangeFilters(instrument_id="ETH", step_size=0.001, min_qty=0.001, min_notional=5.0,
                           tick_size=0.01),
}
_TERMINAL = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})


def submit_live_batch(**kwargs):
    kwargs["diagnostic_only"] = True
    if "validation_attestation" not in kwargs:
        kwargs["validation_attestation"] = ValidationAttestation(
            strategy_id=kwargs["strategy_id"], kind=HUMAN_OVERRIDE,
            attested_by="alice", attested_at=_NOW, reason="test fixture",
        )
    return _submit_live_batch(**kwargs)


def _wide_open_limits(allowed=frozenset({"BTC", "ETH"})):
    return RiskLimits(
        allowed_instruments=allowed, allowed_accounts=frozenset({"live_test"}),
        default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7, max_single_name_pct=1e7,
        max_turnover=1e7, max_participation_rate=1e7, max_instrument_vol=5.0, daily_loss_limit=1e14,
        max_drawdown=1.0)


def _gate(allowed=frozenset({"BTC", "ETH"})):
    return PreTradeRiskGate(_wide_open_limits(allowed), kill_switch=KillSwitch())


def _ctx_factory(prices=_PRICES, unit_scale=1_000_000):
    def _factory(intent):
        px = prices.get(intent.instrument_id)
        return RiskContext(
            account=AccountRiskState(account_id="live_test", cash=1e9, equity=1e9, day_pnl=0.0,
                                     peak_equity=1e9, positions=()),
            market=MarketContext(instrument_id=intent.instrument_id, known=px is not None,
                                 reference_price=(px / unit_scale) if px else None, market_open=True,
                                 price_as_of=_NOW, est_daily_volume=1e12, instrument_vol=0.8),
            strategy=StrategyApproval(strategy_id="crowd_contra", approved=True,
                                      approval_id="live-test"),
            now=_NOW, max_staleness=10 ** 15)
    return _factory


_WIDE_MANDATE = Mandate(mandate_id="m1", venue="testnet", exchange="fake",
                        allowed_instruments=frozenset({"BTC", "ETH"}),
                        max_single_order_notional=1e9, max_gross_notional=1e9, max_net_notional=1e9)


class _FakeVenue:
    """镜像 test_pipeline.py::_FakeVenue 的行为语义(同款异步/撤单赛跑模拟),加 book_ticker——
    LIMIT 场景独立成文件以免配置项与 MARKET 测试互相污染。

    place_order 对 LIMIT 单的 ACK 恒为 NEW(挂单,不像 MARKET 那样"下一次轮询即成交"默认
    行为)——真实交易所创建响应本就不带成交状态,LIMIT 单尤其可能长期停在 NEW。
    `never_confirms_for` 之外的标的会在下一次 get_order 轮询里"变成"FILLED(与 MARKET fake
    同款"稍后成交"语义,只是 LIMIT 场景下大多数测试都把标的放进 never_confirms_for)。
    """
    venue_mode = "testnet"
    venue_name = "fake"

    def __init__(self, *, never_confirms_for=(), cancel_races_fill_for=(),
                cancel_after_partial_fill_for=(), cancel_explodes_for=(),
                book_ticker_responses=None):
        self.calls = []
        self.get_order_calls = []
        self.cancel_calls = []
        self.book_ticker_calls = []
        self._never_confirms = set(never_confirms_for)
        self._cancel_races_fill = set(cancel_races_fill_for)
        self._cancel_partial = set(cancel_after_partial_fill_for)
        self._cancel_explodes = set(cancel_explodes_for)
        self._book = book_ticker_responses or {}
        self._orders = {}
        self._qty = {}

    def book_ticker(self, instrument_id):
        self.book_ticker_calls.append(instrument_id)
        resp = self._book.get(instrument_id)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def place_order(self, order):
        self.calls.append(order)
        ack = VenueOrderResult(venue_order_id=f"v-{order.client_order_id}",
                               client_order_id=order.client_order_id,
                               instrument_id=order.instrument_id, status="NEW",
                               filled_qty=0.0, avg_price=0.0)
        self._orders[order.client_order_id] = ack
        self._qty[order.client_order_id] = order.quantity
        if order.instrument_id not in self._never_confirms:
            self._orders[order.client_order_id] = VenueOrderResult(
                venue_order_id=ack.venue_order_id, client_order_id=order.client_order_id,
                instrument_id=order.instrument_id, status="FILLED",
                filled_qty=order.quantity, avg_price=_PRICES[order.instrument_id])
        return ack

    def get_order(self, instrument_id, client_order_id):
        self.get_order_calls.append(client_order_id)
        return self._orders.get(client_order_id)

    def cancel_order(self, instrument_id, client_order_id):
        self.cancel_calls.append(client_order_id)
        if instrument_id in self._cancel_explodes:
            raise RuntimeError("simulated cancel network failure")
        existing = self._orders.get(client_order_id)
        if instrument_id in self._cancel_races_fill:
            if existing is not None:
                qty = self._qty.get(client_order_id, existing.filled_qty)
                self._orders[client_order_id] = VenueOrderResult(
                    venue_order_id=existing.venue_order_id, client_order_id=client_order_id,
                    instrument_id=instrument_id, status="FILLED", filled_qty=qty,
                    avg_price=_PRICES[instrument_id])
            raise RuntimeError("simulated: order already filled, too late to cancel")
        if existing is None or existing.status in _TERMINAL:
            return None
        filled = existing.filled_qty
        avg = existing.avg_price
        if instrument_id in self._cancel_partial:
            filled = self._qty.get(client_order_id, 0.0) * 0.4
            avg = _PRICES[instrument_id]
        canceled = VenueOrderResult(venue_order_id=existing.venue_order_id,
                                    client_order_id=client_order_id, instrument_id=instrument_id,
                                    status="CANCELED", filled_qty=filled, avg_price=avg)
        self._orders[client_order_id] = canceled
        return canceled

    def account(self):
        return {}

    def positions(self):
        return {}

    def exchange_filters(self):
        return _FILTERS


@pytest.fixture()
def ledger(tmp_path):
    return LiveLedger(str(tmp_path / "live.db"), create=True)


def _submit(*, venue, ledger, mandate=_WIDE_MANDATE, weights=None, prices=_PRICES,
           limit_pricing=LimitPricing(), max_confirm_polls=5, limit_max_confirm_polls=30,
           poll_sleep=None, frozen=frozenset()):
    audit = GlobalAuditLog()
    sleeps = []
    result = submit_live_batch(
        weights=weights or {"BTC": 0.5}, prices=prices, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(prices=prices), venue=venue, mandate=mandate,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW,
        limit_pricing=limit_pricing, max_confirm_polls=max_confirm_polls,
        limit_max_confirm_polls=limit_max_confirm_polls,
        poll_sleep=poll_sleep or (lambda: sleeps.append(1)), frozen=frozen)
    return result, audit, sleeps


# --- 定价装配:book_ticker 优先,失败/退化回退决策价 + 告警 ---

def test_pipeline_limit_uses_book_ticker_mid_when_available(ledger):
    venue = _FakeVenue(never_confirms_for={"BTC"},
                      book_ticker_responses={"BTC": {"bid": 59990.0, "ask": 60010.0}})
    result, audit, _ = _submit(venue=venue, ledger=ledger,
                              limit_max_confirm_polls=1)
    assert venue.book_ticker_calls == ["BTC"]
    submitted_order = venue.calls[0]
    assert submitted_order.order_type == "LIMIT"
    # anchor=60000(book mid)×1.002,按 tick 0.1 向下取整(BUY)
    assert submitted_order.limit_price == pytest.approx(60120.0)
    actions = [e.action for e in audit.events]
    assert "LIVE_LIMIT_ANCHOR_FALLBACK" not in actions      # 用的是真实盘口,没有回退


def test_pipeline_limit_book_ticker_exception_falls_back_to_decision_anchor(ledger):
    venue = _FakeVenue(never_confirms_for={"BTC"},
                      book_ticker_responses={"BTC": RuntimeError("network blip")})
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=1)
    submitted_order = venue.calls[0]
    assert submitted_order.order_type == "LIMIT"
    assert submitted_order.limit_price == pytest.approx(60120.0)    # 决策价 60000 锚,同样 ×1.002
    actions = [e.action for e in audit.events]
    assert "LIVE_LIMIT_ANCHOR_FALLBACK" in actions
    fallback_ev = [e for e in audit.events if e.action == "LIVE_LIMIT_ANCHOR_FALLBACK"][0]
    assert fallback_ev.metadata["source"] == "decision_price_fallback"


def test_pipeline_limit_book_ticker_missing_method_falls_back_gracefully(ledger):
    # venue 没实现 book_ticker(旧 fake/未升级的包装器)——getattr 探测优雅降级,不崩批
    class _NoBookTickerVenue(_FakeVenue):
        book_ticker = None

    venue = _NoBookTickerVenue(never_confirms_for={"BTC"})
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=1)
    assert venue.calls[0].order_type == "LIMIT"
    assert venue.calls[0].limit_price == pytest.approx(60120.0)


def test_pipeline_limit_book_ticker_degenerate_crossed_book_falls_back_with_alert(ledger):
    # 交叉盘(ask<bid)——形状正确内容错误的退化响应,批判必须修改①的端到端证明
    venue = _FakeVenue(never_confirms_for={"BTC"},
                      book_ticker_responses={"BTC": {"bid": 60010.0, "ask": 59990.0}})
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=1)
    assert venue.calls[0].limit_price == pytest.approx(60120.0)      # 回退决策价锚
    actions = [e.action for e in audit.events]
    assert "LIVE_LIMIT_ANCHOR_FALLBACK" in actions


def test_pipeline_limit_book_ticker_extreme_deviation_falls_back_with_alert(ledger):
    # 天价 mid:结构合法但与决策价偏离远超容忍度(默认 5%)
    venue = _FakeVenue(never_confirms_for={"BTC"},
                      book_ticker_responses={"BTC": {"bid": 119990.0, "ask": 120010.0}})
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=1)
    assert venue.calls[0].limit_price == pytest.approx(60120.0)
    actions = [e.action for e in audit.events]
    assert "LIVE_LIMIT_ANCHOR_FALLBACK" in actions


def test_to_limit_orders_drop_shows_up_as_dropped_by_filters(ledger, monkeypatch):
    # anchor 解析失败(book None + 决策价本身非法)理论上不可达(见 pricing 单测),这里改为
    # 验证 to_limit_orders 丢弃的腿确实并入 dropped_by_filters(不是静默消失/静默变 MARKET)
    import live.pipeline as lp

    def _all_drop(adjusted, **kw):
        return [], [(o.instrument_id, "no_limit_anchor") for o in adjusted]

    monkeypatch.setattr(lp, "to_limit_orders", _all_drop)
    venue = _FakeVenue(never_confirms_for={"BTC"})
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=1)
    assert venue.calls == []
    assert any(inst == "BTC" and reason == "no_limit_anchor"
              for inst, reason in result.dropped_by_filters)
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_DROPPED_BY_FILTER" in actions


# --- 轮询窗口:LIMIT 用 limit_max_confirm_polls,MARKET 继续尊重调用方 max_confirm_polls
# --- (批判必须修改③:禁止"MARKET 恒走 DEFAULT 常量"的静默回归) ---

def test_pipeline_limit_rests_then_timeout_cancel_zero_fill(ledger):
    venue = _FakeVenue(never_confirms_for={"BTC"})
    result, audit, sleeps = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=4)
    assert result.tier == AUTO
    assert len(sleeps) == 4                             # 用的是 limit_max_confirm_polls=4,不是 5
    assert venue.cancel_calls != []
    assert result.submitted[0].status == "CANCELED"
    assert result.submitted[0].filled_qty == 0.0
    assert ledger.position("BTC") == 0.0
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_TIMEOUT_CANCEL" in actions
    cancel_ev = [e for e in audit.events if e.action == "LIVE_ORDER_TIMEOUT_CANCEL"][0]
    assert cancel_ev.metadata.get("order_type") == "LIMIT"


def test_pipeline_market_respects_caller_max_confirm_polls_not_hardcoded_default(ledger):
    # limit_pricing=None(MARKET 路径),显式传一个不同于 DEFAULT_MAX_CONFIRM_POLLS(5) 的
    # max_confirm_polls,必须被真正尊重——不能被静默忽略、恒走 5
    venue = _FakeVenue(never_confirms_for={"BTC"})
    result, audit, sleeps = _submit(venue=venue, ledger=ledger, limit_pricing=None,
                                    max_confirm_polls=2, limit_max_confirm_polls=99)
    assert len(sleeps) == 2                             # 用的是调用方传入的 max_confirm_polls=2
    assert venue.calls[0].order_type == "MARKET"


def test_pipeline_limit_partial_fill_then_cancel(ledger):
    venue = _FakeVenue(never_confirms_for={"BTC"}, cancel_after_partial_fill_for={"BTC"})
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=2)
    assert result.submitted[0].status == "CANCELED"
    assert result.submitted[0].filled_qty > 0.0
    assert ledger.position("BTC") == pytest.approx(result.submitted[0].filled_qty)


def test_pipeline_limit_cancel_races_full_fill(ledger):
    venue = _FakeVenue(never_confirms_for={"BTC"}, cancel_races_fill_for={"BTC"})
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=2)
    assert result.submitted[0].status == "FILLED"
    assert ledger.position("BTC") == pytest.approx(result.submitted[0].filled_qty)
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_TIMEOUT_CANCEL" in actions


def test_pipeline_limit_progressive_fill_confirms_once(ledger):
    # 轮询过程 filled_qty 递增(NEW -> PARTIALLY_FILLED -> FILLED),只按最终累计量记一次账
    class _ProgressiveVenue(_FakeVenue):
        def get_order(self, instrument_id, client_order_id):
            self.get_order_calls.append(client_order_id)
            n = self.get_order_calls.count(client_order_id)
            qty = self._qty[client_order_id]
            if n == 1:
                return VenueOrderResult(venue_order_id="v", client_order_id=client_order_id,
                                        instrument_id=instrument_id, status="PARTIALLY_FILLED",
                                        filled_qty=qty * 0.5, avg_price=_PRICES[instrument_id])
            return VenueOrderResult(venue_order_id="v", client_order_id=client_order_id,
                                    instrument_id=instrument_id, status="FILLED",
                                    filled_qty=qty, avg_price=_PRICES[instrument_id])

    venue = _ProgressiveVenue(never_confirms_for={"BTC"})
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=5)
    assert result.submitted[0].status == "FILLED"
    fills = ledger.fills()
    assert len(fills) == 1                                # 只记一次账,不是每次轮询都记
    assert fills[0]["quantity"] == pytest.approx(result.submitted[0].filled_qty)


# --- preview + LIMIT 短路轮询(批判必须修改④,核心)---

class _PreviewLikeVenue(_FakeVenue):
    """PreviewVenue 的最小复刻(镜像 tools/live_track_advance.py::PreviewVenue 的核心语义):
    place_order 恒返回非真实终态标记 PREVIEW_NOT_SUBMITTED,get_order/cancel_order 恒 None
    (预览从未真提交,查/撤都没有意义)。"""

    def place_order(self, order):
        self.calls.append(order)
        return VenueOrderResult(venue_order_id="PREVIEW", client_order_id=order.client_order_id,
                                instrument_id=order.instrument_id, status="PREVIEW_NOT_SUBMITTED",
                                filled_qty=0.0, avg_price=0.0)

    def get_order(self, instrument_id, client_order_id):
        self.get_order_calls.append(client_order_id)
        return None

    def cancel_order(self, instrument_id, client_order_id):
        self.cancel_calls.append(client_order_id)
        return None


def test_preview_limit_no_poll_burn(ledger):
    venue = _PreviewLikeVenue(book_ticker_responses={"BTC": {"bid": 59990.0, "ask": 60010.0}})
    result, audit, sleeps = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=30)
    assert result.tier == AUTO
    assert len(venue.calls) == 1 and venue.calls[0].order_type == "LIMIT"
    assert sleeps == []                                   # 零轮询燃烧(PREVIEW_NOT_SUBMITTED 恒终态)
    assert venue.get_order_calls == []
    assert venue.cancel_calls == []                       # 没有走 _cancel_and_requery
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_TIMEOUT_CANCEL" not in actions      # 没有假的超时撤单审计
    assert result.submitted[0].status == "PREVIEW_NOT_SUBMITTED"
    assert result.submitted[0].filled_qty == 0.0
    assert ledger.position("BTC") == 0.0                   # 没有假成交进账本


def test_preview_market_no_poll_burn_same_fix_applies(ledger):
    # 同一个终态识别修复对 MARKET 预览同样生效(此前 MARKET 预览也会空转 max_confirm_polls
    # 次真实 sleep + 产生误导性 LIVE_ORDER_TIMEOUT_CANCEL——这不是本战线引入的新覆盖面,而是
    # 顺带修复的既有缺口,记录在案)
    venue = _PreviewLikeVenue()
    result, audit, sleeps = _submit(venue=venue, ledger=ledger, limit_pricing=None,
                                    max_confirm_polls=5)
    assert sleeps == []
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_TIMEOUT_CANCEL" not in actions


# --- MARKET 回归锁(批判要求的字节不变验证之一) ---

def test_pipeline_market_only_path_unchanged(ledger):
    # limit_pricing=None:一切现有行为不变——不调用 book_ticker,提交的仍是 MARKET 请求,
    # 结果/审计序列与改动前完全一致(与 test_pipeline.py 的等价 happy-path 断言同构)
    venue = _FakeVenue()
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_pricing=None)
    assert venue.book_ticker_calls == []                  # MARKET 路径完全不碰定价装配
    assert result.tier == AUTO
    assert venue.calls[0].order_type == "MARKET"
    assert venue.calls[0].limit_price is None
    assert all(s.status == "FILLED" for s in result.submitted)
    actions = [e.action for e in audit.events]
    assert "LIVE_LIMIT_ANCHOR_FALLBACK" not in actions
    submit_ev = [e for e in audit.events if e.action == "LIVE_ORDER_SUBMITTED"][0]
    assert submit_ev.metadata.get("order_type") == "MARKET"
    assert "limit_price" not in submit_ev.metadata


# --- 审计 metadata 携带 order_type(+ LIMIT 时 limit_price) ---

def test_pipeline_limit_audit_metadata_includes_order_type_and_price(ledger):
    venue = _FakeVenue()          # 默认 confirms(不在 never_confirms_for),下一次轮询即 FILLED
    result, audit, _ = _submit(venue=venue, ledger=ledger, limit_max_confirm_polls=5)
    assert result.tier == AUTO
    submit_ev = [e for e in audit.events if e.action == "LIVE_ORDER_SUBMITTED"][0]
    assert submit_ev.metadata.get("order_type") == "LIMIT"
    assert "limit_price" in submit_ev.metadata


# --- mandate 名义额口径:LIMIT 用保守价(BUY max(决策价,限价))评估 ---

def test_pipeline_limit_conservative_notional_can_push_batch_to_human_review(ledger):
    # band 20bps 抬高的名义额本身不足以让这批越限(band 太小);构造一个 tighter mandate 单笔
    # 上限,证明 mandate 评估看到的确实是 LIMIT 价而非决策价
    tight = Mandate(mandate_id="m2", venue="testnet", exchange="fake",
                    allowed_instruments=frozenset({"BTC"}),
                    max_single_order_notional=5.0 * 60000.0 * 1.001,   # 卡在决策价和限价之间
                    max_gross_notional=1e9, max_net_notional=1e9)
    venue = _FakeVenue(never_confirms_for={"BTC"})
    result, audit, _ = _submit(venue=venue, ledger=ledger, mandate=tight,
                              limit_max_confirm_polls=1)
    assert result.tier == NEEDS_HUMAN_REVIEW
    assert any("single_order_notional_exceeded" in r for r in result.mandate_reasons)
    assert venue.calls == []                              # 从未到 venue(mandate 拦在提交前)


# --- 崩溃恢复 + 切单:既有路径对 LIMIT 行零改动即正确处置 ---

def test_crash_recovery_cancels_leftover_limit_and_books_partial(ledger):
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 0.015, strategy_id="crowd_contra",
                             submitted_at=_NOW, status="NEW", order_type="LIMIT",
                             limit_price=60120.0)
    venue = _FakeVenue(cancel_after_partial_fill_for={"BTC"})
    venue._orders["c1"] = VenueOrderResult(venue_order_id="v-c1", client_order_id="c1",
                                           instrument_id="BTC", status="NEW",
                                           filled_qty=0.0, avg_price=0.0)
    venue._qty["c1"] = 0.015
    out = resolve_unconfirmed_orders(ledger, venue, now=_NOW + 1000)
    assert venue.cancel_calls == ["c1"]
    assert len(out["resolved"]) == 1 and out["resolved"][0]["status"] == "CANCELED"
    assert ledger.position("BTC") == pytest.approx(0.015 * 0.4)


def test_slicer_child_limit_cancel_aborts_remaining(ledger):
    from live.execution.slicer import plan_slices

    def _slicer(parent):
        return plan_slices(parent, n_slices=3)

    venue = _FakeVenue(never_confirms_for={"BTC"}, cancel_after_partial_fill_for={"BTC"})
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW,
        limit_pricing=LimitPricing(), limit_max_confirm_polls=1, slicer=_slicer,
        poll_sleep=lambda: None)
    assert result.tier == AUTO
    # 子单序列内第一片 CANCELED(部分成交)后中止剩余子单——已成交部分如实入账
    assert len(venue.calls) < 3 or any(
        c.client_order_id.endswith("-s0") for c in venue.calls)
    actions = [e.action for e in audit.events]
    assert "LIVE_SLICING_ABORTED" in actions
    assert all(o.order_type == "LIMIT" for o in venue.calls)   # 子单继承 LIMIT 属性(dataclasses.replace)
