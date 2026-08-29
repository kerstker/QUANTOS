"""MEXC 现货 × submit_live_batch pipeline 级集成测试(2026-07 MEXC 对接 1.8)。

fake-transport `MexcSpotAdapter`(http_request/clock/sleep 全注入,零真网)走
`live/pipeline.py::submit_live_batch` 全链:attestation → plan → 真 gate(driver 现货口径
`gate_limits`)→ 交易所过滤器(真 `MexcSpotAdapter.exchange_filters` 假 transport)→
mandate(venue="mainnet", exchange="mexc")→ 预记账 → 下单 → **成交确认轮询** →
**取消竞态**(撤单时已部分成交)→ 账本/审计对账。外加崩溃恢复 `resolve_unconfirmed_orders`、
attestation/mandate fail-closed、现货现金门 `spot_cash_shortfail` 与 driver 现货 gate 限额参数。

响应 JSON 形状依 MEXC 公开 v3 文档假设(与 tests/live/test_mexc_spot.py 同一诚实边界)。
"""
import json
import urllib.parse

import pytest

from live.contracts import LiveOrderRequest
from live.exchange.mexc_spot import MexcSpotAdapter
from live.execution.position_ledger import LiveLedger
from live.governance.mandate import AUTO, NEEDS_HUMAN_REVIEW, Mandate
from live.governance.validation_attestation import HUMAN_OVERRIDE, ValidationAttestation
from live.pipeline import (
    _submit_live_batch_technical_diagnostic as submit_live_batch,
    resolve_unconfirmed_orders,
)
from quantos.contracts.config import SafetyConfig
from quantos.contracts.trading import OrderSide
from services.monitoring.audit_log import GlobalAuditLog
from services.risk.pre_trade.gate import PreTradeRiskGate
from services.risk.pre_trade.kill_switch import KillSwitch
from tools.live_track_advance import (gate_limits, make_ctx_factory, spot_cash_shortfall)

_KEY, _SECRET = "test-mexc-key", "test-mexc-secret"
_ARMED = SafetyConfig(live_trading_enabled=True, real_broker_enabled=True,
                      allow_order_submission=True, allow_ai_direct_trading=False)
_NOW = 1_700_000_000_000
_CLOCK = lambda: _NOW / 1000.0  # noqa: E731
_PX = 60_000.0

_EXCHANGE_INFO = {"symbols": [{
    "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "minQty": "0.00001", "stepSize": "0.00001"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ],
}]}


def _account(usdt="10000.0", btc="0.0"):
    return {"canTrade": True, "canWithdraw": False, "balances": [
        {"asset": "BTC", "free": btc, "locked": "0"},
        {"asset": "USDT", "free": usdt, "locked": "0"},
    ]}


def _order_payload(client_order_id, *, status="FILLED", executed="0.01", quote="600.0"):
    return {"symbol": "BTCUSDT", "orderId": 123456789, "clientOrderId": client_order_id,
            "status": status, "side": "BUY", "type": "MARKET", "price": "0",
            "origQty": executed, "executedQty": executed,
            "cummulativeQuoteQty": quote, "transactTime": _NOW}


