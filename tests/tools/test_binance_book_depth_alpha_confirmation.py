from __future__ import annotations

import json

import pytest

from services.data.binance_book_depth_archive import DATASET, DAY_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from tools import binance_book_depth_alpha_confirmation as confirmation
from tests.tools.test_binance_book_depth_alpha_research import _seed


def _contract() -> dict:
    payload = json.loads(
        confirmation.DEFAULT_CONFIRMATION.read_text(encoding="utf-8")
    )
    payload["minimum_decisions"] = 3
    return payload


def _artifacts(contract: dict) -> dict:
    supplement = {
        "study_id": "supplement",
        "minimum_combined_decisions": 3,
    }
    return {
        "confirmation": {
            "commit": "a" * 40,
            "sha256": "a" * 64,
            "repository_path": "confirmation.json",
            "payload": contract,
        },
        "supplement": {
            "commit": "b" * 40,
            "sha256": "b" * 64,
            "repository_path": "supplement.json",
            "payload": supplement,
        },
    }


def _observations(tmp_path, contract: dict):
    store_path = tmp_path / "depth.db"
    _seed(store_path, contract, days=5, observed_at_ms=100 * DAY_MS)
    store = BinanceSystemicStore(str(store_path))
    try:
        rows = store.range_observations(
            DATASET,
            start_event_time_ms=1,
            end_event_time_ms=5 * DAY_MS + 1,
            instruments=contract["universe"],
        )
        head = store.authority_head()
    finally:
        store.close()
    return rows, head


def test_driver_evaluates_once_after_all_bindings_and_never_authorizes(
    tmp_path, monkeypatch
):
    contract = _contract()
    artifacts = _artifacts(contract)
    rows, head = _observations(tmp_path, contract)
    calls = []
    monkeypatch.setattr(confirmation, "_require_clean_source", lambda: "c" * 40)
    monkeypatch.setattr(
        confirmation, "_verify_immutable_artifacts", lambda: artifacts
    )

    def registry(**_kwargs):
        calls.append("registry")
        return {
            "selected_config_hash": "d" * 64,
            "confirmation_trial_increment": 0,
        }

    def bound_input(**_kwargs):
        calls.append("input")
        return rows, {
            "input_observation_count": len(rows),
            "input_observation_sha256": "e" * 64,
            "actual_adjacent_24h_decision_count": 4,
            "authority_head": head,
            "authority_chain_valid": True,
        }

    real_evaluate = confirmation.evaluate_confirmation

    def evaluate(observations, frozen, **kwargs):
        calls.append("evaluate")
        assert kwargs == {"include_period_evidence": True}
        return real_evaluate(observations, frozen, **kwargs)

    monkeypatch.setattr(confirmation, "_verify_registry", registry)
    monkeypatch.setattr(confirmation, "_load_bound_input", bound_input)
    monkeypatch.setattr(confirmation, "evaluate_confirmation", evaluate)
    output = tmp_path / "confirmation.json"

    result = confirmation.run_confirmation(
        output_path=output,
        generated_at_ms=101 * DAY_MS,
    )

    assert calls == ["registry", "input", "evaluate"]
    assert result["decision_count"] == 4
    assert result["confirmation_evaluated_once"] is True
    assert len(result["period_evidence"]) == 4
    assert len(result["period_evidence_sha256"]) == 64
    assert result["formal_validation_started"] is False
    assert result["formal_admission"] is False
    assert result["forward_credit"] is False
    assert result["testnet_authorized"] is False
    assert result["order_submission_authorized"] is False
    assert result["experiment_registry"]["confirmation_trial_increment"] == 0
    assert result["safety"]["credentials_accessed"] is False
    assert json.loads(output.read_text(encoding="utf-8")) == result

    with pytest.raises(confirmation.BookDepthConfirmationError, match="already exists"):
        confirmation.run_confirmation(output_path=output)


def test_driver_stops_before_input_or_evaluation_when_registry_drifted(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(confirmation, "_require_clean_source", lambda: "c" * 40)
    monkeypatch.setattr(
        confirmation,
        "_verify_immutable_artifacts",
        lambda: _artifacts(_contract()),
    )
    monkeypatch.setattr(
        confirmation,
        "_verify_registry",
        lambda **_kwargs: (_ for _ in ()).throw(
            confirmation.BookDepthConfirmationError("registry drift")
        ),
    )
    monkeypatch.setattr(
        confirmation,
        "_load_bound_input",
        lambda **_kwargs: pytest.fail("input must not be accessed"),
    )
    monkeypatch.setattr(
        confirmation,
        "evaluate_confirmation",
        lambda *_args, **_kwargs: pytest.fail("outcome must not be accessed"),
    )

    with pytest.raises(confirmation.BookDepthConfirmationError, match="registry drift"):
        confirmation.run_confirmation(output_path=tmp_path / "result.json")


def test_real_immutable_artifact_graph_is_valid() -> None:
    artifacts = confirmation._verify_immutable_artifacts()

    assert artifacts["confirmation"]["commit"] == confirmation.CONFIRMATION_COMMIT
    assert artifacts["capacity_pass"]["payload"]["status"] == "PASS"
    assert artifacts["capacity_pass"]["payload"]["confirmation_return_accessed"] is False
    assert artifacts["supplement"]["payload"]["minimum_combined_decisions"] == 80
