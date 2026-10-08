"""tools/live_track_advance.py 里可离线测的纯逻辑(gate_limits/make_ctx_factory/PreviewVenue)。
main() 本身是薄胶水(数据刷新+真实 venue I/O),不单测——同 tools/forward_track_advance.py 的
既有惯例(核心逻辑在被测模块里,驱动器用真实运行验证)。设计:
docs/engineering/specs/2026-07-04-live-execution-stack-design.md §0 延伸。"""
import pytest

from live.contracts import LiveOrderRequest, VenueOrderResult
from quantos.contracts.trading import OrderSide
from tools.live_track_advance import PreviewVenue, gate_limits, make_ctx_factory

_UNIT_SCALE = 1_000_000


class _FakeIntent:
    def __init__(self, instrument_id):
        self.instrument_id = instrument_id


def test_gate_limits_bare_quantity_fields_are_effectively_unlimited():
    limits = gate_limits(["BTC", "ETH"], gross_cap=10000.0, net_cap=1500.0,
                        single_order_cap=2500.0, daily_loss_cap=500.0)
    # 多资产 book 里裸数量阈值不该成为真正的约束(见模块 docstring:不同币单价差好几个数量级)
    assert limits.fat_finger_qty > 10 ** 10
    assert limits.default_max_position_qty > 10 ** 10


def test_gate_limits_notional_fields_come_from_params():
    limits = gate_limits(["BTC"], gross_cap=10000.0, net_cap=1500.0,
                        single_order_cap=2500.0, daily_loss_cap=500.0)
    assert limits.max_gross_exposure == 10000.0
    assert limits.max_net_exposure == 1500.0
    assert limits.max_order_notional == 2500.0
    assert limits.daily_loss_limit == 500.0
    assert limits.allowed_instruments == frozenset({"BTC"})


def _factory_kwargs(**overrides):
    base = dict(prices={"BTC": 60000.0}, aum=100_000.0, now=1_700_000_000_000,
               price_as_of=1_700_000_000_000, current_positions={}, day_pnl=0.0,
               peak_equity=100_000.0)
    base.update(overrides)
    prices = base.pop("prices")
    return prices, base


def test_make_ctx_factory_scales_reference_price_by_unit_scale():
    prices, kw = _factory_kwargs()
    factory = make_ctx_factory(prices, **kw)
    ctx = factory(_FakeIntent("BTC"))
    assert ctx.market.reference_price == pytest.approx(60000.0 / _UNIT_SCALE)
    assert ctx.market.known is True
    assert ctx.account.equity == 100_000.0


def test_make_ctx_factory_handles_missing_price_honestly():
    prices, kw = _factory_kwargs(prices={})
    factory = make_ctx_factory(prices, **kw)
    ctx = factory(_FakeIntent("GHOST"))
    assert ctx.market.known is False
    assert ctx.market.reference_price is None


# --- 频率护栏回填(审计 P1-5):recent_timestamps 透传进 ctx,接活 check_rate_limit ---

def test_make_ctx_factory_threads_recent_timestamps():
    prices, kw = _factory_kwargs()
    ctx = make_ctx_factory(prices, recent_timestamps=(111, 222, 333), **kw)(_FakeIntent("BTC"))
    assert ctx.recent_timestamps == (111, 222, 333)


def test_make_ctx_factory_threads_recent_fingerprints_and_statuses():
    prices, kw = _factory_kwargs()
    ctx = make_ctx_factory(
        prices,
        recent_order_fingerprints=("fp1", "fp2"),
        recent_order_statuses=("FILLED", "CANCELED"),
        **kw,
    )(_FakeIntent("BTC"))
    assert ctx.recent_order_fingerprints == ("fp1", "fp2")
    assert ctx.recent_order_statuses == ("FILLED", "CANCELED")


def test_make_ctx_factory_recent_timestamps_default_empty_keeps_old_behavior():
    # 默认空 -> check_rate_limit 恒 no-op(与接线前行为一致,零回归)
    prices, kw = _factory_kwargs()
    ctx = make_ctx_factory(prices, **kw)(_FakeIntent("BTC"))
    assert ctx.recent_timestamps == ()


# --- 真实 ADV/波动接线(Phase 4 子项1,P1-5 深化):升级结构性失效的 check_liquidity/check_volatility ---

def test_make_ctx_factory_adv_vol_default_none_keeps_placeholder():
    # 未提供(None)-> 保留占位符 1e12/0.8(向后兼容零回归,gate 不因此误拒)
    prices, kw = _factory_kwargs()
    ctx = make_ctx_factory(prices, **kw)(_FakeIntent("BTC"))
    assert ctx.market.est_daily_volume == 1e12 and ctx.market.instrument_vol == 0.8


def test_make_ctx_factory_uses_real_adv_vol_when_provided():
    prices, kw = _factory_kwargs()
    ctx = make_ctx_factory(prices, adv_units={"BTC": 2e7}, inst_vol={"BTC": 0.9}, **kw)(_FakeIntent("BTC"))
    assert ctx.market.est_daily_volume == 2e7 and ctx.market.instrument_vol == 0.9


def test_make_ctx_factory_missing_adv_vol_fails_closed_none():
    # 提供了 dict 但缺该标的 -> None -> check_liquidity/check_volatility fail-closed 拒(缺数据不放行)
    prices, kw = _factory_kwargs()
    ctx = make_ctx_factory(prices, adv_units={}, inst_vol={}, **kw)(_FakeIntent("GHOST"))
    assert ctx.market.est_daily_volume is None and ctx.market.instrument_vol is None


# --- 2026-07-05 机构审计修复:新鲜度门真实接线 + 账户上下文去虚构 ---

def test_price_as_of_is_real_decision_time_not_wall_clock():
    # 修复前 price_as_of 恒等于 now,新鲜度门 (now - price_as_of > max_staleness) 数学上永远
    # 不触发。decided_ts 明显早于 now 时,ctx 必须诚实带出这个差值。
    prices, kw = _factory_kwargs(now=1_700_000_100_000, price_as_of=1_700_000_000_000)
    factory = make_ctx_factory(prices, **kw)
    ctx = factory(_FakeIntent("BTC"))
    assert ctx.market.price_as_of == 1_700_000_000_000
    assert ctx.now - ctx.market.price_as_of == 100_000


def test_real_positions_appear_in_account_context():
    prices, kw = _factory_kwargs(current_positions={"BTC": 0.01})
    factory = make_ctx_factory(prices, **kw)
    ctx = factory(_FakeIntent("BTC"))
    assert len(ctx.account.positions) == 1
    assert ctx.account.position_qty("BTC") == 10_000       # 0.01 * UNIT_SCALE


def test_real_day_pnl_and_peak_equity_flow_through():
    prices, kw = _factory_kwargs(day_pnl=-250.0, peak_equity=120_000.0, aum=100_000.0)
    factory = make_ctx_factory(prices, **kw)
    ctx = factory(_FakeIntent("BTC"))
    assert ctx.account.day_pnl == -250.0
    assert ctx.account.peak_equity == 120_000.0
    # 回撤门必须能用真实峰值算出真实回撤(120000 峰值、100000 权益 -> ~16.7% 回撤),
    # 而不是修复前 peak_equity=aum 恒等式导致的回撤恒 0
    assert (ctx.account.peak_equity - ctx.account.equity) / ctx.account.peak_equity > 0.15


# --- PreviewVenue ---

class _FakeRealVenue:
    venue_mode = "testnet"
    venue_name = "fake"

    def __init__(self):
        self.place_order_calls = []

    def place_order(self, order):
        self.place_order_calls.append(order)
        return VenueOrderResult(venue_order_id="REAL-SHOULD-NEVER-HAPPEN",
                                client_order_id=order.client_order_id,
                                instrument_id=order.instrument_id, status="FILLED",
                                filled_qty=order.quantity, avg_price=100.0)

    def get_order(self, instrument_id, client_order_id):
        return "should-not-be-called-in-preview"

    def exchange_filters(self):
        return {"BTC": "real-filters"}

    def account(self):
        return {"totalWalletBalance": "5000.0"}

    def positions(self):
        return {"BTC": "real-position"}


