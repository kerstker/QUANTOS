"""live/pipeline.py::submit_live_batch —— 端到端编排(复用真实 PreTradeRiskGate,非假门,证明
真整合)。设计:docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.7。

关键要证明的性质:① 缩放整数(喂 gate)<->真实浮点(喂 venue)往返正确;② gate 拒绝的从不到
mandate/venue;③ 过滤器丢弃的从不到 mandate/venue;④ mandate NEEDS_HUMAN_REVIEW 时零提交;
⑤ venue 提交失败 fail-soft,不阻塞其它笔;⑥ 全程审计(GlobalAuditLog)。
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
    venue_mode = "testnet"

    def __init__(self, *, fail_for=()):
        self.calls = []
        self._fail_for = set(fail_for)

    def place_order(self, order):
        self.calls.append(order)
        if order.instrument_id in self._fail_for:
            raise RuntimeError("simulated venue failure")
        return VenueOrderResult(venue_order_id=f"v-{order.client_order_id}",
                                client_order_id=order.client_order_id,
                                instrument_id=order.instrument_id, status="FILLED",
                                filled_qty=order.quantity, avg_price=_PRICES[order.instrument_id])

    def get_order(self, instrument_id, client_order_id):
        return None

    def account(self):
        return {}

    def positions(self):
        return {}

    def exchange_filters(self):
        return _FILTERS


@pytest.fixture()
def ledger(tmp_path):
    return LiveLedger(str(tmp_path / "live.db"), create=True)


# --- plan_scaled_targets ---

def test_plan_scaled_targets_scales_and_drops_missing_price():
    out = plan_scaled_targets({"BTC": 0.5, "GHOST": 0.1}, {"BTC": 60000.0}, 600_000.0)
    assert out == {"BTC": 5_000_000}                          # 0.5*600000/60000=5.0 coin *1e6 units
    assert "GHOST" not in out


# --- happy path: 缩放往返 + AUTO 提交 + ledger 记录 + 审计 ---

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
    assert result.submission_errors == ()

    submitted_qty = {c.instrument_id: c.quantity for c in venue.calls}
    assert submitted_qty["BTC"] == pytest.approx(5.0)          # 往返:0.5*600000/60000
    assert submitted_qty["ETH"] == pytest.approx(100.0)        # |−0.5|*600000/3000
    assert venue.calls[0].side in (OrderSide.BUY, OrderSide.SELL)

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


# --- venue 提交失败 fail-soft,不阻塞其它笔 ---

def test_venue_failure_on_one_order_does_not_block_others(ledger):
    venue = _FakeVenue(fail_for={"BTC"})
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights={"BTC": 0.5, "ETH": -0.5}, prices=_PRICES, aum=600_000.0, current_positions={},
        gate=_gate(), ctx_factory=_ctx_factory(), venue=venue, mandate=_WIDE_MANDATE,
        ledger=ledger, audit=audit, strategy_id="crowd_contra", decided_ts=_NOW, now=_NOW)
    assert result.tier == AUTO
    assert len(result.submitted) == 1 and result.submitted[0].instrument_id == "ETH"
    assert len(result.submission_errors) == 1 and result.submission_errors[0][0].instrument_id == "BTC"
    assert ledger.position("ETH") == pytest.approx(-100.0)
    assert ledger.position("BTC") == 0.0                        # 失败的那笔从未落账
    actions = [e.action for e in audit.events]
    assert "LIVE_ORDER_SUBMIT_FAILED" in actions and "LIVE_ORDER_SUBMITTED" in actions
