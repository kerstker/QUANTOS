"""Registry-level tests for tools/ic_panel_registry.py — each universe builds a real panel and
each factor's signal_fn evaluates without error against it (catches a broken expression/typo'd
DSL operator before a full driver run, e.g. the 2026-07-10 'ew_sub' vs 'sub' head-name mistake
this exact test class would have caught immediately)."""
import math

import pytest

from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore
from services.research import alpha_expression as ax
from tools import ic_panel_registry as registry_module
from tools.ic_panel_registry import (
    FACTOR_REGISTRY,
    UNIVERSE_REGISTRY,
    UNIVERSE_REQUIRED_FILES,
    missing_universe_artifacts,
)


def _synthetic_panel():
    instruments = [f"S{i}" for i in range(12)]
    event_times = list(range(320))
    close = [
        [
            100.0
            + 4.0 * instrument
            + period * (0.05 + 0.005 * instrument)
            + (2.0 + 0.2 * instrument)
            * math.sin((period + 3 * instrument) / (5.0 + 0.2 * instrument))
            for instrument in range(len(instruments))
        ]
        for period in event_times
    ]
    return ax.FieldPanel(
        instruments,
        event_times,
        {
            "close": close,
            "volume": [
                [1_000_000.0 + 10_000.0 * instrument + 100.0 * period
                 for instrument in range(len(instruments))]
                for period in event_times
            ],
            "funding": [
                [0.0001 * (instrument - 2) + 0.000001 * period
                 for instrument in range(len(instruments))]
                for period in event_times
            ],
            "global_ls": [
                [0.8 + 0.1 * instrument + 0.001 * period
                 for instrument in range(len(instruments))]
                for period in event_times
            ],
        },
    )


@pytest.mark.parametrize("universe_name", sorted(UNIVERSE_REGISTRY))
def test_universe_builds_a_real_nonempty_panel(universe_name):
    missing = missing_universe_artifacts(universe_name)
    if missing:
        pytest.skip("local PIT cache unavailable: " + ", ".join(missing))
    panel = UNIVERSE_REGISTRY[universe_name]()
    assert len(panel.instruments) >= 2
    assert len(panel.event_times) >= 2
    assert "close" in panel.matrices


@pytest.mark.parametrize("entry", FACTOR_REGISTRY, ids=lambda e: e["name"])
def test_factor_signal_fn_evaluates_against_its_registered_universe(entry):
    panel = _synthetic_panel()
    signal = entry["signal_fn"](panel)
    assert len(signal) == len(panel.event_times)
    assert len(signal[0]) == len(panel.instruments)
    n_valid = sum(1 for row in signal for v in row if v is not None)
    assert n_valid > 0, f"{entry['name']}: signal is all-None against its own registered universe"


def test_equity_daily_universe_is_registered():
    """2026-07-10 (gap audit rank #8): the first non-crypto/FX asset class registered in the IC
    panel — regression guard against silently dropping it."""
    assert "equity_daily" in UNIVERSE_REGISTRY
    names = {e["name"] for e in FACTOR_REGISTRY if e["universe"] == "equity_daily"}
    assert {"equity_momentum_12_1", "equity_low_vol"} <= names


def test_versioned_fx_universe_and_factors_are_registered():
    """The non-crypto baseline must be runnable from committed evidence, not a
    workstation-only cache."""
    assert "fx_tradable" in UNIVERSE_REGISTRY
    assert missing_universe_artifacts("fx_tradable") == ()
    names = {e["name"] for e in FACTOR_REGISTRY if e["universe"] == "fx_tradable"}
    assert {"fx_momentum_120", "fx_low_vol_60"} == names


def test_every_registered_universe_declares_its_local_artifacts():
    assert set(UNIVERSE_REQUIRED_FILES) == set(UNIVERSE_REGISTRY)


def _bar(instrument: str, event_time: int, knowledge_time: int, close: float) -> Bar:
    return Bar(
        instrument_id=instrument,
        event_time=event_time,
        knowledge_time=knowledge_time,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000.0,
    )


def test_binance_registry_panel_uses_close_time_vintage_in_mixed_cache(
    tmp_path,
    monkeypatch,
):
    day = 86_400_000
    store = SqlitePITStore(str(tmp_path / "mixed.db"), create=True)
    for instrument, offset in (("A", 0.0), ("B", 10.0)):
        store.add(instrument, _bar(instrument, 0, 0, 100.0 + offset))
        store.add(instrument, _bar(instrument, 0, day, 101.0 + offset))
    store._conn.close()
    monkeypatch.setattr(registry_module, "_CACHE", str(tmp_path))

    panel = registry_module._build(
        "mixed.db",
        ("A", "B"),
        decision_lag=day,
        minimum_knowledge_lag=day,
    )

    assert panel.event_times == (0,)
    assert panel.decision_times == (day,)
    assert panel.matrices["close"] == [[101.0, 111.0]]


def test_non_binance_registry_panel_keeps_existing_own_event_time_contract(
    tmp_path,
    monkeypatch,
):
    store = SqlitePITStore(str(tmp_path / "equity.db"), create=True)
    for instrument, close in (("A", 100.0), ("B", 110.0)):
        store.add(instrument, _bar(instrument, 20260102, 20260102, close))
    store._conn.close()
    monkeypatch.setattr(registry_module, "_CACHE", str(tmp_path))

    panel = registry_module._build("equity.db", ("A", "B"))

    assert panel.event_times == (20260102,)
    assert panel.decision_times == (20260102,)
    assert panel.matrices["close"] == [[100.0, 110.0]]
