"""CadenceConfig(services/research/cadence.py):把散落的硬编码周度参数收敛成可切换 preset。
核心 parity 测试:WEEKLY preset 必须逐字复刻现有硬编码值(7天新鲜度 / 6.5天 min_gap / ppy=52 /
lookback=7 …),把"周度路径字节等价、零回归"钉死;任何回归即红。cadence-shift-1h 调查(2026-07-07)。"""
import pytest

from services.research.cadence import HOURLY_1H, WEEKLY, cadence

_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000


# --- WEEKLY parity:逐字复刻现有散落硬编码(改任一即红)---

def test_weekly_matches_current_hardcoded_values():
    # tools/live_track_advance.py:_MAX_STALENESS_MS = 7*86_400_000
    assert WEEKLY.max_staleness_ms == 7 * _DAY_MS
    # services/research/paper_trading/forward.py:DEFAULT_MIN_GAP_MS = int(6.5*_DAY_MS)
    assert WEEKLY.min_gap_ms == int(6.5 * _DAY_MS)
    # services/execution/dry_run_pipeline.py / dry_run_crowd_contra_multi.py: ppy=52
    assert WEEKLY.annualization_factor == 52
    # tools/live_track_advance.py:_SIGNAL_MAX_LOOKBACK_ROWS = 7
    assert WEEKLY.signal_lookback_bars == 7
    # dry_run vol_target lookback=8
    assert WEEKLY.vol_lookback_bars == 8
    # forward.py:DEFAULT_EXTREME_ABS_RETURN = 0.20
    assert WEEKLY.extreme_abs_return == 0.20
    # tools/live_data_refresh.py:STALE_LIMITS (days -> bars, 日频 1bar=1day)
    assert dict(WEEKLY.stale_limit_bars) == {
        "prices": 3,
        "funding": 3,
        "oi_metrics": 5,
        "perp_mark_prices": 3,
    }
    assert WEEKLY.bar_interval_ms == _DAY_MS
    assert WEEKLY.rebalance_interval_ms == 7 * _DAY_MS
    assert WEEKLY.name == "weekly"


# --- HOURLY_1H 健全性(不是 parity,是设计约束)---

def test_hourly_bar_and_rebalance_are_1h():
    assert HOURLY_1H.bar_interval_ms == _HOUR_MS


def test_hourly_staleness_tolerates_at_most_a_couple_bars():
    # 1H 新鲜度门必须收紧——不能把周度的"7 倍 bar"宽容度线性搬来。<= 3 bars。
    assert HOURLY_1H.max_staleness_ms <= 3 * HOURLY_1H.bar_interval_ms
    assert HOURLY_1H.max_staleness_ms >= HOURLY_1H.bar_interval_ms


def test_hourly_min_gap_is_below_one_bar_no_lookahead():
    # 前向兑现最小间隔必须 < 一根 bar,否则同 bar 前视 / 或要等很久才兑现。0.5~1 bar。
    assert HOURLY_1H.min_gap_ms < HOURLY_1H.bar_interval_ms
    assert HOURLY_1H.min_gap_ms > 0.5 * HOURLY_1H.bar_interval_ms


def test_hourly_annualization_is_24_7_hours():
    assert HOURLY_1H.annualization_factor == 24 * 365


# --- 注册表 fail-closed ---

def test_cadence_lookup_known():
    assert cadence("weekly") is WEEKLY
    assert cadence("hourly_1h") is HOURLY_1H


def test_cadence_unknown_raises():
    with pytest.raises(KeyError):
        cadence("every_full_moon")


def test_config_is_frozen():
    with pytest.raises(Exception):
        WEEKLY.max_staleness_ms = 1   # frozen dataclass
