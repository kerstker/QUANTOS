from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from services.research.alpha_expression import FieldPanel
from tools import onchain_alpha_evaluation as driver


DAY_MS = 86_400_000
T0 = (1_700_000_000_000 // DAY_MS) * DAY_MS
INSTRUMENTS = (
    "ADAUSDT", "BCHUSDT", "BTCUSDT", "DOGEUSDT",
    "ETCUSDT", "ETHUSDT", "LTCUSDT", "XLMUSDT",
)


def _panel(n_periods: int = 112) -> FieldPanel:
    event_times = tuple(T0 + row * DAY_MS for row in range(n_periods))
    close = [
        [
            100.0 + row * 0.2 + column * 0.4 + ((row + column) % 3) * 0.1
            for column in range(len(INSTRUMENTS))
        ]
        for row in range(n_periods)
    ]
    return FieldPanel(INSTRUMENTS, event_times, {"close": close})


def _snapshot(panel: FieldPanel) -> dict:
    series = {}
    for column, instrument in enumerate(panel.instruments):
        asset = instrument.removesuffix("USDT")
        series[asset] = {
            "AdrActCnt": {
                str(timestamp): 1000.0 + row * (2 + column % 3) + column
                for row, timestamp in enumerate(panel.event_times)
            },
            "TxCnt": {
                str(timestamp): 2000.0 + row * (3 + column % 4) + column
                for row, timestamp in enumerate(panel.event_times)
            },
            "CapMVRVCur": {
                str(timestamp): 0.8 + 0.04 * ((row + column) % 15)
                for row, timestamp in enumerate(panel.event_times)
            },
        }
    return {
        "schema_version": 1,
        "provenance": "REAL_COINMETRICS_COMMUNITY_ONCHAIN_HISTORY",
        "license_boundary": "CC BY-NC 4.0 / non-commercial research only",
        "metrics": ["AdrActCnt", "TxCnt", "CapMVRVCur"],
        "series": series,
    }


def _attached_panel() -> FieldPanel:
    panel = _panel()
    coverage = driver.attach_onchain_metrics(
        panel,
        _snapshot(panel),
        min_instruments=5,
        min_common_rows=90,
    )
    assert coverage["usable_rows"] == len(panel.event_times)
    assert coverage["covered_instruments"] == len(panel.instruments)
    return panel


def test_family_is_fixed_and_contains_no_parameter_scan() -> None:
    assert tuple(driver.CANDIDATE_EXPRESSIONS) == (
        "network_activity_growth",
        "network_value",
        "activity_value_confirmation",
    )
    assert driver.EVALUATION_CONTRACT["activity_window_days"] == 30
    assert driver.EVALUATION_CONTRACT["fwd_horizon"] == 7
    assert driver.EVALUATION_CONTRACT["quantile"] == 0.3
    assert driver.EVALUATION_CONTRACT["parameter_scan"] is False
    assert "coinmetrics_active_addresses" in str(driver.NETWORK_ACTIVITY_GROWTH_EXPRESSION)
    assert "coinmetrics_mvrv" in str(driver.NETWORK_VALUE_EXPRESSION)


def test_attach_onchain_fails_closed_on_thin_or_wrongly_licensed_data() -> None:
    panel = _panel()
    snapshot = _snapshot(panel)
    snapshot["series"] = dict(list(snapshot["series"].items())[:3])
    with pytest.raises(RuntimeError, match="covered instruments"):
        driver.attach_onchain_metrics(
            panel, snapshot, min_instruments=5, min_common_rows=90
        )

    snapshot = _snapshot(panel)
    snapshot["license_boundary"] = "UNKNOWN"
    with pytest.raises(RuntimeError, match="license boundary"):
        driver.attach_onchain_metrics(
            panel, snapshot, min_instruments=5, min_common_rows=90
        )


def test_load_panel_uses_repository_base_asset_identifiers(
    tmp_path, monkeypatch
) -> None:
    from tools import crypto_oi_research

    panel = _panel()
    snapshot = _snapshot(panel)
    path = tmp_path / "coinmetrics.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    captured = {}

    def build_panel(*, only):
        captured["only"] = tuple(only)
        return panel, panel.instruments

    monkeypatch.setattr(crypto_oi_research, "build_panel", build_panel)
    monkeypatch.setattr(
        driver,
        "attach_onchain_metrics",
        lambda _panel_value, _snapshot_value: {"usable_rows": 1},
    )

    _loaded, _coverage, _digest = driver.load_panel(path)
    assert captured["only"] == tuple(snapshot["series"])


def test_build_candidate_batch_uses_one_shared_no_overlap_calendar() -> None:
    candidates, evidence, mappings = driver.build_candidate_batch(_attached_panel())

    assert len(candidates) == 3
    assert list(mappings) == list(driver.CANDIDATE_EXPRESSIONS)
    assert set(evidence) == {
        mapping["research_alpha_id"] for mapping in mappings.values()
    }
    calendars = [
        tuple(period.event_time for period in periods)
        for _definition, periods in candidates
    ]
    assert all(calendar == calendars[0] for calendar in calendars[1:])
    assert len(calendars[0]) >= 4
    assert all(
        right - left >= driver.FWD_HORIZON * DAY_MS
        for left, right in zip(calendars[0], calendars[0][1:])
    )
    value = next(
        definition
        for definition, _periods in candidates
        if definition.name == "network_value"
    )
    assert value.factor_ids == ("coinmetrics_mvrv",)


def test_registered_run_is_noncommercial_research_only(tmp_path, monkeypatch) -> None:
    captured = {}
    fake_registry = SimpleNamespace(get_evaluation=lambda _run_id: None)

    def run_batch(candidates, **kwargs):
        captured["candidates"] = tuple(candidates)
        captured.update(kwargs)
        return {"recorded_run_ids": [], "registered_candidate_count": 3}

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

    assert len(captured["candidates"]) == 3
    assert captured["budget"].max_trials == 3
    assert captured["budget"].max_feature_count == 3
    assert captured["uses_locked_oos"] is False
    assert document["status"] == "PENDING"
    assert document["decision"]["testnet_eligible"] is False
    assert document["safety"]["commercial_entitlement"] is False
    assert document["safety"]["credentials_accessed"] is False
    assert document["safety"]["order_submission_allowed"] is False
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == document


def test_selection_requires_verified_formal_pass() -> None:
    evaluations = {
        "network_activity_growth": {
            "verified": True,
            "verdict": "PASS",
            "net_sharpe": 0.7,
        },
        "network_value": {
            "verified": True,
            "verdict": "FAIL",
            "net_sharpe": 3.0,
        },
        "activity_value_confirmation": {
            "verified": True,
            "verdict": "PASS",
            "net_sharpe": 1.1,
        },
    }
    assert driver._decision(evaluations) == {
        "selected_alias": "activity_value_confirmation",
        "formal_evaluation_pass": True,
        "human_review_required": False,
        "forward_admission_allowed": True,
        "testnet_eligible": False,
    }
