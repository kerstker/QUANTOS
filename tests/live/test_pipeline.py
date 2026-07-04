"""live/pipeline.py::submit_live_batch —— 端到端编排(复用真实 PreTradeRiskGate,非假门,证明
真整合)。设计:docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.7。

关键要证明的性质:① 缩放整数(喂 gate)<->真实浮点(喂 venue)往返正确;② gate 拒绝的从不到
mandate/venue;③ 过滤器丢弃的从不到 mandate/venue;④ mandate NEEDS_HUMAN_REVIEW 时零提交;
⑤ venue 提交失败 fail-soft,不阻塞其它笔;⑥ 全程审计(GlobalAuditLog);⑦ MARKET 单同步 ACK
不是终态,必须轮询 get_order 确认(2026-07-04 真实测试网联调证伪"同步=终态"这个假设,对抗式
审查发现 #1/#6/#12——本文件的 `_FakeVenue` 因此刻意模拟这个真实异步行为,而不是像早期版本那样
让 place_order 同步返回 FILLED,后者曾让漏记成交的 bug 在"71 tests 全绿"下溜走);⑧ venue 调用
异常后必须用 get_order 回查权威状态,不能想当然地判定失败(发现 #4/#5);⑨ 记账失败不能让一笔
真实成功的订单被误记成失败(发现 #2);⑩ exchange_filters() 失败必须 fail-closed 而非崩溃
(发现 #3)。
"""
import pytest

from live.contracts import ExchangeFilters, VenueOrderResult
from live.execution.position_ledger import LiveLedger
from live.governance.mandate import AUTO, NEEDS_HUMAN_REVIEW, Mandate
from live.pipeline import UNIT_SCALE, plan_scaled_targets, submit_live_batch
from quantos.contracts.trading import OrderSide
from services.monitoring.audit_log import GlobalAuditLog
from services.risk.pre_trade.config import RiskLimits
from services.risk.pre_trade.context import AccountRiskState, MarketContext, RiskContext, StrategyApproval
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch

_NOW = 1_700_000_000_000
_PRICES = {"BTC": 60000.0, "ETH": 3000.0}
_FILTERS = {
    "BTC": ExchangeFilters(instrument_id="BTC", step_size=0.0001, min_qty=0.0001, min_notional=5.0),
    "ETH": ExchangeFilters(instrument_id="ETH", step_size=0.001, min_qty=0.001, min_notional=5.0),
}


def _wide_open_limits(allowed=frozenset({"BTC", "ETH"})):
    return RiskLimits(
        allowed_instruments=allowed, allowed_accounts=frozenset({"live_test"}),
        default_max_position_qty=10 ** 13, max_order_notional=1e14, fat_finger_qty=10 ** 13,
        max_gross_exposure=1e14, max_net_exposure=1e14, max_leverage=1e7, max_single_name_pct=1e7,
        max_turnover=1e7, max_participation_rate=1e7, max_instrument_vol=5.0, daily_loss_limit=1e14,
        max_drawdown=1.0)


def _gate(allowed=frozenset({"BTC", "ETH"})):
    return PreTradeRiskGate(_wide_open_limits(allowed), kill_switch=KillSwitch())


def _ctx_factory(prices=_PRICES, unit_scale=UNIT_SCALE):
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


_WIDE_MANDATE = Mandate(mandate_id="m1", venue="testnet",
                        allowed_instruments=frozenset({"BTC", "ETH"}),
                        max_single_order_notional=1e9, max_gross_notional=1e9, max_net_notional=1e9)
_TINY_MANDATE = Mandate(mandate_id="m2", venue="testnet",
                        allowed_instruments=frozenset({"BTC", "ETH"}),
                        max_single_order_notional=1.0, max_gross_notional=1.0, max_net_notional=1.0)


