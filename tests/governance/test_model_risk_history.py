from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from quantos.contracts.hashing import stable_hash
from scripts.update_model_risk_history import main as history_main
from services.governance.model_risk_history import (
    ModelRiskHistoryError,
    append_model_risk_revision,
    build_model_risk_revision,
    history_matches_evidence,
    validate_model_risk_history,
)


REPO = Path(__file__).resolve().parents[2]


def _evidence() -> dict[str, object]:
    return json.loads(
        (REPO / "evidence/model_risk_baseline.json").read_text(encoding="utf-8")
    )


def _record(payload: dict[str, object], artifact_type: str, artifact_id: str) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "content_hash": stable_hash(payload),
        "payload": payload,
    }


def _rehash_runtime_events(evidence: dict[str, object]) -> None:
    events = evidence["runtime_events"]
    assert isinstance(events, list)
    previous_hash = ""
    for sequence, event in enumerate(events, start=1):
        assert isinstance(event, dict)
        payload = event["payload"]
        assert isinstance(payload, dict)
        event.update(
            {
                "sequence": sequence,
                "invocation_id": payload["invocation_id"],
                "recorded_at": payload["recorded_at"],
                "model_ref": payload["model_ref"],
                "prompt_ref": payload["prompt_ref"],
                "tool_manifest_ref": payload["tool_manifest_ref"],
                "action": payload["action"],
                "previous_hash": previous_hash,
            }
        )
        previous_hash = stable_hash(
            {"previous_hash": previous_hash, "invocation": payload}
        )
        event["event_hash"] = previous_hash
    evidence["runtime_history_sha256"] = previous_hash
    if "runtime_invocation_count" in evidence:
        evidence["runtime_invocation_count"] = len(events)


def _rechain_history(history: dict[str, object]) -> None:
    revisions = history["revisions"]
    assert isinstance(revisions, list)
    previous_hash = ""
    for sequence, revision in enumerate(revisions, start=1):
        assert isinstance(revision, dict)
        snapshot = revision["snapshot"]
        assert isinstance(snapshot, dict)
        canonical = build_model_risk_revision(snapshot)
        revision_sha = canonical["revision_sha256"]
        entry_hash = stable_hash(
            {
                "sequence": sequence,
                "previous_hash": previous_hash,
                "revision_sha256": revision_sha,
            }
        )
        revision.update(
            {
                "sequence": sequence,
                "previous_hash": previous_hash,
                "revision_sha256": revision_sha,
                "entry_hash": entry_hash,
            }
        )
        previous_hash = entry_hash
    history["history_sha256"] = previous_hash


def _with_real_second_revision(evidence: dict[str, object]) -> dict[str, object]:
    revised = copy.deepcopy(evidence)
    artifacts = revised["artifacts"]
    assert isinstance(artifacts, list)

    ridge_card = next(
        record
        for record in artifacts
        if record["artifact_id"] == "model-card:fx-stack-ridge:v1"
    )
    card_payload = copy.deepcopy(ridge_card["payload"])
    card_payload["version"] = "v2"
    artifacts.append(
        _record(card_payload, "ModelCard", "model-card:fx-stack-ridge:v2")
    )

    ridge_evaluation = next(
        record
        for record in artifacts
        if record["artifact_id"] == "evaluation:fx-ridge:v1"
    )
    evaluation_payload = copy.deepcopy(ridge_evaluation["payload"])
    evaluation_payload["version"] = "v2"
    evaluation_payload["model_ref"] = "fx-stack-ridge@v2"
    artifacts.append(
        _record(evaluation_payload, "OfflineEvaluation", "evaluation:fx-ridge:v2")
    )
    return revised


def test_revision_history_is_hash_chained_idempotent_and_current() -> None:
    evidence = _evidence()

    history, appended = append_model_risk_revision(None, evidence)

    assert appended is True
    assert history["revision_count"] == 1
    assert history["status"] == "SINGLE_REVISION_BASELINE"
    assert len(history["history_sha256"]) == 64
    assert validate_model_risk_history(history)["revision_count"] == 1
    assert history_matches_evidence(history, evidence) is True

    unchanged, appended = append_model_risk_revision(history, evidence)
    assert appended is False
    assert unchanged == history


def test_revision_history_rejects_snapshot_and_chain_tampering() -> None:
    history, _ = append_model_risk_revision(None, _evidence())
    tampered_snapshot = copy.deepcopy(history)
    tampered_snapshot["revisions"][0]["snapshot"]["source_snapshot_sha256"] = "0" * 64
    with pytest.raises(ModelRiskHistoryError, match="revision digest"):
        validate_model_risk_history(tampered_snapshot)

    tampered_chain = copy.deepcopy(history)
    tampered_chain["revisions"][0]["entry_hash"] = "0" * 64
    with pytest.raises(ModelRiskHistoryError, match="entry hash"):
        validate_model_risk_history(tampered_chain)


