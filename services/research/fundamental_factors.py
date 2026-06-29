"""Fundamental factor panels (value factors) from a PIT fundamentals store + price store.

For each period t the signal is the latest-knowable fundamental as of t, optionally
divided by the latest-knowable price (E/P, B/M, ...). Forward returns come from the
price store. PIT throughout (both stores filtered by as_of t → no lookahead).
Reuses the survivorship-free universe gate and delisting guard. Deterministic.
"""
from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

from services.alpha.panel_backtest import PanelPeriod
from .panel_builder import _iso

_MODES = ("raw", "ratio_to_price")


def build_fundamental_periods(fund_store, price_store, instruments: Sequence[str], metric: str, *,
                              event_times: Sequence[int], fwd_horizon: int,
                              mode: str = "ratio_to_price", universe=None) -> list:
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {_MODES}, got {mode!r}")
    if fwd_horizon < 1:
        raise ValueError("fwd_horizon must be >= 1 (a forward return looks AHEAD)")
    insts = sorted(instruments)
    ets = sorted(event_times)
    periods = []
    for i, t in enumerate(ets):
        j = i + fwd_horizon
        if j >= len(ets):
            break                                        # no realized forward return
        t_fwd = ets[j]
        iso_t = _iso(t) if universe is not None else None
        sig_insts, sigs, fwds = [], [], []
        for inst in insts:
            if universe is not None and not universe.is_member_as_of(inst, iso_t):
                continue                                 # survivorship-free gate
            fv = fund_store.latest_as_of(metric, inst, t)    # PIT: latest announced by t
            if fv is None or fv.value is None:
                continue
            now_bar = price_store.latest_as_of(inst, t)
            fwd_bar = price_store.latest_as_of(inst, t_fwd)
            # genuine later observation required (delisting/halt guard), and a usable price
            if (now_bar is None or fwd_bar is None or now_bar.close <= 0
                    or fwd_bar.event_time <= now_bar.event_time):
                continue
            if mode == "ratio_to_price":
                signal = fv.value / now_bar.close        # E/P, B/M, ...
            else:
                signal = fv.value
            if not math.isfinite(signal):                # 防御非有限信号(审查 P1,深度防御)
                continue
            sig_insts.append(inst)
            sigs.append(float(signal))
            fwds.append(fwd_bar.close / now_bar.close - 1.0)
        if len(sig_insts) >= 2:
            periods.append(PanelPeriod(instruments=tuple(sig_insts),
                                       signals=tuple(sigs), forward_returns=tuple(fwds)))
    return periods


def make_fundamental_periods_provider(fund_store, price_store, instruments: Sequence[str], *,
                                      event_times: Sequence[int], fwd_horizon: int,
                                      universe=None) -> Callable:
    """Adapt an AlphaDefinition (params: metric, optional mode) to a periods provider —
    pass to GuidedSearchLoop.search so the agent can search fundamental value factors."""
    def provider(defn):
        metric = defn.params["metric"]
        mode = defn.params.get("mode", "ratio_to_price")
        return build_fundamental_periods(fund_store, price_store, instruments, metric,
                                         event_times=event_times, fwd_horizon=fwd_horizon,
                                         mode=mode, universe=universe)
    return provider


def make_combined_periods_provider(price_store, fund_store, instruments: Sequence[str], *,
                                   event_times: Sequence[int], fwd_horizon: int,
                                   universe=None) -> Callable:
    """Dispatch a periods provider by AlphaDefinition family: a fundamental hypothesis
    (params carry 'metric') builds a value-factor panel; a price hypothesis builds the
    price panel. Lets ONE GuidedSearchLoop search both families (CombinedHypothesisSpace)."""
    from .panel_builder import make_data_periods_provider
    price_provider = make_data_periods_provider(price_store, instruments, event_times=event_times,
                                                fwd_horizon=fwd_horizon, universe=universe)

    def provider(defn):
        if "metric" in defn.params:
            return build_fundamental_periods(
                fund_store, price_store, instruments, defn.params["metric"],
                event_times=event_times, fwd_horizon=fwd_horizon,
                mode=defn.params.get("mode", "ratio_to_price"), universe=universe)
        return price_provider(defn)

    def _leakage_ok(defn):
        # 断点1 (方案A): price-family hypotheses delegate to the price provider's REAL bar-based detector.
        # Fundamental-family ("metric") factors read a SEPARATE fundamental PIT store; the bar-based leakage
        # detector cannot assess that path, so we return None (honest NOT-ASSESSED -> evaluator fail-closed) —
        # the fundamental side's PIT-safety is enforced by fund_store.as_of, not faked here.
        if "metric" in defn.params:
            return None
        return price_provider.leakage_ok(defn)

    provider.leakage_ok = _leakage_ok
    return provider
