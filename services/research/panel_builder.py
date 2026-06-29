"""Build PanelPeriods from a persisted PIT bar store (pipeline integration seam).

For each period timestamp t, the cross-sectional signal uses ONLY bars knowable by
t (store.as_of -> no lookahead); the forward return is the realized outcome between
t and t+fwd_horizon. Instruments lacking enough history / a forward close are
dropped from that cross-section (fail-soft); a period with <2 survivors is not
emitted (the long/short panel needs both legs). Deterministic. This is what lets
GuidedSearchLoop / AlphaFactory run on the real persisted data substrate."""
from __future__ import annotations

from datetime import date as _date
from typing import Callable, Optional, Sequence

from services.alpha.panel_backtest import PanelPeriod
from .leakage_evidence import signal_leakage_ok
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


def build_panel_periods(store, instruments: Sequence[str], *, event_times: Sequence[int],
                        fwd_horizon: int, signal_fn: Callable = None, universe=None,
                        volume_signal_fn: Callable = None) -> list:
    if fwd_horizon < 1:
        raise ValueError("fwd_horizon must be >= 1 (a forward return looks AHEAD)")
    if (signal_fn is None) == (volume_signal_fn is None):
        raise ValueError("pass exactly one of signal_fn (price) or volume_signal_fn (volume)")
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
            hist = store.as_of(inst, t)              # PIT: bars knowable by t
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
                               event_times: Sequence[int], fwd_horizon: int, universe=None) -> Callable:
    """Adapt an AlphaDefinition (params operator/window) to a periods provider that
    builds PanelPeriods from the persisted store — pass to GuidedSearchLoop.search.
    With a PITUniverse, panel building is survivorship-bias-free."""
    def provider(defn):
        op = defn.params["operator"]
        window = int(defn.params["window"])
        fn = SIGNAL_OPS[op]
        return build_panel_periods(store, instruments, event_times=event_times,
                                   fwd_horizon=fwd_horizon,
                                   signal_fn=lambda closes: fn(closes, window), universe=universe)

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


def make_volume_periods_provider(store, instruments: Sequence[str], *,
                                 event_times: Sequence[int], fwd_horizon: int, universe=None) -> Callable:
    """Volume/liquidity factor provider: an AlphaDefinition (params operator/window, family
    'volume') -> PanelPeriods whose signal is a VOLUME_OPS operator over the per-instrument
    price+volume history. Same PIT / survivorship-free guarantees as the price provider."""
    from .volume_signals import VOLUME_OPS

    def provider(defn):
        op = defn.params["operator"]
        window = int(defn.params["window"])
        fn = VOLUME_OPS[op]
        return build_panel_periods(store, instruments, event_times=event_times, fwd_horizon=fwd_horizon,
                                   volume_signal_fn=lambda c, v: fn(c, v, window), universe=universe)
    return provider


def universe_data_gaps(store, universe, event_times: Sequence[int]) -> dict:
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