def test_revision_history_rejects_artifact_version_reuse() -> None:
    evidence = _evidence()
    history, _ = append_model_risk_revision(None, evidence)
    reused = copy.deepcopy(evidence)
    record = next(
        item
        for item in reused["artifacts"]
        if item["artifact_id"] == "model-card:fx-stack-ridge:v1"
    )
    record["payload"]["intended_use"] = "changed without a version bump"
    record["content_hash"] = stable_hash(record["payload"])

    with pytest.raises(ModelRiskHistoryError, match="version reused"):
        append_model_risk_revision(history, reused)


def test_revision_history_rejects_duplicate_runtime_invocation_identity() -> None:
    duplicated = copy.deepcopy(_evidence())
    events = duplicated["runtime_events"]
    assert isinstance(events, list)
    events.append(copy.deepcopy(events[0]))
    _rehash_runtime_events(duplicated)

    with pytest.raises(ModelRiskHistoryError, match="duplicate runtime invocation"):
        append_model_risk_revision(None, duplicated)


def test_revision_history_rejects_runtime_invocation_reuse_and_discard() -> None:
    evidence = _evidence()
    history, _ = append_model_risk_revision(None, evidence)

    reused = _with_real_second_revision(evidence)
    reused_events = reused["runtime_events"]
    assert isinstance(reused_events, list)
    reused_payload = reused_events[0]["payload"]
    assert isinstance(reused_payload, dict)
    reused_payload["input_sha256"] = "f" * 64
    _rehash_runtime_events(reused)
    with pytest.raises(ModelRiskHistoryError, match="reused with different payload"):
        append_model_risk_revision(history, reused)

    discarded = _with_real_second_revision(evidence)
    discarded_events = discarded["runtime_events"]
    assert isinstance(discarded_events, list)
    replacement = copy.deepcopy(discarded_events[0])
    replacement_payload = replacement["payload"]
    assert isinstance(replacement_payload, dict)
    replacement_payload["invocation_id"] = "replacement-runtime-v2"
    discarded["runtime_events"] = [replacement]
    _rehash_runtime_events(discarded)
    with pytest.raises(ModelRiskHistoryError, match="cannot discard runtime invocations"):
        append_model_risk_revision(history, discarded)


def test_history_validation_rejects_recomputed_runtime_rewrite() -> None:
    evidence = _evidence()
    history, _ = append_model_risk_revision(None, evidence)
    history, _ = append_model_risk_revision(
        history, _with_real_second_revision(evidence)
    )

    rewritten = copy.deepcopy(history)
    rewritten_snapshot = rewritten["revisions"][1]["snapshot"]
    rewritten_payload = rewritten_snapshot["runtime_events"][0]["payload"]
    rewritten_payload["input_sha256"] = "f" * 64
    _rehash_runtime_events(rewritten_snapshot)
    _rechain_history(rewritten)
    with pytest.raises(ModelRiskHistoryError, match="reused with different payload"):
        validate_model_risk_history(rewritten)

    discarded = copy.deepcopy(history)
    discarded_snapshot = discarded["revisions"][1]["snapshot"]
    replacement = copy.deepcopy(discarded_snapshot["runtime_events"][0])
    replacement["payload"]["invocation_id"] = "replacement-runtime-v2"
    discarded_snapshot["runtime_events"] = [replacement]
    _rehash_runtime_events(discarded_snapshot)
    _rechain_history(discarded)
    with pytest.raises(ModelRiskHistoryError, match="cannot discard runtime invocations"):
        validate_model_risk_history(discarded)


def test_revision_history_accepts_new_versions_but_rejects_rollback() -> None:
    evidence = _evidence()
    history, _ = append_model_risk_revision(None, evidence)
    revised = _with_real_second_revision(evidence)

    history, appended = append_model_risk_revision(history, revised)

    assert appended is True
    assert history["revision_count"] == 2
    assert history["status"] == "MULTI_REVISION_HISTORY"
    assert validate_model_risk_history(history)["revision_count"] == 2
    assert history_matches_evidence(history, revised) is True
    assert history_matches_evidence(history, evidence) is False

    with pytest.raises(ModelRiskHistoryError, match="rollback"):
        append_model_risk_revision(history, evidence)


def test_history_cli_is_idempotent_and_fails_closed_on_stale_evidence(tmp_path) -> None:
    evidence_path = tmp_path / "model-risk.json"
    history_path = tmp_path / "model-risk-history.json"
    evidence_path.write_text(
        json.dumps(_evidence(), sort_keys=True), encoding="utf-8"
    )

    args = ["--evidence", str(evidence_path), "--history", str(history_path)]
    assert history_main(args) == 0
    first = history_path.read_bytes()
    assert history_main([*args, "--check"]) == 0
    assert history_main(args) == 0
    assert history_path.read_bytes() == first

    stale = json.loads(evidence_path.read_text(encoding="utf-8"))
    stale["source_snapshot_sha256"] = "0" * 64
    evidence_path.write_text(json.dumps(stale, sort_keys=True), encoding="utf-8")
    assert history_main([*args, "--check"]) == 1
