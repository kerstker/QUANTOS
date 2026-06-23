"""Phase 7 step 2 — optimizer: inverse-vol, risk parity, concentration cap,
cost-aware rebalancing."""
import pytest

from services.portfolio.optimizer import (
    cap_weights, cost_aware_rebalance, inverse_vol_weights, risk_parity_weights,
)
from services.portfolio.risk_model import percent_risk_contributions


def test_inverse_vol_is_proportional_to_inverse_volatility():
    cov = [[0.04, 0.0], [0.0, 0.16]]      # vols 0.2 and 0.4
    w = inverse_vol_weights(cov)
    assert w == pytest.approx([2 / 3, 1 / 3])   # 1/0.2 : 1/0.4 = 2:1
    assert sum(w) == pytest.approx(1.0)


def test_risk_parity_equalizes_risk_contributions():
    cov = [[0.04, 0.01, 0.0], [0.01, 0.09, 0.02], [0.0, 0.02, 0.16]]
    w = risk_parity_weights(cov)
    prc = percent_risk_contributions(w, cov)
    assert max(prc) - min(prc) < 0.01          # equal risk contribution
    assert sum(w) == pytest.approx(1.0) and all(x >= 0 for x in w)


def test_risk_parity_reduces_to_inverse_vol_when_uncorrelated():
    cov = [[0.04, 0.0], [0.0, 0.16]]
    assert risk_parity_weights(cov) == pytest.approx(inverse_vol_weights(cov), abs=1e-3)


def test_cap_weights_enforces_concentration_limit():
    w = cap_weights([0.7, 0.2, 0.1], max_weight=0.4)
    assert max(w) <= 0.4 + 1e-9 and sum(w) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        cap_weights([0.5, 0.5], max_weight=0.4)   # 0.4*2 < 1 -> infeasible


def test_cost_aware_no_trade_band_suppresses_small_moves():
    prev = {"a": 0.5, "b": 0.5}
    target = {"a": 0.52, "b": 0.48}            # 0.02 moves
    held, turnover = cost_aware_rebalance(prev, target, no_trade_band=0.05)
    assert held == pytest.approx({"a": 0.5, "b": 0.5})   # within band -> no trade
    assert turnover == pytest.approx(0.0)
    # a large move does rebalance
    moved, turn2 = cost_aware_rebalance(prev, {"a": 0.8, "b": 0.2}, no_trade_band=0.05)
    assert moved["a"] == pytest.approx(0.8) and turn2 > 0
