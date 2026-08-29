"""Contract tests for the durable carry/crowd catalog registration driver."""
from __future__ import annotations

import pytest

from quantos.contracts.alpha import AlphaDefinition
from services.alpha.catalog import AlphaCatalog
from services.execution.backtest_authority import (
    BacktestIntegrityBlocked,
    LEGACY_INVALIDATION_CODE,
)
from services.research.alpha_expression import FieldPanel
from tools.register_alpha_catalog import (
    build_candidates,
    registration_dataset_version,
)


# ---------------------------------------------------------------------------
# build_candidates — real panel construction (synthetic FieldPanel, real ax.* calls)
# ---------------------------------------------------------------------------

def _synthetic_panel(n_periods: int = 40, n_instruments: int = 12) -> FieldPanel:
    """A hand-built FieldPanel with close/funding/global_ls fields — enough shape for CARRY/
    CROWD_CONTRA to evaluate and for panel_to_periods to yield periods above min_breadth(10)."""
    instruments = tuple(f"COIN{i}" for i in range(n_instruments))
    event_times = tuple(1_700_000_000_000 + t * 86_400_000 for t in range(n_periods))
    close, funding, global_ls = [], [], []
    for t in range(n_periods):
        close.append([100.0 + t * 0.1 + i * 0.5 + (0.3 if (t + i) % 3 == 0 else -0.2) for i in range(n_instruments)])
        funding.append([0.0001 * ((i % 5) - 2) for i in range(n_instruments)])
        global_ls.append([1.0 + 0.1 * ((i + t) % 7) for i in range(n_instruments)])
    return FieldPanel(instruments, event_times,
                      {"close": close, "funding": funding, "global_ls": global_ls})


def test_build_candidates_produces_periods_and_leakage_evidence_for_both_defaults():
    panel = _synthetic_panel()
    candidates, evidence = build_candidates(panel)
    ids = {defn.alpha_id for defn, _periods in candidates}
    assert ids == {"carry_composite", "crowd_contra"}
    for defn, periods in candidates:
        assert len(periods) > 0                      # real cross-sectional periods were built
        assert all(len(p.instruments) >= 2 for p in periods)
        assert defn.factor_ids                         # never an empty dependency list
    # leakage evidence computed (not silently skipped) for both — True/False/None all acceptable,
    # the point is compute_leakage_ok was actually invoked, not that it always says True
    assert set(evidence) == {"carry_composite", "crowd_contra"}
    for aid in evidence:
        assert "leakage_ok" in evidence[aid]


def test_build_candidates_respects_custom_candidate_list():
    panel = _synthetic_panel()
    custom = (("solo", "Solo test alpha", ("field", "close"), ("close",)),)
    candidates, evidence = build_candidates(panel, custom)
    assert [defn.alpha_id for defn, _ in candidates] == ["solo"]
    assert set(evidence) == {"solo"}


def test_build_candidates_fwd_horizon_changes_period_count():
    panel = _synthetic_panel(n_periods=40)
    custom = (("solo", "Solo", ("field", "close"), ("close",)),)
    _, _ = build_candidates(panel, custom, fwd_horizon=1)
    c1, _ = build_candidates(panel, custom, fwd_horizon=1)
    c7, _ = build_candidates(panel, custom, fwd_horizon=7)
    # more forward-horizon lookahead -> fewer valid periods (last `fwd_horizon` rows dropped)
    assert len(c7[0][1]) < len(c1[0][1])


def test_registration_dataset_version_binds_panel_contents_and_cadence():
    panel = _synthetic_panel()
    baseline = registration_dataset_version(panel)
    assert baseline == registration_dataset_version(panel)

    changed = _synthetic_panel()
    changed.matrices["close"][0][0] += 1.0
    assert registration_dataset_version(changed) != baseline


def test_main_uses_fail_closed_registry_and_weekly_registered_batch(
    monkeypatch,
    capsys,
):
    from tools import crypto_oi_research
    from tools import register_alpha_catalog as driver

    panel = _synthetic_panel()
    captured = {}

    class FakeRegistry:
        @staticmethod
        def load_into(_catalog):
            raise AssertionError("existing lifecycle state must not turn a real rerun into a no-op")

    class RegistryContext:
        def __enter__(self):
            captured["registry_opened"] = True
            return FakeRegistry()

        @staticmethod
        def __exit__(_exc_type, _exc, _traceback):
            return False

    def fake_batch(candidates, **kwargs):
        captured["candidates"] = tuple(candidates)
        captured["kwargs"] = kwargs
        return {
            "report": {
                definition.alpha_id: {"error": "TEST_STUB", "state": None}
                for definition, _periods in candidates
            }
        }

    monkeypatch.setattr(crypto_oi_research, "build_panel", lambda: (panel, panel.instruments))
    monkeypatch.setattr(driver, "production_experiment_registry", RegistryContext)
    monkeypatch.setattr(driver, "run_registered_candidate_batch", fake_batch)
    monkeypatch.setattr(driver.time, "time_ns", lambda: 123_456_789_000_000)

    search_id = driver._main_legacy_diagnostic()

    assert captured["registry_opened"] is True
    assert len(captured["candidates"]) == 2
    kwargs = captured["kwargs"]
    assert kwargs["periods_per_year"] == 52
    assert kwargs["dataset_version"] == registration_dataset_version(panel)
    assert kwargs["search_kind"] == "ALPHA_CATALOG_REGISTRATION"
    assert kwargs["search_source"] == "tools.register_alpha_catalog"
    assert kwargs["recorded_at"] == 123_456_789
    assert kwargs["config"].compute_regime_evidence is True
    assert kwargs["search_id"] == search_id
    assert f"search_id={search_id}" in capsys.readouterr().out


def test_public_main_quarantines_before_clock_cache_or_registry_access(
    monkeypatch,
) -> None:
    from tools import crypto_oi_research
    from tools import register_alpha_catalog as driver

    touched: list[str] = []

    def forbidden(label):
        def _forbidden(*_args, **_kwargs):
            touched.append(label)
            pytest.fail(f"{label} touched before legacy authority quarantine")

        return _forbidden

    monkeypatch.setattr(driver.time, "time_ns", forbidden("clock"))
    monkeypatch.setattr(crypto_oi_research, "build_panel", forbidden("panel"))
    monkeypatch.setattr(
        driver,
        "production_experiment_registry",
        forbidden("experiment registry"),
    )

    with pytest.raises(BacktestIntegrityBlocked, match=LEGACY_INVALIDATION_CODE):
        driver.main()

    assert touched == []
