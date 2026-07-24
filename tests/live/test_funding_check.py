"""live/execution/funding_check.py —— 实时资金费交叉核对(2026-07-23)。

镜像 test_price_check.py:纯函数、逐冻结原因覆盖、容差边界、None/畸形 fail-closed。
背景:funding 信号策略(carry_oi_majors)的边在资金费现金流,其信号来自日频离线快照
(crypto_funding_rates.json)——快照陈旧/被污染时跨源**价格**核对完全抓不到。
venue `last_funding_rate` 是当前 8h 费率,日快照值是当日 3 个 8h 费率之和,故比较
`venue_rate × 3` vs 日值;容差刻意宽松(陈旧/垃圾探测器,不是精确等值——8h 费率
日内合法漂移,见模块 docstring)。
"""
import pytest

from live.contracts import PremiumIndexSnapshot
from live.execution.funding_check import flag_unverifiable_funding_instruments

_INTERVAL = 8 * 3_600_000
_GRACE = 3_600_000
_MAX_AGE = 2 * 86_400_000
_NOW = 1_760_000_000_000


def _venue(rate=0.0001, next_ms=_NOW + 4 * 3_600_000):
    return {"last_funding_rate": rate, "next_funding_time_ms": next_ms}


def _daily(rate=0.0003, day_ms=_NOW - 12 * 3_600_000):
    return {"rate": rate, "day_ms": day_ms}


_UNSET = object()


def _flag(instruments=("BTC",), venue=_UNSET, daily=_UNSET, **overrides):
    venue_map = {"BTC": _venue() if venue is _UNSET else venue}
    daily_map = {"BTC": _daily() if daily is _UNSET else daily}
    kwargs = dict(now_ms=_NOW, funding_interval_ms=_INTERVAL, next_funding_grace_ms=_GRACE,
                  max_snapshot_age_ms=_MAX_AGE)
    kwargs.update(overrides)
    return flag_unverifiable_funding_instruments(instruments, venue_map, daily_map, **kwargs)


# --- OK 路径 ---

def test_clean_evidence_is_not_flagged():
    # venue 8h 0.01% ×3 = 0.03% 与日快照 0.03% 精确一致 -> 通过
    assert _flag() == {}


def test_intraday_drift_within_tolerance_passes():
    # venue 当前 8h 费率漂到 0.015%(日化 0.045% vs 日快照 0.03%,偏离 0.015%)——
    # 日内合法漂移,远在 abs=0.001(0.1%)容差内 -> 通过(探测器不抓漂移,只抓垃圾)
    assert _flag(venue=_venue(rate=0.00015)) == {}


def test_negative_rates_are_compared_symmetrically():
    # 负费率(空头付多头)同口径:venue -0.01% ×3 = -0.03% vs 日快照 -0.03% -> 通过
    assert _flag(venue=_venue(rate=-0.0001), daily=_daily(rate=-0.0003)) == {}


def test_large_daily_rate_uses_relative_band():
    # 大费率日(日 1%):rel=2.0 给出 ±2% 带宽——venue 日化 2.5%(偏离 1.5%)合法漂移 -> 通过;
    # venue 日化 4%(偏离 3% > 0.1% + 2×1% = 2.1%)是单位错/垃圾 -> 冻结
    assert _flag(venue=_venue(rate=0.025 / 3), daily=_daily(rate=0.01)) == {}
    out = _flag(venue=_venue(rate=0.04 / 3), daily=_daily(rate=0.01))
    assert "BTC" in out and "diverges" in out["BTC"]


def test_deviation_exactly_at_limit_is_not_flagged():
    # 边界:偏离恰好等于 abs_tol + rel×|daily|(> 不是 >=)不算超出
    daily = 0.001
    limit = 0.001 + 2.0 * abs(daily)          # 0.003
    venue_rate = (daily + limit) / 3          # 日化恰好 daily + limit
    assert _flag(venue=_venue(rate=venue_rate), daily=_daily(rate=daily)) == {}


def test_divergence_beyond_tolerance_is_flagged():
    # venue 8h 0.1% ×3 = 0.3% vs 日快照 0.03%——偏离 0.27% > 0.1% + 2×0.03%:
    # 典型单位错(百分比 vs 小数)/垃圾/严重陈旧量级 -> 冻结
    out = _flag(venue=_venue(rate=0.001))
    assert "BTC" in out and "diverges" in out["BTC"]
    assert "staleness/garbage detector" in out["BTC"]


# --- venue 快照 fail-closed ---

def test_missing_venue_snapshot_fails_closed():
    out = _flag(venue=None)
    assert "BTC" in out and "unverifiable" in out["BTC"]


def test_absent_venue_entry_fails_closed():
    out = flag_unverifiable_funding_instruments(["GHOST"], {}, {"GHOST": _daily()},
                                                now_ms=_NOW)
    assert "GHOST" in out and "missing venue premium-index snapshot" in out["GHOST"]


