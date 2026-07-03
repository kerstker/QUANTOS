from services.research.book_combine import (align_on_common_weeks, combine_returns,
                                            inverse_vol_weights, rolling_inverse_vol_weights)

_WEEK = 7 * 24 * 3600 * 1000
_T0 = 1_600_000_000_000        # ~2020-09


def test_align_keeps_only_common_weeks():
    a_ts = [_T0 + i * _WEEK for i in range(5)]
    b_ts = [_T0 + i * _WEEK for i in range(2, 7)]     # overlaps weeks 2,3,4
    al = align_on_common_weeks([0.1, 0.2, 0.3, 0.4, 0.5], a_ts,
                               [1.0, 2.0, 3.0, 4.0, 5.0], b_ts)
    assert len(al["weeks"]) == 3
    assert al["a"] == [0.3, 0.4, 0.5]                 # a's weeks 2,3,4
    assert al["b"] == [1.0, 2.0, 3.0]                 # b's weeks 2,3,4 (its first 3)


def test_inverse_vol_weights_favor_lower_vol():
    calm = [0.01, -0.01, 0.01, -0.01]                 # low vol
    wild = [0.10, -0.10, 0.10, -0.10]                 # high vol
    w = inverse_vol_weights([calm, wild])
    assert w[0] > w[1] and abs(sum(w) - 1.0) < 1e-9


def test_inverse_vol_equal_weight_fallback_on_zero_vol():
    flat = [0.0, 0.0, 0.0]
    other = [0.01, -0.01, 0.02]
    assert inverse_vol_weights([flat, other]) == [0.5, 0.5]


def test_rolling_inverse_vol_is_pit_and_favors_calm():
    calm = [0.01, -0.01, 0.01, -0.01, 0.01, -0.01]      # 低波
    wild = [0.10, -0.10, 0.10, -0.10, 0.10, -0.10]      # 高波
    # 在 end_idx=6 用 lookback=4 的 trailing 窗口 → 低波 sleeve 权重更大
    w = rolling_inverse_vol_weights([calm, wild], end_idx=6, lookback=4, min_history=4)
    assert w[0] > w[1] and abs(sum(w) - 1.0) < 1e-9


def test_rolling_inverse_vol_equal_weight_when_insufficient_history():
    # end_idx=2,lookback=4,min_history=4 → 可用历史仅 2 < 4 → 等权回退
    assert rolling_inverse_vol_weights([[0.01, 0.02], [0.03, 0.04]], end_idx=2, lookback=4,
                                       min_history=4) == [0.5, 0.5]


def test_rolling_inverse_vol_excludes_future():
    # end_idx=3 只看 [0,3):前 3 期都低波 → 等波(权重≈0.5/0.5),未来的大波动被排除
    a = [0.01, -0.01, 0.01, 5.0, -5.0]
    b = [0.01, -0.01, 0.01, 0.0, 0.0]
    w = rolling_inverse_vol_weights([a, b], end_idx=3, lookback=10, min_history=3)
    assert abs(w[0] - w[1]) < 0.05                       # 未来的 5.0/-5.0 未泄漏进权重


def test_combine_returns_weighted_sum():
    import pytest
    assert combine_returns([[0.1, 0.2], [0.3, 0.4]], [0.5, 0.5]) == pytest.approx([0.2, 0.3])


def test_combine_returns_length_mismatch_raises():
    import pytest
    with pytest.raises(ValueError):
        combine_returns([[0.1, 0.2], [0.3]], [0.5, 0.5])