class _FakeVenue:
    """刻意模拟 Binance MARKET 单的真实异步行为(而非早期版本的"同步即终态"错误假设):
    place_order 的 ACK 恒为 status=NEW/filled_qty=0(除非该标的在 never_confirms_for 之外,
    此时"稍后"——也就是下一次 get_order 轮询——才会看到 FILLED)。

    fail_recoverable_for: place_order 抛异常,但订单其实已经在 venue 侧登记成功(get_order 能查到)
    ——模拟"响应丢了,请求没丢"。
    fail_unrecoverable_for: place_order 抛异常且订单确实从未抵达 venue(get_order 也查不到)
    ——真失败。
    never_confirms_for: 订单永远停在 NEW,轮询耗尽也不会看到 FILLED。
    """
    venue_mode = "testnet"

    def __init__(self, *, fail_recoverable_for=(), fail_unrecoverable_for=(), never_confirms_for=()):
        self.calls = []
        self.get_order_calls = []
        self._fail_recoverable = set(fail_recoverable_for)
        self._fail_unrecoverable = set(fail_unrecoverable_for)
        self._never_confirms = set(never_confirms_for)
        self._orders = {}          # client_order_id -> VenueOrderResult(最新已知状态)

    def place_order(self, order):
        self.calls.append(order)
        if order.instrument_id in self._fail_unrecoverable:
            raise RuntimeError("simulated venue failure (never reached venue)")
        ack = VenueOrderResult(venue_order_id=f"v-{order.client_order_id}",
                               client_order_id=order.client_order_id,
                               instrument_id=order.instrument_id, status="NEW",
                               filled_qty=0.0, avg_price=0.0)
        self._orders[order.client_order_id] = ack
        if order.instrument_id not in self._never_confirms:
            # "稍后"成交:下一次 get_order 轮询会看到这个,而不是 place_order 自己同步返回
            self._orders[order.client_order_id] = VenueOrderResult(
                venue_order_id=ack.venue_order_id, client_order_id=order.client_order_id,
                instrument_id=order.instrument_id, status="FILLED",
                filled_qty=order.quantity, avg_price=_PRICES[order.instrument_id])
        if order.instrument_id in self._fail_recoverable:
            raise RuntimeError("simulated venue failure (response lost, order exists)")
        return ack

    def get_order(self, instrument_id, client_order_id):
        self.get_order_calls.append(client_order_id)
        return self._orders.get(client_order_id)

    def account(self):
        return {}

    def positions(self):
        return {}

    def exchange_filters(self):
        return _FILTERS


class _ExplodingFiltersVenue(_FakeVenue):
    def exchange_filters(self):
        raise TimeoutError("simulated exchange timeout")


class _LedgerThatBreaksOn:
    """真实 LiveLedger 的薄包装:对指定标的的 record_submission 抛异常,其它照常委托。"""

    def __init__(self, inner: LiveLedger, *, break_for=()):
        self._inner = inner
        self._break_for = set(break_for)

    def record_submission(self, client_order_id, instrument_id, side, quantity, **kw):
        if instrument_id in self._break_for:
            raise RuntimeError("simulated database is locked")
        return self._inner.record_submission(client_order_id, instrument_id, side, quantity, **kw)

    def record_fill(self, *a, **kw):
        return self._inner.record_fill(*a, **kw)

    def position(self, instrument_id):
        return self._inner.position(instrument_id)

    def all_positions(self):
        return self._inner.all_positions()


@pytest.fixture()
def ledger(tmp_path):
    return LiveLedger(str(tmp_path / "live.db"), create=True)


# --- plan_scaled_targets ---

def test_plan_scaled_targets_scales_and_drops_missing_price():
    out = plan_scaled_targets({"BTC": 0.5, "GHOST": 0.1}, {"BTC": 60000.0}, 600_000.0)
    assert out == {"BTC": 5_000_000}                          # 0.5*600000/60000=5.0 coin *1e6 units
    assert "GHOST" not in out


# --- happy path: 缩放往返 + 轮询确认 + AUTO 提交 + ledger 记录 + 审计 ---