class _FakeTransport:
    """{path: (status, payload) | [逐次消费序列]};path 按 url 去 query 归一。下单响应按
    newClientOrderId 参数动态生成(每个 client_order_id 一笔),订单状态序列由测试编排:
    - get_order_seq:get_order 逐次消费(超出重复最后一个);
    - cancel_result="race_rejected":DELETE 被 -2011 拒(订单已终态,撤单与成交赛跑);
    - cancel_result="partial_fill":DELETE 成功且回报 0.004 BTC 部分成交,随后 get_order
      回查同样报 CANCELED+部分成交(撤单 ≠ 零成交)。"""

    def __init__(self, *, account=None, order_ack_status="NEW", get_order_seq=None,
                 cancel_result="race_rejected"):
        self.account = account if account is not None else _account()
        self.order_ack_status = order_ack_status
        self.get_order_seq = list(get_order_seq or [])
        self.cancel_result = cancel_result
        self.calls = []
        self._get_order_calls = 0
        self._canceled_partial = False
        self.placed = []

    def __call__(self, method, url, *, body=None, headers=None):
        path = url.split("?")[0].replace(MexcSpotAdapter.MAINNET_BASE, "")
        self.calls.append({"method": method, "url": url, "body": body})
        if path == "/api/v3/exchangeInfo":
            return 200, json.dumps(_EXCHANGE_INFO).encode()
        if path == "/api/v3/time":
            return 200, json.dumps({"serverTime": _NOW}).encode()
        if path == "/api/v3/account":
            return 200, json.dumps(self.account).encode()
        if path == "/api/v3/order":
            raw = (body or b"").decode() if method == "POST" else (
                url.split("?", 1)[1] if "?" in url else "")
            params = dict(urllib.parse.parse_qsl(raw))
            cid = params.get("newClientOrderId") or params.get("origClientOrderId")
            if method == "POST":
                self.placed.append(params)
                payload = _order_payload(cid, status=self.order_ack_status,
                                         executed="0", quote="0")
                return 200, json.dumps(payload).encode()
            if method == "GET":
                self._get_order_calls += 1
                if self._canceled_partial:
                    return 200, json.dumps(_order_payload(
                        cid, status="CANCELED", executed="0.004",
                        quote=f"{0.004 * _PX:.4f}")).encode()
                seq = self.get_order_seq
                status = seq[min(self._get_order_calls - 1, len(seq) - 1)] if seq else "FILLED"
                qty = self.placed[-1]["quantity"] if self.placed else "0.01"
                quote = f"{float(qty) * _PX:.4f}"
                return 200, json.dumps(_order_payload(cid, status=status, executed=qty,
                                                      quote=quote)).encode()
            if method == "DELETE":
                if self.cancel_result == "race_rejected":
                    return 400, json.dumps({"code": -2011, "msg": "Unknown order sent."}).encode()
                self._canceled_partial = True
                qty = "0.004"
                return 200, json.dumps(_order_payload(cid, status="CANCELED", executed=qty,
                                                      quote=f"{0.004 * _PX:.4f}")).encode()
        return 404, json.dumps({"msg": f"unexpected {method} {path}"}).encode()


def _venue(transport):
    return MexcSpotAdapter(api_key=_KEY, api_secret=_SECRET,
                           base_url=MexcSpotAdapter.MAINNET_BASE,
                           http_request=transport, clock=_CLOCK, safety_config=_ARMED,
                           sleep=lambda _s: None)


_EQUITY = 10_000.0


def _spot_gate():
    # driver 现货口径(1.4):单名满仓 long 是合法目标——1×equity 敞口/单名上限,杠杆 1.0
    return PreTradeRiskGate(
        gate_limits({"BTC"}, gross_cap=_EQUITY * 1.0, net_cap=_EQUITY * 1.0,
                    single_order_cap=_EQUITY * 1.0, daily_loss_cap=_EQUITY * 0.1,
                    max_leverage=1.0, max_single_name_pct=1.0),
        kill_switch=KillSwitch())


def _ctx():
    return make_ctx_factory({"BTC": _PX}, aum=_EQUITY, now=_NOW, price_as_of=_NOW,
                            current_positions={}, day_pnl=0.0, peak_equity=_EQUITY,
                            strategy_id="btc_spot_trend")


_MANDATE = Mandate(mandate_id="mexc-m1", venue="mainnet", exchange="mexc",
                   allowed_instruments=frozenset({"BTC"}),
                   max_single_order_notional=1e9, max_gross_notional=1e9,
                   max_net_notional=1e9)
_ATTESTATION = ValidationAttestation(strategy_id="btc_spot_trend", kind=HUMAN_OVERRIDE,
                                     attested_by="alice", attested_at=_NOW,
                                     reason="test fixture")


def _run_batch(transport, *, weights=None, mandate=_MANDATE, attestation=_ATTESTATION,
               aum=_EQUITY, current_positions=None, ledger=None):
    venue = _venue(transport)
    ledger = ledger or LiveLedger(":memory:", create=True)
    audit = GlobalAuditLog()
    result = submit_live_batch(
        weights=weights if weights is not None else {"BTC": 0.5},
        prices={"BTC": _PX}, aum=aum,
        current_positions=current_positions or {}, gate=_spot_gate(), ctx_factory=_ctx(),
        venue=venue, mandate=mandate, validation_attestation=attestation,
        ledger=ledger, audit=audit, strategy_id="btc_spot_trend",
        decided_ts=_NOW, now=_NOW, poll_sleep=lambda: None, diagnostic_only=True)
    return result, ledger, audit, venue


# --- 全链 happy path:attestation→gate→filters→mandate→下单→轮询确认→记账 ---

