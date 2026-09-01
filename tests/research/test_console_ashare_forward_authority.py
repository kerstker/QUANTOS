from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from quantos.contracts.hashing import stable_hash
from services.console.api import ashare_views
from tools.ashare_forward_observation import _SCHEMA


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode(
        "utf-8"
    )


def _publish_forward_authority(monkeypatch, tmp_path: Path) -> dict[str, Path | dict]:
    cache = tmp_path / "data" / "cache"
    cache.mkdir(parents=True)
    canonical = tmp_path / "data" / "stocks" / "pit" / "tdx" / "ashare_tdx_pit.sqlite"
    canonical.parent.mkdir(parents=True)
    source_connection = sqlite3.connect(canonical)
    source_connection.execute("CREATE TABLE daily_prices(trade_date INTEGER NOT NULL)")
    source_connection.execute("INSERT INTO daily_prices VALUES (20260828)")
    source_connection.commit()
    source_connection.close()
    source_sha256 = hashlib.sha256(canonical.read_bytes()).hexdigest()
    candidate = {
        "name": "reversal_5_h5",
        "family": "reversal",
        "window": 5,
        "skip": 0,
        "hold_days": 5,
    }
    cutoff = 20260828
    prefix = {
        "schema_version": 1,
        "algorithm": "SHA256_SQLITE_DAILY_PRICES_PK_PREFIX_V1",
        "cutoff": cutoff,
        "rows": 10,
        "sha256": "1" * 64,
    }
    observation = {
        "freeze_cutoff": cutoff,
        "starts_after": cutoff,
        "minimum_sessions": 60,
        "candidate_specs": [candidate],
        "candidate_set_hash": stable_hash([candidate]),
        "selection_locked": True,
        "promotion_allowed": False,
    }
    frozen_search = {
        "artifact_kind": "ASHARE_ALPHA_SEARCH_INSTITUTIONAL_SURFACES_V2",
        "evidence_status": "TECHNICAL_VALIDATION_ONLY",
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "source": {"sha256": source_sha256, "last_date": cutoff},
        "untouched_forward_observation": observation,
    }
    search_bytes = _json_bytes(frozen_search)
    search_sha256 = hashlib.sha256(search_bytes).hexdigest()
    artifacts = cache / "ashare_alpha_search_artifacts"
    artifacts.mkdir()
    (artifacts / f"{search_sha256}.json").write_bytes(search_bytes)

    freeze = {
        "schema_version": 1,
        "artifact_kind": "ASHARE_UNTOUCHED_FORWARD_FREEZE_V1",
        "evidence_status": "TECHNICAL_VALIDATION_ONLY",
        "freeze_cutoff": cutoff,
        "starts_after": cutoff,
        "minimum_sessions": 60,
        "candidate_specs": [candidate],
        "candidate_set_hash": stable_hash([candidate]),
        "source_database_sha256_at_freeze": source_sha256,
        "source_rows_at_freeze": 10,
        "source_prefix": prefix,
        "selection_locked": True,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "search_artifact_sha256": search_sha256,
    }
    freeze_path = cache / "ashare_forward_freeze.json"
    freeze_bytes = _json_bytes(freeze)
    freeze_path.write_bytes(freeze_bytes)
    freeze_sha256 = hashlib.sha256(freeze_bytes).hexdigest()

    ledger_path = cache / "ashare_forward_observation.db"
    connection = sqlite3.connect(ledger_path)
    connection.executescript(_SCHEMA)
    recorded_at_ms = 1_000
    authority_payload = {
        "schema_version": 1,
        "freeze_sha256": freeze_sha256,
        "candidate_set_hash": freeze["candidate_set_hash"],
        "freeze_cutoff": cutoff,
        "source_prefix_sha256": prefix["sha256"],
        "source_prefix_rows": prefix["rows"],
        "source_database_sha256_at_freeze": source_sha256,
        "search_artifact_sha256": search_sha256,
        "recorded_at_ms": recorded_at_ms,
    }
    authority_hash = ashare_views._forward_record_hash(
        authority_payload,
        "0" * 64,
    )
    connection.execute(
        "INSERT INTO ledger_authority VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            1,
            *(authority_payload[name] for name in ashare_views._FORWARD_AUTHORITY_FIELDS),
            "0" * 64,
            authority_hash,
        ),
    )
    checkpoint_payload = {
        "sequence": 1,
        "through_date": cutoff,
        "prefix_algorithm": prefix["algorithm"],
        "prefix_sha256": prefix["sha256"],
        "prefix_rows": prefix["rows"],
        "source_database_sha256": source_sha256,
        "observation_status": "FREEZE_ANCHOR",
        "decision_deadline_ms": 0,
        "recorded_at_ms": recorded_at_ms,
    }
    checkpoint_hash = ashare_views._forward_record_hash(
        checkpoint_payload,
        "0" * 64,
    )
    connection.execute(
        "INSERT INTO source_checkpoints VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            *(checkpoint_payload[name] for name in ashare_views._FORWARD_CHECKPOINT_FIELDS),
            "0" * 64,
            checkpoint_hash,
        ),
    )
    connection.commit()
    connection.close()

    report = {
        "schema_version": 1,
        "artifact_kind": "ASHARE_UNTOUCHED_FORWARD_OBSERVATION_V1",
        "evidence_status": "TECHNICAL_VALIDATION_ONLY",
        "status": "ARMED_AWAITING_NEW_SESSIONS",
        "generated_at_ms": recorded_at_ms,
        "freeze_cutoff": cutoff,
        "latest_source_date": cutoff,
        "sessions_observed": 0,
        "calendar_sessions_since_freeze": 0,
        "missed_observation_sessions": 0,
        "observation_gaps_detected": False,
        "observation_gaps": [],
        "minimum_sessions": 60,
        "minimum_sessions_met": False,
        "selection_locked": True,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "source": {"sha256": source_sha256},
        "cycle": {
            "new_decisions": 0,
            "new_outcomes": 0,
            "decision_count": 0,
            "outcome_count": 0,
            "pending_count": 0,
            "skipped": {},
        },
        "ledger": {
            "path": str(ledger_path),
            "append_only": True,
            "chain_valid": True,
            "freeze_sha256": freeze_sha256,
            "authority_hash": authority_hash,
            "source_checkpoint_count": 1,
            "source_checkpoint_head": checkpoint_hash,
            "source_checkpoint_added": True,
            "new_source_checkpoints": 1,
            "checkpoint_schema_version": 2,
            "legacy_checkpoint_migration": None,
            "decision_head": "0" * 64,
            "outcome_head": "0" * 64,
        },
        "chain_valid": True,
        "candidates": {
            candidate["name"]: {
                "candidate": candidate,
                "realized": {
                    "n_periods": 0,
                    "total_return": None,
                    "annualized_return": None,
                    "annualized_sharpe": None,
                    "max_drawdown": None,
                    "mean_return": None,
                    "t_stat": None,
                    "one_sided_p": None,
                },
                "average_turnover": None,
                "unpriced_legs": 0,
                "periods": [],
            }
        },
        "research_contract": {
            "due_decisions_expected": 0,
            "due_decisions_recorded": 0,
            "due_decision_coverage_complete": True,
            "required_decision_gaps": [],
        },
        "safety": {
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }
    report_path = cache / "ashare_forward_observation.json"
    report_path.write_bytes(_json_bytes(report))
    monkeypatch.setattr(ashare_views, "_FORWARD_OBSERVATION_PATH", report_path)
    monkeypatch.setattr(ashare_views, "_FORWARD_FREEZE_PATH", freeze_path)
    monkeypatch.setattr(ashare_views, "_FORWARD_LEDGER_PATH", ledger_path)
    monkeypatch.setattr(ashare_views, "_CANONICAL_TDX_SQLITE", canonical)
    monkeypatch.setattr(ashare_views, "_FORWARD_AUTHORITY_CACHE", None)
    return {
        "report_path": report_path,
        "freeze_path": freeze_path,
        "ledger_path": ledger_path,
        "search_path": artifacts / f"{search_sha256}.json",
        "report": report,
        "freeze": freeze,
    }


def test_forward_api_recomputes_complete_immutable_authority(monkeypatch, tmp_path):
    _publish_forward_authority(monkeypatch, tmp_path)

    payload = ashare_views._ashare_forward_observation_payload()

    assert payload["snapshot_present"] is True
    assert payload["data_provenance"] == "REAL_ASHARE_FORWARD_OBSERVATION"
    assert payload["chain_valid"] is True


@pytest.mark.parametrize(
    "target",
    ["report", "minimum_gate", "metrics", "ledger", "freeze", "search"],
)
def test_forward_api_fails_closed_after_authority_tampering(
    monkeypatch,
    tmp_path,
    target,
):
    fixture = _publish_forward_authority(monkeypatch, tmp_path)
    assert ashare_views._ashare_forward_observation_payload()["snapshot_present"] is True

    if target == "report":
        report = fixture["report"]
        assert isinstance(report, dict)
        report["ledger"]["decision_head"] = "f" * 64
        fixture["report_path"].write_bytes(_json_bytes(report))
    elif target == "minimum_gate":
        report = fixture["report"]
        assert isinstance(report, dict)
        report["minimum_sessions_met"] = True
        fixture["report_path"].write_bytes(_json_bytes(report))
    elif target == "metrics":
        report = fixture["report"]
        assert isinstance(report, dict)
        report["candidates"]["reversal_5_h5"]["realized"][
            "annualized_sharpe"
        ] = 99.0
        fixture["report_path"].write_bytes(_json_bytes(report))
    elif target == "freeze":
        freeze = fixture["freeze"]
        assert isinstance(freeze, dict)
        freeze["minimum_sessions"] = 61
        fixture["freeze_path"].write_bytes(_json_bytes(freeze))
    elif target == "search":
        fixture["search_path"].write_bytes(
            fixture["search_path"].read_bytes() + b" "
        )
    else:
        connection = sqlite3.connect(fixture["ledger_path"])
        connection.execute("DROP TRIGGER source_checkpoints_no_update")
        connection.execute(
            "UPDATE source_checkpoints SET prefix_sha256=? WHERE sequence=1",
            ("e" * 64,),
        )
        connection.commit()
        connection.close()

    payload = ashare_views._ashare_forward_observation_payload()

    assert payload["snapshot_present"] is False
    assert payload["data_provenance"] == "UNAVAILABLE"
    assert payload["status"] == "INVALID_AUTHORITY"
    assert payload["chain_valid"] is False
    assert payload["selection_locked"] is False
