"""钉死预注册揭盲纪律纯核(WP3 W3-1):重叠折扣、端点相接、缺标签 fail-closed、
regime 覆盖阈值、政策哈希漂移、扩展窗三分位无前视。"""
from __future__ import annotations

import pytest

from services.research import unblinding as ub


DAY = 86_400_000
POLICY = ub.UnblindingPolicy()


def _window(decided_day: int, as_of_day: int) -> ub.RealizationWindow:
    return ub.RealizationWindow(decided_ts=decided_day * DAY, as_of_ts=as_of_day * DAY)


def _weekly_windows(n: int = 8, *, base_day: int = 100) -> list[ub.RealizationWindow]:
    """互不重叠(端点相接)的周度窗口。"""
    return [_window(base_day + 7 * i, base_day + 7 * (i + 1)) for i in range(n)]


def test_policy_defaults_and_content_hash() -> None:
    policy = ub.UnblindingPolicy()
    assert policy.min_periods == 8
    assert policy.min_independent_periods == 8
    assert policy.min_regime_classes == 2
    assert policy.max_single_regime_share == 0.75
    assert policy.policy_version == "unblinding_policy.v1"
    digest = policy.content_hash()
    assert isinstance(digest, str) and len(digest) == 64
    assert policy.content_hash() == digest


