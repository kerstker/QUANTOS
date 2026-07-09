"""Registry-level tests for tools/ic_panel_registry.py — each universe builds a real panel and
each factor's signal_fn evaluates without error against it (catches a broken expression/typo'd
DSL operator before a full driver run, e.g. the 2026-07-10 'ew_sub' vs 'sub' head-name mistake
this exact test class would have caught immediately)."""
import pytest

from tools.ic_panel_registry import FACTOR_REGISTRY, UNIVERSE_REGISTRY


@pytest.mark.parametrize("universe_name", sorted(UNIVERSE_REGISTRY))
def test_universe_builds_a_real_nonempty_panel(universe_name):
    panel = UNIVERSE_REGISTRY[universe_name]()
    assert len(panel.instruments) >= 2
    assert len(panel.event_times) >= 2
    assert "close" in panel.matrices


@pytest.mark.parametrize("entry", FACTOR_REGISTRY, ids=lambda e: e["name"])
def test_factor_signal_fn_evaluates_against_its_registered_universe(entry):
    panel = UNIVERSE_REGISTRY[entry["universe"]]()
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
