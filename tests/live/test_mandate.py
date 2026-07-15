"""分级授权 mandate(live/governance/mandate.py):PreTradeRiskGate 批准之后的第二层——
限额内 AUTO(机械执行),否则 NEEDS_HUMAN_REVIEW。fail-closed 到底,绝不默认 AUTO。设计:
docs/engineering/specs/2026-07-04-live-execution-stack-design.md §3.6。"""
import pytest

from live.contracts import LiveOrderRequest
from live.governance.mandate import AUTO, NEEDS_HUMAN_REVIEW, Mandate, evaluate_mandate
from quantos.contracts.trading import OrderSide

_M = Mandate(
    mandate_id="m1", venue="binance_testnet", exchange="binance",
    allowed_instruments=frozenset({"BTC", "ETH"}),
    max_single_order_notional=1000.0, max_gross_notional=2000.0, max_net_notional=1500.0,
    per_instrument_max_notional=(("BTC", 800.0),),
    approved_by=("alice",), issued_at=0, expires_at=10_000,
)


def _order(inst, side, qty, cid="c"):
    return LiveOrderRequest(instrument_id=inst, side=side, quantity=qty, client_order_id=cid)


def test_no_mandate_is_needs_review_even_for_empty_batch():
    d = evaluate_mandate([], {}, None, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "mandate_missing" in d.reasons


def test_expired_mandate_needs_review():
    d = evaluate_mandate([], {}, _M, venue="binance_testnet", exchange="binance", now=999_999)
    assert d.tier == NEEDS_HUMAN_REVIEW and "mandate_expired" in d.reasons


def test_venue_mismatch_needs_review():
    orders = [_order("BTC", OrderSide.BUY, 0.001)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="bybit_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "venue_mismatch" in d.reasons


def test_empty_batch_with_valid_mandate_is_auto():
    d = evaluate_mandate([], {}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == AUTO


def test_within_all_limits_is_auto():
    orders = [_order("BTC", OrderSide.BUY, 0.01, "c1"), _order("ETH", OrderSide.SELL, 0.1, "c2")]
    prices = {"BTC": 60000.0, "ETH": 3000.0}   # BTC notional=$600, ETH notional=$300
    d = evaluate_mandate(orders, prices, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == AUTO and d.reasons == ()


def test_instrument_not_in_allowlist_needs_review():
    orders = [_order("SOL", OrderSide.BUY, 1.0)]
    d = evaluate_mandate(orders, {"SOL": 100.0}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("instrument_not_allowed" in r for r in d.reasons)


def test_single_order_notional_exceeded_needs_review():
    orders = [_order("BTC", OrderSide.BUY, 0.1)]           # 0.1 * 60000 = $6000 > single cap $1000
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("single_order_notional_exceeded" in r for r in d.reasons)


def test_gross_notional_exceeded_needs_review():
    orders = [_order("BTC", OrderSide.BUY, 0.015, "c1"), _order("ETH", OrderSide.SELL, 0.4, "c2")]
    # BTC $900 + ETH $1200 = $2100 gross > max_gross_notional $2000; both individually under single cap
    d = evaluate_mandate(orders, {"BTC": 60000.0, "ETH": 3000.0}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "gross_notional_exceeded" in d.reasons


def test_net_notional_exceeded_needs_review():
    m = Mandate(mandate_id="m2", venue="binance_testnet", exchange="binance", allowed_instruments=frozenset({"BTC", "ETH"}),
               max_single_order_notional=2000.0, max_gross_notional=5000.0, max_net_notional=500.0)
    orders = [_order("BTC", OrderSide.BUY, 0.01, "c1"), _order("ETH", OrderSide.BUY, 0.1, "c2")]
    # 同向:BTC $600 + ETH $300 = net $900 > max_net $500(gross $900 < max_gross $5000,不触发 gross)
    d = evaluate_mandate(orders, {"BTC": 60000.0, "ETH": 3000.0}, m, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "net_notional_exceeded" in d.reasons


def test_per_instrument_cap_accounts_for_existing_position():
    # 已有 BTC 仓位 0.01(=$600),再买 0.005(=$300)-> 累计 $900 > per-instrument cap $800
    orders = [_order("BTC", OrderSide.BUY, 0.005)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="binance_testnet", exchange="binance", now=1,
                         current_positions={"BTC": 0.01})
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("per_instrument_cap_exceeded" in r for r in d.reasons)


def test_per_instrument_cap_ok_with_existing_position_when_under_cap():
    orders = [_order("BTC", OrderSide.BUY, 0.001)]        # existing 0.005*60000=$300 + $60 = $360 < $800
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="binance_testnet", exchange="binance", now=1,
                         current_positions={"BTC": 0.005})
    assert d.tier == AUTO


def test_missing_price_needs_review():
    orders = [_order("BTC", OrderSide.BUY, 0.01)]
    d = evaluate_mandate(orders, {}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("no_price" in r for r in d.reasons)


def test_evaluation_error_defaults_to_review_not_auto():
    # 批次非空但元素不是合法 LiveOrderRequest(无 .instrument_id)-> 内部 AttributeError
    # -> fail-closed 到 NEEDS_HUMAN_REVIEW,绝不 AUTO(注意:None/[] 都是"空批次"->AUTO 的合法输入,
    # 不是异常输入——这里用非空但畸形的元素才能真正触发求值异常)
    d = evaluate_mandate(["not-a-valid-order"], {}, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("evaluation_error" in r for r in d.reasons)


def test_empty_allowed_instruments_rejects_everything():
    m = Mandate(mandate_id="m3", venue="binance_testnet", exchange="binance", allowed_instruments=frozenset(),
               max_single_order_notional=1e9, max_gross_notional=1e9, max_net_notional=1e9)
    orders = [_order("BTC", OrderSide.BUY, 0.001)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, m, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW


def test_exchange_mismatch_needs_review():
    # 两家交易所的 testnet 都叫 "testnet"——mandate 必须显式签署交易所域,给 Binance 签的
    # mandate 绝不能被 Bybit 误认(2026-07-05)
    orders = [_order("BTC", OrderSide.BUY, 0.001)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, _M, venue="binance_testnet", exchange="bybit",
                         now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "exchange_mismatch" in d.reasons


def test_exchange_unset_on_mandate_needs_review():
    m = Mandate(mandate_id="m4", venue="binance_testnet", allowed_instruments=frozenset({"BTC"}),
               max_single_order_notional=1e9, max_gross_notional=1e9, max_net_notional=1e9)
    assert m.exchange == ""                                    # 未签署交易所域
    orders = [_order("BTC", OrderSide.BUY, 0.001)]
    d = evaluate_mandate(orders, {"BTC": 60000.0}, m, venue="binance_testnet", exchange="binance",
                         now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW and "exchange_unset" in d.reasons


# --- 账户级 gross/net 聚合(2026-07-06 审计 P1-3/P1-2):gross/net 此前只算当批订单,
# --- 一批各自 < max_gross 仍可让账户总敞口(已有仓位 + 本批)超签署上限;多策略共享同一
# --- mandate 时第二策略看不到第一策略的敞口。账户级投影(现有敞口 + 本批 delta)只新增
# --- reason(永不放宽当批检查),因 live 传的是账户级仓位,同时挡住多策略超发。 ---

def test_account_level_gross_aggregates_existing_position_over_mandate():
    # 已有 BTC 空头 $1500(-0.025 @ 60000);本批 ETH BUY 0.3 @ 3000 = $900:单笔 < $1000、
    # 当批 gross $900 < $2000(旧逻辑 AUTO)。账户 gross = |−1500| + |900| = $2400 > $2000。
    # 用空头让 net 部分抵消($−600,不触发 net),干净地只考核 gross。
    orders = [_order("ETH", OrderSide.BUY, 0.3, "c1")]
    d = evaluate_mandate(orders, {"BTC": 60000.0, "ETH": 3000.0}, _M,
                         venue="binance_testnet", exchange="binance", now=1,
                         current_positions={"BTC": -0.025})
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("account_gross_notional_exceeded" in r for r in d.reasons)


def test_account_level_net_aggregates_existing_same_direction():
    # 已有 ETH 多头 $1200(0.4 @ 3000)+ 本批 ETH BUY 0.3 @ 3000 = $900... 但 ETH 会触发
    # per-instrument?_M 只对 BTC 设 cap。account net = $1200 + $900 = $2100 > max_net $1500。
    # 当批 net = $900 < $1500(旧逻辑不触发 net)。
    orders = [_order("ETH", OrderSide.BUY, 0.3, "c1")]
    d = evaluate_mandate(orders, {"ETH": 3000.0}, _M,
                         venue="binance_testnet", exchange="binance", now=1,
                         current_positions={"ETH": 0.4})
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("account_net_notional_exceeded" in r for r in d.reasons)


def test_account_position_unpriced_fails_closed():
    # 持有一笔无法定价的非零仓位 -> 无法诚实计算账户 gross -> 升人工(fail-closed)
    orders = [_order("ETH", OrderSide.BUY, 0.1, "c1")]        # $300,当批各项内
    d = evaluate_mandate(orders, {"ETH": 3000.0}, _M, venue="binance_testnet", exchange="binance",
                         now=1, current_positions={"BTC": 0.02})   # 持 BTC 但未提供 BTC 价
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("account_position_unpriced" in r for r in d.reasons)


def test_account_level_check_does_not_relax_within_limits():
    # 已有小仓位 + 小单,账户 gross/net 仍在限内 -> 不新增 account 类 reason(不误升人工)
    orders = [_order("ETH", OrderSide.BUY, 0.1, "c1")]        # $300
    d = evaluate_mandate(orders, {"BTC": 60000.0, "ETH": 3000.0}, _M,
                         venue="binance_testnet", exchange="binance", now=1,
                         current_positions={"BTC": 0.005})    # $300 long; account gross $600 < $2000
    assert d.tier == AUTO and d.reasons == ()


# --- 2026-07-05 安全审查 HIGH 发现:NaN 参与比较恒为 False,静默绕过限额检查 ---
# 纵深防御:即便 mandate_store 已经在加载时挡住了,evaluate_mandate 自己也不该信任
# "price 参数是有限数"这个假设(price 来自实时行情,不是 mandate 文件)。

def test_nan_price_input_fails_closed_not_silently_passes():
    orders = [_order("BTC", OrderSide.BUY, 1.0)]
    d = evaluate_mandate(orders, {"BTC": float("nan")}, _M, venue="binance_testnet",
                         exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW


def test_infinite_price_input_fails_closed_not_silently_passes():
    orders = [_order("BTC", OrderSide.BUY, 1.0)]
    d = evaluate_mandate(orders, {"BTC": float("inf")}, _M, venue="binance_testnet",
                         exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW


def test_mandate_with_nan_threshold_fails_closed_even_if_constructed_directly():
    # 即便有人绕过 mandate_store 直接构造 Mandate(如未来新增的调用路径),evaluate_mandate
    # 本身也不该被 NaN 阈值架空——纵深防御,不能只靠加载层一道关卡
    m = Mandate(mandate_id="m", venue="binance_testnet", exchange="binance",
               allowed_instruments=frozenset({"BTC"}), max_single_order_notional=float("nan"),
               max_gross_notional=1e9, max_net_notional=1e9)
    orders = [_order("BTC", OrderSide.BUY, 1000.0)]      # 名义额 $60,000,000
    d = evaluate_mandate(orders, {"BTC": 60000.0}, m, venue="binance_testnet",
                         exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW


# --- LIMIT 扩展(2026-07-06 战线②,批判必须修改 #5):名义额口径 = BUY 取
# max(决策价,限价)/SELL 取 min(决策价,限价)——保守、零成本修正;偏差源是锚(实时盘口 mid)
# 相对决策价的漂移(可能滞后 1-3 天),不是 band 本身(band 只有数十 bps) ---

def test_limit_buy_notional_higher_price_pushes_over_single_order_cap():
    # single cap=$1000(见 _M)。0.015 * 决策价 60000 = $900(不超);限价 70000 -> $1050(超)
    limit_order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                                   client_order_id="c1", order_type="LIMIT", limit_price=70000.0)
    d = evaluate_mandate([limit_order], {"BTC": 60000.0}, _M, venue="binance_testnet",
                         exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("single_order_notional_exceeded" in r for r in d.reasons)


def test_limit_buy_notional_lower_limit_price_does_not_shrink_below_decision_price():
    # BUY 限价 LOWER 于决策价:保守口径取 max,名义额仍按决策价评估(不因限价更低而"省"出额度)
    limit_order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.015,
                                   client_order_id="c1", order_type="LIMIT", limit_price=50000.0)
    d_limit = evaluate_mandate([limit_order], {"BTC": 60000.0}, _M, venue="binance_testnet",
                              exchange="binance", now=1)
    market_order = _order("BTC", OrderSide.BUY, 0.015, "c2")
    d_market = evaluate_mandate([market_order], {"BTC": 60000.0}, _M, venue="binance_testnet",
                                exchange="binance", now=1)
    assert d_limit.reasons == d_market.reasons and d_limit.tier == d_market.tier


def test_limit_sell_notional_lower_limit_price_does_not_shrink_below_decision_price():
    # SELL 限价 LOWER 于决策价:保守口径恒取 max(与 BUY 同公式),名义额仍按更大的决策价
    # 评估,不因限价更低而"省"出额度(2026-07-06 对抗式复审修复前,SELL 分支错用 min,
    # 会在此场景"侥幸"给出同样结果——真正的反例在下一测试)。
    limit_order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.SELL, quantity=0.015,
                                   client_order_id="c1", order_type="LIMIT", limit_price=50000.0)
    d_limit = evaluate_mandate([limit_order], {"BTC": 60000.0}, _M, venue="binance_testnet",
                              exchange="binance", now=1)
    market_order = _order("BTC", OrderSide.SELL, 0.015, "c2")
    d_market = evaluate_mandate([market_order], {"BTC": 60000.0}, _M, venue="binance_testnet",
                                exchange="binance", now=1)
    assert d_limit.reasons == d_market.reasons and d_limit.tier == d_market.tier


def test_limit_sell_notional_higher_limit_price_escalates_via_per_instrument_cap():
    # 2026-07-06 对抗式复审 CONFIRMED 修复的反例:决策价评估的名义额本身未超限(0.013*60000=
    # $780 < BTC per-instrument cap $800),但市场在决策之后上涨、限价随实时盘口走高到 65000
    # (0.013*65000=$845,超过 $800)——修复前的 SELL-min() bug 会捡回更小的决策价、把这单
    # 静默放成 AUTO;修复后恒取 max,必须正确升人工。
    limit_order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.SELL, quantity=0.013,
                                   client_order_id="c1", order_type="LIMIT", limit_price=65000.0)
    d = evaluate_mandate([limit_order], {"BTC": 60000.0}, _M, venue="binance_testnet",
                         exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("per_instrument_cap_exceeded" in r for r in d.reasons)


def test_limit_order_without_limit_price_falls_back_to_decision_price_only():
    # 防御性:即便有人绕过契约校验直接拼出 order_type="LIMIT" 但 limit_price=None 的对象
    # (真实构造路径已被 __post_init__ 挡死,这里模拟 duck-typed 假订单),不能崩溃
    class _FakeLimitOrder:
        instrument_id = "BTC"
        side = OrderSide.BUY
        quantity = 0.01
        order_type = "LIMIT"
        limit_price = None

    d = evaluate_mandate([_FakeLimitOrder()], {"BTC": 60000.0}, _M, venue="binance_testnet",
                         exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW              # fail-closed(异常求值),绝不 AUTO


# --- unknown order_type 恒升人工(2026-07-06,§5 纵深防御;构造期已挡死,这里用
# duck-typed 假订单模拟"万一有别的构造路径"的场景) ---

def test_unknown_order_type_escalates_to_human_review():
    class _FakeUnknownOrder:
        instrument_id = "BTC"
        side = OrderSide.BUY
        quantity = 0.01
        order_type = "STOP_LIMIT"
        limit_price = None

    d = evaluate_mandate([_FakeUnknownOrder()], {"BTC": 60000.0}, _M, venue="binance_testnet",
                         exchange="binance", now=1)
    assert d.tier == NEEDS_HUMAN_REVIEW
    assert any("unknown_order_type" in r for r in d.reasons)


def test_market_orders_missing_order_type_attr_still_evaluate_normally():
    # getattr(o, "order_type", "MARKET") 的向后兼容:LiveOrderRequest 本身总有该字段
    # (默认 "MARKET"),真实调用路径不会走到这个分支,但纵深防御不能假设它一定存在
    orders = [_order("BTC", OrderSide.BUY, 0.01, "c1"), _order("ETH", OrderSide.SELL, 0.1, "c2")]
    prices = {"BTC": 60000.0, "ETH": 3000.0}
    d = evaluate_mandate(orders, prices, _M, venue="binance_testnet", exchange="binance", now=1)
    assert d.tier == AUTO and d.reasons == ()
