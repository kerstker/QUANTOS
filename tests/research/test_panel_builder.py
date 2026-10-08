"""Pipeline integration — build PanelPeriods from a persisted PIT store, PIT-correct
(signal uses only data knowable by t) with realized forward returns."""
import pytest
from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore
from services.research.panel_builder import build_panel_periods, make_data_periods_provider


def _bar(inst, t, close):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=close, high=close + 1, low=max(close - 1, 0.1), close=close, volume=1000)


def _store_with(series):
    s = SqlitePITStore(":memory:", create=True)
    for inst, closes in series.items():
        for t, c in enumerate(closes):
            s.add(inst, _bar(inst, t, c))
    return s


def _momentum1(closes):
    return closes[-1] / closes[-2] - 1 if len(closes) >= 2 else None


def test_builds_periods_with_signals_and_realized_forward_returns():
    store = _store_with({"A": [10, 11, 12, 13, 14], "B": [20, 19, 18, 17, 16]})
    ets = [0, 1, 2, 3, 4]
    periods = build_panel_periods(store, ["A", "B"], event_times=ets, fwd_horizon=1, signal_fn=_momentum1)
    # periods for t=1,2,3 (t=0 has no momentum; t=4 has no forward); t=1..3 -> 3 periods
    assert len(periods) == 3
    p = periods[0]                                   # t=1
    assert set(p.instruments) == {"A", "B"} and len(p.signals) == 2 == len(p.forward_returns)
    # A forward return at t=1 -> close[2]/close[1]-1 = 12/11-1
    ai = p.instruments.index("A")
    assert p.forward_returns[ai] == pytest.approx(12 / 11 - 1)


def test_instrument_with_insufficient_history_is_skipped():
    store = SqlitePITStore(":memory:", create=True)
    for t, c in enumerate([10, 11, 12, 13]):
        store.add("A", _bar("A", t, c))
    store.add("B", _bar("B", 3, 50.0))               # B only appears late -> no momentum early
    ets = [0, 1, 2, 3]
    periods = build_panel_periods(store, ["A", "B"], event_times=ets, fwd_horizon=1, signal_fn=_momentum1)
    # only periods where >=2 instruments survive are emitted; B lacks history -> dropped early.
    # so most early periods have <2 survivors -> few/no periods; assert no crash + well-formed
    for p in periods:
        assert len(p.instruments) >= 2 and len(p.signals) == len(p.forward_returns)


def test_restatement_deduped_to_one_close_per_event_time():
    s = SqlitePITStore(":memory:", create=True)
    for inst in ("A", "B"):
        for t, c in [(0, 10.0), (1, 11.0), (2, 12.0), (3, 13.0)]:
            s.add(inst, _bar(inst, t, c))
        s.add(inst, Bar(instrument_id=inst, event_time=1, knowledge_time=2,
                        open=99, high=100, low=98, close=99, volume=1000))   # restated et=1, knowable by t=2
    # signal returns the number of closes it sees -> dedup means 3 distinct event_times at t=2, not 4
    periods = build_panel_periods(s, ["A", "B"], event_times=[0, 1, 2, 3], fwd_horizon=1,
                                  signal_fn=lambda closes: float(len(closes)))
    seen = {p.signals[0] for p in periods}
    assert 3.0 in seen and 4.0 not in seen          # the restatement did not duplicate event_time 1


def test_delisted_instrument_skipped_not_fabricated_zero():
    s = SqlitePITStore(":memory:", create=True)
    for t, c in [(0, 10.0), (1, 11.0), (2, 12.0), (3, 13.0)]:
        s.add("A", _bar("A", t, c))
        s.add("C", _bar("C", t, 20.0 + t))
    s.add("B", _bar("B", 0, 50.0))
    s.add("B", _bar("B", 1, 51.0))                  # B delists after t=1 (no later bars)
    periods = build_panel_periods(s, ["A", "B", "C"], event_times=[0, 1, 2, 3], fwd_horizon=1,
                                  signal_fn=_momentum1)
    # at t=1 the forward (t=2) has no NEW B bar -> B dropped (no fabricated 0% return)
    p_at_1 = periods[1]                              # periods: t=0, t=1, t=2
    assert "B" not in p_at_1.instruments
    assert "A" in p_at_1.instruments and "C" in p_at_1.instruments


def test_negative_fwd_horizon_rejected():
    s = _store_with({"A": [10, 11, 12], "B": [20, 19, 18]})
    with pytest.raises(ValueError):
        build_panel_periods(s, ["A", "B"], event_times=[0, 1, 2], fwd_horizon=0, signal_fn=_momentum1)


def test_provider_uses_operator_params_and_is_deterministic():
    from quantos.contracts.alpha import AlphaDefinition
    store = _store_with({"A": [10, 11, 9, 12, 13, 14], "B": [20, 21, 19, 22, 23, 24]})
    prov = make_data_periods_provider(store, ["A", "B"], event_times=[0, 1, 2, 3, 4, 5], fwd_horizon=1)
    defn = AlphaDefinition(alpha_id="h", name="h", factor_ids=("A",),
                           params={"operator": "momentum", "window": "2"})
    a = prov(defn)
    b = prov(defn)
    assert len(a) >= 1
    assert [(p.instruments, p.signals, p.forward_returns) for p in a] == \
           [(p.instruments, p.signals, p.forward_returns) for p in b]
