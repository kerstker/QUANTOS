import math
import pytest
from quantos.contracts.research import AlphaState
from services.alpha.panel_backtest import PanelPeriod
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.guided_search import GuidedSearchLoop

GOOD_WINDOW = 60
BAD_WINDOW = 10


def _space():
    return HypothesisSpace(factors=("a", "b"), operators=("zscore", "rank"),
                           windows=(BAD_WINDOW, GOOD_WINDOW))


def _provider(defn):
    """Synthetic periods where the GOOD window aligns signal with forward return
    (positive net Sharpe) and others anti-align (negative). Deterministic, with
    per-period variation so the Sharpe is finite."""
    w = int(defn.params["window"])
    edge = 0.012 if w == GOOD_WINDOW else -0.006
    n_inst, n_periods = 12, 14
    insts = tuple(f"i{k}" for k in range(n_inst))
    mid = (n_inst - 1) / 2.0
    periods = []
    for t in range(n_periods):
        signals = tuple(float(k) for k in range(n_inst))
        fwd = tuple(edge * (k - mid) + 0.001 * (((k * 3 + t * 7) % 5) - 2) for k in range(n_inst))
        periods.append(PanelPeriod(instruments=insts, signals=signals, forward_returns=fwd))
    return periods


def test_bandit_learns_good_window_outscores_bad():
    loop = GuidedSearchLoop(_space())
    out = loop.search(rounds=4, batch_size=2, periods_provider=_provider)
    means = out["arm_means"]
    assert means[("window", str(GOOD_WINDOW))] > means[("window", str(BAD_WINDOW))]


def test_search_never_reaches_approved():
    loop = GuidedSearchLoop(_space())
    loop.search(rounds=4, batch_size=2, periods_provider=_provider)
    cat = loop.inner_loop.catalog
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())


def test_provider_error_is_isolated_and_search_continues():
    # a periods_provider that raises for one hypothesis must NOT abort the whole
    # multi-round search (review): that hypothesis is scored 0 and recorded, the rest run.
    loop = GuidedSearchLoop(_space())

    def flaky(defn):
        if int(defn.params["window"]) == BAD_WINDOW:
            raise RuntimeError("provider boom")
        return _provider(defn)

    out = loop.search(rounds=4, batch_size=2, periods_provider=flaky)
    assert any(h.get("error") for h in out["history"])          # failures recorded, not raised
    assert ("window", str(GOOD_WINDOW)) in out["arm_means"]     # good arm still learned
    cat = loop.inner_loop.catalog
    assert all(cat.state(a) != AlphaState.APPROVED for a in cat.all_ids())


def test_search_is_deterministic():
    a = GuidedSearchLoop(_space()).search(rounds=4, batch_size=2, periods_provider=_provider)
    b = GuidedSearchLoop(_space()).search(rounds=4, batch_size=2, periods_provider=_provider)
    assert a["arm_means"] == b["arm_means"]
