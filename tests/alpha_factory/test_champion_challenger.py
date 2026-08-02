"""Champion/Challenger (727 §10): freeze pins + multi-metric comparison policy."""

from __future__ import annotations

import pytest

from services.alpha_factory.champion_challenger import (
    ChampionChallengerError,
    ChampionSnapshot,
    assert_frozen,
    champion_snapshot_hash,
    compare_champion_challenger,
)


def _snapshot(**overrides):
    base = dict(
        alpha_id="ax_champion",
        alpha_version="v3",
        code_hash="abc123",
        config_hash="cfg456",
        factor_versions=("carry@1.2", "crowd@2.0"),
        dataset_policy_hash="dsp",
        cost_policy_hash="csp",
        risk_policy_hash="rsp",
        frozen_at_ms=1000,
    )
    base.update(overrides)
    return ChampionSnapshot(**base)


def test_snapshot_hash_is_content_derived() -> None:
    first = _snapshot()
    assert champion_snapshot_hash(first) == champion_snapshot_hash(
        _snapshot(frozen_at_ms=9999)  # freezing time is provenance, not a pin
    )
    assert champion_snapshot_hash(first) != champion_snapshot_hash(
        _snapshot(alpha_version="v4")
    )
    assert len(champion_snapshot_hash(first)) == 64


def test_assert_frozen_detects_both_drifts() -> None:
    snapshot = _snapshot()
    assert assert_frozen(
        snapshot, current_code_hash="abc123", current_config_hash="cfg456"
    ) == ()
    assert assert_frozen(
        snapshot, current_code_hash="DIFFERENT", current_config_hash="cfg456"
    ) == ("CHAMPION_CODE_DRIFT",)
    assert assert_frozen(
        snapshot, current_code_hash="DIFFERENT", current_config_hash="ALSO"
    ) == ("CHAMPION_CODE_DRIFT", "CHAMPION_CONFIG_DRIFT")


def test_assert_frozen_unpinned_has_no_opinion_not_a_pass() -> None:
    unpinned = _snapshot(code_hash="", config_hash="")
    assert assert_frozen(
        unpinned, current_code_hash="anything", current_config_hash="anything"
    ) == ()


def test_snapshot_validation() -> None:
    with pytest.raises(ChampionChallengerError):
        _snapshot(alpha_id="")
    with pytest.raises(ChampionChallengerError):
        _snapshot(frozen_at_ms=-1)
    with pytest.raises(ChampionChallengerError):
        _snapshot(factor_versions="carry@1.2")


def test_compare_challenger_better_requires_double_superiority() -> None:
    result = compare_champion_challenger(
        {"net_sharpe": 1.0, "max_drawdown": 0.20, "sortino": 1.2},
        {"net_sharpe": 1.4, "max_drawdown": 0.12, "sortino": 1.6},
    )
    assert result["verdict"] == "CHALLENGER_BETTER"
    assert result["metrics"]["net_sharpe"]["direction"] == "higher_is_better"
    assert result["metrics"]["max_drawdown"]["direction"] == "lower_is_better"


def test_compare_single_metric_win_never_suffices() -> None:
    """§10.3「不得只比较总收益」: better Sharpe but worse drawdown is not BETTER."""
    result = compare_champion_challenger(
        {"net_sharpe": 1.0, "max_drawdown": 0.10},
        {"net_sharpe": 1.8, "max_drawdown": 0.35},
    )
    assert result["verdict"] == "INCONCLUSIVE"
    assert result["verdict"] != "CHALLENGER_BETTER"


def test_compare_guard_regression_blocks_better() -> None:
    result = compare_champion_challenger(
        {"net_sharpe": 1.0, "max_drawdown": 0.20, "regime_stability": 0.8},
        {"net_sharpe": 1.4, "max_drawdown": 0.12, "regime_stability": 0.3},
    )
    assert result["verdict"] == "INCONCLUSIVE"
    assert "regime_stability" in result["reason"]


def test_compare_missing_key_metrics_is_inconclusive() -> None:
    result = compare_champion_challenger(
        {"net_sharpe": 1.0},
        {"net_sharpe": 1.5, "max_drawdown": 0.1},
    )
    assert result["verdict"] == "INCONCLUSIVE"
    assert result["metrics"]["max_drawdown"]["champion"] is None  # honest absence


def test_compare_champion_stands() -> None:
    result = compare_champion_challenger(
        {"net_sharpe": 1.5, "max_drawdown": 0.10},
        {"net_sharpe": 1.2, "max_drawdown": 0.15},
    )
    assert result["verdict"] == "CHAMPION_STANDS"


def test_compare_marginal_ir_only_when_inputs_exist() -> None:
    with_ir = compare_champion_challenger(
        {"net_sharpe": 1.0, "max_drawdown": 0.2, "ir": 1.0},
        {"net_sharpe": 1.2, "max_drawdown": 0.15, "ir": 1.2, "correlation": 0.5},
    )
    assert with_ir["marginal_ir"] is not None
    without = compare_champion_challenger(
        {"net_sharpe": 1.0, "max_drawdown": 0.2},
        {"net_sharpe": 1.2, "max_drawdown": 0.15},
    )
    assert without["marginal_ir"] is None


def test_compare_fail_closed_on_bad_metrics() -> None:
    with pytest.raises(ChampionChallengerError):
        compare_champion_challenger({"net_sharpe": float("nan")}, {})
    with pytest.raises(ChampionChallengerError):
        compare_champion_challenger("not-a-mapping", {})