def test_preview_venue_never_calls_real_place_order():
    real = _FakeRealVenue()
    preview = PreviewVenue(real)
    order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=0.01,
                             client_order_id="c1")
    result = preview.place_order(order)
    assert real.place_order_calls == []                      # 真 venue 从未被调用
    assert preview.would_submit == [order]
    assert result.status == "PREVIEW_NOT_SUBMITTED"
    assert result.filled_qty == 0.0


def test_preview_venue_get_order_returns_none_not_real_call():
    preview = PreviewVenue(_FakeRealVenue())
    assert preview.get_order("BTC", "c1") is None             # 不透传真实 venue(预览从未真提交)


def test_preview_venue_readonly_methods_pass_through_to_real_venue():
    real = _FakeRealVenue()
    preview = PreviewVenue(real)
    assert preview.exchange_filters() == {"BTC": "real-filters"}
    assert preview.account() == {"totalWalletBalance": "5000.0"}
    assert preview.positions() == {"BTC": "real-position"}
    assert preview.venue_mode == "testnet"


def test_preview_venue_book_ticker_passes_through_to_real_venue():
    # LIMIT 定价锚来源(2026-07-06 战线②):book_ticker 是只读方法,preview 模式必须像
    # exchange_filters/account/positions 一样透传真实 venue(定价基于真实市况,不是造假数据)
    class _FakeRealVenueWithBookTicker(_FakeRealVenue):
        def book_ticker(self, instrument_id):
            return {"bid": 59990.0, "ask": 60010.0}

    real = _FakeRealVenueWithBookTicker()
    preview = PreviewVenue(real)
    assert preview.book_ticker("BTC") == {"bid": 59990.0, "ask": 60010.0}


# --- LIMIT CLI flag(2026-07-06 战线②):默认 market,limit_pricing=None,驱动器默认行为
# --- 一个字节不变(批判必须修改④ open question:CLI flag 而非 mandate schema) ---

def test_resolve_limit_pricing_defaults_to_market_with_none_pricing():
    from tools.live_track_advance import resolve_limit_pricing
    order_type, limit_pricing = resolve_limit_pricing([])
    assert order_type == "market" and limit_pricing is None


def test_resolve_limit_pricing_no_order_type_flag_among_other_args():
    from tools.live_track_advance import resolve_limit_pricing
    order_type, limit_pricing = resolve_limit_pricing(["prog", "--submit", "--venue", "binance"])
    assert order_type == "market" and limit_pricing is None


def test_resolve_limit_pricing_limit_flag_enables_limit_pricing():
    from live.execution.pricing import LimitPricing
    from tools.live_track_advance import resolve_limit_pricing
    order_type, limit_pricing = resolve_limit_pricing(["--order-type", "limit"])
    assert order_type == "limit"
    assert isinstance(limit_pricing, LimitPricing)


def test_resolve_limit_pricing_unknown_value_raises_systemexit():
    from tools.live_track_advance import resolve_limit_pricing
    with pytest.raises(SystemExit):
        resolve_limit_pricing(["--order-type", "bogus"])


# --- limit_fill_stats 数据源(2026-07-06,批判必须修改⑥):从账本 live_orders(quantity=
# --- 请求量)+ live_fills(quantity=实际成交量)按 client_order_id 关联算数量加权成交率——
# --- VenueOrderResult 不带请求量,ledger 才是唯一同时知道两者的地方 ---

def test_compute_limit_fill_stats_empty_ids_is_honest_zero(tmp_path):
    from live.execution.position_ledger import LiveLedger
    from tools.live_track_advance import compute_limit_fill_stats
    ledger = LiveLedger(str(tmp_path / "l.db"), create=True)
    stats = compute_limit_fill_stats(ledger, [])
    assert stats == {"n_limit": 0, "n_filled": 0, "n_canceled_zero_fill": 0, "n_partial": 0,
                     "fill_rate_qty_weighted": None}


def test_compute_limit_fill_stats_aggregates_fill_and_cancel_outcomes(tmp_path):
    from live.execution.position_ledger import LiveLedger
    from tools.live_track_advance import compute_limit_fill_stats
    ledger = LiveLedger(str(tmp_path / "l.db"), create=True)
    # c1: 全部成交
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 1.0, strategy_id="s", submitted_at=1,
                             status="FILLED", order_type="LIMIT", limit_price=60000.0)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 1.0, 60000.0, strategy_id="s",
                       filled_at=1)
    # c2: 撤单零成交
    ledger.record_submission("c2", "ETH", OrderSide.SELL, 2.0, strategy_id="s", submitted_at=1,
                             status="CANCELED", order_type="LIMIT", limit_price=3000.0)
    # c3: 部分成交后撤单(40%)
    ledger.record_submission("c3", "BTC", OrderSide.BUY, 1.0, strategy_id="s", submitted_at=1,
                             status="CANCELED", order_type="LIMIT", limit_price=60100.0)
    ledger.record_fill("f3", "c3", "BTC", OrderSide.BUY, 0.4, 60100.0, strategy_id="s",
                       filled_at=1)
    # c4: MARKET 单(不应计入 LIMIT 统计)
    ledger.record_submission("c4", "BTC", OrderSide.BUY, 1.0, strategy_id="s", submitted_at=1,
                             status="FILLED")
    ledger.record_fill("f4", "c4", "BTC", OrderSide.BUY, 1.0, 60000.0, strategy_id="s",
                       filled_at=1)

    stats = compute_limit_fill_stats(ledger, ["c1", "c2", "c3", "c4"])
    assert stats["n_limit"] == 3                     # c4 是 MARKET,不计入
    assert stats["n_filled"] == 1
    assert stats["n_canceled_zero_fill"] == 1
    assert stats["n_partial"] == 1
    # 请求量 1+2+1=4,成交量 1+0+0.4=1.4
    assert stats["fill_rate_qty_weighted"] == pytest.approx(1.4 / 4.0)


def test_compute_limit_fill_stats_ignores_ids_outside_the_batch(tmp_path):
    from live.execution.position_ledger import LiveLedger
    from tools.live_track_advance import compute_limit_fill_stats
    ledger = LiveLedger(str(tmp_path / "l.db"), create=True)
    ledger.record_submission("c1", "BTC", OrderSide.BUY, 1.0, strategy_id="s", submitted_at=1,
                             status="FILLED", order_type="LIMIT", limit_price=60000.0)
    ledger.record_fill("f1", "c1", "BTC", OrderSide.BUY, 1.0, 60000.0, strategy_id="s",
                       filled_at=1)
    # 另一笔历史 LIMIT 单不在本轮 client_order_ids 里 —— 不应污染本轮统计
    ledger.record_submission("c-old", "ETH", OrderSide.SELL, 5.0, strategy_id="s", submitted_at=0,
                             status="CANCELED", order_type="LIMIT", limit_price=1.0)
    stats = compute_limit_fill_stats(ledger, ["c1"])
    assert stats["n_limit"] == 1
    assert stats["fill_rate_qty_weighted"] == pytest.approx(1.0)


# --- _latest_weights_and_prices:信号行选择(2026-07-05 修复"最后一行 OI 全空 -> 权重为空") ---

def _panel_with_stale_last_row():
    """10 个标的、4 行:前 3 行 global_ls/close 齐全,最后一行 global_ls 全 None
    (模拟真实现象:K线刷新比 OI 指标快,最新行还没有持仓数据)。"""
    import services.research.alpha_expression as ax
    insts = [f"C{i}" for i in range(10)]
    ev = [1000, 2000, 3000, 4000]
    ls_full = [float(i + 1) for i in range(10)]
    close_full = [100.0 + i for i in range(10)]
    matrices = {
        "global_ls": [ls_full, ls_full, ls_full, [None] * 10],
        "close": [close_full, close_full, [c + 1 for c in close_full], close_full],
    }
    return ax.FieldPanel(instruments=insts, event_times=ev, matrices=matrices)


