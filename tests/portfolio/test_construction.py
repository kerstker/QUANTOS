"""Phase 7 step 3 — construction: ranked alpha streams -> RebalancePlan."""
import math

import pytest

from services.portfolio.config import PortfolioConfig
from services.portfolio.construction import build_portfolio, risk_budget_report

CFG = PortfolioConfig(min_overlap=10, max_weight=1.0, no_trade_band=0.02)


def _streams():
    return {
        "a": tuple(0.01 * math.sin(i * 0.3) for i in range(80)),
        "b": tuple(0.02 * math.cos(i * 0.5) for i in range(80)),
        "c": tuple(0.015 * math.sin(i * 0.7 + 1.0) for i in range(80)),
    }


def test_build_portfolio_produces_normalized_nonnegative_plan():
    plan = build_portfolio("p1", "pf", _streams(), config=CFG)
    assert {t.instrument_id for t in plan.targets} == {"a", "b", "c"}
    assert sum(t.target_weight for t in plan.targets) == pytest.approx(1.0)
    assert all(t.target_weight >= 0 for t in plan.targets)
    assert plan.turnover == pytest.approx(1.0)               # initial build from flat


def test_concentration_cap_is_respected():
    cfg = PortfolioConfig(min_overlap=10, max_weight=0.4, no_trade_band=0.0)
    plan = build_portfolio("p1", "pf", _streams(), config=cfg)
    assert max(t.target_weight for t in plan.targets) <= 0.4 + 1e-9


def test_no_trade_band_reduces_turnover_vs_prior_book():
    streams = _streams()
    target_only = build_portfolio("p1", "pf", streams, config=CFG)
    prev = {t.instrument_id: t.target_weight for t in target_only.targets}
    # rebuild against an almost-identical prior book -> tiny moves suppressed
    plan = build_portfolio("p2", "pf", streams, config=CFG, prev_weights=prev)
    assert plan.turnover < 0.05                              # near-zero churn


def test_build_is_deterministic():
    a = build_portfolio("p1", "pf", _streams(), config=CFG)
    b = build_portfolio("p1", "pf", _streams(), config=CFG)
    assert [(t.instrument_id, t.target_weight) for t in a.targets] == \
           [(t.instrument_id, t.target_weight) for t in b.targets]
    assert a.turnover == b.turnover


def test_risk_budget_report_sums_to_one():
    streams = _streams()
    plan = build_portfolio("p1", "pf", streams, config=CFG)
    w = {t.instrument_id: t.target_weight for t in plan.targets}
    rpt = risk_budget_report(streams, w, config=CFG)
    assert sum(rpt.values()) == pytest.approx(1.0)


def test_build_fails_closed_on_thin_overlap():
    short = {"a": tuple(range(5)), "b": tuple(range(5))}
    with pytest.raises(ValueError):
        build_portfolio("p1", "pf", short, config=PortfolioConfig(min_overlap=60))
