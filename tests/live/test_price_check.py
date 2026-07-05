"""live/execution/price_check.py —— 跨源核价(2026-07-05,机构审计 P0)。

背景:实盘决策此前只用单一数据源(Binance)的价格——一次静默数据滞后会同时误导双所下单,
且价格新鲜度门此前被结构性喂成恒真(price_as_of=now)从未能拦住这类问题(见另一修复)。
本模块是新鲜度门之外的第二道防线:用独立第二源(CoinGecko)交叉核价,单标的偏离超阈值或
无法验证(第二源缺该标的)一律**从本轮批次剔除**(fail-soft per-instrument,不因一个标的
的核价失败拖垮整批——与既有 apply_exchange_filters 的丢弃哲学一致),而不是让可疑价格
静默参与下单决策。
"""
import pytest

from live.execution.price_check import flag_divergent_instruments


def test_within_threshold_is_not_flagged():
    out = flag_divergent_instruments({"BTC": 60000.0}, {"BTC": 60050.0}, max_divergence_pct=0.03)
    assert out == {}


def test_beyond_threshold_is_flagged_with_reason():
    # 20% 偏离——远超正常跨源价差,该标的必须被剔除
    out = flag_divergent_instruments({"BTC": 60000.0}, {"BTC": 72000.0}, max_divergence_pct=0.03)
    assert "BTC" in out
    assert "diverges" in out["BTC"]


def test_missing_secondary_price_is_flagged_as_unverifiable():
    out = flag_divergent_instruments({"BTC": 60000.0, "GHOST": 1.0}, {"BTC": 60050.0},
                                     max_divergence_pct=0.03)
    assert "GHOST" in out and "unverifiable" in out["GHOST"]
    assert "BTC" not in out


def test_secondary_source_entirely_unavailable_flags_nothing_by_default():
    # 第二源整体拿不到(网络故障)—— fail-soft:不逐个标的误判,调用方另行整体告警
    out = flag_divergent_instruments({"BTC": 60000.0}, {}, max_divergence_pct=0.03,
                                     secondary_unavailable=True)
    assert out == {}


def test_nonpositive_primary_price_is_flagged_unverifiable():
    out = flag_divergent_instruments({"BTC": 0.0}, {"BTC": 60000.0}, max_divergence_pct=0.03)
    assert "BTC" in out and "unverifiable" in out["BTC"]


def test_divergence_is_symmetric_regardless_of_direction():
    # 价格更高或更低,只要幅度一样就该同等对待
    a = flag_divergent_instruments({"BTC": 60000.0}, {"BTC": 54000.0}, max_divergence_pct=0.05)
    b = flag_divergent_instruments({"BTC": 60000.0}, {"BTC": 66000.0}, max_divergence_pct=0.05)
    assert "BTC" in a and "BTC" in b


def test_exactly_at_threshold_is_not_flagged():
    # 边界值:精确等于阈值不算超出(> 不是 >=)
    out = flag_divergent_instruments({"BTC": 100.0}, {"BTC": 103.0}, max_divergence_pct=0.03)
    assert out == {}


# --- 交错时间窗:决策价格过旧时不该跟"当前"价比(苹果比橘子,见 tools/live_track_advance.py) ---

def test_pure_function_has_no_timestamp_awareness_by_design():
    # flag_divergent_instruments 本身不知道时间戳——staleness 判断是调用方(驱动器)的职责,
    # 这里只验证纯函数确实不做任何隐式时间过滤(职责边界测试)
    import inspect
    sig = inspect.signature(flag_divergent_instruments)
    assert "now" not in sig.parameters and "decided_ts" not in sig.parameters