def test_latest_weights_walks_back_to_last_row_with_signal_coverage():
    from tools.live_track_advance import _latest_weights_and_prices
    panel = _panel_with_stale_last_row()
    weights, prices, decided_ts, ls_row, _evidence = _latest_weights_and_prices((panel, panel.instruments))
    assert weights                                  # 不再因最后一行全空而产出空 book
    assert decided_ts == 3000                       # 用的是最近一行有信号的行(诚实:决策带数据时间)
    assert prices["C0"] == 101.0                    # 价格取同一行(决策快照自洽)
    assert ls_row["C0"] == pytest.approx(1.0)       # 拥挤度门证据源:同一行的 global_ls


def test_latest_weights_all_rows_empty_is_honest_empty():
    import services.research.alpha_expression as ax
    from tools.live_track_advance import _latest_weights_and_prices
    insts = [f"C{i}" for i in range(10)]
    matrices = {"global_ls": [[None] * 10] * 3,
                "close": [[100.0] * 10] * 3}
    panel = ax.FieldPanel(instruments=insts, event_times=[1, 2, 3], matrices=matrices)
    weights, prices, decided_ts, ls_row, evidence = _latest_weights_and_prices((panel, insts))
    assert weights == {}                            # 回看窗口内全空 -> 诚实空,不硬凑
    assert decided_ts == 3
    assert ls_row == {}
    assert evidence == {}                           # 空 book -> 空证据


def _panel_with_evidence_history(n_rows=15):
    """够长历史(z 需 ≥12)+ volume 矩阵(ADV 需)+ close 变化(vol 需)。C0 global_ls 随时间爬升
    (拥挤度上升,会被 short 且有充足方差 -> 有限 z);其余名近恒定(z 零方差 -> None)。"""
    import services.research.alpha_expression as ax
    insts = [f"C{i}" for i in range(4)]
    ev = [1000 * (r + 1) for r in range(n_rows)]
    matrices = {"global_ls": [], "close": [], "volume": []}
    for r in range(n_rows):
        matrices["global_ls"].append([1.0 + r * 0.2, 2.0, 3.0, 4.0])
        matrices["close"].append([100.0 + r, 200.0 + r * 2, 300.0 + r, 400.0 - r])
        matrices["volume"].append([1000.0, 2000.0, 3000.0, 4000.0])
    return ax.FieldPanel(instruments=insts, event_times=ev, matrices=matrices)


def test_latest_weights_returns_per_leg_evidence():
    # 5-tuple 穿线:第 5 元素 evidence[name] = {adv_units, inst_vol, crowding_z},从同一决策行算
    from tools.live_track_advance import _latest_weights_and_prices
    panel = _panel_with_evidence_history()
    result = _latest_weights_and_prices((panel, panel.instruments))
    assert len(result) == 5
    weights, prices, decided_ts, ls_row, evidence = result
    assert weights and isinstance(evidence, dict)
    for name in weights:
        assert name in evidence
        assert evidence[name]["adv_units"] is not None     # volume present -> 真实 ADV
        assert evidence[name]["inst_vol"] is not None      # close 历史 -> 真实 vol
        assert "crowding_z" in evidence[name]              # z 键在(方差不足的名字为 None)


def test_vol_annualize_matches_threshold_units_and_passes_normal_vol():
    # vol-annualize 口径必须和 gate_limits 的 max_instrument_vol=5.0(年化)一致——配错会静默失配
    # (太小=门死、太大=误拒)。日频对数收益 pstdev × sqrt(365) = 年化。
    import math
    from live.execution.market_stats import realized_volatility
    from tools.live_track_advance import _VOL_ANNUALIZE, _VOL_WINDOW
    assert _VOL_ANNUALIZE == 365.0                       # drift-lock(签署口径)
    closes = [100.0 * (1.0 + 0.02 * math.sin(i)) for i in range(40)]   # ~2% 日波动的合成价
    v = realized_volatility(closes, window=_VOL_WINDOW, annualize=_VOL_ANNUALIZE)
    assert v is not None and 0.0 < v < 5.0               # 正常波动远在阈内 -> 过门(只缺数据/病态才拒)


def test_latest_weights_evidence_fail_closed_on_missing_data():
    import services.research.alpha_expression as ax
    from tools.live_track_advance import _latest_weights_and_prices
    insts = [f"C{i}" for i in range(4)]
    # 无 volume 矩阵 + 只 3 行(z 历史 <12)-> adv/z fail-closed None
    matrices = {"global_ls": [[1.0, 2.0, 3.0, 4.0]] * 3, "close": [[100.0, 210.0, 300.0, 390.0]] * 3}
    panel = ax.FieldPanel(instruments=insts, event_times=[1, 2, 3], matrices=matrices)
    weights, prices, decided_ts, ls_row, evidence = _latest_weights_and_prices((panel, insts))
    assert weights
    for name in weights:
        assert evidence[name]["adv_units"] is None         # 无 volume -> fail-closed None
        assert evidence[name]["crowding_z"] is None        # 历史 <12 -> None


# --- 2026-07-06 P0(空权重根因调查 CONFIRMED 修复):walkback 覆盖率门 + 跳批语义 ---

def _panel_with_marginal_then_full_rows():
    """r0/r1 满行(10/10 有 OI),r2 毛边行(恰 4/10——过 _legs 的绝对门槛 4 但远低于
    0.8×10 覆盖率),r3 全空(OI 归档还没到)。模拟归档逐 symbol 发布窗口内的真实缓存形态。"""
    import services.research.alpha_expression as ax
    insts = [f"C{i}" for i in range(10)]
    ls_full = [1.0 + i * 0.1 for i in range(10)]
    ls_marginal = ls_full[:4] + [None] * 6
    close_full = [100.0 + i for i in range(10)]
    matrices = {
        "global_ls": [ls_full, ls_full, ls_marginal, [None] * 10],
        "close": [close_full, close_full, close_full, close_full],
    }
    return ax.FieldPanel(instruments=insts, event_times=[1000, 2000, 3000, 4000],
                         matrices=matrices)


def test_walkback_skips_marginal_partial_coverage_row():
    # 修复前:r2 毛边行 4 个非 None 过 `if legs:` 即被接受 -> 2 腿退化 book;
    # 修复后:覆盖率 4/10 < 0.8×(窗口内最大 10) -> 跳过毛边行,落到 r1 满行
    from tools.live_track_advance import _latest_weights_and_prices
    panel = _panel_with_marginal_then_full_rows()
    weights, prices, decided_ts, ls_row, _evidence = _latest_weights_and_prices((panel, panel.instruments))
    assert decided_ts == 2000                       # 满行,不是毛边行的 3000
    assert len(weights) >= 4                        # 满截面 book,不是 2 腿退化 book


def test_walkback_uniform_low_coverage_universe_is_accepted():
    # 整个窗口都是 6/10(退市币现实)——覆盖率相对窗口内最大截面数,最新行照常接受
    import services.research.alpha_expression as ax
    from tools.live_track_advance import _latest_weights_and_prices
    insts = [f"C{i}" for i in range(10)]
    ls_row6 = [1.0 + i * 0.1 for i in range(6)] + [None] * 4
    close_full = [100.0 + i for i in range(10)]
    matrices = {"global_ls": [ls_row6, ls_row6, ls_row6],
                "close": [close_full, close_full, close_full]}
    panel = ax.FieldPanel(instruments=insts, event_times=[1, 2, 3], matrices=matrices)
    weights, prices, decided_ts, ls_row, _evidence = _latest_weights_and_prices((panel, insts))
    assert weights
    assert decided_ts == 3


def test_batch_skip_reason_empty_weights():
    from tools.live_track_advance import batch_skip_reason
    assert batch_skip_reason({}, crowding_blocked=False, expected_legs=None) is not None


