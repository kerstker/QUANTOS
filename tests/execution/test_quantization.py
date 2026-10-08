"""services/execution/quantization.py -- 纯数值量化判断(F10 审计,2026-07-08 抽取)。

`round_to_step` 的用例原样搬自 tests/live/test_filters.py(证明搬迁后行为逐字节不变);
`meets_min_notional` 是新加的姊妹函数,独立覆盖。"""
import pytest

from services.execution.quantization import meets_min_notional, round_to_step


# --- round_to_step (ported from tests/live/test_filters.py) -------------------

def test_round_to_step_floors_to_grid():
    assert round_to_step(1.2345, 0.001) == pytest.approx(1.234)
    assert round_to_step(1.0, 0.1) == pytest.approx(1.0)
    assert round_to_step(0.0009, 0.001) == pytest.approx(0.0)


def test_round_to_step_handles_whole_number_step():
    assert round_to_step(333.7, 1.0) == pytest.approx(333.0)


def test_round_to_step_zero_step_is_passthrough():
    assert round_to_step(1.2345, 0.0) == pytest.approx(1.2345)


def test_round_to_step_negative_step_is_passthrough():
    assert round_to_step(1.2345, -1.0) == pytest.approx(1.2345)


# --- meets_min_notional --------------------------------------------------------

def test_meets_min_notional_true_when_above_threshold():
    assert meets_min_notional(0.02, 60000.0, 5.0) is True   # 1200 >= 5


def test_meets_min_notional_false_when_below_threshold():
    assert meets_min_notional(0.00005, 60000.0, 5.0) is False  # 3 < 5


def test_meets_min_notional_boundary_exactly_equal_passes():
    assert meets_min_notional(1.0, 5.0, 5.0) is True  # 5 >= 5


def test_meets_min_notional_non_positive_threshold_is_unconstrained():
    assert meets_min_notional(0.0, 0.0, 0.0) is True
    assert meets_min_notional(1.0, 1.0, -1.0) is True