def test_invalid_venue_rate_fails_closed():
    assert "BTC" in _flag(venue=_venue(rate=float("nan")))
    assert "BTC" in _flag(venue=_venue(rate=None))


def test_invalid_next_funding_timestamp_fails_closed():
    assert "BTC" in _flag(venue=_venue(next_ms=0))
    assert "BTC" in _flag(venue=_venue(next_ms=float("nan")))


def test_next_funding_in_past_beyond_grace_is_stale():
    # next_funding_time 落后 now 超过 grace = venue 调度数据过期(结算点已过还没更新)
    out = _flag(venue=_venue(next_ms=_NOW - _GRACE - 1))
    assert "BTC" in out and "stale venue funding schedule" in out["BTC"]


def test_next_funding_in_past_within_grace_passes():
    # 恰在结算边界附近(落后但未超 grace)——时钟偏移/结算窗口的合法形态
    assert _flag(venue=_venue(next_ms=_NOW - _GRACE + 1)) == {}
    assert _flag(venue=_venue(next_ms=_NOW - _GRACE)) == {}      # 边界:> 不是 >=


def test_next_funding_absurdly_far_future_fails_closed():
    # 领先超过一个资金费区间 + grace = 响应畸形(正常 next 最多 8h 后)
    out = _flag(venue=_venue(next_ms=_NOW + _INTERVAL + _GRACE + 1))
    assert "BTC" in out and "ahead of local time" in out["BTC"]
    # 边界:恰好 interval + grace 不冻结
    assert _flag(venue=_venue(next_ms=_NOW + _INTERVAL + _GRACE)) == {}


# --- 离线日快照 fail-closed ---

def test_missing_daily_observation_fails_closed():
    out = flag_unverifiable_funding_instruments(["BTC"], {"BTC": _venue()}, {}, now_ms=_NOW)
    assert "BTC" in out and "no daily funding snapshot rate available" in out["BTC"]


def test_invalid_daily_rate_fails_closed():
    assert "BTC" in _flag(daily=_daily(rate=float("nan")))
    assert "BTC" in _flag(daily=_daily(rate=None))


def test_invalid_daily_timestamp_fails_closed():
    assert "BTC" in _flag(daily=_daily(day_ms=0))


def test_stale_daily_snapshot_fails_closed():
    # 最新日桶超过 max_snapshot_age_ms 未刷新 = 刷新断档,证据不可信
    out = _flag(daily=_daily(day_ms=_NOW - _MAX_AGE - 1))
    assert "BTC" in out and "stale daily funding snapshot" in out["BTC"]
    # 边界:恰好 max_age 不冻结
    assert _flag(daily=_daily(day_ms=_NOW - _MAX_AGE)) == {}


def test_daily_snapshot_ahead_of_now_fails_closed():
    out = _flag(daily=_daily(day_ms=_NOW + _MAX_AGE + 1))
    assert "BTC" in out and "ahead of local time" in out["BTC"]


# --- 输入形态与参数校验 ---

def test_premium_index_snapshot_dataclass_is_accepted():
    # 真实适配器返回 live.contracts.PremiumIndexSnapshot(dataclass)——鸭子类型字段访问
    # 必须同时接受 dataclass 与 Mapping(本模块不 import contracts,注入值优先)
    snap = PremiumIndexSnapshot(instrument_id="BTC", mark_price=60000.0, index_price=59990.0,
                                last_funding_rate=0.0001,
                                next_funding_time_ms=_NOW + 4 * 3_600_000, time_ms=_NOW)
    out = flag_unverifiable_funding_instruments(["BTC"], {"BTC": snap}, {"BTC": _daily()},
                                                now_ms=_NOW)
    assert out == {}


def test_per_instrument_isolation():
    # 一个标的证据坏只冻结它自己,不拖垮整批(fail-soft per-instrument)
    out = flag_unverifiable_funding_instruments(
        ["BTC", "ETH"], {"BTC": _venue(), "ETH": None},
        {"BTC": _daily(), "ETH": _daily()}, now_ms=_NOW)
    assert set(out) == {"ETH"}


def test_param_validation_raises():
    with pytest.raises(ValueError):
        _flag(now_ms=0)
    with pytest.raises(ValueError):
        _flag(funding_interval_ms=0)
    with pytest.raises(ValueError):
        _flag(funding_interval_ms=2 * 86_400_000)      # 区间超过一天 = 配置错误
    with pytest.raises(ValueError):
        _flag(next_funding_grace_ms=-1)
    with pytest.raises(ValueError):
        _flag(abs_rate_tolerance=-0.1)
    with pytest.raises(ValueError):
        _flag(rel_rate_tolerance=float("nan"))
