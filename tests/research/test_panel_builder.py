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
