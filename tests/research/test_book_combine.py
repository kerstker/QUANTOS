from services.research.book_combine import (align_on_common_weeks, combine_returns,
                                            inverse_vol_weights)

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


def test_combine_returns_weighted_sum():
    import pytest
    assert combine_returns([[0.1, 0.2], [0.3, 0.4]], [0.5, 0.5]) == pytest.approx([0.2, 0.3])


def test_combine_returns_length_mismatch_raises():
    import pytest
    with pytest.raises(ValueError):
        combine_returns([[0.1, 0.2], [0.3]], [0.5, 0.5])
