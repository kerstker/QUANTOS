"""P0-1 — non-finite risk-parameter lockdown.

Every limit in checks.py sits on the RIGHT of a `>`/`<`/`>=` comparison, so a NaN
limit makes the comparison False and the check silently no-ops (measured: a NaN
max_gross_exposure/daily_loss_limit/max_drawdown approves a real breach; a NaN
min_cancel_rate_samples bypasses the sample floor because `NaN < 1` is False; a
NaN/-1 rate_window_ms collapses the runaway window). `0` stays legitimate
(fail-closed tight limit) and tools pass 1e9/1e7 as effectively-unlimited, so the
guard rejects ONLY non-finite, bool and negative scalars — no upper bounds.
"""
from __future__ import annotations

from dataclasses import fields, replace

import pytest

from services.risk.pre_trade import RiskLimits
from services.risk.research_risk.domain.models import RiskConfig

_NAN, _INF, _NEG_INF = float("nan"), float("inf"), float("-inf")

# -1 must die too: it does not skip the comparison, it inverts it (a -1ms rate
# window looks into the future -> zero orders counted -> guard off). True is a
# bool silently satisfying any `>= 0` arithmetic test.
_INVALID = (_NAN, _INF, _NEG_INF, -1, True)
_INVALID_IDS = ("nan", "inf", "-inf", "-1", "True")

# Every scalar field of RiskLimits (max_position_qty caps are swept separately).
_LIMIT_SCALARS = (
    "default_max_position_qty",
    "max_order_notional",
    "fat_finger_qty",
    "max_price_deviation_pct",
    "max_gross_exposure",
    "max_net_exposure",
    "max_leverage",
    "max_single_name_pct",
    "daily_loss_limit",
    "max_drawdown",
    "max_turnover",
    "max_orders_per_window",
    "rate_window_ms",
    "max_cancel_rate",
    "min_cancel_rate_samples",
    "max_participation_rate",
    "max_instrument_vol",
    "cash_buffer_pct",
)

# min_capacity/seed stay sign-permissive (no caller passes a negative one, but the
# sign is not a safety property of these two); everything else must be >= 0.
_SIGN_PERMISSIVE = frozenset({"min_capacity", "seed"})


def _risk_config_scalars() -> tuple[str, ...]:
    return tuple(f.name for f in fields(RiskConfig)
                 if isinstance(getattr(RiskConfig, f.name, None), (int, float)))


@pytest.mark.parametrize("field_name", _LIMIT_SCALARS)
@pytest.mark.parametrize("value", _INVALID, ids=_INVALID_IDS)
def test_risk_limits_reject_non_finite_bool_and_negative_scalars(field_name, value):
    with pytest.raises(ValueError):
        RiskLimits(**{field_name: value})


@pytest.mark.parametrize("value", _INVALID, ids=_INVALID_IDS)
def test_risk_limits_reject_bad_position_cap(value):
    """A NaN cap makes `abs(proj) > limits.position_cap(...)` False — the position
    limit disappears for that instrument."""
    with pytest.raises(ValueError):
        RiskLimits(max_position_qty=(("AAA", value),))


@pytest.mark.parametrize("value", (_NAN, _INF, _NEG_INF, 0, -1, True), ids=("nan", "inf", "-inf", "0", "-1", "True"))
def test_min_cancel_rate_samples_cannot_bypass_the_sample_floor(value):
    """NaN sailed through the old `sample_count < 1` rule (NaN < 1 is False)."""
    with pytest.raises(ValueError):
        RiskLimits(min_cancel_rate_samples=value)


def test_limit_sweep_covers_every_scalar_field():
    """Drift guard: a new scalar field must be added to the sweep above."""
    scalar_fields = tuple(f.name for f in fields(RiskLimits)
                          if isinstance(getattr(RiskLimits, f.name, None), (int, float))
                          and not isinstance(getattr(RiskLimits, f.name, None), bool))
    assert set(scalar_fields) == set(_LIMIT_SCALARS)


