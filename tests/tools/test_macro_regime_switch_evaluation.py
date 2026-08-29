from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace

from quantos.contracts.feature import FeatureValue
from services.data.fundamental_store import SqliteFundamentalStore
from services.research import alpha_expression as ax
from services.research.alpha_expression import FieldPanel
from tools import macro_regime_switch_evaluation as driver


def _panel(n_periods: int = 70, n_instruments: int = 12) -> FieldPanel:
    instruments = tuple(f"COIN{i}" for i in range(n_instruments))
    event_times = tuple(1_700_000_000_000 + row * 86_400_000 for row in range(n_periods))
    close = []
    funding = []
    global_ls = []
    for row in range(n_periods):
        close.append(
            [
                100.0 + row * 0.2 + column * 0.4 + ((row + column) % 3) * 0.1
                for column in range(n_instruments)
            ]
        )
        funding.append(
            [0.0001 * (((row + column) % 5) - 2) for column in range(n_instruments)]
        )
        global_ls.append(
            [1.0 + 0.1 * ((row + column) % 7) for column in range(n_instruments)]
        )
    return FieldPanel(
        instruments,
        event_times,
        {"close": close, "funding": funding, "global_ls": global_ls},
    )


def test_selector_uses_fixed_vix_threshold_and_one_horizon_lag() -> None:
    panel = _panel()
    carry = ax.evaluate(driver.CARRY_EXPRESSION, panel)
    crowd = ax.evaluate(driver.CROWD_EXPRESSION, panel)
    vix = [15.0] * len(panel.event_times)
    vix[7] = 25.0
    vix[14] = None

    selected = driver.select_regime_signal(carry, crowd, vix)

    assert all(value is None for row in selected[:7] for value in row)
    assert selected[7] == carry[7]
    assert selected[14] == crowd[14]
    assert all(value is None for value in selected[21])
    assert driver.VIX_STRESS_THRESHOLD == 20.0
    assert driver.REGIME_LAG_ROWS == 7
    assert driver.EVALUATION_QUANTILE == 0.3


def test_selector_is_invariant_to_appended_extreme_future_rows() -> None:
    panel = _panel()
    carry = ax.evaluate(driver.CARRY_EXPRESSION, panel)
    crowd = ax.evaluate(driver.CROWD_EXPRESSION, panel)
    vix = [15.0 if row % 2 == 0 else 25.0 for row in range(len(carry))]
    base = driver.select_regime_signal(carry, crowd, vix)

    future_carry = [*carry, *([[999.0] * len(panel.instruments) for _ in range(9)])]
    future_crowd = [*crowd, *([[-999.0] * len(panel.instruments) for _ in range(9)])]
    future_vix = [*vix, *([10_000.0] * 9)]
    extended = driver.select_regime_signal(future_carry, future_crowd, future_vix)

    assert extended[: len(base)] == base


def test_load_vix_values_uses_pit_authority(tmp_path) -> None:
    panel = _panel()
    path = tmp_path / "macro.db"
    store = SqliteFundamentalStore(str(path), create=True)
    first_ymd = driver._ymd(panel.event_times[0])
    assert store.add(
        FeatureValue(
            feature_id="VIXCLS",
            instrument_id="MACRO",
            event_time=first_ymd,
            knowledge_time=first_ymd,
            value=17.5,
        )
    )

    values = driver.load_vix_values(panel, macro_db=path)

    assert values == [17.5] * len(panel.event_times)


