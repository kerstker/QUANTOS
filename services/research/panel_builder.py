"""Build PanelPeriods from a persisted PIT bar store (pipeline integration seam).

For each period timestamp t, the cross-sectional signal uses ONLY bars knowable by
t (store.as_of -> no lookahead); the forward return is the realized outcome between
t and t+fwd_horizon, measured from the bar AT t. Instruments lacking enough history /
a bar at t / a forward close are dropped from that cross-section (fail-soft); a period
with <2 survivors is not emitted (the long/short panel needs both legs). Deterministic.
This is what lets GuidedSearchLoop / AlphaFactory run on the real persisted data
substrate.

The knowledge clock is explicit: with the default decision_lag=0 /
minimum_knowledge_lag=0 the own-event-time PIT read contract (as_of(t)) is used,
unchanged.  With explicit lags the decision clock of an event t is t+decision_lag and a
vintage is admissible only inside [t+minimum_knowledge_lag, t+decision_lag] - the same
policy as services.data.available_time.fetch_bars_for_event_decisions.

One deliberate behaviour change applies in BOTH branches (review P1-6): the
decision-moment bar must BE the bar at t (`now_bar.event_time != t` drops the
instrument). Previously a stale `latest_as_of` bar was accepted as the current price
and as the forward-return base, so the realized return for [t, t+h] was measured from
an earlier date. The own-event-time READ policy is unchanged; what changed is that an
instrument without a bar exactly at t is now dropped instead of being silently
measured from a stale bar."""
from __future__ import annotations

from datetime import date as _date
from typing import Callable, Sequence

from services.alpha.panel_backtest import PanelPeriod
from .leakage_evidence import signal_leakage_ok, volume_leakage_ok
from .ports.market_data import PointInTimeMarketDataPort
from .signals import SIGNAL_OPS


def _iso(event_time: int) -> str:
    """整数 YYYYMMDD -> ISO 'YYYY-MM-DD'(供 universe 字典序成员比较)。纯格式化、确定性。
    用 datetime.date 校验真实日历日,失败关闭(拒 2024-02-30 这类不存在的日期,审查 P2)。"""
    et = int(event_time)
    y, m, d = et // 10000, (et // 100) % 100, et % 100
    try:
        _date(y, m, d)
    except ValueError as exc:
        raise ValueError(f"event_time is not a valid YYYYMMDD date: {event_time}") from exc
    return f"{y:04d}-{m:02d}-{d:02d}"


def _admissible(rows, *, decision_lag: int, minimum_knowledge_lag: int) -> list:
    """Vintages admissible under the explicit knowledge clock: an event's own close must be
    knowable inside [event_time+minimum_knowledge_lag, event_time+decision_lag]. Same
    admissibility predicate as services/data/available_time.fetch_bars_for_event_decisions
    (which stays the bulk-window implementation of this policy)."""
    return [b for b in rows
            if b.event_time + minimum_knowledge_lag <= b.knowledge_time <= b.event_time + decision_lag]


def _vintage_at(rows, event_time: int):
    """Latest admissible vintage of `event_time` (highest knowledge_time), or None."""
    best = None
    for b in rows:
        if b.event_time == event_time and (best is None or b.knowledge_time > best.knowledge_time):
            best = b
    return best