def test_full_batch_auto_submits_with_correct_real_quantities(ledger):
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5, "ETH": -0.5}, prices=_PRICES, aum=600_000.0,
        current_positions={}, gate=_gate(), ctx_factory=_ctx_factory(), venue=venue,
        mandate=_WIDE_MANDATE, ledger=ledger, audit=audit, strategy_id="crowd_contra",
        decided_ts=_NOW, now=_NOW)

    assert result.tier == AUTO
    assert len(result.submitted) == 2
    assert result.rejected_by_gate == () and result.dropped_by_filters == ()
    assert result.submission_errors == () and result.ledger_write_errors == ()
    # 确认后的状态应该是 FILLED(轮询拿到的),不是 place_order 同步 ACK 的 NEW
    assert all(s.status == "FILLED" for s in result.submitted)

    submitted_qty = {c.instrument_id: c.quantity for c in venue.calls}
    assert submitted_qty["BTC"] == pytest.approx(5.0)          # 往返:0.5*600000/60000
    assert submitted_qty["ETH"] == pytest.approx(100.0)        # |−0.5|*600000/3000
    assert venue.calls[0].side in (OrderSide.BUY, OrderSide.SELL)
    assert venue.get_order_calls                                # 确实轮询了,不是只信同步 ACK

    assert ledger.position("BTC") == pytest.approx(5.0)
    assert ledger.position("ETH") == pytest.approx(-100.0)

    actions = [e.action for e in audit.events]
    assert "LIVE_BATCH_START" in actions
    assert actions.count("LIVE_ORDER_SUBMITTED") == 2
    assert audit.chain_valid()


def test_already_at_target_generates_no_intents(ledger):
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0,
        current_positions={"BTC": 5.0},                        # 已经在目标上
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert result.tier == AUTO
    assert result.submitted == () and venue.calls == []


# --- gate 拒绝从不到 mandate/venue ---

def test_gate_rejected_instrument_never_reaches_venue_or_mandate(ledger):
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5, "ETH": -0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(allowed=frozenset({"ETH"})),                # BTC 不在允许清单 -> 门拒绝
        ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE, ledger=ledger, audit=audit,
        strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert len(result.rejected_by_gate) == 1
    assert result.rejected_by_gate[0][0].instrument_id == "BTC"
    assert all(c.instrument_id != "BTC" for c in venue.calls)   # BTC 从未到 venue
    assert result.tier == AUTO and len(result.submitted) == 1   # ETH 正常走完


# --- 过滤器丢弃从不到 mandate/venue ---

def test_filter_dropped_instrument_never_reaches_venue(ledger):
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    # aum 极小 -> BTC 目标名义额远低于 min_notional=$5,过滤器丢弃
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=1.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert venue.calls == []
    assert result.submitted == ()
    # 缩放后 units 可能已经是 0(gate 那层就没生成意图)或是过滤器丢弃——两种诚实结果之一,总之不提交
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_SUBMITTED" not in actions


# --- exchange_filters() 失败必须 fail-closed,不能崩溃(发现 #3) ---

