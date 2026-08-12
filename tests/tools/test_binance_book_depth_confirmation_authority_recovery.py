from __future__ import annotations

import json

import pytest

from tools import binance_book_depth_confirmation_authority_recovery as recovery


def _artifacts() -> dict:
    contract = {
        "recovery_id": "recovery-test",
        "reconstruction": {
            "day_count": 97,
            "end_exclusive_ms": 97 * recovery.DAY_MS,
            "universe": [f"S{index}" for index in range(14)],
        },
        "exact_acceptance": {
            "excluded_day_count": 6,
            "excluded_days_ms": [1, 2, 3, 4, 5, 6],
            "accepted_observation_count": 30_576,
            "complete_day_count": 91,
            "parent_complete_day_count": 85,
            "supplement_complete_day_count": 6,
            "actual_adjacent_24h_decision_count": 84,
        },
        "old_authority": {"recoverable": False},
    }
    confirmation = {
        "frozen_unseen_window_evidence": {"observed_at_ms": 10},
        "confirmation_window": {
            "start_day_ms": 7 * recovery.DAY_MS,
            "end_exclusive_ms": 97 * recovery.DAY_MS,
        },
        "horizon_hours": 24,
    }
    return {
        "recovery_contract": {
            "commit": "a" * 40,
            "sha256": "a" * 64,
            "repository_path": "contract.json",
            "payload": contract,
        },
        "confirmation": {
            "commit": "b" * 40,
            "sha256": "b" * 64,
            "repository_path": "confirmation.json",
            "payload": confirmation,
        },
    }


def _rows(observed_at: int) -> tuple[dict, ...]:
    return tuple(
        {
            "dataset": recovery.DATASET,
            "instrument_id": "S0",
            "event_time_ms": index + 1,
            "knowledge_time_ms": observed_at,
            "forward_eligible_from_ms": observed_at,
            "external_price_alignment": "PASS",
        }
        for index in range(30_576)
    )


def _backfill() -> dict:
    return {
        "status": "PASS",
        "processed_day_count": 97,
        "quality_excluded_day_count": 6,
        "quality_exclusions": [
            {
                "day_ms": day,
                "failures": [
                    {
                        "book_depth_checksum_verified": True,
                        "mark_price_checksum_verified": True,
                    }
                ],
            }
            for day in (1, 2, 3, 4, 5, 6)
        ],
    }


def _facts() -> dict:
    return {
        "dataset_row_count": 30_576,
        "authority_head": {"seq": 30_576, "row_hash": "h" * 64},
        "authority_chain_valid": True,
        "parent_capacity": {"complete_day_count": 85},
        "combined_capacity": {
            "complete_day_count": 91,
            "actual_adjacent_24h_decision_count": 84,
        },
    }


def test_recovery_publishes_only_after_exact_source_facts(tmp_path, monkeypatch):
    artifacts = _artifacts()
    calls = []
    monkeypatch.setattr(recovery, "_require_clean_source", lambda: "c" * 40)
    monkeypatch.setattr(recovery, "_verify_artifacts", lambda: artifacts)

    def backfill(**kwargs):
        calls.append("backfill")
        assert json.loads((tmp_path / "status.json").read_text())["generated_at_ms"] == 20
        assert kwargs["observed_at_ms"] == 20
        assert kwargs["minimum_snapshots_per_archive"] == 1
        return _backfill()

    monkeypatch.setattr(recovery, "run_backfill", backfill)
    monkeypatch.setattr(
        recovery,
        "_store_facts",
        lambda **_kwargs: (calls.append("facts") or _rows(20), _facts()),
    )
    output = tmp_path / "evidence.json"
    result = recovery.run_recovery(
        store_path=tmp_path / "store.db",
        archive_root=tmp_path / "raw",
        status_path=tmp_path / "status.json",
        output_path=output,
        observed_at_ms=20,
        max_workers=2,
    )

    assert calls == ["backfill", "facts"]
    assert result["status"] == "PASS"
    assert result["source_facts_exactly_reproduced"] is True
    assert result["new_authority"]["claims_old_chain_identity"] is False
    assert result["actual_adjacent_24h_decision_count"] == 84
    assert result["alpha_outcome_accessed"] is False
    assert result["confirmation_metrics_computed"] is False
    assert result["formal_admission"] is False
    assert result["testnet_authorized"] is False
    assert json.loads(output.read_text(encoding="utf-8")) == result


def test_recovery_rejects_fact_drift_without_publishing(tmp_path, monkeypatch):
    artifacts = _artifacts()
    monkeypatch.setattr(recovery, "_require_clean_source", lambda: "c" * 40)
    monkeypatch.setattr(recovery, "_verify_artifacts", lambda: artifacts)
    monkeypatch.setattr(recovery, "run_backfill", lambda **_kwargs: _backfill())
    bad_facts = _facts()
    bad_facts["combined_capacity"] = {
        "complete_day_count": 91,
        "actual_adjacent_24h_decision_count": 83,
    }
    monkeypatch.setattr(
        recovery, "_store_facts", lambda **_kwargs: (_rows(20), bad_facts)
    )
    output = tmp_path / "evidence.json"

    with pytest.raises(recovery.BookDepthAuthorityRecoveryError, match="exactly"):
        recovery.run_recovery(
            store_path=tmp_path / "store.db",
            archive_root=tmp_path / "raw",
            status_path=tmp_path / "status.json",
            output_path=output,
            observed_at_ms=20,
        )

    assert not output.exists()


def test_resume_reuses_first_network_knowledge_time(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps({"generated_at_ms": 20, "status": "FAIL"}), encoding="utf-8"
    )
    artifacts = _artifacts()
    monkeypatch.setattr(recovery, "_require_clean_source", lambda: "c" * 40)
    monkeypatch.setattr(recovery, "_verify_artifacts", lambda: artifacts)
    seen = []

    def backfill(**kwargs):
        seen.append(kwargs["observed_at_ms"])
        return _backfill()

    monkeypatch.setattr(recovery, "run_backfill", backfill)
    monkeypatch.setattr(
        recovery, "_store_facts", lambda **_kwargs: (_rows(20), _facts())
    )
    recovery.run_recovery(
        store_path=tmp_path / "store.db",
        archive_root=tmp_path / "raw",
        status_path=status,
        output_path=tmp_path / "evidence.json",
        observed_at_ms=99,
    )

    assert seen == [20]


def test_real_recovery_artifact_graph_is_valid() -> None:
    artifacts = recovery._verify_artifacts()

    assert artifacts["recovery_contract"]["commit"] == recovery.CONTRACT_COMMIT
    assert artifacts["capacity_pass"]["payload"]["confirmation_return_accessed"] is False
