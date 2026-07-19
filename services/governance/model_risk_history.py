"""Tamper-evident longitudinal history for governed model-risk revisions.

The history is offline evidence only. It grants no promotion, execution,
credential, or live-trading authority.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError

from quantos.contracts.hashing import stable_hash
from quantos.contracts.model_risk import (
    ChallengerReview,
    ModelCard,
    OfflineEvaluation,
    PromptTemplateCard,
    RedTeamReport,
    RuntimeInvocation,
    ToolManifest,
)


class ModelRiskHistoryError(ValueError):
    """The revision history is missing, inconsistent, stale, or corrupt."""


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MODE = "APPEND_ONLY_MODEL_RISK_REVISION_HISTORY"
_ARTIFACT_MODELS: dict[str, type[BaseModel]] = {
    cls.__name__: cls
    for cls in (
        ModelCard,
        PromptTemplateCard,
        ToolManifest,
        OfflineEvaluation,
        RedTeamReport,
        ChallengerReview,
    )
}
_SNAPSHOT_FIELDS = (
    "recorded_at",
    "source_result_verdict",
    "source_snapshot_ref",
    "source_snapshot_sha256",
    "dataset_ref",
    "dataset_sha256",
    "source_dataset_ref",
    "source_dataset_sha256",
    "dataset_bundle_status",
    "dataset_bundle_manifest_ref",
    "dataset_bundle_manifest_sha256",
    "dataset_bundle_row_count",
    "runtime_history_sha256",
    "runtime_events",
    "artifacts",
    "red_team_report_ref",
    "challenger_review_refs",
)
_HISTORY_FIELDS = {
    "schema_version",
    "mode",
    "status",
    "revision_count",
    "history_sha256",
    "revisions",
}
_REVISION_FIELDS = {
    "sequence",
    "previous_hash",
    "revision_sha256",
    "entry_hash",
    "snapshot",
}


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelRiskHistoryError(f"{label} must be an object")
    return value


def _digest(value: object, label: str, *, optional: bool = False) -> str:
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ModelRiskHistoryError(f"{label} must be a SHA-256 digest")
    return value


def _text(value: object, label: str, *, optional: bool = False) -> str:
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value.strip():
        raise ModelRiskHistoryError(f"{label} must be non-empty text")
    return value


def _artifact_records(value: object) -> tuple[list[dict[str, Any]], list[BaseModel]]:
    if not isinstance(value, list) or not value:
        raise ModelRiskHistoryError("artifacts must be a non-empty list")
    records: list[dict[str, Any]] = []
    artifacts: list[BaseModel] = []
    seen: set[str] = set()
    for index, candidate in enumerate(value):
        record = _mapping(candidate, f"artifacts[{index}]")
        if set(record) != {"artifact_id", "artifact_type", "content_hash", "payload"}:
            raise ModelRiskHistoryError(f"artifacts[{index}] has an invalid shape")
        artifact_id = _text(record.get("artifact_id"), f"artifacts[{index}].artifact_id")
        artifact_type = _text(
            record.get("artifact_type"), f"artifacts[{index}].artifact_type"
        )
        model_type = _ARTIFACT_MODELS.get(artifact_type)
        if model_type is None:
            raise ModelRiskHistoryError(f"unsupported artifact type: {artifact_type}")
        payload = _mapping(record.get("payload"), f"artifacts[{index}].payload")
        try:
            artifact = model_type.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ModelRiskHistoryError(
                f"artifact contract failed for {artifact_id}: {exc}"
            ) from exc
        actual_id = getattr(artifact, "artifact_id", "")
        if actual_id != artifact_id:
            raise ModelRiskHistoryError(f"artifact identity mismatch: {artifact_id}")
        content_hash = _digest(
            record.get("content_hash"), f"artifacts[{index}].content_hash"
        )
        if stable_hash(dict(payload)) != content_hash:
            raise ModelRiskHistoryError(f"artifact content hash mismatch: {artifact_id}")
        if artifact_id in seen:
            raise ModelRiskHistoryError(f"duplicate artifact identity: {artifact_id}")
        seen.add(artifact_id)
        records.append(copy.deepcopy(dict(record)))
        artifacts.append(artifact)
    ordered = sorted(zip(records, artifacts), key=lambda item: item[0]["artifact_id"])
    return [item[0] for item in ordered], [item[1] for item in ordered]


def _runtime_events(value: object) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(value, list) or not value:
        raise ModelRiskHistoryError("runtime_events must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    previous_hash = ""
    seen_invocations: set[str] = set()
    required = {
        "sequence",
        "invocation_id",
        "recorded_at",
        "model_ref",
        "prompt_ref",
        "tool_manifest_ref",
        "action",
        "previous_hash",
        "event_hash",
        "payload",
    }
    for expected_sequence, candidate in enumerate(value, start=1):
        event = _mapping(candidate, f"runtime_events[{expected_sequence - 1}]")
        if set(event) != required:
            raise ModelRiskHistoryError("runtime event has an invalid shape")
        payload = _mapping(event.get("payload"), "runtime event payload")
        try:
            invocation = RuntimeInvocation.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ModelRiskHistoryError(f"runtime invocation contract failed: {exc}") from exc
        if invocation.invocation_id in seen_invocations:
            raise ModelRiskHistoryError(
                f"duplicate runtime invocation identity: {invocation.invocation_id}"
            )
        seen_invocations.add(invocation.invocation_id)
        if (
            event.get("sequence") != expected_sequence
            or event.get("invocation_id") != invocation.invocation_id
            or event.get("recorded_at") != invocation.recorded_at
            or event.get("model_ref") != invocation.model_ref
            or event.get("prompt_ref") != invocation.prompt_ref
            or event.get("tool_manifest_ref") != invocation.tool_manifest_ref
            or event.get("action") != invocation.action
            or event.get("previous_hash") != previous_hash
        ):
            raise ModelRiskHistoryError("runtime invocation lineage mismatch")
        expected_hash = stable_hash(
            {"previous_hash": previous_hash, "invocation": dict(payload)}
        )
        if event.get("event_hash") != expected_hash:
            raise ModelRiskHistoryError("runtime invocation hash mismatch")
        previous_hash = expected_hash
        normalized.append(copy.deepcopy(dict(event)))
    return normalized, previous_hash


def _validate_runtime_progression(
    prior_events: list[dict[str, Any]],
    current_events: list[dict[str, Any]],
) -> None:
    prior_by_id = {
        event["invocation_id"]: stable_hash(event["payload"])
        for event in prior_events
    }
    current_by_id = {
        event["invocation_id"]: stable_hash(event["payload"])
        for event in current_events
    }
    reused = sorted(
        invocation_id
        for invocation_id, payload_hash in prior_by_id.items()
        if invocation_id in current_by_id
        and current_by_id[invocation_id] != payload_hash
    )
    if reused:
        raise ModelRiskHistoryError(
            "runtime invocation identity reused with different payload: "
            + ", ".join(reused)
        )
    missing = sorted(set(prior_by_id) - set(current_by_id))
    if missing:
        raise ModelRiskHistoryError(
            "model-risk history cannot discard runtime invocations: "
            + ", ".join(missing)
        )
    if current_events[: len(prior_events)] != prior_events:
        raise ModelRiskHistoryError(
            "runtime invocation history must append to the prior event chain"
        )


def _validate_references(
    artifacts: list[BaseModel],
    *,
    dataset_ref: str,
    dataset_sha256: str,
    red_team_report_ref: str,
    challenger_review_refs: list[str],
) -> None:
    models = [item for item in artifacts if isinstance(item, ModelCard)]
    prompts = [item for item in artifacts if isinstance(item, PromptTemplateCard)]
    tools = [item for item in artifacts if isinstance(item, ToolManifest)]
    evaluations = [item for item in artifacts if isinstance(item, OfflineEvaluation)]
    red_teams = [item for item in artifacts if isinstance(item, RedTeamReport)]
    reviews = [item for item in artifacts if isinstance(item, ChallengerReview)]
    if not all((models, prompts, tools, evaluations, red_teams, reviews)):
        raise ModelRiskHistoryError("revision lacks a required model-risk artifact type")

    model_refs = {item.model_ref for item in models}
    prompt_refs = {item.prompt_ref for item in prompts}
    tool_refs = {item.manifest_ref for item in tools}
    evaluation_refs = {item.evaluation_ref for item in evaluations}
    for model in models:
        if model.prompt_template_ref and model.prompt_template_ref not in prompt_refs:
            raise ModelRiskHistoryError(f"unknown prompt reference: {model.prompt_template_ref}")
        if model.tool_manifest_ref and model.tool_manifest_ref not in tool_refs:
            raise ModelRiskHistoryError(f"unknown tool reference: {model.tool_manifest_ref}")
        if model.dataset_ref and (
            model.dataset_ref != dataset_ref or model.dataset_sha256 != dataset_sha256
        ):
            raise ModelRiskHistoryError(f"model dataset mismatch: {model.model_ref}")
    for prompt in prompts:
        if prompt.model_ref not in model_refs or (
            prompt.tool_manifest_ref and prompt.tool_manifest_ref not in tool_refs
        ):
            raise ModelRiskHistoryError(f"prompt lineage mismatch: {prompt.prompt_ref}")
    for evaluation in evaluations:
        if evaluation.model_ref not in model_refs:
            raise ModelRiskHistoryError(
                f"evaluation model is unknown: {evaluation.evaluation_ref}"
            )
        if (
            evaluation.dataset_ref != dataset_ref
            or evaluation.dataset_sha256 != dataset_sha256
        ):
            raise ModelRiskHistoryError(
                f"evaluation dataset mismatch: {evaluation.evaluation_ref}"
            )
    red_team_refs = {item.artifact_id for item in red_teams}
    if red_team_report_ref not in red_team_refs:
        raise ModelRiskHistoryError("red-team report reference is unknown")
    for report in red_teams:
        if report.model_ref not in model_refs or report.tool_manifest_ref not in tool_refs:
            raise ModelRiskHistoryError(f"red-team lineage mismatch: {report.artifact_id}")
    review_refs = {item.artifact_id for item in reviews}
    if set(challenger_review_refs) != review_refs:
        raise ModelRiskHistoryError("challenger review references are incomplete")
    for review in reviews:
        if (
            review.incumbent_model_ref not in model_refs
            or review.challenger_model_ref not in model_refs
            or review.incumbent_evaluation_ref not in evaluation_refs
            or review.challenger_evaluation_ref not in evaluation_refs
        ):
            raise ModelRiskHistoryError(f"challenger lineage mismatch: {review.artifact_id}")


def build_model_risk_revision(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical, reconstructable revision snapshot and its digest."""
    document = _mapping(evidence, "model-risk evidence")
    missing = [field for field in _SNAPSHOT_FIELDS if field not in document]
    if missing:
        raise ModelRiskHistoryError("model-risk evidence is missing: " + ", ".join(missing))
    recorded_at = document.get("recorded_at")
    if type(recorded_at) is not int or recorded_at < 0:
        raise ModelRiskHistoryError("recorded_at must be a non-negative integer")

    dataset_ref = _text(document.get("dataset_ref"), "dataset_ref")
    dataset_sha256 = _digest(document.get("dataset_sha256"), "dataset_sha256")
    bundle_status = _text(document.get("dataset_bundle_status"), "dataset_bundle_status")
    manifest_ref = _text(
        document.get("dataset_bundle_manifest_ref"),
        "dataset_bundle_manifest_ref",
        optional=True,
    )
    manifest_sha = _digest(
        document.get("dataset_bundle_manifest_sha256"),
        "dataset_bundle_manifest_sha256",
        optional=True,
    )
    row_count = document.get("dataset_bundle_row_count")
    if type(row_count) is not int or row_count < 0:
        raise ModelRiskHistoryError("dataset_bundle_row_count must be non-negative")
    if bundle_status == "VERSIONED_PIT_SUBSET_BUNDLE":
        if not manifest_ref or not manifest_sha or row_count <= 0:
            raise ModelRiskHistoryError("versioned dataset bundle metadata is incomplete")

    records, artifacts = _artifact_records(document.get("artifacts"))
    events, runtime_hash = _runtime_events(document.get("runtime_events"))
    if runtime_hash != _digest(
        document.get("runtime_history_sha256"), "runtime_history_sha256"
    ):
        raise ModelRiskHistoryError("runtime history digest mismatch")
    red_team_ref = _text(document.get("red_team_report_ref"), "red_team_report_ref")
    review_refs = document.get("challenger_review_refs")
    if (
        not isinstance(review_refs, list)
        or not review_refs
        or any(not isinstance(item, str) or not item for item in review_refs)
        or len(set(review_refs)) != len(review_refs)
    ):
        raise ModelRiskHistoryError("challenger_review_refs must be unique non-empty text")
    normalized_reviews = sorted(review_refs)
    _validate_references(
        artifacts,
        dataset_ref=dataset_ref,
        dataset_sha256=dataset_sha256,
        red_team_report_ref=red_team_ref,
        challenger_review_refs=normalized_reviews,
    )

    snapshot = {
        "recorded_at": recorded_at,
        "source_result_verdict": _text(
            document.get("source_result_verdict"), "source_result_verdict"
        ),
        "source_snapshot_ref": _text(
            document.get("source_snapshot_ref"), "source_snapshot_ref"
        ),
        "source_snapshot_sha256": _digest(
            document.get("source_snapshot_sha256"), "source_snapshot_sha256"
        ),
        "dataset_ref": dataset_ref,
        "dataset_sha256": dataset_sha256,
        "source_dataset_ref": _text(
            document.get("source_dataset_ref"), "source_dataset_ref"
        ),
        "source_dataset_sha256": _digest(
            document.get("source_dataset_sha256"), "source_dataset_sha256"
        ),
        "dataset_bundle_status": bundle_status,
        "dataset_bundle_manifest_ref": manifest_ref,
        "dataset_bundle_manifest_sha256": manifest_sha,
        "dataset_bundle_row_count": row_count,
        "runtime_history_sha256": runtime_hash,
        "runtime_events": events,
        "artifacts": records,
        "red_team_report_ref": red_team_ref,
        "challenger_review_refs": normalized_reviews,
    }
    return {"revision_sha256": stable_hash(snapshot), "snapshot": snapshot}