def build_panel_periods(store: PointInTimeMarketDataPort, instruments: Sequence[str], *, event_times: Sequence[int],
                        fwd_horizon: int, signal_fn: Callable = None, universe=None,
                        volume_signal_fn: Callable = None,
                        decision_lag: int = 0, minimum_knowledge_lag: int = 0) -> list:
    if fwd_horizon < 1:
        raise ValueError("fwd_horizon must be >= 1 (a forward return looks AHEAD)")
    if (signal_fn is None) == (volume_signal_fn is None):
        raise ValueError("pass exactly one of signal_fn (price) or volume_signal_fn (volume)")
    # explicit knowledge clock (same guards as services.data.available_time): a vintage is
    # admissible only inside [event_time+minimum_knowledge_lag, event_time+decision_lag]
    if decision_lag < 0 or minimum_knowledge_lag < 0:
        raise ValueError("knowledge lags must be non-negative")
    if decision_lag < minimum_knowledge_lag:
        raise ValueError("decision_lag cannot be shorter than minimum_knowledge_lag")
    aware_clock = bool(decision_lag or minimum_knowledge_lag)
    insts = sorted(instruments)
    ets = sorted(event_times)
    periods = []
    for i, t in enumerate(ets):
        j = i + fwd_horizon
        if j >= len(ets):
            break                                    # no realized forward return
        t_fwd = ets[j]
        iso_t = _iso(t) if universe is not None else None
        sig_insts, sigs, fwds = [], [], []
        for inst in insts:
            # survivorship-bias-free: only include names that WERE universe members
            # as of the decision moment t (membership judged at t only; the forward
            # return over [t, t+h] is still realized even if the name later leaves).
            if universe is not None and not universe.is_member_as_of(inst, iso_t):
                continue
            if aware_clock:
                # decision clock = t+decision_lag; reject legacy backdated vintages and
                # pick the latest vintage knowable at that decision (two-lag policy)
                hist = _admissible(store.as_of(inst, t + decision_lag),
                                   decision_lag=decision_lag,
                                   minimum_knowledge_lag=minimum_knowledge_lag)
                now_bar = _vintage_at(hist, t)
                fwd_bar = _vintage_at(_admissible(
                    store.as_of(inst, t_fwd + decision_lag),
                    decision_lag=decision_lag,
                    minimum_knowledge_lag=minimum_knowledge_lag), t_fwd)
            else:
                hist = store.as_of(inst, t)          # PIT: bars knowable by t
                now_bar = store.latest_as_of(inst, t)
                fwd_bar = store.latest_as_of(inst, t_fwd)
            # collapse restatements to ONE bar per event_time (latest knowledge), so a
            # revision does not duplicate an event_time and corrupt the window op
            by_close, by_vol = {}, {}
            for b in hist:                            # as_of is ordered (event, knowledge)
                by_close[b.event_time] = b.close      # later (higher knowledge) wins
                by_vol[b.event_time] = b.volume
            ordered = sorted(by_close)
            closes = [by_close[et] for et in ordered]
            if volume_signal_fn is not None:
                volumes = [by_vol[et] for et in ordered]
                sig = volume_signal_fn(closes, volumes) if closes else None
            else:
                sig = signal_fn(closes) if closes else None
            # require a GENUINE later observation for the forward return — else the
            # instrument is delisted/halted and latest_as_of returns a stale bar that
            # would fabricate a 0% return (review: skip it instead)
            # ALSO require the decision-moment bar to BE the bar at t: with an interior
            # gap (or a delisting) latest_as_of hands back an older bar, and using it as
            # the base measures the realized return from the wrong date while stamping it
            # as the [t, t+h] outcome (review P1-6). Fail-soft: drop the instrument.
            if (sig is None or now_bar is None or fwd_bar is None or now_bar.close == 0
                    or now_bar.event_time != t
                    or fwd_bar.event_time <= now_bar.event_time):
                continue
            sig_insts.append(inst)
            sigs.append(float(sig))
            fwds.append(fwd_bar.close / now_bar.close - 1.0)
        if len(sig_insts) >= 2:
            periods.append(PanelPeriod(instruments=tuple(sig_insts),
                                       signals=tuple(sigs), forward_returns=tuple(fwds),
                                       event_time=int(t)))
    return periods


