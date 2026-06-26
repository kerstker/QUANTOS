"""Volume / liquidity signal operators (tests-first).

Price-only factors ignore who is trading. Volume operators read the per-instrument volume
series (and price, for dollar-volume / illiquidity). Each maps (closes, volumes, window) to a
per-instrument scalar or None (degenerate). Pure, deterministic, PIT (caller passes only
knowable-by-t history).
"""
from services.research.volume_signals import VOLUME_OPS

CLOSES = [10.0, 11.0, 12.0, 11.5, 13.0, 12.5, 14.0]
VOL_UP = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 200.0]      # rising volume
VOL_FLAT = [100.0] * 7


def test_all_ops_registered_and_callable():
    assert set(VOLUME_OPS) == {"vol_momentum", "vol_zscore", "dollar_vol_trend", "amihud"}
    for fn in VOLUME_OPS.values():
        v = fn(CLOSES, VOL_UP, 3)
        assert v is None or isinstance(v, float)


def test_vol_momentum_positive_when_volume_rising():
    assert VOLUME_OPS["vol_momentum"](CLOSES, VOL_UP, 3) > 0
    assert VOLUME_OPS["vol_momentum"](CLOSES, [200, 150, 100, 90, 80, 70, 60], 3) < 0   # falling


def test_vol_zscore_zero_variance_is_none():
    assert VOLUME_OPS["vol_zscore"](CLOSES, VOL_FLAT, 5) is None         # flat volume -> undefined


def test_dollar_vol_trend_spike_is_positive():
    vols = [100.0] * 6 + [1000.0]                                        # last bar volume spike
    assert VOLUME_OPS["dollar_vol_trend"](CLOSES, vols, 5) > 0


def test_amihud_higher_for_thinner_volume():
    thin = VOLUME_OPS["amihud"](CLOSES, [1.0] * 7, 3)
    thick = VOLUME_OPS["amihud"](CLOSES, [1e6] * 7, 3)
    assert thin is not None and thick is not None and thin > thick      # less volume = more illiquid


def test_fail_closed_short_history_and_bad_denominator():
    assert VOLUME_OPS["vol_momentum"]([1.0, 2.0], [10.0, 20.0], 5) is None     # window > len
    assert VOLUME_OPS["vol_momentum"](CLOSES, [0.0, 0.0, 0.0, 0.0], 3) is None  # zero base volume


def test_make_volume_periods_provider_builds_periods():
    from services.data.persistent_store import SqlitePITStore
    from services.research.panel_builder import make_volume_periods_provider
    from quantos.contracts.data import Bar
    from quantos.contracts.alpha import AlphaDefinition
    st = SqlitePITStore(":memory:", create=True)
    insts = ["A", "B", "C"]
    for k, inst in enumerate(insts):
        for i in range(14):
            px, vol = 100.0 + i + k, 1000.0 + i * 10 + k * 100      # rising volume + xs spread
            st.add(inst, Bar(instrument_id=inst, event_time=i + 1, knowledge_time=i + 1,
                             open=px, high=px + 1, low=px - 1, close=px, volume=vol))
    prov = make_volume_periods_provider(st, insts, event_times=list(range(1, 15)), fwd_horizon=1)
    periods = prov(AlphaDefinition(alpha_id="hv_vol_momentum_3", name="v", factor_ids=("volume",),
                                   params={"operator": "vol_momentum", "window": "3", "family": "volume"}))
    assert periods and len(periods[-1].instruments) >= 2
    assert len(periods[-1].signals) == len(periods[-1].instruments)


def test_build_panel_requires_exactly_one_signal_source():
    import pytest
    from services.data.persistent_store import SqlitePITStore
    from services.research.panel_builder import build_panel_periods
    st = SqlitePITStore(":memory:", create=True)
    with pytest.raises(ValueError):                                  # neither fn -> fail closed
        build_panel_periods(st, ["A"], event_times=[1, 2], fwd_horizon=1)
    with pytest.raises(ValueError):                                  # both fn -> ambiguous
        build_panel_periods(st, ["A"], event_times=[1, 2], fwd_horizon=1,
                            signal_fn=lambda c: 1.0, volume_signal_fn=lambda c, v: 1.0)