def _render_history(revisions: list[dict[str, Any]]) -> dict[str, Any]:
    history_hash = revisions[-1]["entry_hash"]
    return {
        "schema_version": 1,
        "mode": _MODE,
        "status": (
            "SINGLE_REVISION_BASELINE"
            if len(revisions) == 1
            else "MULTI_REVISION_HISTORY"
        ),
        "revision_count": len(revisions),
        "history_sha256": history_hash,
        "revisions": revisions,
    }


def validate_model_risk_history(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the complete history and return canonical summary metadata."""
    history = _mapping(document, "model-risk revision history")
    if set(history) != _HISTORY_FIELDS:
        raise ModelRiskHistoryError("model-risk history has an invalid shape")
    if history.get("schema_version") != 1 or history.get("mode") != _MODE:
        raise ModelRiskHistoryError("model-risk history metadata is unsupported")
    revisions = history.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        raise ModelRiskHistoryError("model-risk history must contain a revision")
    if history.get("revision_count") != len(revisions):
        raise ModelRiskHistoryError("model-risk revision count mismatch")

    previous_hash = ""
    seen_revisions: set[str] = set()
    artifact_versions: dict[str, str] = {}
    prior_runtime_events: list[dict[str, Any]] | None = None
    for expected_sequence, candidate in enumerate(revisions, start=1):
        revision = _mapping(candidate, f"revisions[{expected_sequence - 1}]")
        if set(revision) != _REVISION_FIELDS:
            raise ModelRiskHistoryError("model-risk revision has an invalid shape")
        if (
            revision.get("sequence") != expected_sequence
            or revision.get("previous_hash") != previous_hash
        ):
            raise ModelRiskHistoryError("model-risk revision chain is discontinuous")
        canonical = build_model_risk_revision(
            _mapping(revision.get("snapshot"), "revision snapshot")
        )
        if canonical["snapshot"] != revision.get("snapshot"):
            raise ModelRiskHistoryError("revision snapshot is not canonical")
        revision_sha = _digest(
            revision.get("revision_sha256"), "revision_sha256"
        )
        if revision_sha != canonical["revision_sha256"]:
            raise ModelRiskHistoryError("revision digest mismatch")
        if revision_sha in seen_revisions:
            raise ModelRiskHistoryError("duplicate model-risk revision")
        seen_revisions.add(revision_sha)
        expected_entry_hash = stable_hash(
            {
                "sequence": expected_sequence,
                "previous_hash": previous_hash,
                "revision_sha256": revision_sha,
            }
        )
        if revision.get("entry_hash") != expected_entry_hash:
            raise ModelRiskHistoryError("revision entry hash mismatch")
        for record in canonical["snapshot"]["artifacts"]:
            artifact_id = record["artifact_id"]
            content_hash = record["content_hash"]
            existing = artifact_versions.get(artifact_id)
            if existing is not None and existing != content_hash:
                raise ModelRiskHistoryError(
                    f"artifact version reused with different content: {artifact_id}"
                )
            artifact_versions[artifact_id] = content_hash
        current_runtime_events = canonical["snapshot"]["runtime_events"]
        if prior_runtime_events is not None:
            _validate_runtime_progression(
                prior_runtime_events,
                current_runtime_events,
            )
        prior_runtime_events = current_runtime_events
        previous_hash = expected_entry_hash

    expected_status = (
        "SINGLE_REVISION_BASELINE"
        if len(revisions) == 1
        else "MULTI_REVISION_HISTORY"
    )
    if history.get("status") != expected_status:
        raise ModelRiskHistoryError("model-risk history status mismatch")
    if history.get("history_sha256") != previous_hash:
        raise ModelRiskHistoryError("model-risk history digest mismatch")
    return {
        "status": expected_status,
        "revision_count": len(revisions),
        "history_sha256": previous_hash,
        "current_revision_sha256": revisions[-1]["revision_sha256"],
    }


def append_model_risk_revision(
    history: Mapping[str, Any] | None,
    evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Append a real new revision, or return the current history unchanged."""
    current = build_model_risk_revision(evidence)
    if history is None:
        revisions: list[dict[str, Any]] = []
        previous_hash = ""
    else:
        summary = validate_model_risk_history(history)
        revisions = copy.deepcopy(list(history["revisions"]))
        previous_hash = summary["history_sha256"]
        if summary["current_revision_sha256"] == current["revision_sha256"]:
            return copy.deepcopy(dict(history)), False
        if any(
            item["revision_sha256"] == current["revision_sha256"]
            for item in revisions
        ):
            raise ModelRiskHistoryError("model-risk revision rollback is forbidden")

        _validate_runtime_progression(
            revisions[-1]["snapshot"]["runtime_events"],
            current["snapshot"]["runtime_events"],
        )

        prior_artifacts = {
            record["artifact_id"]: record["content_hash"]
            for record in revisions[-1]["snapshot"]["artifacts"]
        }
        current_artifacts = {
            record["artifact_id"]: record["content_hash"]
            for record in current["snapshot"]["artifacts"]
        }
        missing = sorted(set(prior_artifacts) - set(current_artifacts))
        if missing:
            raise ModelRiskHistoryError(
                "model-risk history cannot discard artifacts: " + ", ".join(missing)
            )
        reused = sorted(
            artifact_id
            for artifact_id, content_hash in prior_artifacts.items()
            if current_artifacts[artifact_id] != content_hash
        )
        if reused:
            raise ModelRiskHistoryError(
                "artifact version reused with different content: " + ", ".join(reused)
            )
        if set(current_artifacts) == set(prior_artifacts):
            raise ModelRiskHistoryError(
                "a new model-risk revision requires a new versioned artifact"
            )

    sequence = len(revisions) + 1
    entry_hash = stable_hash(
        {
            "sequence": sequence,
            "previous_hash": previous_hash,
            "revision_sha256": current["revision_sha256"],
        }
    )
    revisions.append(
        {
            "sequence": sequence,
            "previous_hash": previous_hash,
            "revision_sha256": current["revision_sha256"],
            "entry_hash": entry_hash,
            "snapshot": current["snapshot"],
        }
    )
    rendered = _render_history(revisions)
    validate_model_risk_history(rendered)
    return rendered, True


def history_matches_evidence(
    history: Mapping[str, Any], evidence: Mapping[str, Any]
) -> bool:
    """Fail closed when history is corrupt or its tail is not the current evidence."""
    try:
        summary = validate_model_risk_history(history)
        current = build_model_risk_revision(evidence)
    except (ModelRiskHistoryError, TypeError, ValueError):
        return False
    return summary["current_revision_sha256"] == current["revision_sha256"]


__all__ = [
    "ModelRiskHistoryError",
    "append_model_risk_revision",
    "build_model_risk_revision",
    "history_matches_evidence",
    "validate_model_risk_history",
]
