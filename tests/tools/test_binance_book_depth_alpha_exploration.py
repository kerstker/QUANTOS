from __future__ import annotations

import json

import pytest

from services.data.binance_book_depth_archive import HOUR_MS
from services.research.experiment_registry.store import SqliteExperimentStore
from tools import binance_book_depth_alpha_exploration as exploration
from tools import binance_book_depth_alpha_research as research
from tests.tools.test_binance_book_depth_alpha_research import _seed


def _artifacts(tmp_path, *, days: int = 90):
    preregistration = json.loads(research.DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    exploration_contract = json.loads(
        exploration.DEFAULT_EXPLORATION.read_text(encoding="utf-8")
    )
    store_path = tmp_path / "depth.db"
    _seed(
        store_path,
        preregistration,
        days=days,
        observed_at_ms=200 * 86_400_000,
    )
    store = research.BinanceSystemicStore(str(store_path))
    try:
        common_end = days * 86_400_000
        observations = store.range_observations(
            research.DATASET,
            start_event_time_ms=1,
            end_event_time_ms=common_end + 1,
            instruments=tuple(preregistration["universe"]),
        )
        authority_head = store.authority_head()
    finally:
        store.close()
    source_output = tmp_path / "source.json"
    source = {
        "status": "FAIL",
        "formal_admission": False,
        "dataset": research.DATASET,
        "last_decision_event_time_ms": common_end - 4 * HOUR_MS,
        "input_observation_count": len(observations),
        "input_observation_sha256": research._observation_digest(list(observations)),
        "authority_head": authority_head,
    }
    source_output.write_text(json.dumps(source), encoding="utf-8")
    experiment_db = tmp_path / "experiment.db"
    SqliteExperimentStore(str(experiment_db), create=True).close()
    return preregistration, exploration_contract, store_path, source_output, experiment_db


def test_exploration_registers_all_trials_before_evaluation_and_never_authorizes(
    tmp_path, monkeypatch
):
    prereg, contract, store_path, source_path, experiment_db = _artifacts(tmp_path)
    monkeypatch.setattr(exploration, "_require_clean_source", lambda: "c" * 40)
    monkeypatch.setattr(
        exploration,
        "_verify_preregistration",
        lambda _path: (research.PREREGISTRATION_COMMIT, "a" * 64, prereg),
    )

    def committed(path, *, commit, repository_path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ("b" * 64, contract) if "exploration" in repository_path else ("d" * 64, payload)

    monkeypatch.setattr(exploration, "_committed_json", committed)
    real_evaluate = exploration.evaluate_exploration

    def evaluate_after_registration(observations, preregistration, frozen):
        check = SqliteExperimentStore(str(experiment_db), create=False)
        try:
            assert check.trial_count() == 60
        finally:
            check.close()
        return real_evaluate(observations, preregistration, frozen)

    monkeypatch.setattr(exploration, "evaluate_exploration", evaluate_after_registration)
    output = tmp_path / "result.json"
    result = exploration.run_exploration(
        store_path=store_path,
        experiment_db_path=experiment_db,
        source_evidence_path=source_path,
        output_path=output,
        generated_at_ms=201 * 86_400_000,
    )

    assert result["trial_count"] == 60
    assert result["experiment_registry"]["candidate_count"] == 60
    assert result["experiment_registry"]["global_trial_count_delta"] == 60
    assert result["experiment_registry"]["terminal_status"] == "COMPLETED"
    assert result["experiment_registry"]["search_history_valid"] is True
    assert result["confirmation_required"] is True
    assert result["formal_admission"] is False
    assert result["testnet_authorized"] is False
    assert result["safety"]["credentials_accessed"] is False
    assert json.loads(output.read_text(encoding="utf-8")) == result


def test_exploration_rejects_source_digest_drift(tmp_path, monkeypatch):
    prereg, contract, store_path, source_path, experiment_db = _artifacts(tmp_path)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["input_observation_sha256"] = "0" * 64
    source_path.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setattr(exploration, "_require_clean_source", lambda: "c" * 40)
    monkeypatch.setattr(
        exploration,
        "_verify_preregistration",
        lambda _path: (research.PREREGISTRATION_COMMIT, "a" * 64, prereg),
    )
    monkeypatch.setattr(
        exploration,
        "_committed_json",
        lambda path, **_kwargs: (
            "b" * 64,
            contract if path.name.endswith("exploration.json") else json.loads(path.read_text()),
        ),
    )

    with pytest.raises(exploration.BookDepthExplorationError, match="digest"):
        exploration.run_exploration(
            store_path=store_path,
            experiment_db_path=experiment_db,
            source_evidence_path=source_path,
            output_path=tmp_path / "result.json",
            generated_at_ms=201 * 86_400_000,
        )


def test_real_frozen_exploration_and_source_evidence_match_commits() -> None:
    exploration_digest, exploration_payload = exploration._committed_json(
        exploration.DEFAULT_EXPLORATION,
        commit=exploration.EXPLORATION_COMMIT,
        repository_path="config/binance_book_depth_alpha_exploration.json",
    )
    source_digest, source = exploration._committed_json(
        exploration.DEFAULT_SOURCE_EVIDENCE,
        commit=exploration.SOURCE_EVIDENCE_COMMIT,
        repository_path="evidence/binance_book_depth_alpha_research.json",
    )

    assert len(exploration_digest) == len(source_digest) == 64
    assert exploration_payload["trial_count"] == 60
    assert source["status"] == "FAIL"
    assert source["formal_admission"] is False