def test_full_chain_market_buy_confirmed_by_polling(tmp_path):
    # ACK 是 NEW(2026-07-04 真实联调证伪"同步=终态"),第一次 get_order 仍 NEW,第二次 FILLED
    t = _FakeTransport(get_order_seq=["NEW", "FILLED"])
    result, ledger, audit, _v = _run_batch(t)
    assert result.tier == AUTO
    assert len(t.placed) == 1
    assert t.placed[0]["symbol"] == "BTCUSDT" and t.placed[0]["side"] == "BUY"
    assert t.placed[0]["type"] == "MARKET"
    assert t._get_order_calls >= 2                      # 确认轮询真的发生了
    submitted = result.submitted[0]
    assert submitted.status == "FILLED"
    assert submitted.avg_price == pytest.approx(_PX)    # cummulativeQuoteQty/executedQty 折算
    orders = ledger.orders()
    assert orders[0]["status"] == "FILLED"
    fills = ledger.fills()
    assert len(fills) == 1 and fills[0]["instrument_id"] == "BTC"
    assert fills[0]["side"] == OrderSide.BUY.value
    assert ledger.all_positions()["BTC"] == pytest.approx(fills[0]["quantity"])
    assert audit.chain_valid()


def test_full_position_long_not_killed_by_spot_gate_limits():
    # 1.4 的核心回归:单名满仓 long 在期货形默认(net 0.3×aum/单名 0.5)下会被误杀,现货口径
    # (1×equity)必须放行。aum 取 0.95×equity —— 镜像驱动器 deploy_aum 的 0.95 mandate 余量
    # (缩放整数取整会向上顶过 1.000×equity 的裸边界,真实驱动路径本来就留这 5% headroom)。
    t = _FakeTransport(get_order_seq=["FILLED"])
    result, ledger, _audit, _v = _run_batch(t, weights={"BTC": 1.0}, aum=_EQUITY * 0.95)
    assert result.tier == AUTO
    assert not result.rejected_by_gate
    assert ledger.all_positions()["BTC"] > 0


def test_futures_shaped_limits_would_reject_full_position():
    # 对照:同一个 w=1.0 在期货形 net_cap=0.3×aum 下确实被 gate 拒——证明现货口径是必要而非放松
    from services.risk.pre_trade.gate import PreTradeRiskGate as _Gate
    gate = _Gate(gate_limits({"BTC"}, gross_cap=_EQUITY * 2, net_cap=_EQUITY * 0.3,
                             single_order_cap=_EQUITY * 0.5, daily_loss_cap=_EQUITY * 0.1),
                 kill_switch=KillSwitch())
    venue = _venue(_FakeTransport())
    ledger = LiveLedger(":memory:", create=True)
    result = submit_live_batch(
        weights={"BTC": 1.0}, prices={"BTC": _PX}, aum=_EQUITY * 0.95, current_positions={},
        gate=gate, ctx_factory=_ctx(), venue=venue, mandate=_MANDATE,
        validation_attestation=_ATTESTATION, ledger=ledger, audit=GlobalAuditLog(),
        strategy_id="btc_spot_trend", decided_ts=_NOW, now=_NOW, poll_sleep=lambda: None,
        diagnostic_only=True)
    assert result.rejected_by_gate, "futures-shaped net cap must reject a full-position long"


# --- 取消竞态:轮询打满仍 NEW → 主动撤单 → 撤单被拒(-2011)→ 回查见已成交 ---

def test_cancel_race_resolves_via_authoritative_requery():
    # 5 次轮询全 NEW(打满)→ 主动撤单被 -2011 拒(与成交赛跑)→ 第 6 次 get_order 回查权威 FILLED
    t = _FakeTransport(get_order_seq=["NEW"] * 5 + ["FILLED"], cancel_result="race_rejected")
    result, ledger, audit, _v = _run_batch(t)
    # 轮询耗尽 → _cancel_and_requery:撤单被 -2011 拒(已终态/不存在)→ get_order 回查权威 FILLED
    assert result.tier == AUTO
    orders = ledger.orders()
    assert orders[0]["status"] == "FILLED"
    assert ledger.all_positions()["BTC"] > 0
    outcomes = [e for e in audit.events if e.action == "LIVE_ORDER_TIMEOUT_CANCEL"]
    assert outcomes, "cancel-race path must leave a timeout-cancel audit event"


def test_cancel_after_partial_fill_records_partial_not_zero():
    t = _FakeTransport(get_order_seq=["NEW"] * 10, cancel_result="partial_fill")
    result, ledger, audit, _v = _run_batch(t)
    orders = ledger.orders()
    assert orders[0]["status"] == "CANCELED"
    # 撤单响应带 0.004 BTC 部分成交——必须如实记账,绝不能当成"撤单=零成交"
    assert ledger.all_positions()["BTC"] == pytest.approx(0.004)