def make_data_periods_provider(store: PointInTimeMarketDataPort, instruments: Sequence[str], *,
                               event_times: Sequence[int], fwd_horizon: int, universe=None,
                               decision_lag: int = 0, minimum_knowledge_lag: int = 0) -> Callable:
    """Adapt an AlphaDefinition (params operator/window) to a periods provider that
    builds PanelPeriods from the persisted store — pass to GuidedSearchLoop.search.
    With a PITUniverse, panel building is survivorship-bias-free. decision_lag /
    minimum_knowledge_lag default to the own-event-time contract (0, 0)."""
    def provider(defn):
        op = defn.params["operator"]
        window = int(defn.params["window"])
        fn = SIGNAL_OPS[op]
        return build_panel_periods(store, instruments, event_times=event_times,
                                   fwd_horizon=fwd_horizon,
                                   signal_fn=lambda closes: fn(closes, window), universe=universe,
                                   decision_lag=decision_lag,
                                   minimum_knowledge_lag=minimum_knowledge_lag)

    def _leakage_ok(defn):
        # 断点1 (方案A): this provider DOES hold the raw PIT bars + the op, so it can run the real leakage
        # detector and hand the verdict to the search loop. Bounded recent slice (leakage is structural;
        # a representative window suffices and stays cheap). None when no decision time is assessable.
        ets = list(event_times)
        lk_ets = ets[-80:] if len(ets) > 80 else ets
        if len(lk_ets) < 4:
            return None
        bars = [b for inst in sorted(instruments)
                for b in store.as_of(inst, lk_ets[-1]) if b.event_time >= lk_ets[0]]
        times = lk_ets[len(lk_ets) // 2: len(lk_ets) // 2 + 3]
        return signal_leakage_ok(defn.params["operator"], int(defn.params["window"]), bars, times)

    provider.leakage_ok = _leakage_ok
    return provider


def make_volume_periods_provider(store: PointInTimeMarketDataPort, instruments: Sequence[str], *,
                                 event_times: Sequence[int], fwd_horizon: int, universe=None,
                                 decision_lag: int = 0, minimum_knowledge_lag: int = 0) -> Callable:
    """Volume/liquidity factor provider: an AlphaDefinition (params operator/window, family
    'volume') -> PanelPeriods whose signal is a VOLUME_OPS operator over the per-instrument
    price+volume history. Same PIT / survivorship-free guarantees as the price provider
    (including the explicit decision_lag / minimum_knowledge_lag knowledge clock)."""
    from .volume_signals import VOLUME_OPS

    def provider(defn):
        op = defn.params["operator"]
        window = int(defn.params["window"])
        fn = VOLUME_OPS[op]
        return build_panel_periods(store, instruments, event_times=event_times, fwd_horizon=fwd_horizon,
                                   volume_signal_fn=lambda c, v: fn(c, v, window), universe=universe,
                                   decision_lag=decision_lag,
                                   minimum_knowledge_lag=minimum_knowledge_lag)

    def _leakage_ok(defn):
        # 断点1 (方案A): real leakage detection for a VOLUME factor over the bounded recent bar slice.
        ets = list(event_times)
        lk_ets = ets[-80:] if len(ets) > 80 else ets
        if len(lk_ets) < 4:
            return None
        bars = [b for inst in sorted(instruments)
                for b in store.as_of(inst, lk_ets[-1]) if b.event_time >= lk_ets[0]]
        times = lk_ets[len(lk_ets) // 2: len(lk_ets) // 2 + 3]
        return volume_leakage_ok(defn.params["operator"], int(defn.params["window"]), bars, times)

    provider.leakage_ok = _leakage_ok
    return provider


def universe_data_gaps(
    store: PointInTimeMarketDataPort,
    universe,
    event_times: Sequence[int],
) -> dict:
    """诚实度信号:对每个交易日,统计"当日属 universe 成员但无 bar"的标的数。
    返回 {"total_gaps": int, "by_date": {iso: count}}。成员当日缺数据 = 数据缺口。"""
    by_date: dict = {}
    total = 0
    for t in sorted(event_times):
        iso_t = _iso(t)
        missing = 0
        for inst in universe.members_as_of(iso_t):
            bar = store.latest_as_of(inst, t)        # 截至 t 的最新可知 bar
            if bar is None or bar.event_time != t:   # 当日(event_time==t)无 bar
                missing += 1
        if missing:
            by_date[iso_t] = missing
        total += missing
    return {"total_gaps": total, "by_date": by_date}