def test_batch_skip_reason_crowding_block():
    from tools.live_track_advance import batch_skip_reason
    assert batch_skip_reason({"BTC": 0.5}, crowding_blocked=True, expected_legs=None) is not None


def test_batch_skip_reason_signal_shrink_below_ratio():
    # 上次决策 14 腿,这轮只剩 7 腿(< 0.8×14)——数据质量问题,跳批保仓,不是"平掉一半 book"
    from tools.live_track_advance import batch_skip_reason
    weights = {f"C{i}": 0.1 for i in range(7)}
    assert batch_skip_reason(weights, crowding_blocked=False, expected_legs=14) is not None


def test_batch_skip_reason_healthy_batch_returns_none():
    from tools.live_track_advance import batch_skip_reason
    weights = {f"C{i}": 0.1 for i in range(13)}
    assert batch_skip_reason(weights, crowding_blocked=False, expected_legs=14) is None
    assert batch_skip_reason({"BTC": 0.5}, crowding_blocked=False, expected_legs=None) is None


def test_maintenance_skip_reads_status_file_and_stops_adding_on_retired(tmp_path):
    # Phase 5 item 1:维护 sweep 快照标本策略 RETIRED/PAUSED -> 驱动器读到一条 skip 原因(停止新增)。
    import json
    from tools.live_track_advance import maintenance_skip
    p = tmp_path / "alpha_maintenance_status.json"
    json.dump({"strategies": {"crowd_contra": {"state": "RETIRED", "reasons": ["decay"]}}},
              open(p, "w"))
    assert maintenance_skip("crowd_contra", path=str(p)).startswith("maintenance_retired:")
    assert maintenance_skip("carry", path=str(p)) is None       # 只影响被降级的策略


def test_maintenance_skip_absent_file_is_none_byte_identical(tmp_path):
    # 快照不存在(未调度 sweep)-> None -> skip_reason 不变 -> 旧行为字节等价
    from tools.live_track_advance import maintenance_skip
    assert maintenance_skip("crowd_contra", path=str(tmp_path / "absent.json")) is None


def test_maintenance_skip_degraded_does_not_stop(tmp_path):
    # DEGRADED 只观察不停(保守加法,不越权自动停一个仍合规的策略)
    import json
    from tools.live_track_advance import maintenance_skip
    p = tmp_path / "m.json"
    json.dump({"strategies": {"crowd_contra": {"state": "DEGRADED", "reasons": ["ic"]}}}, open(p, "w"))
    assert maintenance_skip("crowd_contra", path=str(p)) is None


def test_maintenance_skip_prefers_authoritative_store_over_stale_json(tmp_path):
    import json

    from services.alpha.maintenance_store import MaintenanceStore
    from tools.live_track_advance import maintenance_skip

    status = tmp_path / "alpha_maintenance_status.json"
    database = tmp_path / "alpha_maintenance.db"
    json.dump({"strategies": {}}, open(status, "w"))
    snapshot = {
        "generated_at": 1000,
        "strategies": {"crowd_contra": {"state": "RETIRED", "reasons": ["decay"]}},
    }
    store = MaintenanceStore(str(database), create=True)
    store.save_snapshot(snapshot)
    store.close()

    assert maintenance_skip(
        "crowd_contra",
        path=str(status),
        db_path=str(database),
    ).startswith("maintenance_retired:")


def test_maintenance_skip_fails_closed_when_authoritative_store_is_corrupt(tmp_path):
    from services.alpha.maintenance_store import MaintenanceStore
    from tools.live_track_advance import maintenance_skip

    database = tmp_path / "alpha_maintenance.db"
    store = MaintenanceStore(str(database), create=True)
    store.save_snapshot({"generated_at": 1000, "strategies": {}})
    store._conn.execute("DROP TRIGGER maintenance_records_no_update")
    store._conn.execute("UPDATE maintenance_records SET snapshot_hash='evil'")
    store._conn.commit()
    store.close()

    assert maintenance_skip(
        "crowd_contra",
        path=str(tmp_path / "absent.json"),
        db_path=str(database),
    ) == "maintenance_state_invalid"


def test_binance_margin_extracts_balance_and_maint():
    from tools.live_track_advance import _binance_margin
    mb, mm = _binance_margin({"totalMarginBalance": "5000", "totalMaintMargin": "100"})
    assert mb == 5000.0 and mm == 100.0


def test_binance_margin_present_zero_is_zero_not_none():
    # 有仓但无维持要求(或空账户)-> 字段present="0" -> 0.0(evaluate_risk_state 据此判"无维持要求=安全")
    from tools.live_track_advance import _binance_margin
    mb, mm = _binance_margin({"totalMarginBalance": "100", "totalMaintMargin": "0"})
    assert mm == 0.0


def test_binance_margin_absent_fields_return_none_failclosed():
    from tools.live_track_advance import _binance_margin
    mb, mm = _binance_margin({})
    assert mb is None and mm is None      # 字段缺失 -> None -> 上游 fail-closed 记 breach


def test_bybit_margin_extracts_from_v5_wallet():
    from tools.live_track_advance import _bybit_margin
    acct = {"result": {"list": [{"totalMarginBalance": "3000", "totalMaintenanceMargin": "50"}]}}
    mb, mm = _bybit_margin(acct)
    assert mb == 3000.0 and mm == 50.0


def test_bybit_margin_absent_returns_none():
    from tools.live_track_advance import _bybit_margin
    assert _bybit_margin({"result": {"list": [{}]}}) == (None, None)
    assert _bybit_margin({}) == (None, None)


def test_each_venue_has_its_own_mandate_ledger_and_state_paths():
    # 一个 mandate 只授权一个交易所;不同交易所 = 不同账户,账本/状态/mandate 绝不共用
    from tools.live_track_advance import _VENUES, _venue_paths
    mandates = {v["mandate_file"] for v in _VENUES.values()}
    assert len(mandates) == len(_VENUES)
    ledgers = {_venue_paths(v["suffix"])["ledger"] for v in _VENUES.values()}
    assert len(ledgers) == len(_VENUES)


# --- 2026-07-06 安全审查 punch-list:peak_equity/day_state 竞态 + 崩溃安全 ---
# 风险哨兵(小时级)与周度驱动器共享同一批状态文件、各自独立进程、无锁——与 kill-switch
# 那次(2026-07-05)同一类竞态,见 concurrent-state-write-races 记忆。


def test_save_peak_equity_writes_when_higher_than_disk(tmp_path):
    from tools.live_track_advance import _load_peak_equity, _save_peak_equity
    path = str(tmp_path / "peak.json")
    _save_peak_equity(path, 100.0)
    _save_peak_equity(path, 150.0)
    assert _load_peak_equity(path, default=0.0) == 150.0


def test_save_peak_equity_never_lowers_a_concurrently_written_higher_peak(tmp_path):
    # 模拟真实竞态:driver 读到 peak=100(稍早的快照)-> 与此同时 sentinel 观测到更高权益,
    # 把磁盘上的 peak 写成 150 -> driver 基于自己那份稍旧的读数算出 100,调用 _save_peak_equity
    # 时绝不能把磁盘上 sentinel 刚写入的 150 覆盖回 100(回撤计算会因此被低估,越限检测延迟/漏判)
    from tools.live_track_advance import _load_peak_equity, _save_peak_equity
    path = str(tmp_path / "peak.json")
    _save_peak_equity(path, 100.0)                 # driver 的旧读数对应的历史起点

    _save_peak_equity(path, 150.0)                 # sentinel: 观测到更高权益,真实新峰值

    _save_peak_equity(path, 100.0)                 # driver: 基于自己那份旧快照重新写回 100
    assert _load_peak_equity(path, default=0.0) == 150.0   # sentinel 的新峰值必须存活


def test_save_day_state_writes_when_no_existing_state(tmp_path):
    from tools.live_track_advance import _load_day_state, _save_day_state
    path = str(tmp_path / "day.json")
    _save_day_state(path, {"day": "2026-07-06", "start_equity": 1000.0})
    assert _load_day_state(path) == {"day": "2026-07-06", "start_equity": 1000.0}


