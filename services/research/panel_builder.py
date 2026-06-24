"""Build PanelPeriods from a persisted PIT bar store (pipeline integration seam).

For each period timestamp t, the cross-sectional signal uses ONLY bars knowable by
t (store.as_of -> no lookahead); the forward return is the realized outcome between
t and t+fwd_horizon. Instruments lacking enough history / a forward close are
dropped from that cross-section (fail-soft); a period with <2 survivors is not
emitted (the long/short panel needs both legs). Deterministic. This is what lets
GuidedSearchLoop / AlphaFactory run on the real persisted data substrate."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from services.alpha.panel_backtest import PanelPeriod
from .signals import SIGNAL_OPS


def build_panel_periods(store, instruments: Sequence[str], *, event_times: Sequence[int],
                        fwd_horizon: int, signal_fn: Callable) -> list:
    if fwd_horizon < 1:
        raise ValueError("fwd_horizon must be >= 1 (a forward return looks AHEAD)")
    insts = sorted(instruments)
    ets = sorted(event_times)
    periods = []
    for i, t in enumerate(ets):
        j = i + fwd_horizon
        if j >= len(ets):
            break                                    # no realized forward return
        t_fwd = ets[j]
        sig_insts, sigs, fwds = [], [], []
        for inst in insts:
            hist = store.as_of(inst, t)              # PIT: bars knowable by t
            # collapse restatements to ONE close per event_time (latest knowledge),
            # so a revision does not duplicate an event_time and corrupt the window op
            by_et = {}
            for b in hist:                            # as_of is ordered (event, knowledge)
                by_et[b.event_time] = b.close         # later (higher knowledge) wins
            closes = [by_et[et] for et in sorted(by_et)]
            sig = signal_fn(closes) if closes else None
            now_bar = store.latest_as_of(inst, t)
            fwd_bar = store.latest_as_of(inst, t_fwd)
            # require a GENUINE later observation for the forward return — else the
            # instrument is delisted/halted and latest_as_of returns a stale bar that
            # would fabricate a 0% return (review: skip it instead)
            if (sig is None or now_bar is None or fwd_bar is None or now_bar.close == 0
                    or fwd_bar.event_time <= now_bar.event_time):
                continue
            sig_insts.append(inst)
            sigs.append(float(sig))
            fwds.append(fwd_bar.close / now_bar.close - 1.0)
        if len(sig_insts) >= 2:
            periods.append(PanelPeriod(instruments=tuple(sig_insts),
                                       signals=tuple(sigs), forward_returns=tuple(fwds)))
    return periods


def make_data_periods_provider(store, instruments: Sequence[str], *,
                               event_times: Sequence[int], fwd_horizon: int) -> Callable:
    """Adapt an AlphaDefinition (params operator/window) to a periods provider that
    builds PanelPeriods from the persisted store — pass to GuidedSearchLoop.search."""
    def provider(defn):
        op = defn.params["operator"]
        window = int(defn.params["window"])
        fn = SIGNAL_OPS[op]
        return build_panel_periods(store, instruments, event_times=event_times,
                                   fwd_horizon=fwd_horizon,
                                   signal_fn=lambda closes: fn(closes, window))
    return provider