def test_exchange_filters_failure_is_fail_closed_not_a_crash(ledger):
    venue = _ExplodingFiltersVenue()
    audit = GlobalAuditLog()
    result = submit_live_batch(          # 不应该抛异常
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert result.tier == NEEDS_HUMAN_REVIEW
    assert result.submitted == () and venue.calls == []
    actions = [e.action for e in audit.events]
    assert "LIVE_EXCHANGE_FILTERS_FAILED" in actions
    assert "LIVE_ORDER_SUBMITTED" not in actions


# --- mandate NEEDS_HUMAN_REVIEW: 零提交,留存待批 ---

def test_mandate_needs_review_blocks_all_submission(ledger):
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5, "ETH": -0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_TINY_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert result.tier == NEEDS_HUMAN_REVIEW
    assert venue.calls == []
    assert result.submitted == ()
    assert len(result.pending_review) == 2
    assert ledger.position("BTC") == 0.0 and ledger.position("ETH") == 0.0   # ledger 未动
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_SUBMITTED" not in actions
    assert actions.count("LIVE_ORDER_PENDING_HUMAN_REVIEW") == 2


def test_missing_mandate_forces_review(ledger):
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=None,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert result.tier == NEEDS_HUMAN_REVIEW
    assert venue.calls == []


# --- 成交确认:同步 ACK 不是终态,必须轮询(核心修复,发现 #1/#6/#12) ---

def test_unconfirmed_after_max_polls_records_honest_new_status_not_a_fake_fill(ledger):
    venue = _FakeVenue(never_confirms_for={"BTC"})
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW,
        max_confirm_polls=3)
    assert result.tier == AUTO
    assert len(result.submitted) == 1
    assert result.submitted[0].status == "NEW"                  # 诚实:没确认到终态就不假装 FILLED
    assert result.submitted[0].filled_qty == 0.0
    assert ledger.position("BTC") == 0.0                        # 没有假成交被记进账本
    assert len(venue.get_order_calls) == 3                      # 确实打光了 max_confirm_polls 次
    events = {e.action: e for e in audit.events}
    submit_ev = [e for e in audit.events if e.action == "LIVE_ORDER_SUBMITTED"][0]
    assert submit_ev.metadata["confirmed"] == "False"


# --- 记账/成交(不是提交)失败 fail-soft:不阻塞其它笔,不冒充真失败 ---

def test_venue_call_truly_fails_with_no_recovery_is_a_genuine_error(ledger):
    venue = _FakeVenue(fail_unrecoverable_for={"BTC"})
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5, "ETH": -0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert result.tier == AUTO
    assert len(result.submitted) == 1 and result.submitted[0].instrument_id == "ETH"
    assert len(result.submission_errors) == 1 and result.submission_errors[0][0].instrument_id == "BTC"
    assert ledger.position("ETH") == pytest.approx(-100.0)
    assert ledger.position("BTC") == 0.0                        # 真失败的那笔从未落账
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_SUBMIT_FAILED" in actions and "LIVE_ORDER_SUBMITTED" in actions
    assert "LIVE_ORDER_RECOVERED_AFTER_SUBMIT_ERROR" not in actions


def test_venue_call_error_recovers_via_get_order_when_order_actually_exists(ledger):
    # place_order 抛异常(模拟响应丢失),但订单其实已经在 venue 侧登记成功——不能被当成真失败
    venue = _FakeVenue(fail_recoverable_for={"BTC"})
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert result.tier == AUTO
    assert result.submission_errors == ()                        # 不是失败
    assert len(result.submitted) == 1 and result.submitted[0].instrument_id == "BTC"
    assert result.submitted[0].status == "FILLED"
    assert ledger.position("BTC") == pytest.approx(5.0)           # 真实存在的成交被正确记账
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_RECOVERED_AFTER_SUBMIT_ERROR" in actions
    assert "LIVE_ORDER_SUBMIT_FAILED" not in actions


# --- 记账失败 ≠ 下单失败:真实成功的订单绝不能被误记成失败(发现 #2) ---

def test_ledger_write_failure_still_counts_order_as_submitted_not_failed(ledger):
    broken_ledger = _LedgerThatBreaksOn(ledger, break_for={"BTC"})
    venue = _FakeVenue()
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=broken_ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert result.tier == AUTO
    assert result.submission_errors == ()                         # 绝不能算成"下单失败"
    assert len(result.submitted) == 1 and result.submitted[0].status == "FILLED"
    assert len(result.ledger_write_errors) == 1
    assert result.ledger_write_errors[0][0].instrument_id == "BTC"
    actions = [e.action for e in audit.events]
    assert "LIVE_LEDGER_WRITE_FAILED" in actions
    assert "LIVE_ORDER_SUBMIT_FAILED" not in actions
    assert "LIVE_ORDER_SUBMITTED" in actions                      # 仍然记为已提交