def test_save_day_state_keeps_first_writers_baseline_for_same_day(tmp_path):
    # 模拟真实竞态:两个进程在同一天的日界附近都读到"昨天"的旧状态,都为今天算出各自的
    # start_equity 基线;sentinel 先写入(start_equity=1000)成为今天的权威起点 ->
    # driver 基于自己那份稍旧的读数,试图用不同的 start_equity(1050)重新建立今天的基线 ->
    # 必须是"同一天首个写入者生效",不能被后到的写入覆盖(否则日内止损起点悄悄漂移)
    from tools.live_track_advance import _load_day_state, _save_day_state
    path = str(tmp_path / "day.json")
    _save_day_state(path, {"day": "2026-07-06", "start_equity": 1000.0})   # sentinel: 今天首写

    _save_day_state(path, {"day": "2026-07-06", "start_equity": 1050.0})  # driver: 迟到的第二次今天首写尝试
    assert _load_day_state(path) == {"day": "2026-07-06", "start_equity": 1000.0}


def test_save_day_state_writes_when_day_actually_changes(tmp_path):
    from tools.live_track_advance import _load_day_state, _save_day_state
    path = str(tmp_path / "day.json")
    _save_day_state(path, {"day": "2026-07-05", "start_equity": 900.0})
    _save_day_state(path, {"day": "2026-07-06", "start_equity": 1000.0})
    assert _load_day_state(path) == {"day": "2026-07-06", "start_equity": 1000.0}


# --- 2026-07-08 安全加固:MAINNET key 权限硬门接线(tools/verify_key_permissions.py 此前
# --- "建好了没打开",审计 2026-07-07 §有序阻塞清单 #6)。镜像 firm_gate/maintenance 同一桥
# --- 模式:main() 是薄胶水、不单测(见本文件顶部 docstring),被测对象是桥函数
# --- mainnet_key_safety_skip_reason。头号验收标准:当前这台机器 0 个 MAINNET 凭据的环境下,
# --- --submit 行为必须逐字节不变 —— 用调用记录(跟随本文件既有惯例,不引入 mock 库)直接证明
# --- check_binance/bybit_key_permissions 从未被调用,不是"调用了但恰好没影响"。

def test_mainnet_key_safety_skip_reason_no_mainnet_creds_is_exact_noop(monkeypatch):
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    def _raise_not_found(_venue):
        raise RuntimeError("binance MAINNET credentials not found")

    calls = []
    monkeypatch.setattr(vkp, "_load_mainnet_credentials", _raise_not_found)
    monkeypatch.setattr(vkp, "_CHECKERS", {
        "binance": lambda k, s: calls.append(("binance", k, s)),
        "bybit": lambda k, s: calls.append(("bybit", k, s)),
    })

    assert mainnet_key_safety_skip_reason("binance") is None
    assert mainnet_key_safety_skip_reason("bybit") is None
    assert calls == []   # check_*_key_permissions 从未被调用 —— 真 no-op,不是"调用了但恰好没影响"


def test_mainnet_key_safety_skip_reason_no_mainnet_creds_real_loader_end_to_end(monkeypatch, tmp_path):
    # 不 mock _load_mainnet_credentials 本身:真实凭据查找路径(env 未设 + key/ 目录下无 mainnet
    # key 文件)也必须落到同一个"未配置"分支 —— 不是只靠上一个测试的 mock 掩盖问题。
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    for env_key in ("BINANCE_MAINNET_API_KEY", "BINANCE_MAINNET_API_SECRET",
                    "BYBIT_MAINNET_API_KEY", "BYBIT_MAINNET_API_SECRET"):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.chdir(tmp_path)   # key/binance_mainnet_key.json / key/bybit_mainnet_key.json 均不存在

    calls = []
    monkeypatch.setattr(vkp, "_CHECKERS", {
        "binance": lambda k, s: calls.append(("binance", k, s)),
        "bybit": lambda k, s: calls.append(("bybit", k, s)),
    })

    assert mainnet_key_safety_skip_reason("binance") is None
    assert mainnet_key_safety_skip_reason("bybit") is None
    assert calls == []


def test_mainnet_key_safety_skip_reason_safe_key_passes(monkeypatch):
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    monkeypatch.setattr(vkp, "_load_mainnet_credentials", lambda venue: ("k", "s"))
    safe_result = vkp.KeyPermissionResult(venue="binance", safety=vkp.KeySafety.SAFE,
                                          reasons=("enableWithdrawals=false", "ipRestrict=true"))
    monkeypatch.setattr(vkp, "_CHECKERS", {"binance": lambda k, s: safe_result})

    assert mainnet_key_safety_skip_reason("binance") is None


def test_mainnet_key_safety_skip_reason_unsafe_key_is_skip_reason(monkeypatch):
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    monkeypatch.setattr(vkp, "_load_mainnet_credentials", lambda venue: ("k", "s"))
    unsafe_result = vkp.KeyPermissionResult(
        venue="binance", safety=vkp.KeySafety.UNSAFE,
        reasons=("enableWithdrawals=true — this key CAN withdraw funds",))
    monkeypatch.setattr(vkp, "_CHECKERS", {"binance": lambda k, s: unsafe_result})

    reason = mainnet_key_safety_skip_reason("binance")
    assert reason is not None
    assert "mainnet_key_permission_unsafe" in reason and "binance" in reason


def test_mainnet_key_safety_skip_reason_unknown_key_is_skip_reason(monkeypatch):
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    monkeypatch.setattr(vkp, "_load_mainnet_credentials", lambda venue: ("k", "s"))
    unknown_result = vkp.KeyPermissionResult(venue="bybit", safety=vkp.KeySafety.UNKNOWN,
                                             reasons=("non-2xx status 500",))
    monkeypatch.setattr(vkp, "_CHECKERS", {"bybit": lambda k, s: unknown_result})

    reason = mainnet_key_safety_skip_reason("bybit")
    assert reason is not None
    assert "mainnet_key_permission_unknown" in reason


def test_mainnet_key_safety_skip_reason_checker_exception_fails_closed_to_unknown(monkeypatch):
    # 网络请求异常(交易所 API 不可达等)—— 必须 fail-closed 成"跳批",不能让检查本身的异常
    # 变成"跳过检查、直接放行"(check_*_key_permissions 本身已经把这类异常优雅收敛成 UNKNOWN,
    # 这里模拟的是纵深防御那一层:万一未来实现变了也不会失守)。
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    def _boom(_k, _s):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(vkp, "_load_mainnet_credentials", lambda venue: ("k", "s"))
    monkeypatch.setattr(vkp, "_CHECKERS", {"binance": _boom})

    reason = mainnet_key_safety_skip_reason("binance")
    assert reason is not None
    assert "ConnectionError" in reason


def test_mainnet_key_safety_skip_reason_venues_are_independent(monkeypatch):
    # "该 venue 这一批被跳过,其它 venue 不受影响":本函数按 venue 字符串纯查找,不共享任何
    # 跨 venue 状态 —— binance UNSAFE 不影响 bybit 单独求值的结果。
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    monkeypatch.setattr(vkp, "_load_mainnet_credentials", lambda venue: ("k", "s"))
    unsafe_result = vkp.KeyPermissionResult(venue="binance", safety=vkp.KeySafety.UNSAFE,
                                            reasons=("enableWithdrawals=true",))
    safe_result = vkp.KeyPermissionResult(venue="bybit", safety=vkp.KeySafety.SAFE, reasons=("ok",))
    monkeypatch.setattr(vkp, "_CHECKERS", {
        "binance": lambda k, s: unsafe_result,
        "bybit": lambda k, s: safe_result,
    })

    assert mainnet_key_safety_skip_reason("binance") is not None
    assert mainnet_key_safety_skip_reason("bybit") is None


