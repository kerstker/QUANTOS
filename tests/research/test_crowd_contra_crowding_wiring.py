"""Rank #10a wiring — the crowd_contra dry-run driver's per-week crowding-evidence builder.

_crowding_rank turns the raw global_ls matrix into a scale-free cross-sectional crowding proxy
in [0,1] (higher = more crowded-long) that the crowding gate's thresholds can act on. The gate
itself + its aggregation are unit-tested in tests/portfolio; here we pin the driver's evidence math."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.dry_run_crowd_contra_multi import _MAX_CROWDING_Z, _crowding_rank, _crowding_zscore


def test_cross_sectional_rank_spans_zero_to_one():
    # 3 instruments with distinct trailing global_ls -> ranks 0.0, 0.5, 1.0 by ascending crowding
    gmat = [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]]
    ranks = _crowding_rank(gmat, r=1, idx=[0, 1, 2], lookback=2)
    assert ranks[0] == 0.0 and ranks[1] == 0.5 and ranks[2] == 1.0


def test_uses_trailing_window_mean_not_a_single_row():
    # instrument 0 averages (1+3)/2=2, instrument 1 averages (4+0)/2=2, instrument 2 averages (0+6)/2=3
    gmat = [[1.0, 4.0, 0.0], [3.0, 0.0, 6.0]]
    ranks = _crowding_rank(gmat, r=1, idx=[0, 1, 2], lookback=2)
    assert ranks[2] == 1.0                       # highest trailing mean -> most crowded
    assert {ranks[0], ranks[1]} == {0.0, 0.5}    # the two tied-at-2 means split the low ranks


def test_missing_values_are_skipped_and_thin_cross_section_returns_empty():
    gmat = [[None, 2.0, None], [None, 3.0, None]]   # only one instrument has evidence
    assert _crowding_rank(gmat, r=1, idx=[0, 1, 2], lookback=2) == {}


def test_none_only_window_yields_no_evidence():
    gmat = [[None, None], [None, None]]
    assert _crowding_rank(gmat, r=1, idx=[0, 1], lookback=2) == {}


# --- 绝对拥挤 z-score 未校准保守安全规则 ---

def test_crowding_zscore_deviation_from_own_history():
    # 历史 [1..12] 均值 6.5,当前 20 -> 正 z(远高于自己历史)
    gmat = [[float(v)] for v in range(1, 13)] + [[20.0]]
    z = _crowding_zscore(gmat, r=12, i=0, min_hist=12)
    assert z is not None and z > 3.0


def test_crowding_zscore_insufficient_history_returns_none():
    gmat = [[1.0], [2.0], [3.0], [4.0]]              # 只有 4 期历史 < min_hist=12
    assert _crowding_zscore(gmat, r=3, i=0, min_hist=12) is None


def test_crowding_zscore_zero_variance_returns_none():
    gmat = [[5.0] for _ in range(13)]                # 历史零方差 -> None(不制造 inf z)
    assert _crowding_zscore(gmat, r=12, i=0, min_hist=12) is None


def test_uncalibrated_conservative_max_crowding_z_is_active():
    # 数值 drift-lock 仅维持保守安全行为，不授予校准权威。
    assert _MAX_CROWDING_Z == 3.0
