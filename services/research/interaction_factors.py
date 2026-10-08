"""Feature-interaction composite factors: combine two atomic legs into one signal.

Each period, both legs' per-instrument raw signals are cross-sectionally z-scored
(so disparate scales become comparable) then combined — sum (a blended factor) or
product (a conditional/interaction effect). A leg is atomic price (operator,window)
or fundamental (metric,mode). PIT throughout (both legs + price via as_of t).
This is the building block the evolutionary search (sub-project 2) crosses parents
into. Pure-Python, deterministic, fail-closed.
"""
from __future__ import annotations

import math
import statistics
from typing import Callable, Optional, Sequence

from quantos.contracts.panel import PanelPeriod
from .signals import SIGNAL_OPS
from .panel_builder import _iso

_COMBINE = ("sum", "product")


def _pit_closes(price_store, inst: str, t: int) -> list:
    """Closes knowable by t, restatements collapsed to one close per event_time
    (latest knowledge wins), oldest→newest. Same PIT rule as panel_builder."""
    by_et: dict = {}
    for b in price_store.as_of(inst, t):          # ordered (event_time, knowledge_time)
        by_et[b.event_time] = b.close
    return [by_et[et] for et in sorted(by_et)]


def _leg_raw(leg, inst: str, t: int, now_close: float, price_store, fund_store) -> Optional[float]:
    kind = leg[0]
    if kind == "price":
        _, operator, window = leg
        fn = SIGNAL_OPS.get(operator)
        if fn is None:
            raise ValueError(f"unknown price operator: {operator!r}")
        closes = _pit_closes(price_store, inst, t)
        return fn(closes, int(window)) if closes else None
    if kind == "fund":
        _, metric, mode = leg
        if fund_store is None:
            return None
        fv = fund_store.latest_as_of(metric, inst, t)
        if fv is None or fv.value is None:
            return None
        if mode == "ratio_to_price":
            return fv.value / now_close
        if mode == "raw":
            return fv.value
        raise ValueError(f"unknown fundamental mode: {mode!r}")
    raise ValueError(f"unknown leg kind: {kind!r}")


def _zscore_xs(vals: Sequence[float]) -> Optional[list]:
    """Cross-sectional z-score; None if the cross-section is (numerically) constant so
    the period is skipped. A bare `sd==0` test is unsafe: a leg that is constant up to
    FP noise (e.g. a time-series z-score of near-linear series) has a tiny-but-nonzero
    sd, which would amplify FP noise into garbage z-scores. Guard with a relative spread
    tolerance instead."""
    if len(vals) < 2:
        return None
    lo, hi = min(vals), max(vals)
    scale = max(abs(lo), abs(hi)) or 1.0     # genuinely relative; 1.0 only when all ~0
    if (hi - lo) <= 1e-9 * scale:
        return None
    m = statistics.fmean(vals)
    sd = statistics.pstdev(vals)
    return [(v - m) / sd for v in vals]


def build_interaction_periods(price_store, fund_store, instruments: Sequence[str], legA, legB,
                              combine: str, *, event_times: Sequence[int], fwd_horizon: int,
                              universe=None) -> list:
    if combine not in _COMBINE:
        raise ValueError(f"combine must be one of {_COMBINE}, got {combine!r}")
    if fwd_horizon < 1:
        raise ValueError("fwd_horizon must be >= 1 (a forward return looks AHEAD)")
    insts = sorted(instruments)
    ets = sorted(event_times)
    periods = []
    for i, t in enumerate(ets):
        j = i + fwd_horizon
        if j >= len(ets):
            break
        t_fwd = ets[j]
        iso_t = _iso(t) if universe is not None else None
        rows = []                                   # (inst, rawA, rawB, fwd_return)
        for inst in insts:
            if universe is not None and not universe.is_member_as_of(inst, iso_t):
                continue
            now_bar = price_store.latest_as_of(inst, t)
            fwd_bar = price_store.latest_as_of(inst, t_fwd)
            if (now_bar is None or fwd_bar is None or now_bar.close <= 0
                    or fwd_bar.event_time <= now_bar.event_time):
                continue
            a = _leg_raw(legA, inst, t, now_bar.close, price_store, fund_store)
            b = _leg_raw(legB, inst, t, now_bar.close, price_store, fund_store)
            if a is None or b is None or not math.isfinite(a) or not math.isfinite(b):
                continue
            rows.append((inst, a, b, fwd_bar.close / now_bar.close - 1.0))
        if len(rows) < 2:
            continue
        zA = _zscore_xs([r[1] for r in rows])
        zB = _zscore_xs([r[2] for r in rows])
        if zA is None or zB is None:               # a degenerate leg this period → skip
            continue
        if combine == "sum":
            comp = [zA[k] + zB[k] for k in range(len(rows))]
        else:                                       # product
            comp = [zA[k] * zB[k] for k in range(len(rows))]
        periods.append(PanelPeriod(instruments=tuple(r[0] for r in rows),
                                   signals=tuple(comp), forward_returns=tuple(r[3] for r in rows),
                                   event_time=int(t)))
    return periods


def _leg_id(leg) -> str:
    # ":" is the leg-id delimiter; a component containing it would make the id->leg
    # encoding non-injective, colliding distinct leg pairs to the same alpha_id and
    # drifting multiple-testing counting (review P1). Fail closed.
    if any(":" in str(x) for x in leg):
        raise ValueError(f"leg components must not contain ':' (id delimiter): {leg!r}")
    return f"{leg[0]}:{leg[1]}:{leg[2]}"


def interaction_definition(legA, legB, combine: str):
    """Encode a 2-leg interaction as an AlphaDefinition (the bridge the evolutionary
    search uses to turn a crossover genome into an evaluable hypothesis)."""
    from quantos.contracts.alpha import AlphaDefinition
    if combine not in _COMBINE:
        raise ValueError(f"combine must be one of {_COMBINE}, got {combine!r}")
    a_id, b_id = _leg_id(legA), _leg_id(legB)
    params = {
        "family": "interaction", "combine": combine,
        "legA_kind": legA[0], "legA_a": str(legA[1]), "legA_b": str(legA[2]),
        "legB_kind": legB[0], "legB_a": str(legB[1]), "legB_b": str(legB[2]),
    }
    aid = f"hx_{combine}_{a_id}_{b_id}".replace(":", "-")
    return AlphaDefinition(alpha_id=aid, name=f"HX({combine}:{a_id}x{b_id})",
                           factor_ids=(a_id, b_id), params=params)


def _leg_from_params(params: dict, prefix: str) -> tuple:
    kind = params[prefix + "_kind"]
    a, b = params[prefix + "_a"], params[prefix + "_b"]
    if kind not in ("price", "fund"):
        raise ValueError(f"unknown leg kind: {kind!r}")
    return (kind, a, b)


def make_interaction_provider(price_store, fund_store, instruments: Sequence[str], *,
                              event_times: Sequence[int], fwd_horizon: int, universe=None) -> Callable:
    def provider(defn):
        legA = _leg_from_params(defn.params, "legA")
        legB = _leg_from_params(defn.params, "legB")
        return build_interaction_periods(price_store, fund_store, instruments, legA, legB,
                                         defn.params["combine"], event_times=event_times,
                                         fwd_horizon=fwd_horizon, universe=universe)
    return provider