# --- 2026-07-09 对抗式审查修复:bybit_demo(_VENUES 第三个 key)此前未走过任何测试——
# --- _MAINNET_CREDENTIAL_CONFIG/_CHECKERS 只认 "binance"/"bybit" 两个交易所家族,直传
# --- venue_arg="bybit_demo" 会在 _MAINNET_CREDENTIAL_CONFIG[venue] 上抛未捕获 KeyError,
# --- 不是本函数承诺的 "no mainnet 凭据 -> None" no-op。回归锁:归一化到 exchange family 后,
# --- bybit_demo 必须与 bybit 表现逐字节一致(no-op / SAFE / UNSAFE 三态都要覆盖,不能只测 no-op
# --- 掩盖归一化本身接错 checker 的可能)。

def test_mainnet_key_safety_skip_reason_bybit_demo_no_mainnet_creds_is_exact_noop(monkeypatch):
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    def _raise_not_found(_venue):
        raise RuntimeError("bybit MAINNET credentials not found")

    calls = []
    monkeypatch.setattr(vkp, "_load_mainnet_credentials", _raise_not_found)
    monkeypatch.setattr(vkp, "_CHECKERS", {
        "binance": lambda k, s: calls.append(("binance", k, s)),
        "bybit": lambda k, s: calls.append(("bybit", k, s)),
    })

    assert mainnet_key_safety_skip_reason("bybit_demo") is None
    assert calls == []   # 归一化后仍是未配置凭据 -> 真 no-op,不是 KeyError 崩溃


def test_mainnet_key_safety_skip_reason_bybit_demo_no_mainnet_creds_real_loader_end_to_end(
        monkeypatch, tmp_path):
    # 不 mock _load_mainnet_credentials:真实凭据查找路径(env 未设 + key/ 目录下无
    # bybit_mainnet_key.json)也必须落到"未配置"分支,证明 KeyError 不会从
    # _MAINNET_CREDENTIAL_CONFIG["bybit_demo"] 这个不存在的 key 上冒出来。
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    for env_key in ("BYBIT_MAINNET_API_KEY", "BYBIT_MAINNET_API_SECRET"):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.chdir(tmp_path)   # key/bybit_mainnet_key.json 不存在

    calls = []
    monkeypatch.setattr(vkp, "_CHECKERS", {
        "bybit": lambda k, s: calls.append(("bybit", k, s)),
    })

    assert mainnet_key_safety_skip_reason("bybit_demo") is None
    assert calls == []


def test_mainnet_key_safety_skip_reason_bybit_demo_maps_to_bybit_checker(monkeypatch):
    # bybit_demo 归一化后必须真的走 bybit 的 checker(不是自成一路、也不是误接 binance)。
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    seen_venues = []
    monkeypatch.setattr(vkp, "_load_mainnet_credentials",
                        lambda venue: (seen_venues.append(venue), ("k", "s"))[1])
    unsafe_result = vkp.KeyPermissionResult(
        venue="bybit", safety=vkp.KeySafety.UNSAFE,
        reasons=("enableWithdrawals=true — this key CAN withdraw funds",))
    monkeypatch.setattr(vkp, "_CHECKERS", {"bybit": lambda k, s: unsafe_result})

    reason = mainnet_key_safety_skip_reason("bybit_demo")
    assert reason is not None
    assert "mainnet_key_permission_unsafe" in reason and "bybit_demo" in reason
    assert seen_venues == ["bybit"]   # 凭据加载走归一化后的交易所家族,不是原始 venue 字符串


# --- 2026-07-09 对抗式审查修复(medium):_load_mainnet_credentials 只在"确认未配置"时抛
# --- RuntimeError;若 mainnet key 文件存在但损坏(JSON 解析失败/不可读),抛的是
# --- json.JSONDecodeError(ValueError 子类)/OSError,不该被当成"没配置"而 no-op 放行——
# --- 必须 fail-closed 成非 None 的 skip 原因,而不是让异常穿透 main()。

def test_mainnet_key_safety_skip_reason_corrupt_credential_load_fails_closed_not_noop(monkeypatch):
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    def _raise_json_decode_error(_venue):
        import json
        raise json.JSONDecodeError("Expecting value", "not json", 0)

    calls = []
    monkeypatch.setattr(vkp, "_load_mainnet_credentials", _raise_json_decode_error)
    monkeypatch.setattr(vkp, "_CHECKERS", {
        "binance": lambda k, s: calls.append(("binance", k, s)),
    })

    reason = mainnet_key_safety_skip_reason("binance")
    assert reason is not None                     # 绝不能是 None(那等同"未配置"的 no-op 语义)
    assert "mainnet_key_permission_unknown" in reason
    assert "JSONDecodeError" in reason
    assert calls == []   # 凭据都没拿到,checker 不应该被调用


def test_mainnet_key_safety_skip_reason_unreadable_credential_file_fails_closed_not_noop(monkeypatch):
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    def _raise_os_error(_venue):
        raise OSError("permission denied")

    monkeypatch.setattr(vkp, "_load_mainnet_credentials", _raise_os_error)
    monkeypatch.setattr(vkp, "_CHECKERS", {"bybit": lambda k, s: (_ for _ in ()).throw(
        AssertionError("checker must not be called"))})

    reason = mainnet_key_safety_skip_reason("bybit")
    assert reason is not None
    assert "mainnet_key_permission_unknown" in reason
    assert "OSError" in reason


def test_mainnet_key_safety_skip_reason_corrupt_credential_real_file_end_to_end(monkeypatch, tmp_path):
    # 不 mock _load_mainnet_credentials:真实文件系统路径上放一个损坏的 mainnet key 文件,
    # 证明修复覆盖的是真实的 json.load 失败模式,不只是 mock 出来的异常类型。
    import tools.verify_key_permissions as vkp
    from tools.live_track_advance import mainnet_key_safety_skip_reason

    for env_key in ("BINANCE_MAINNET_API_KEY", "BINANCE_MAINNET_API_SECRET"):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.chdir(tmp_path)
    key_dir = tmp_path / "key"
    key_dir.mkdir()
    (key_dir / "binance_mainnet_key.json").write_text("{not valid json", encoding="utf-8")

    calls = []
    monkeypatch.setattr(vkp, "_CHECKERS", {
        "binance": lambda k, s: calls.append(("binance", k, s)),
    })

    reason = mainnet_key_safety_skip_reason("binance")
    assert reason is not None
    assert "mainnet_key_permission_unknown" in reason
    assert calls == []


# --- 兜底回归:对 _VENUES 里**每一个**真实注册的 venue(不只是硬编码的 "binance"/"bybit")
# --- 跑一遍 mainnet_key_safety_skip_reason,断言不抛异常——drift(将来 _VENUES 新增第四个 venue
# --- 而 verify_key_permissions 的交易所家族归一化没跟上)被自动捕获,而不是要等到生产任务崩溃。

def test_mainnet_key_safety_skip_reason_never_raises_for_any_registered_venue(monkeypatch, tmp_path):
    from tools.live_track_advance import _VENUES, mainnet_key_safety_skip_reason

    for env_key in ("BINANCE_MAINNET_API_KEY", "BINANCE_MAINNET_API_SECRET",
                    "BYBIT_MAINNET_API_KEY", "BYBIT_MAINNET_API_SECRET"):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.chdir(tmp_path)   # 无 mainnet key 文件 -> 全部 venue 应落到 no-op None 分支

    for venue in _VENUES:
        assert mainnet_key_safety_skip_reason(venue) is None


def test_live_driver_registers_all_forward_alphas():
    from tools.live_track_advance import _STRATEGIES

    assert set(_STRATEGIES) == {"crowd_contra", "carry_oi_majors", "top_contra", "btc_spot_trend"}
    assert _STRATEGIES["crowd_contra"]["crowding_gate"] is True
    assert _STRATEGIES["carry_oi_majors"]["crowding_gate"] is False
    assert _STRATEGIES["top_contra"]["crowding_gate"] is False
    # MEXC 现货趋势(2026-07 MEXC 对接):现货无 OI 维度,策略级拥挤门恒关闭
    assert _STRATEGIES["btc_spot_trend"]["crowding_gate"] is False


