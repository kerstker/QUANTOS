#!/usr/bin/env python
"""Create a versioned model-risk refresh without rewriting immutable history.

The regular baseline generator describes one registry snapshot.  When the
governed implementation changes, reusing an old artifact identity would make
the longitudinal history unverifiable.  This driver therefore keeps the old
artifacts and runtime events, adds a new versioned artifact set, and appends a
new runtime segment to the existing hash chain.  It is offline-only and grants
no promotion or execution authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from quantos.contracts.model_risk import (
    ChallengerReview,
    ExternalProviderBinding,
    ModelCard,
    ModelRiskArtifact,
    OfflineEvaluation,
    PromptTemplateCard,
    RedTeamReport,
    RuntimeInvocation,
    ToolManifest,
)
from scripts.generate_model_risk_evidence import (
    build_model_risk_evidence,
    execute_agent_boundary_red_team,
    execute_runtime_boundary_red_team,
)
from services.governance.model_risk_history import (
    ModelRiskHistoryError,
    append_model_risk_revision,
    validate_model_risk_history,
)
from services.governance.model_risk_registry import (
    MODEL_RISK_STORE_KIND,
    MODEL_RISK_STORE_SCHEMA_VERSION,
    GovernedModelRegistry,
    SqliteModelRiskStore,
)
from services.intelligence.runtime import GovernedModelRuntime, RESEARCH_EVALUATION_TEMPLATE


_ARTIFACT_TYPES: dict[str, type[ModelRiskArtifact]] = {
    cls.__name__: cls
    for cls in (
        ModelCard,
        PromptTemplateCard,
        ToolManifest,
        ExternalProviderBinding,
        OfflineEvaluation,
        RedTeamReport,
        ChallengerReview,
    )
}
_DEFAULT_EVIDENCE = REPO / "evidence/model_risk_baseline.json"
_DEFAULT_HISTORY = REPO / "evidence/model_risk_history.json"
_DEFAULT_OUTPUT = REPO / "evidence/model_risk_baseline.json"
_DEFAULT_SNAPSHOT = REPO / "data/cache/ml_stack_snapshot.json"
_DEFAULT_DATASET = REPO / "data/cache/fx.db"
_DEFAULT_BUNDLE = REPO / "evidence/datasets/fx_pit_tradable_v1.jsonl.gz"
_DEFAULT_MANIFEST = REPO / "evidence/datasets/fx_pit_tradable_v1.manifest.json"
_VERSION_PATTERN = re.compile(r"^v([1-9][0-9]*)$")
_REPORT_VERSION_PATTERN = re.compile(r"-v([1-9][0-9]*)$")


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelRiskHistoryError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelRiskHistoryError(f"{label} must be an object")
    return value


def _write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _replace_version(ref: str, version: str) -> str:
    prefix, separator, _old = ref.rpartition("@")
    return f"{prefix}@{version}" if separator else ref


def _replace_report_version(report_id: str, version: str) -> str:
    stem, separator, _old = report_id.rpartition("-v")
    return f"{stem}-{version}" if separator else f"{report_id}-{version}"


def _replace_review_version(review_id: str, version: str) -> str:
    return _replace_report_version(review_id, version)


def _next_model_version(records: Sequence[Mapping[str, Any]]) -> str:
    versions: list[int] = []
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            continue
        match = _VERSION_PATTERN.fullmatch(str(payload.get("version", "")))
        if match is not None:
            versions.append(int(match.group(1)))
    return f"v{max(versions, default=0) + 1}"


def _next_report_version(records: Sequence[Mapping[str, Any]]) -> str:
    versions: list[int] = []
    for record in records:
        if record.get("artifact_type") != "RedTeamReport":
            continue
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            continue
        match = _REPORT_VERSION_PATTERN.search(str(payload.get("report_id", "")))
        if match is not None:
            versions.append(int(match.group(1)))
    return f"v{max(versions, default=0) + 1}"


def _record_to_artifact(record: Mapping[str, Any]) -> ModelRiskArtifact:
    artifact_type = record.get("artifact_type")
    payload = record.get("payload")
    model_type = _ARTIFACT_TYPES.get(str(artifact_type))
    if model_type is None or not isinstance(payload, Mapping):
        raise ModelRiskHistoryError("invalid model-risk artifact record")
    artifact = model_type.model_validate(dict(payload))
    if artifact.artifact_id != record.get("artifact_id"):
        raise ModelRiskHistoryError("artifact identity mismatch while refreshing")
    if artifact.content_hash != record.get("content_hash"):
        raise ModelRiskHistoryError("artifact digest mismatch while refreshing")
    return artifact


def _render_record(artifact: ModelRiskArtifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_type": type(artifact).__name__,
        "content_hash": artifact.content_hash,
        "payload": artifact.model_dump(mode="json"),
    }


def _versioned_artifact(record: Mapping[str, Any], *, model_version: str, review_version: str) -> ModelRiskArtifact | None:
    """Clone a non-red-team artifact under a new immutable identity."""
    artifact_type = str(record.get("artifact_type"))
    payload = dict(record.get("payload") or {})
    if artifact_type in {"ToolManifest", "ExternalProviderBinding"}:
        return None  # The current tool boundary is unchanged and remains v1.
    if artifact_type == "RedTeamReport":
        return None  # Reports are regenerated below against the new runtime.
    if artifact_type == "ModelCard":
        payload["version"] = model_version
        if payload.get("model_id") == "research-agent-loop":
            payload["prompt_template_ref"] = f"research-evaluation@{model_version}"
    elif artifact_type == "PromptTemplateCard":
        payload["version"] = model_version
        payload["model_ref"] = f"research-agent-loop@{model_version}"
    elif artifact_type == "OfflineEvaluation":
        payload["version"] = model_version
        payload["model_ref"] = _replace_version(str(payload["model_ref"]), model_version)
    elif artifact_type == "ChallengerReview":
        payload["review_id"] = _replace_review_version(str(payload["review_id"]), review_version)
        for key in ("incumbent_model_ref", "challenger_model_ref", "incumbent_evaluation_ref", "challenger_evaluation_ref"):
            payload[key] = _replace_version(str(payload[key]), model_version)
    else:
        raise ModelRiskHistoryError(f"unsupported refresh artifact type: {artifact_type}")
    model_type = _ARTIFACT_TYPES[artifact_type]
    return model_type.model_validate(payload)


def _runtime_events(registry: GovernedModelRegistry) -> list[dict[str, Any]]:
    return [
        {
            "sequence": row["sequence"],
            "invocation_id": row["invocation_id"],
            "recorded_at": row["recorded_at"],
            "model_ref": row["model_ref"],
            "prompt_ref": row["prompt_ref"],
            "tool_manifest_ref": row["tool_manifest_ref"],
            "action": row["action"],
            "previous_hash": row["previous_hash"],
            "event_hash": row["event_hash"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in registry.runtime_rows()
    ]


def refresh_model_risk_evidence(
    repo_root: str | Path = REPO,
    *,
    prior_evidence_path: str | Path = _DEFAULT_EVIDENCE,
    prior_history_path: str | Path = _DEFAULT_HISTORY,
    snapshot_path: str | Path = _DEFAULT_SNAPSHOT,
    dataset_path: str | Path = _DEFAULT_DATASET,
    dataset_bundle_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    model_version: str | None = None,
    report_version: str | None = None,
    recorded_at: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(repo_root).resolve()
    prior = _read_object(Path(prior_evidence_path), "prior model-risk evidence")
    history = _read_object(Path(prior_history_path), "prior model-risk history")
    validate_model_risk_history(history)
    prior_records = prior.get("artifacts")
    prior_events = prior.get("runtime_events")
    if not isinstance(prior_records, list) or not isinstance(prior_events, list) or not prior_events:
        raise ModelRiskHistoryError("prior model-risk evidence lacks artifacts or runtime events")
    typed_prior_records = [
        record for record in prior_records if isinstance(record, Mapping)
    ]
    model_version = model_version or _next_model_version(typed_prior_records)
    report_version = report_version or _next_report_version(typed_prior_records)
    if _VERSION_PATTERN.fullmatch(model_version) is None:
        raise ModelRiskHistoryError("model_version must use canonical vN form")
    if _VERSION_PATTERN.fullmatch(report_version) is None:
        raise ModelRiskHistoryError("report_version must use canonical vN form")
    prior_ids = {record.get("artifact_id") for record in prior_records if isinstance(record, Mapping)}
    current_recorded_at = int(recorded_at if recorded_at is not None else int(prior.get("recorded_at", 0)) + 1)
    if current_recorded_at <= int(prior.get("recorded_at", -1)):
        raise ModelRiskHistoryError("refresh recorded_at must advance the prior evidence")

    candidate = build_model_risk_evidence(
        root,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        dataset_bundle_path=dataset_bundle_path,
        dataset_manifest_path=dataset_manifest_path,
        baseline_recorded_at=int(prior.get("recorded_at", current_recorded_at - 1)),
        recorded_at=current_recorded_at,
        include_longitudinal_v2=True,
    )
    candidate_records = candidate.get("artifacts")
    if not isinstance(candidate_records, list):
        raise ModelRiskHistoryError("fresh model-risk candidate lacks artifacts")
    new_artifacts: list[ModelRiskArtifact] = []
    for record in candidate_records:
        artifact = _versioned_artifact(record, model_version=model_version, review_version=model_version)
        if artifact is not None:
            if artifact.artifact_id in prior_ids:
                raise ModelRiskHistoryError(f"refresh identity already exists: {artifact.artifact_id}")
            new_artifacts.append(artifact)
    tool = next(
        (_record_to_artifact(record) for record in prior_records if record.get("artifact_type") == "ToolManifest"),
        None,
    )
    if not isinstance(tool, ToolManifest):
        raise ModelRiskHistoryError("prior evidence lacks the governed tool manifest")
    base_for_red_team = [
        _record_to_artifact(record)
        for record in prior_records
        if record.get("artifact_type") != "RedTeamReport"
    ]
    base_for_red_team.extend(new_artifacts)
    new_agent_report = execute_agent_boundary_red_team(
        recorded_at=current_recorded_at,
        model_ref=f"research-agent-loop@{model_version}",
        tool_manifest_ref=tool.manifest_ref,
        report_id=f"research-agent-boundary-{report_version}",
    )
    new_runtime_report = execute_runtime_boundary_red_team(
        [*base_for_red_team, new_agent_report],
        recorded_at=current_recorded_at,
        model_ref=f"research-agent-loop@{model_version}",
        prompt_ref=f"research-evaluation@{model_version}",
        tool_manifest_ref=tool.manifest_ref,
        report_id=f"research-agent-runtime-boundary-{report_version}",
        agent_report_id=f"research-agent-boundary-{report_version}",
    )
    new_artifacts.extend((new_agent_report, new_runtime_report))

    old_artifacts = [_record_to_artifact(record) for record in prior_records]
    all_artifacts = [*old_artifacts, *new_artifacts]
    if len({artifact.artifact_id for artifact in all_artifacts}) != len(all_artifacts):
        raise ModelRiskHistoryError("refresh produced duplicate artifact identities")

    with tempfile.TemporaryDirectory(prefix="quantos-model-risk-refresh-") as directory:
        store = SqliteModelRiskStore(str(Path(directory) / "registry.db"), create=True)
        try:
            registry = GovernedModelRegistry(store)
            for artifact in sorted(all_artifacts, key=lambda item: item.artifact_id):
                registry.record_artifact(artifact)
            for event in prior_events:
                payload = event.get("payload") if isinstance(event, Mapping) else None
                if not isinstance(payload, Mapping):
                    raise ModelRiskHistoryError("prior runtime event payload is invalid")
                registry.record_runtime_invocation(RuntimeInvocation.model_validate(dict(payload)))
            runtime = GovernedModelRuntime(
                registry,
                model_ref=f"research-agent-loop@{model_version}",
                prompt_ref=f"research-evaluation@{model_version}",
                tool_manifest_ref=tool.manifest_ref,
                prompt_template=RESEARCH_EVALUATION_TEMPLATE,
            )
            runtime.invoke(
                invocation_id=f"model-risk-baseline-runtime-{report_version}",
                action="EVALUATE",
                input_payload={
                    "artifact_count": len(all_artifacts),
                    "evidence_mode": "OFFLINE_MODEL_RISK_VERSIONED_REFRESH",
                    "prior_runtime_event_count": len(prior_events),
                },
                execute=lambda payload: {"action": "EVALUATE", "verdict": "RESEARCH_ONLY"},
                recorded_at=current_recorded_at,
            )
            runtime.invoke(
                invocation_id=f"model-risk-runtime-red-team-{report_version}",
                action="EVALUATE",
                input_payload={
                    "artifact_count": len(all_artifacts),
                    "evidence_mode": "OFFLINE_MODEL_RISK_VERSIONED_RED_TEAM",
                    "red_team_report_ref": new_runtime_report.artifact_id,
                },
                execute=lambda payload: {
                    "action": "EVALUATE",
                    "case_count": len(new_runtime_report.cases),
                    "verdict": "BOUNDARY_HELD",
                },
                recorded_at=current_recorded_at,
            )
            if not registry.verify_all() or not registry.verify_runtime_history():
                raise ModelRiskHistoryError("refreshed registry failed integrity verification")
            events = _runtime_events(registry)
            runtime_hash = registry.runtime_history_hash()
            artifact_history_hash = registry.artifact_history_hash()
        finally:
            store.close()

    records = [_render_record(artifact) for artifact in sorted(all_artifacts, key=lambda item: item.artifact_id)]
    counts = Counter(record["artifact_type"] for record in records)
    fresh = dict(candidate)
    fresh.update(
        {
            "recorded_at": current_recorded_at,
            "prompt_template_count": counts.get("PromptTemplateCard", 0),
            "runtime_invocation_count": len(events),
            "registry_store_kind": MODEL_RISK_STORE_KIND,
            "registry_store_schema_version": MODEL_RISK_STORE_SCHEMA_VERSION,
            "artifact_history_sha256": artifact_history_hash,
            "runtime_history_sha256": runtime_hash,
            "runtime_events": events,
            "artifact_count": len(records),
            "artifact_type_counts": dict(sorted(counts.items())),
            "artifacts": records,
            "red_team_report_ref": new_runtime_report.artifact_id,
            "red_team_verdict": new_runtime_report.verdict.value,
            "challenger_review_refs": sorted(
                record["artifact_id"]
                for record in records
                if record["artifact_type"] == "ChallengerReview"
            ),
            "challenger_verdicts": [
                record["payload"]["verdict"]
                for record in sorted(
                    (record for record in records if record["artifact_type"] == "ChallengerReview" and record["payload"]["review_id"].endswith(f"-{model_version}")),
                    key=lambda record: record["artifact_id"],
                )
            ],
        }
    )
    refreshed_history, _appended = append_model_risk_revision(history, fresh)
    return fresh, refreshed_history


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append a versioned model-risk evidence refresh")
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--prior-evidence", default=str(_DEFAULT_EVIDENCE))
    parser.add_argument("--prior-history", default=str(_DEFAULT_HISTORY))
    parser.add_argument("--snapshot", default=str(_DEFAULT_SNAPSHOT))
    parser.add_argument("--dataset", default=str(_DEFAULT_DATASET))
    parser.add_argument("--dataset-bundle", default=str(_DEFAULT_BUNDLE))
    parser.add_argument("--dataset-manifest", default=str(_DEFAULT_MANIFEST))
    parser.add_argument("--output", default=str(_DEFAULT_OUTPUT))
    parser.add_argument("--history-output", default=str(_DEFAULT_HISTORY))
    parser.add_argument("--model-version")
    parser.add_argument("--report-version")
    parser.add_argument("--recorded-at", type=int)
    args = parser.parse_args(argv)
    try:
        evidence, history = refresh_model_risk_evidence(
            args.repo_root,
            prior_evidence_path=args.prior_evidence,
            prior_history_path=args.prior_history,
            snapshot_path=args.snapshot,
            dataset_path=args.dataset,
            dataset_bundle_path=args.dataset_bundle,
            dataset_manifest_path=args.dataset_manifest,
            model_version=args.model_version,
            report_version=args.report_version,
            recorded_at=args.recorded_at,
        )
        _write(Path(args.output), evidence)
        _write(Path(args.history_output), history)
    except (OSError, ValueError, ModelRiskHistoryError, TypeError, json.JSONDecodeError) as exc:
        print(f"model-risk refresh failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"model-risk refresh: {evidence['artifact_count']} artifacts; "
        f"runtime_events={evidence['runtime_invocation_count']}; "
        f"history_revisions={history['revision_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