@pytest.mark.parametrize(
    "field",
    [
        "min_periods",
        "min_independent_periods",
        "min_regime_classes",
        "max_single_regime_share",
        "policy_version",
    ],
)
def test_policy_content_hash_drifts_on_any_identity_field(field: str) -> None:
    base = ub.UnblindingPolicy().content_hash()
    replacement = (
        "unblinding_policy.v2"
        if field == "policy_version"
        else (0.5 if field == "max_single_regime_share" else 99)
    )
    changed = ub.UnblindingPolicy(**{field: replacement})
    assert changed.content_hash() != base


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_periods": 0},
        {"min_independent_periods": 0},
        {"min_regime_classes": 0},
        {"min_periods": True},
        {"max_single_regime_share": 0.0},
        {"max_single_regime_share": 1.5},
        {"max_single_regime_share": True},
        {"policy_version": ""},
    ],
)
def test_policy_validation_fail_closed(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ub.UnblindingPolicy(**kwargs)


def test_window_validation() -> None:
    with pytest.raises(ValueError):
        ub.RealizationWindow(decided_ts=10, as_of_ts=10)
    with pytest.raises(ValueError):
        ub.RealizationWindow(decided_ts=10, as_of_ts=9)
    with pytest.raises(ValueError):
        ub.RealizationWindow(decided_ts=-1, as_of_ts=9)
    with pytest.raises(ValueError):
        ub.RealizationWindow(decided_ts=True, as_of_ts=9)


def test_overlapping_windows_discount_independent_count() -> None:
    windows = [_window(0, 10), _window(5, 15), _window(10, 20)]
    assert ub.effective_independent_periods(windows) == 2 < len(windows)
    contained = [_window(0, 10), _window(2, 8)]
    assert ub.effective_independent_periods(contained) == 1


def test_touching_endpoints_count_as_non_overlapping() -> None:
    windows = [_window(0, 10), _window(10, 20), _window(20, 30)]
    assert ub.effective_independent_periods(windows) == 3


def test_independent_count_is_input_order_independent() -> None:
    windows = [_window(21, 28), _window(0, 7), _window(7, 14), _window(3, 12)]
    expected = ub.effective_independent_periods(windows)
    assert expected == 3  # 贪心选 (0,7),(7,14),(21,28),丢弃重叠的 (3,12)
    assert ub.effective_independent_periods(list(reversed(windows))) == expected
    shuffled = [windows[2], windows[0], windows[3], windows[1]]
    assert ub.effective_independent_periods(shuffled) == expected


def test_evaluate_empty_windows_sealed() -> None:
    result = ub.evaluate_unblinding(
        n_periods=99, windows=(), regime_labels=(), policy=POLICY
    )
    assert result["status"] == "SEALED"
    assert "NO_REALIZATION_WINDOWS" in result["blockers"]
    assert result["independent_periods"] == 0
    assert result["policy_hash"] == POLICY.content_hash()


def test_evaluate_missing_regime_label_sealed() -> None:
    windows = _weekly_windows()
    labels: list = [(0, 2)] * 7 + [None]
    result = ub.evaluate_unblinding(
        n_periods=8, windows=windows, regime_labels=labels, policy=POLICY
    )
    assert result["status"] == "SEALED"
    assert "REGIME_EVIDENCE_MISSING" in result["blockers"]
    # 覆盖阈值在缺标签时不重复报告(缺失本身已是 fail-closed 原因)
    assert "REGIME_COVERAGE_INSUFFICIENT" not in result["blockers"]


def test_evaluate_ready_when_all_criteria_pass() -> None:
    windows = _weekly_windows()
    labels = [(0, 2), (1, 2), (2, 2)] * 2 + [(0, 2), (1, 2)]
    result = ub.evaluate_unblinding(
        n_periods=8, windows=windows, regime_labels=labels, policy=POLICY
    )
    assert result["status"] == "READY_TO_UNBLIND"
    assert result["independent_periods"] == 8
    assert result["regime"]["n_classes"] == 3
    assert result["blockers"] == []


def test_evaluate_overlapping_windows_block_maturity() -> None:
    windows = [_window(100 + 3 * i, 107 + 3 * i) for i in range(8)]
    labels = [(0, 2), (1, 2), (2, 2)] * 2 + [(0, 2), (1, 2)]
    result = ub.evaluate_unblinding(
        n_periods=8, windows=windows, regime_labels=labels, policy=POLICY
    )
    assert result["status"] == "SEALED"
    assert result["independent_periods"] < 8
    assert "INSUFFICIENT_INDEPENDENT_PERIODS" in result["blockers"]
    assert "INSUFFICIENT_PERIODS" not in result["blockers"]


def test_evaluate_single_regime_class_blocked() -> None:
    windows = _weekly_windows()
    labels = [(0, 2)] * 8
    result = ub.evaluate_unblinding(
        n_periods=8, windows=windows, regime_labels=labels, policy=POLICY
    )
    assert result["status"] == "SEALED"
    assert "REGIME_COVERAGE_INSUFFICIENT" in result["blockers"]
    assert result["regime"]["max_share"] == 1.0


def test_evaluate_concentrated_regime_share_blocked() -> None:
    windows = _weekly_windows()
    labels = [(0, 0)] * 7 + [(1, 1)]
    result = ub.evaluate_unblinding(
        n_periods=8, windows=windows, regime_labels=labels, policy=POLICY
    )
    assert result["status"] == "SEALED"
    assert "REGIME_CONCENTRATION_EXCEEDED" in result["blockers"]
    assert "REGIME_COVERAGE_INSUFFICIENT" not in result["blockers"]
    assert result["regime"]["n_classes"] == 2
    assert result["regime"]["max_share"] == 0.875


def test_expanding_tercile_no_lookahead() -> None:
    values = [float((i * 37) % 11) for i in range(40)]
    labels = ub.expanding_tercile_labels(values)
    extended = values + [1e12, -1e12, 5e11]
    extended_labels = ub.expanding_tercile_labels(extended)
    assert extended_labels[: len(values)] == labels


def test_expanding_tercile_insufficient_history_is_none_not_warm_started() -> None:
    labels = ub.expanding_tercile_labels([1.0, 2.0, 3.0], min_history=10)
    assert labels == [None, None, None]
    assert ub.expanding_tercile_labels([None, 1.0, None], min_history=1) == [
        None,
        0,
        None,
    ]


def test_trailing_zscore_pit_and_zero_variance() -> None:
    assert ub.trailing_zscore([1.0, 1.0, 1.0], window=3, min_history=2) == [
        None,
        None,
        None,
    ]
    zscores = ub.trailing_zscore([float(i) for i in range(30)], window=30)
    assert zscores[-1] is not None and zscores[-1] > 0
    extended = ub.trailing_zscore([float(i) for i in range(30)] + [1e9], window=30)
    assert extended[:30] == zscores


def test_window_regime_labels_two_dimensional_and_pit() -> None:
    def funding(n: int = 200) -> dict[int, float]:
        return {d * DAY: ((d % 5) - 2) * 0.0001 for d in range(n)}

    def prices(n: int = 201) -> dict[int, float]:
        price = 100.0
        out = {0: price}
        for i in range(1, n):
            magnitude = 0.002 + 0.00005 * i
            price *= 1 + (magnitude if i % 2 == 0 else -magnitude)
            out[i * DAY] = price
        return out

    windows = _weekly_windows()
    labels = ub.window_regime_labels(
        windows, funding_by_ts=funding(), prices_by_ts=prices()
    )
    assert len(labels) == len(windows)
    assert all(label is not None for label in labels)
    assert len(set(labels)) >= 2
    assert all(
        isinstance(label, tuple) and len(label) == 2
        and all(part in (0, 1, 2) for part in label)
        for label in labels
    )
    # 截断不变:序列尾部追加极端未来数据,既有窗口标签逐位不变
    funding_future = dict(funding())
    prices_future = dict(prices())
    funding_future[500 * DAY] = 1e6
    prices_future[500 * DAY] = 1e9
    assert (
        ub.window_regime_labels(
            windows, funding_by_ts=funding_future, prices_by_ts=prices_future
        )
        == labels
    )
    # 数据缺失 → 标签 None(上游 fail-closed SEALED,绝不猜)
    assert ub.window_regime_labels(
        windows, funding_by_ts={}, prices_by_ts=prices()
    ) == (None,) * len(windows)