def test_zero_and_effectively_unlimited_limits_stay_accepted():
    """0 is a legitimate fail-closed setting; 1e13/1e9 are the documented
    effectively-unlimited values tools pass. The guard must not reject either."""
    limits = RiskLimits(
        cash_buffer_pct=0.0, max_single_name_pct=0.0, daily_loss_limit=0.0,
        max_drawdown=0.0, max_turnover=0.0, max_price_deviation_pct=0.0,
        default_max_position_qty=10 ** 13, fat_finger_qty=10 ** 13,
        max_order_notional=1e14, max_orders_per_window=1_000_000,
        max_position_qty=(("AAA", 0), ("BBB", 10 ** 13)),
    )
    assert limits.position_cap("AAA") == 0
    assert limits.position_cap("BBB") == 10 ** 13
    assert limits.position_cap("CCC") == 10 ** 13


def test_cap_pairs_may_still_be_a_list():
    """live callers hand over JSON-derived lists of pairs (never a tuple)."""
    limits = RiskLimits(max_position_qty=[("AAA", 5)])
    assert limits.position_cap("AAA") == 5


def test_replace_cannot_reintroduce_a_non_finite_limit():
    """dataclasses.replace re-runs __post_init__ — the guard must hold there too."""
    limits = RiskLimits()
    with pytest.raises(ValueError):
        replace(limits, max_leverage=_NAN)


@pytest.mark.parametrize("field_name", _risk_config_scalars())
@pytest.mark.parametrize("value", (_NAN, _INF, _NEG_INF, True), ids=("nan", "inf", "-inf", "True"))
def test_risk_config_rejects_non_finite_scalars(field_name, value):
    with pytest.raises(ValueError):
        RiskConfig(**{field_name: value})


@pytest.mark.parametrize("field_name", tuple(sorted(set(_risk_config_scalars()) - _SIGN_PERMISSIVE)))
def test_risk_config_rejects_negative_scalars(field_name):
    with pytest.raises(ValueError):
        RiskConfig(**{field_name: -1})


def test_risk_config_config_sweep_covers_every_field():
    assert set(_risk_config_scalars()) == {
        "max_portfolio_volatility", "max_var_95", "max_cvar_95", "max_drawdown",
        "max_drawdown_duration", "max_single_alpha_weight", "max_cluster_weight",
        "max_pairwise_correlation", "max_average_correlation", "max_liquidity_usage",
        "max_participation_rate", "max_liquidation_days", "max_leverage", "max_turnover",
        "max_cost_drag", "min_effective_number_of_bets", "min_capacity",
        "max_capacity_usage", "max_worst_regime_loss", "max_stress_loss",
        "min_validation_confidence", "min_paper_confidence", "periods_per_year", "seed",
    }


def test_risk_config_zero_floor_stays_accepted():
    """tests/execution/test_authoritative_perpetual_backtest.py passes 0 here."""
    assert RiskConfig(min_effective_number_of_bets=0).min_effective_number_of_bets == 0


def test_risk_config_hash_refuses_non_finite_values():
    """json.dumps emits bare NaN/Infinity by default: hashing one would fingerprint
    an unusable threshold set instead of failing. The guard is local to this method
    (quantos/contracts/hashing.py is deliberately untouched)."""
    config = RiskConfig()
    config.max_drawdown = _NAN
    with pytest.raises(ValueError):
        config.compute_hash()


def test_risk_config_hash_still_stable_for_valid_config():
    assert RiskConfig().compute_hash() == RiskConfig().compute_hash()
    assert len(RiskConfig().compute_hash()) == 16


def test_production_guards_cover_every_scalar_field():
    """The sweeps above are only as good as the guards' own field lists — pin both,
    so a newly added scalar field cannot silently skip validation."""
    from services.risk.pre_trade.config import _NON_NEGATIVE_SCALARS
    from services.risk.research_risk.domain.models import (
        _NON_NEGATIVE_THRESHOLDS,
        _SIGN_PERMISSIVE_SCALARS,
    )
    assert set(_LIMIT_SCALARS) == set(_NON_NEGATIVE_SCALARS) | {
        "rate_window_ms", "min_cancel_rate_samples"}
    assert set(_risk_config_scalars()) == set(_NON_NEGATIVE_THRESHOLDS) | set(
        _SIGN_PERMISSIVE_SCALARS) | {"periods_per_year"}
