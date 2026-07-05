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


def test_each_venue_has_its_own_mandate_ledger_and_state_paths():
    # 一个 mandate 只授权一个交易所;不同交易所 = 不同账户,账本/状态/mandate 绝不共用
    from tools.live_track_advance import _VENUES, _venue_paths
    mandates = {v["mandate_file"] for v in _VENUES.values()}
    assert len(mandates) == len(_VENUES)
    ledgers = {_venue_paths(v["suffix"])["ledger"] for v in _VENUES.values()}
    assert len(ledgers) == len(_VENUES)
