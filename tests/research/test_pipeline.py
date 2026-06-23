"""Phase 5 wiring — the end-to-end research validation pipeline feeds REAL
walk-forward OOS + DSR + PBO + leakage into the gate."""
from quantos.contracts.data import Bar
from quantos.contracts.research import ValidationVerdict
from services.research.backtest_engine.costs import CostModel
from services.research.validation.pipeline import run_research_validation


def _rising_bars(n, rate=0.02, start=100.0):
    bars, p = [], start
    for i in range(n):
        c = round(p, 4)
        eps = c * 0.001 + 0.01
        bars.append(Bar(instrument_id="X", event_time=i + 1, knowledge_time=i + 1,
                        open=c, high=c + eps, low=max(c - eps, 0.01), close=c, volume=1000))
        p *= (1.0 + rate)
    return bars


def test_pipeline_passes_genuine_strategy():
    bars = _rising_bars(30)
    hold = [100] * 30  # buy-and-hold a steadily rising market
    res = run_research_validation("good", bars, [hold], train_size=6, test_size=3, cost=CostModel(0, 0))
    assert res.promotable, res.reasons


def test_pipeline_flags_overfit_candidate_set():
    n, K = 32, 4
    bars = _rising_bars(n)
    seg = n // K
    candidates = []
    for c in range(K):  # each candidate is long only in its own segment (block-diagonal wins)
        t = [0] * n
        for i in range(c * seg, (c + 1) * seg):
            t[i] = 100
        candidates.append(t)
    res = run_research_validation("overfit", bars, candidates, train_size=6, test_size=3, cost=CostModel(0, 0))
    assert not res.promotable, res.reasons


def test_pipeline_fails_on_detected_leakage():
    bars = _rising_bars(30)
    hold = [100] * 30
    res = run_research_validation("leak", bars, [hold], train_size=6, test_size=3,
                                  cost=CostModel(0, 0), leakage_ok=False)
    assert res.verdict == ValidationVerdict.FAIL
    assert any("LEAKAGE" in r for r in res.reasons)