def test_build_candidate_batch_preregisters_controls_and_switch() -> None:
    panel = _panel()
    vix = [15.0 if row < 28 else 25.0 for row in range(len(panel.event_times))]

    candidates, evidence, mappings = driver.build_candidate_batch(panel, vix)

    assert tuple(mappings) == (
        "carry_control",
        "crowd_control",
        "vix_regime_switch",
    )
    assert len(candidates) == 3
    assert set(evidence) == {
        mapping["research_alpha_id"] for mapping in mappings.values()
    }
    for definition, periods in candidates:
        assert periods
        assert definition.definition_hash in {
            mapping["definition_hash"] for mapping in mappings.values()
        }
        assert evidence[definition.alpha_id]["leakage_ok"] is True
        assert all(
            (period.event_time - panel.event_times[7]) % (7 * 86_400_000) == 0
            for period in periods
        )
    switch = next(
        definition
        for definition, _periods in candidates
        if definition.name == "vix_regime_switch"
    )
    assert switch.factor_ids == ("VIXCLS", "funding", "global_ls")
    assert mappings["vix_regime_switch"]["return_basis"] == "PRICE_PLUS_FUNDING"


def test_registered_run_uses_closed_budget_and_never_marks_testnet_eligible(
    tmp_path, monkeypatch
) -> None:
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

    panel = _panel()
    document = driver._run_legacy_diagnostic(
        output=tmp_path / "result.json",
        recorded_at=123,
        source_commit="a" * 40,
        panel=panel,
        vix_values=[15.0] * len(panel.event_times),
    )

    assert len(captured["candidates"]) == 3
    assert captured["quantile"] == 0.3
    assert captured["budget"].max_trials == 3
    assert captured["budget"].max_feature_count == 3
    assert captured["budget"].max_parameter_dimensions == 2
    assert captured["data_source"] == driver.DATA_SOURCE
    assert captured["uses_locked_oos"] is False
    assert captured["code_hash"] == "a" * 40
    assert document["status"] == "PENDING"
    assert document["decision"] == {
        "formal_evaluation_pass": False,
        "human_review_required": False,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
    }
    assert document["safety"]["order_submission_allowed"] is False
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == document


def test_decision_requires_verified_switch_pass() -> None:
    assert driver._decision(None) == {
        "formal_evaluation_pass": False,
        "human_review_required": False,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
    }
    assert driver._decision(
        {"verified": True, "verdict": "REQUIRES_HUMAN_REVIEW"}
    ) == {
        "formal_evaluation_pass": False,
        "human_review_required": True,
        "forward_admission_allowed": False,
        "testnet_eligible": False,
    }
    assert driver._decision({"verified": True, "verdict": "PASS"}) == {
        "formal_evaluation_pass": True,
        "human_review_required": False,
        "forward_admission_allowed": True,
        "testnet_eligible": False,
    }


def test_evaluation_row_uses_immutable_search_link_for_definition_binding() -> None:
    panel = _panel()
    _candidates, _evidence, mappings = driver.build_candidate_batch(
        panel,
        [15.0] * len(panel.event_times),
    )
    mapping = mappings["vix_regime_switch"]
    run_id = "run-regime"
    content_hash = "c" * 64
    registry = SimpleNamespace(
        get_evaluation=lambda candidate_run_id: {
            "run_id": candidate_run_id,
            "alpha_id": mapping["research_alpha_id"],
            "dataset_version": "dataset-v1",
            "content_hash": content_hash,
            "evaluation_json": json.dumps(
                {
                    "verdict": "FAIL",
                    "deflated_sharpe": 0.33,
                    "pbo": 0.15,
                    "net_sharpe": 2.16,
                    "n_periods": 196,
                    "reasons": ["DEFLATED_SHARPE_BELOW_MIN"],
                }
            ),
        },
        search_evaluations=lambda search_id: [
            {
                "search_id": search_id,
                "config_hash": mapping["definition_hash"],
                "run_id": run_id,
                "evaluation_content_hash": content_hash,
            }
        ],
        verify_evaluation=lambda candidate_run_id: candidate_run_id == run_id,
    )

    item = driver._evaluation_row(registry, "search-regime", run_id, mappings)

    assert item is not None
    alias, evaluation = item
    assert alias == "vix_regime_switch"
    assert evaluation["verified"] is True
    assert evaluation["verdict"] == "FAIL"