def test_live_driver_signal_wrappers_use_matching_alpha_expressions(monkeypatch):
    import tools.live_track_advance as driver

    calls = []
    sentinel = ({"BTC": 0.5}, {"BTC": 60_000.0}, 123, {}, {})

    def fake_latest(expression, panel_bundle=None):
        calls.append(expression)
        return sentinel

    monkeypatch.setattr(driver, "_latest_weights_and_prices_for", fake_latest)
    assert driver._latest_weights_and_prices() == sentinel
    assert driver._latest_carry_weights_and_prices() == sentinel
    assert driver._latest_top_contra_weights_and_prices() == sentinel
    assert calls == [driver.CROWD_CONTRA, driver.CARRY, driver.TOP_CONTRA]


def test_carry_signal_does_not_require_global_ls_matrix():
    import services.research.alpha_expression as ax
    from tools.live_track_advance import _latest_carry_weights_and_prices

    insts = ["A", "B", "C", "D"]
    event_times = [1000 * (i + 1) for i in range(20)]
    matrices = {
        "close": [[100 + i, 200 + 2 * i, 300 + i, 400 - i] for i in range(20)],
        "volume": [[1000, 2000, 3000, 4000] for _ in range(20)],
        "funding": [
            [0.001 * (i + 1), -0.001 * (i + 1),
             0.0005 * (i + 1), -0.0005 * (i + 1)]
            for i in range(20)
        ],
    }
    panel = ax.FieldPanel(instruments=insts, event_times=event_times, matrices=matrices)

    weights, prices, decided_ts, ls_row, evidence = _latest_carry_weights_and_prices(
        (panel, insts)
    )
    assert weights
    assert prices
    assert decided_ts == event_times[-1]
    assert ls_row == {}
    assert all(row["crowding_z"] is None for row in evidence.values())


def test_research_readiness_missing_cycle_fails_closed(tmp_path):
    from tools.live_track_advance import research_readiness_skip_reason

    reason = research_readiness_skip_reason(
        "crowd_contra", path=str(tmp_path / "missing.json")
    )
    assert reason == "alpha_paper_cycle_missing_or_invalid"


def test_research_readiness_blocks_explicit_alpha_blockers(tmp_path):
    import json
    from tools.live_track_advance import research_readiness_skip_reason

    path = tmp_path / "cycle.json"
    path.write_text(
        json.dumps(
            {"alphas": {"carry_oi_majors": {"paper_verdict": "BLOCKED",
                                               "blockers": ["FORWARD_EVIDENCE_INSUFFICIENT"]}}}
        ),
        encoding="utf-8",
    )
    reason = research_readiness_skip_reason("carry_oi_majors", path=str(path))
    assert reason == "alpha_paper_blocked:carry_oi_majors:FORWARD_EVIDENCE_INSUFFICIENT"


def test_research_readiness_blocks_non_pass_canonical_risk(tmp_path, monkeypatch):
    import json
    import tools.live_track_advance as live_track_advance
    from tools.live_track_advance import research_readiness_skip_reason

    monkeypatch.setattr(
        live_track_advance, "is_authoritative_backtest_record", lambda _record: True
    )
    rejected_risk = {
        "decision": "RISK_REJECTED",
        "status": "REJECTED",
        "gates_run": 9,
        "gates_passed": 8,
        "engines_run": {"capacity": False},
        "engines_skipped": {},
        "engines_pending": {},
        "provenance": "REAL_RESEARCH_RISK_FULL",
    }

    path = tmp_path / "cycle.json"
    path.write_text(
        json.dumps(
            {"alphas": {"top_contra": {
                "paper_verdict": "PENDING_REVIEW",
                "blockers": [],
                "canonical_dry_run": {
                    "research_risk": rejected_risk,
                    "detail": {"research_risk": rejected_risk},
                },
            }}}
        ),
        encoding="utf-8",
    )
    reason = research_readiness_skip_reason("top_contra", path=str(path))
    assert reason == "research_risk_risk_rejected:top_contra"


def test_research_readiness_rejects_legacy_pass_label(tmp_path):
    import json
    from tools.live_track_advance import research_readiness_skip_reason

    path = tmp_path / "cycle.json"
    path.write_text(
        json.dumps(
            {"alphas": {"crowd_contra": {
                "paper_verdict": "PENDING_REVIEW",
                "blockers": [],
                "canonical_dry_run": {"research_risk_decision": "PASS"},
            }}}
        ),
        encoding="utf-8",
    )
    assert research_readiness_skip_reason(
        "crowd_contra", path=str(path)
    ) == "backtest_authority_invalid:crowd_contra"


@pytest.mark.parametrize('damage', [None, 'missing', 'failed', 'alias', 'stress', 'extra', 'labels_only'])
def test_research_readiness_requires_complete_canonical_risk_reports(tmp_path, monkeypatch, damage):
    import json
    import tools.live_track_advance as live_track_advance
    from tools.live_track_advance import research_readiness_skip_reason

    monkeypatch.setattr(
        live_track_advance, "is_authoritative_backtest_record", lambda _record: True
    )
    engines = {
        name: True
        for name in (
            "market", "liquidity", "concentration", "correlation", "drawdown",
            "regime", "stress", "capacity", "model_alpha",
        )
    }
    approved_risk = {
        "decision": "RISK_APPROVED",
        "status": "APPROVED",
        "gates_run": 9,
        "gates_passed": 9,
        "engines_run": engines,
        "engines_skipped": {},
        "engines_pending": {},
        "provenance": "REAL_RESEARCH_RISK_FULL",
        # Synthetic interface fixture: the consumer requires every retained
        # report's own pass, with the stress report's replay provenance.
        "reports": {name: {"passed": True} for name in engines},
    }
    reports = approved_risk['reports']
    reports['stress']['provenance'] = 'REAL_HISTORICAL_REPLAY'
    if damage == 'missing':
        reports.pop('market')
    elif damage == 'failed':
        reports['capacity']['passed'] = False
    elif damage == 'alias':
        reports['capacity']['passed'] = 1
    elif damage == 'stress':
        reports['stress']['provenance'] = 'SYNTHETIC_SCENARIO'
    elif damage == 'extra':
        reports['unreviewed'] = {'passed': True}
    elif damage == 'labels_only':
        approved_risk['reports'] = {'stress': {'provenance': 'REAL_HISTORICAL_REPLAY'}}

    path = tmp_path / "cycle.json"
    path.write_text(
        json.dumps(
            {"alphas": {"crowd_contra": {
                "paper_verdict": "PENDING_REVIEW",
                "blockers": [],
                "canonical_dry_run": {
                    "research_risk": approved_risk,
                    "detail": {"research_risk": approved_risk},
                },
            }}}
        ),
        encoding="utf-8",
    )
    expected = None if damage is None else 'research_risk_risk_approved:crowd_contra'
    assert research_readiness_skip_reason("crowd_contra", path=str(path)) == expected


