"""tools/live_track_advance.py 里可离线测的纯逻辑(gate_limits/make_ctx_factory/PreviewVenue)。
main() 本身是薄胶水(数据刷新+真实 venue I/O),不单测——同 tools/forward_track_advance.py 的
既有惯例(核心逻辑在被测模块里,驱动器用真实运行验证)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §0 延伸。"""
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
    weights, prices, decided_ts, ls_row = _latest_weights_and_prices((panel, panel.instruments))
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
    weights, prices, decided_ts, ls_row = _latest_weights_and_prices((panel, insts))
    assert weights == {}                            # 回看窗口内全空 -> 诚实空,不硬凑
    assert decided_ts == 3
    assert ls_row == {}


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
    weights, prices, decided_ts, ls_row = _latest_weights_and_prices((panel, panel.instruments))
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
    weights, prices, decided_ts, ls_row = _latest_weights_and_prices((panel, insts))
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