# --- 崩溃恢复:上一轮 PENDING_NEW 遗留单,resolve_unconfirmed_orders 补记 ---

def test_crash_recovery_resolves_pending_new_via_venue_authority():
    ledger = LiveLedger(":memory:", create=True)
    ledger.record_submission("cid-recover", "BTC", OrderSide.BUY, 0.005,
                             strategy_id="btc_spot_trend", submitted_at=_NOW,
                             status="PENDING_NEW")
    t = _FakeTransport()
    t.placed.append({"quantity": "0.005"})                # 交易所侧该单已存在且已成交
    venue = _venue(t)
    recovery = resolve_unconfirmed_orders(ledger, venue, now=_NOW)
    assert recovery["resolved"][0]["status"] == "FILLED"
    assert ledger.orders()[0]["status"] == "FILLED"
    assert ledger.all_positions()["BTC"] == pytest.approx(0.005)


# --- fail-closed:无 attestation / 无 mandate → NEEDS_HUMAN_REVIEW,零下单 ---

def test_missing_attestation_blocks_before_any_venue_write():
    t = _FakeTransport()
    result, ledger, audit, _v = _run_batch(t, attestation=None)
    assert result.tier == NEEDS_HUMAN_REVIEW
    assert not t.placed
    assert ledger.orders() == []


def test_missing_mandate_blocks_before_any_order():
    t = _FakeTransport()
    result, ledger, _audit, _v = _run_batch(t, mandate=None)
    assert result.tier == NEEDS_HUMAN_REVIEW
    assert not t.placed
    assert ledger.orders() == []


def test_mandate_exchange_mismatch_blocks():
    wrong = Mandate(mandate_id="m-x", venue="mainnet", exchange="binance",
                    allowed_instruments=frozenset({"BTC"}),
                    max_single_order_notional=1e9, max_gross_notional=1e9,
                    max_net_notional=1e9)
    t = _FakeTransport()
    result, _ledger, _audit, _v = _run_batch(t, mandate=wrong)
    assert result.tier == NEEDS_HUMAN_REVIEW
    assert not t.placed


# --- 现货现金门(1.4,驱动层 pre-submit,纯函数) ---

def test_spot_cash_shortfall_passes_when_cash_covers_buys():
    reason = spot_cash_shortfall({"BTC": 1.0}, {"BTC": _PX}, {}, deploy_aum=1000.0,
                                 free_quote=1000.0)
    assert reason is None


def test_spot_cash_shortfall_fails_closed_when_insufficient():
    reason = spot_cash_shortfall({"BTC": 1.0}, {"BTC": _PX}, {}, deploy_aum=1000.0,
                                 free_quote=500.0)
    assert reason is not None and "spot_cash_insufficient" in reason


def test_spot_cash_shortfall_only_charges_net_new_buys():
    # 已持有 0.01 BTC(价值 $600),目标 $1000 → 只需为 $400 增量备现金
    reason = spot_cash_shortfall({"BTC": 1.0}, {"BTC": _PX}, {"BTC": 0.01},
                                 deploy_aum=1000.0, free_quote=450.0)
    assert reason is None
    reason2 = spot_cash_shortfall({"BTC": 1.0}, {"BTC": _PX}, {"BTC": 0.01},
                                  deploy_aum=1000.0, free_quote=300.0)
    assert reason2 is not None


def test_spot_cash_shortfall_ignores_sells():
    # 信号关(w=0 → 平仓卖出)不需要现金
    assert spot_cash_shortfall({"BTC": 0.0}, {"BTC": _PX}, {"BTC": 0.01},
                               deploy_aum=1000.0, free_quote=0.0) is None


# --- driver 现货 gate 限额参数(1.4,gate_limits 新增显式参数) ---

def test_gate_limits_spot_params_are_explicit():
    limits = gate_limits(["BTC"], gross_cap=100.0, net_cap=100.0, single_order_cap=100.0,
                         daily_loss_cap=10.0, max_leverage=1.0, max_single_name_pct=1.0)
    assert limits.max_leverage == 1.0
    assert limits.max_single_name_pct == 1.0
    assert limits.max_net_exposure == 100.0


def test_gate_limits_defaults_unchanged_for_futures_callers():
    limits = gate_limits(["BTC"], gross_cap=100.0, net_cap=30.0, single_order_cap=50.0,
                         daily_loss_cap=10.0)
    assert limits.max_leverage == 3.0                  # 期货形默认值零回归
    assert limits.max_single_name_pct == 0.5
