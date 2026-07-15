import math


def test_shared_cost_model_preserves_research_compatibility():
    from quantos.contracts.cost import CostModel
    from services.research.backtest_engine.costs import CostModel as ResearchCostModel

    assert ResearchCostModel is CostModel
    model = CostModel(fee_bps=1.0, slippage_bps=2.0, impact_coef=50.0)
    assert model.impact_bps(25.0, 100.0) == 25.0
    assert model.trade_cost(10_000.0, 25.0, 100.0) == 28.0
    assert CostModel(0.0, 0.0).trade_cost(10_000.0) == 0.0
    assert math.isfinite(model.trade_cost(0.0))


def test_shared_cost_model_rejects_negative_notional():
    from quantos.contracts.cost import CostModel

    try:
        CostModel().trade_cost(-1.0)
    except ValueError as exc:
        assert str(exc) == "notional_abs must be non-negative"
    else:
        raise AssertionError("negative notional must fail closed")