def test_research_readiness_rejects_scalar_approval_without_manifest_v2(tmp_path):
    import json
    from tools.live_track_advance import research_readiness_skip_reason

    path = tmp_path / "alpha_paper_cycle.json"
    path.write_text(
        json.dumps(
            {
                "alphas": {
                    "crowd_contra": {
                        "blockers": [],
                        "paper_verdict": "PENDING_REVIEW",
                        "canonical_dry_run": {
                            "authoritative": True,
                            "research_risk_decision": "RISK_APPROVED",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert research_readiness_skip_reason(
        "crowd_contra", path=str(path)
    ) == "backtest_authority_invalid:crowd_contra"


# --- 2026-07-23 资金费实时交叉核对(funding_check 镜像跨源核价):可测桥 funding_cross_check ---
# --- + latest_daily_funding_observations + _FUNDING_CHECK_STRATEGIES 注册 + PreviewVenue ---
# --- premium_index 透传。main() 是网络胶水不单测(本文件顶部 docstring 惯例),接线顺序由 ---
# --- tests/live/test_strategy_wiring.py 的源码钉子锁。 ---

_FUNDING_NOW = 1_760_000_000_000


def _funding_venue(rate_by_inst, next_ms=_FUNDING_NOW + 4 * 3_600_000):
    """假 venue:premium_index(instrument) -> PremiumIndexSnapshot 形状 mapping(rate_by_inst
    里没有的标的返回 None,镜像适配器 fail-soft)。"""
    class _Venue:
        def __init__(self):
            self.calls = []

        def premium_index(self, instrument_id):
            self.calls.append(instrument_id)
            rate = rate_by_inst.get(instrument_id)
            if rate is None:
                return None
            return {"last_funding_rate": rate, "next_funding_time_ms": next_ms}
    return _Venue()


def _funding_snapshot(tmp_path, rate_by_inst, day_ms=_FUNDING_NOW - 12 * 3_600_000):
    import json
    path = tmp_path / "crypto_funding_rates.json"
    json.dump({inst: {str(day_ms): rate} for inst, rate in rate_by_inst.items()},
              open(path, "w", encoding="utf-8"))
    return str(path)


def test_funding_check_strategies_registry_is_additive():
    # 注册表条目形状一字不动(任务约束:不加键);funding 策略集合是独立模块常量
    from tools.live_track_advance import _FUNDING_CHECK_STRATEGIES, _STRATEGIES
    assert _FUNDING_CHECK_STRATEGIES == frozenset({"carry_oi_majors"})
    for name, cfg in _STRATEGIES.items():
        assert set(cfg) == {"signal", "crowding_gate"}, name
    assert "crowd_contra" not in _FUNDING_CHECK_STRATEGIES


def test_funding_cross_check_skips_non_funding_strategy_without_touching_venue():
    # crowd_contra(非 funding 信号策略)-> 静默跳过:不查 venue、不告警、不冻结
    from tools.live_track_advance import funding_cross_check

    class _NoCallVenue:
        def premium_index(self, instrument_id):
            raise AssertionError("premium_index must not be called for non-funding strategies")

    alerts = []
    flagged, status = funding_cross_check(
        _NoCallVenue(), ("BTC",), strategy_id="crowd_contra", venue_name="binance",
        now_ms=_FUNDING_NOW, alerts=alerts)
    assert flagged == {} and status == "skipped_not_funding_strategy"
    assert alerts == []                                     # 每轮都响是噪音 -> 静默


def test_funding_cross_check_skips_venue_without_premium_index():
    # bybit/mexc 无 premium_index -> 跳过并诚实告警(fail-open:venue 提供不了证据,
    # 冻结全部等于谎称"每个标的都可疑"——与 price_check 的 secondary_unavailable 同一区分)
    from tools.live_track_advance import funding_cross_check

    class _BareVenue:
        pass

    alerts = []
    flagged, status = funding_cross_check(
        _BareVenue(), ("BTC",), strategy_id="carry_oi_majors", venue_name="bybit",
        now_ms=_FUNDING_NOW, alerts=alerts)
    assert flagged == {} and status == "skipped_venue_lacks_premium_index"
    assert len(alerts) == 1 and "premium_index" in alerts[0] and "bybit" in alerts[0]


def test_funding_cross_check_freezes_divergent_and_keeps_clean(tmp_path):
    # carry_oi_majors:ETH venue 8h 0.1%(日化 0.3%)vs 日快照 0.03% = 单位错/垃圾量级 -> 冻结;
    # BTC 两源一致 -> 照常交易(per-instrument fail-soft,不因一个标的拖垮整批)
    from tools.live_track_advance import funding_cross_check
    venue = _funding_venue({"BTC": 0.0001, "ETH": 0.001})
    path = _funding_snapshot(tmp_path, {"BTC": 0.0003, "ETH": 0.0003})
    alerts = []
    flagged, status = funding_cross_check(
        venue, ("BTC", "ETH"), strategy_id="carry_oi_majors", venue_name="binance",
        now_ms=_FUNDING_NOW, alerts=alerts, funding_path=path)
    assert status == "ran"
    assert set(flagged) == {"ETH"}
    assert "diverges" in flagged["ETH"]
    assert venue.calls == ["BTC", "ETH"]                    # 只对当前目标标的查 venue
    assert any("funding cross-check froze 1 instrument" in a for a in alerts)


def test_funding_cross_check_freezes_all_when_daily_snapshot_file_missing(tmp_path):
    # 离线快照文件不存在 -> 每个 funding 标的都无法验证 -> fail-closed 全冻结
    from tools.live_track_advance import funding_cross_check
    venue = _funding_venue({"BTC": 0.0001})
    flagged, status = funding_cross_check(
        venue, ("BTC",), strategy_id="carry_oi_majors", venue_name="binance",
        now_ms=_FUNDING_NOW, alerts=[], funding_path=str(tmp_path / "absent.json"))
    assert status == "ran" and set(flagged) == {"BTC"}


def test_funding_cross_check_venue_exception_freezes_that_instrument(tmp_path):
    # venue 调用本身抛异常(网络错误穿透适配器 fail-soft)——该标的不验证即冻结,不崩整批
    from tools.live_track_advance import funding_cross_check

    class _BoomVenue:
        def premium_index(self, instrument_id):
            raise ConnectionError("network unreachable")

    path = _funding_snapshot(tmp_path, {"BTC": 0.0003})
    alerts = []
    flagged, status = funding_cross_check(
        _BoomVenue(), ("BTC",), strategy_id="carry_oi_majors", venue_name="binance",
        now_ms=_FUNDING_NOW, alerts=alerts, funding_path=path)
    assert status == "ran" and set(flagged) == {"BTC"}
    assert any("ConnectionError" in a for a in alerts)


def test_latest_daily_funding_observations_picks_latest_day(tmp_path):
    # 多日序列 -> 取最新日桶;缺标的/坏条目/非有限值诚实省略
    import json
    from tools.live_track_advance import latest_daily_funding_observations
    day1, day2 = _FUNDING_NOW - 36 * 3_600_000, _FUNDING_NOW - 12 * 3_600_000
    path = tmp_path / "f.json"
    json.dump({"BTC": {str(day1): 0.0001, str(day2): 0.0003},
               "ETH": {str(day2): float("nan")},            # 非有限 -> 省略
               "BAD": "not-a-dict",                          # 畸形 -> 省略
               "BROKEN": {"not-a-day": 0.001}},              # 坏时间键 -> 省略
              open(path, "w", encoding="utf-8"))
    out = latest_daily_funding_observations(["BTC", "ETH", "BAD", "BROKEN", "GHOST"],
                                            path=str(path))
    assert out == {"BTC": {"rate": 0.0003, "day_ms": day2}}


def test_latest_daily_funding_observations_missing_or_corrupt_file_is_empty(tmp_path):
    from tools.live_track_advance import latest_daily_funding_observations
    assert latest_daily_funding_observations(["BTC"], path=str(tmp_path / "none.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert latest_daily_funding_observations(["BTC"], path=str(bad)) == {}


def test_preview_venue_premium_index_passes_through_to_real_venue():
    # 资金费核对证据源(2026-07-23):premium_index 是只读方法,preview 模式必须像
    # book_ticker 一样透传真实 venue(preview 跑与 --submit 完全相同的核对)
    from tools.live_track_advance import PreviewVenue

    class _FakeRealVenueWithPremiumIndex(_FakeRealVenue):
        def premium_index(self, instrument_id):
            return {"last_funding_rate": 0.0001, "next_funding_time_ms": 123}

    real = _FakeRealVenueWithPremiumIndex()
    preview = PreviewVenue(real)
    assert preview.premium_index("BTC") == {"last_funding_rate": 0.0001,
                                            "next_funding_time_ms": 123}
    assert real.place_order_calls == []                     # 只读透传,绝不触下单路径
