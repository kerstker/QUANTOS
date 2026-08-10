from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from services.research.alpha_expression import FieldPanel
from tools import cross_venue_positioning_evaluation as driver


DAY_MS = 86_400_000
T0 = (1_700_000_000_000 // DAY_MS) * DAY_MS


def _panel(n_periods: int = 56, n_instruments: int = 12) -> FieldPanel:
    instruments = tuple(f"COIN{i}" for i in range(n_instruments))
    event_times = tuple(T0 + row * DAY_MS for row in range(n_periods))
    close = []
    global_ls = []
    for row in range(n_periods):
        close.append(
            [
                100.0 + row * 0.2 + column * 0.4 + ((row + column) % 3) * 0.1
                for column in range(n_instruments)
            ]
        )
        global_ls.append(
            [1.0 + 0.1 * ((row + column) % 7) for column in range(n_instruments)]
        )
    return FieldPanel(instruments, event_times, {"close": close, "global_ls": global_ls})


def _snapshot(panel: FieldPanel) -> dict:
    series = {}
    for column, instrument in enumerate(panel.instruments):
        series[instrument] = {
            "open_interest": {
                str(timestamp): 1000.0 + row * 4 + column
                for row, timestamp in enumerate(panel.event_times)
            },
            "long_short_ratio": {
                str(timestamp): 0.9 + 0.08 * ((2 * row + column) % 9)
                for row, timestamp in enumerate(panel.event_times)
            },
        }
    return {
        "schema_version": 1,
        "provenance": "REAL_BYBIT_PUBLIC_POSITIONING_HISTORY",
        "series": series,
    }


def _attached_panel() -> FieldPanel:
    panel = _panel()
    coverage = driver.attach_bybit_positioning(
        panel,
        _snapshot(panel),
        min_instruments=10,
        min_common_rows=30,
    )
    assert coverage["usable_rows"] == len(panel.event_times)
    assert coverage["covered_instruments"] == len(panel.instruments)
    return panel


def test_family_is_fixed_before_evaluation_and_uses_both_venues() -> None:
    assert driver.BINANCE_RETAIL_CONTRA_EXPRESSION == (
        "cs_zscore",
        ("neg", ("field", "global_ls")),
    )
    assert driver.BYBIT_RETAIL_CONTRA_EXPRESSION == (
        "cs_zscore",
        ("neg", ("field", "bybit_global_ls")),
    )
    assert driver.VENUE_DISAGREEMENT_EXPRESSION == (
        "cs_zscore",
        ("sub", ("field", "global_ls"), ("field", "bybit_global_ls")),
    )
    assert tuple(driver.CANDIDATE_EXPRESSIONS) == (
        "binance_retail_contra_control",
        "bybit_retail_contra_control",
        "venue_positioning_disagreement",
        "bybit_oi_confirmed_crowd",
    )
    assert driver.EVALUATION_CONTRACT["quantile"] == 0.3
    assert driver.EVALUATION_CONTRACT["parameter_scan"] is False


def test_attach_positioning_fails_closed_on_insufficient_cross_section() -> None:
    panel = _panel(n_periods=40, n_instruments=12)
    snapshot = _snapshot(panel)
    snapshot["series"] = dict(list(snapshot["series"].items())[:5])

    with pytest.raises(RuntimeError, match="covered instruments"):
        driver.attach_bybit_positioning(
            panel,
            snapshot,
            min_instruments=10,
            min_common_rows=30,
        )


def test_build_candidate_batch_registers_all_trials_before_results() -> None:
    candidates, evidence, mappings = driver.build_candidate_batch(_attached_panel())

    assert len(candidates) == 4
    assert list(mappings) == list(driver.CANDIDATE_EXPRESSIONS)
    assert set(evidence) == {
        mapping["research_alpha_id"] for mapping in mappings.values()
    }
    for definition, periods in candidates:
        assert periods
        assert evidence[definition.alpha_id]["leakage_ok"] is True
    calendars = [tuple(period.event_time for period in periods) for _definition, periods in candidates]
    assert all(calendar == calendars[0] for calendar in calendars[1:])
    assert all(
        right - left >= driver.FWD_HORIZON * DAY_MS
        for left, right in zip(calendars[0], calendars[0][1:])
    )
    disagreement = next(
        definition
        for definition, _periods in candidates
        if definition.name == "venue_positioning_disagreement"
    )
    assert disagreement.factor_ids == ("bybit_global_ls", "global_ls")


def test_registered_run_is_research_only_with_closed_budget(tmp_path, monkeypatch) -> None:
    captured = {}
    fake_registry = SimpleNamespace(get_evaluation=lambda _run_id: None)

    def run_batch(candidates, **kwargs):
        captured["candidates"] = tuple(candidates)
        captured.update(kwargs)
        return {"recorded_run_ids": [], "registered_candidate_count": 4}

    monkeypatch.setattr(driver, "run_registered_candidate_batch", run_batch)
    monkeypatch.setattr(
        driver,
        "production_experiment_registry",
        lambda: nullcontext(fake_registry),
    )

    document = driver.run(
        output=tmp_path / "result.json",
        recorded_at=123,
        source_commit="a" * 40,
        panel=_attached_panel(),
        source_artifact_sha256="b" * 64,
    )

    assert len(captured["candidates"]) == 4
    assert captured["quantile"] == 0.3
    assert captured["budget"].max_trials == 4
    assert captured["budget"].max_feature_count == 2
    assert captured["uses_locked_oos"] is False
    assert driver.EVALUATION_CONTRACT["shared_decision_calendar"] is True
    assert document["status"] == "PENDING"
    assert document["decision"]["testnet_eligible"] is False
    assert document["safety"]["credentials_accessed"] is False
    assert document["safety"]["order_submission_allowed"] is False
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == document


def test_selection_requires_verified_formal_pass() -> None:
    evaluations = {
        "binance_retail_contra_control": {
            "verified": True,
            "verdict": "PASS",
            "net_sharpe": 0.5,
        },
        "bybit_retail_contra_control": {
            "verified": True,
            "verdict": "FAIL",
            "net_sharpe": 3.0,
        },
        "venue_positioning_disagreement": {
            "verified": True,
            "verdict": "PASS",
            "net_sharpe": 1.2,
        },
        "bybit_oi_confirmed_crowd": {
            "verified": True,
            "verdict": "REQUIRES_HUMAN_REVIEW",
            "net_sharpe": 2.0,
        },
    }

    assert driver._decision(evaluations) == {
        "selected_alias": "venue_positioning_disagreement",
        "formal_evaluation_pass": True,
        "human_review_required": True,
        "forward_admission_allowed": True,
        "testnet_eligible": False,
    }
