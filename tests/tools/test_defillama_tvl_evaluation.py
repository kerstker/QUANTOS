from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from services.research.alpha_expression import FieldPanel
from tools import defillama_tvl_evaluation as driver


DAY_MS = 86_400_000
T0 = (1_700_000_000_000 // DAY_MS) * DAY_MS
INSTRUMENTS = (
    "ADA", "APT", "ATOM", "AVAX", "BCH", "BNB",
    "BTC", "DOGE", "ETC", "ETH", "FIL", "ICP",
    "INJ", "LTC", "NEAR", "OP", "SOL", "XLM", "XRP",
)


def _panel(n_periods: int = 140) -> FieldPanel:
    event_times = tuple(T0 + row * DAY_MS for row in range(n_periods))
    close = [
        [
            100.0 + row * 0.15 + column * 0.3 + ((row + column) % 5) * 0.1
            for column in range(len(INSTRUMENTS))
        ]
        for row in range(n_periods)
    ]
    return FieldPanel(INSTRUMENTS, event_times, {"close": close})


def _snapshot(panel: FieldPanel) -> dict:
    return {
        "schema_version": 1,
        "provenance": "REAL_DEFILLAMA_FREE_CHAIN_TVL_HISTORY",
        "source_boundary": "DefiLlama Free API / no authentication",
        "revision_policy": "RETROSPECTIVE_REVISABLE_NO_VINTAGES",
        "pit_admissible_for_promotion": False,
        "series": {
            instrument: {
                "chain": f"Chain-{instrument}",
                "tvl": {
                    str(timestamp): 1_000_000.0 + row * (500 + column * 7)
                    for row, timestamp in enumerate(panel.event_times)
                },
            }
            for column, instrument in enumerate(panel.instruments)
        },
    }


def _attached_panel() -> FieldPanel:
    panel = _panel()
    coverage = driver.attach_chain_tvl(
        panel, _snapshot(panel), min_instruments=10, min_common_rows=100
    )
    assert coverage["covered_instruments"] == len(INSTRUMENTS)
    assert coverage["usable_rows"] == len(panel.event_times)
    return panel


def test_family_is_fixed_before_history_and_has_no_scan() -> None:
    assert tuple(driver.CANDIDATE_EXPRESSIONS) == (
        "chain_tvl_growth",
        "chain_tvl_acceleration",
        "chain_tvl_price_divergence",
    )
    assert driver.EVALUATION_CONTRACT["tvl_window_days"] == 30
    assert driver.EVALUATION_CONTRACT["fwd_horizon"] == 7
    assert driver.EVALUATION_CONTRACT["quantile"] == 0.3
    assert driver.EVALUATION_CONTRACT["parameter_scan"] is False
    assert "defillama_chain_tvl" in str(driver.TVL_GROWTH_EXPRESSION)
    assert "close" in str(driver.TVL_PRICE_DIVERGENCE_EXPRESSION)


def test_attach_fails_closed_below_formal_cross_section() -> None:
    panel = _panel()
    snapshot = _snapshot(panel)
    snapshot["series"] = dict(list(snapshot["series"].items())[:9])
    with pytest.raises(RuntimeError, match="covered instruments"):
        driver.attach_chain_tvl(
            panel, snapshot, min_instruments=10, min_common_rows=100
        )


def test_retrospective_history_blocks_before_trial_registration(monkeypatch) -> None:
    panel = _attached_panel()
    coverage = {
        "covered_instruments": len(INSTRUMENTS),
        "usable_rows": 100,
        "pit_admissible_for_promotion": False,
    }
    monkeypatch.setattr(
        driver,
        "load_panel",
        lambda _path: (panel, coverage, "b" * 64),
    )

    def must_not_register(*_args, **_kwargs):
        raise AssertionError("trial registration must not be reached")

    monkeypatch.setattr(driver, "run_registered_candidate_batch", must_not_register)
    with pytest.raises(RuntimeError, match="not PIT-admissible"):
        driver.run(source_commit="a" * 40)


def test_real_retrospective_artifact_blocks_before_loading_price_inputs(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "defillama.json"
    source.write_text(json.dumps(_snapshot(_panel())), encoding="utf-8")

    import tools.crypto_oi_research as price_inputs

    monkeypatch.setattr(
        price_inputs,
        "build_panel",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("price inputs must not load for a non-PIT source")
        ),
    )

    with pytest.raises(RuntimeError, match="not PIT-admissible"):
        driver.run(source_commit="a" * 40, source_snapshot=source)


def test_candidate_batch_has_shared_no_overlap_calendar() -> None:
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


def test_registered_run_is_public_research_only(tmp_path, monkeypatch) -> None:
    captured = {}
    fake_registry = SimpleNamespace(get_evaluation=lambda _run_id: None)

    def run_batch(candidates, **kwargs):
        captured["candidates"] = tuple(candidates)
        captured.update(kwargs)
        return {"recorded_run_ids": [], "registered_candidate_count": 3}

    monkeypatch.setattr(driver, "run_registered_candidate_batch", run_batch)
    monkeypatch.setattr(
        driver, "production_experiment_registry", lambda: nullcontext(fake_registry)
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
    assert captured["budget"].max_feature_count == 2
    assert document["status"] == "PENDING"
    assert document["decision"]["testnet_eligible"] is False
    assert document["safety"]["deployment_entitlement_reviewed"] is False
    assert document["safety"]["credentials_accessed"] is False
    assert document["safety"]["order_submission_allowed"] is False
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == document


def test_selection_requires_verified_formal_pass() -> None:
    evaluations = {
        "chain_tvl_growth": {"verified": True, "verdict": "PASS", "net_sharpe": 0.8},
        "chain_tvl_acceleration": {"verified": True, "verdict": "FAIL", "net_sharpe": 2.0},
        "chain_tvl_price_divergence": {
            "verified": True, "verdict": "PASS", "net_sharpe": 1.2,
        },
    }
    assert driver._decision(evaluations) == {
        "selected_alias": "chain_tvl_price_divergence",
        "formal_evaluation_pass": True,
        "human_review_required": False,
        "forward_admission_allowed": True,
        "testnet_eligible": False,
    }
