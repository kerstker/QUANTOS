"""Fail-closed evidence for deployable-container reproduction across CI hosts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from services.governance.reproducibility_evidence import (
    ContainerReproducibilityEvidenceError,
    DOCKERIGNORE_REF,
    DOCKERFILE_REF,
    REQUIREMENTS_REF,
    TARGET_PLATFORM,
    _parse_oci_archive,
    _recipe_bindings,
    _verify_file_binding,
    _verify_image_summary,
)


class CrossHostContainerEvidenceError(ValueError):
    """A build claim or cross-host aggregate is malformed or inconsistent."""


CLAIM_MODE = "SINGLE_COLD_OCI_BUILD_RECIPE_BOUND"
EVIDENCE_MODE = "CROSS_HOST_TWO_RUNNERS_BYTE_IDENTICAL"
EXPECTED_BUILDERS = ("ubuntu-22.04", "ubuntu-24.04")
LIMITATIONS = (
    "GITHUB_HOSTED_RUNNERS_NOT_INDEPENDENT_ORGANIZATIONS",
    "AUTHENTICATED_EXTERNAL_RECEIPT_NOT_YET_BOUND",
    "WORM_RETENTION_NOT_PROVEN",
)

_HEX = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ACTION_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_REF = re.compile(r"^moby/buildkit@sha256:[0-9a-f]{64}$")
_BUILDX_VERSION = re.compile(
    r"^github\.com/docker/buildx v[0-9]+(?:\.[0-9]+){2}"
    r"(?:-[A-Za-z0-9.]+)? [0-9a-f]{40}$"
)
_BUILDKIT_VERSION = re.compile(r"^v[0-9]+(?:\.[0-9]+){2}$")
_WORKFLOW_RUN = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/"
    r"actions/runs/[0-9]+(?:/attempts/[0-9]+)?$"
)
_CLAIM_FIELDS = {
    "schema_version",
    "verdict",
    "mode",
    "source_commit",
    "builder",
    "recipe",
    "build_tool",
    "archive",
    "image",
    "claim_sha256",
}
_BUILDER_FIELDS = {"runner_label", "workflow_job"}
_BUILD_TOOL_FIELDS = {
    "setup_buildx_action_sha",
    "buildx_version",
    "buildkit_version",
    "buildkit_image_ref",
}
_ARCHIVE_FIELDS = {"name", "sha256", "size"}
_RECIPE_FIELDS = {
    "source_date_epoch",
    "target_platform",
    "base_image_ref",
    "dockerfile",
    "requirements",
    "dockerignore",
}
_EVIDENCE_FIELDS = {
    "schema_version",
    "verdict",
    "mode",
    "observed_at",
    "source_commit",
    "workflow_ref",
    "workflow_run",
    "builders",
    "archive_byte_identical",
    "archive_sha256",
    "archive_size",
    "manifest_digest",
    "config_digest",
    "recipe",
    "build_tool",
    "image",
    "claim_sha256",
    "limitations",
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CrossHostContainerEvidenceError(f"{label} must be an object")
    return dict(value)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _recipe_document(root: Path) -> dict[str, Any]:
    try:
        recipe = _recipe_bindings(root)
    except (ContainerReproducibilityEvidenceError, OSError) as exc:
        raise CrossHostContainerEvidenceError(str(exc)) from exc
    return {
        "source_date_epoch": recipe["source_date_epoch"],
        "target_platform": TARGET_PLATFORM,
        "base_image_ref": recipe["base_image_ref"],
        "dockerfile": recipe["dockerfile"],
        "requirements": recipe["requirements"],
        "dockerignore": recipe["dockerignore"],
    }


def _validated_build_tool(value: Any) -> dict[str, str]:
    tool = _object(value, "build tool")
    if (
        set(tool) != _BUILD_TOOL_FIELDS
        or not isinstance(tool.get("setup_buildx_action_sha"), str)
        or _ACTION_SHA.fullmatch(tool["setup_buildx_action_sha"]) is None
        or not isinstance(tool.get("buildx_version"), str)
        or _BUILDX_VERSION.fullmatch(tool["buildx_version"]) is None
        or not isinstance(tool.get("buildkit_version"), str)
        or _BUILDKIT_VERSION.fullmatch(tool["buildkit_version"]) is None
        or not isinstance(tool.get("buildkit_image_ref"), str)
        or _DIGEST_REF.fullmatch(tool["buildkit_image_ref"]) is None
    ):
        raise CrossHostContainerEvidenceError("build tool identity is invalid")
    return tool


def _verify_recipe(value: Any, root: Path) -> dict[str, Any]:
    recipe = _object(value, "recipe")
    expected = _recipe_document(root)
    if set(recipe) != _RECIPE_FIELDS:
        raise CrossHostContainerEvidenceError("recipe shape is invalid")
    if (
        recipe.get("source_date_epoch") != expected["source_date_epoch"]
        or recipe.get("target_platform") != TARGET_PLATFORM
        or recipe.get("base_image_ref") != expected["base_image_ref"]
    ):
        raise CrossHostContainerEvidenceError("recipe identity is inconsistent")
    try:
        _verify_file_binding(
            recipe.get("dockerfile"), expected["dockerfile"], DOCKERFILE_REF
        )
        _verify_file_binding(
            recipe.get("requirements"), expected["requirements"], REQUIREMENTS_REF
        )
        _verify_file_binding(
            recipe.get("dockerignore"), expected["dockerignore"], DOCKERIGNORE_REF
        )
    except ContainerReproducibilityEvidenceError as exc:
        raise CrossHostContainerEvidenceError(str(exc)) from exc
    return recipe


def build_container_build_claim(
    repo_root: str | Path,
    *,
    archive: str | Path,
    source_commit: str,
    runner_label: str,
    workflow_job: str,
    build_tool: Mapping[str, Any],
) -> dict[str, Any]:
    """Inspect one complete OCI archive and bind it to runner and recipe identity."""
    root = Path(repo_root).resolve()
    if _COMMIT.fullmatch(source_commit) is None:
        raise CrossHostContainerEvidenceError("source commit is invalid")
    if runner_label not in EXPECTED_BUILDERS:
        raise CrossHostContainerEvidenceError("runner label is not approved")
    if (
        not isinstance(workflow_job, str)
        or not workflow_job
        or len(workflow_job) > 128
    ):
        raise CrossHostContainerEvidenceError("workflow job identity is invalid")
    archive_path = Path(archive).resolve()
    recipe = _recipe_document(root)
    try:
        image = _parse_oci_archive(
            archive_path, recipe["source_date_epoch"]
        )
    except (ContainerReproducibilityEvidenceError, OSError) as exc:
        raise CrossHostContainerEvidenceError(str(exc)) from exc
    claim = {
        "schema_version": 1,
        "verdict": "PASS",
        "mode": CLAIM_MODE,
        "source_commit": source_commit,
        "builder": {
            "runner_label": runner_label,
            "workflow_job": workflow_job,
        },
        "recipe": recipe,
        "build_tool": _validated_build_tool(build_tool),
        "archive": {
            "name": archive_path.name,
            "sha256": _sha256_file(archive_path),
            "size": archive_path.stat().st_size,
        },
        "image": image,
    }
    claim["claim_sha256"] = _canonical_sha256(claim)
    return claim


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_container_build_claim(
    document: Mapping[str, Any], repo_root: str | Path
) -> dict[str, Any]:
    """Verify a retained single-runner OCI build claim against the current recipe."""
    claim = _object(document, "container build claim")
    retained_claim_sha256 = claim.get("claim_sha256")
    claim_body = {key: value for key, value in claim.items() if key != "claim_sha256"}
    if (
        set(claim) != _CLAIM_FIELDS
        or claim.get("schema_version") != 1
        or claim.get("verdict") != "PASS"
        or claim.get("mode") != CLAIM_MODE
        or not isinstance(claim.get("source_commit"), str)
        or _COMMIT.fullmatch(claim["source_commit"]) is None
        or not isinstance(retained_claim_sha256, str)
        or _HEX.fullmatch(retained_claim_sha256) is None
        or retained_claim_sha256 != _canonical_sha256(claim_body)
    ):
        raise CrossHostContainerEvidenceError("container build claim is invalid")
    builder = _object(claim.get("builder"), "builder")
    if (
        set(builder) != _BUILDER_FIELDS
        or builder.get("runner_label") not in EXPECTED_BUILDERS
        or not isinstance(builder.get("workflow_job"), str)
        or not builder["workflow_job"]
        or len(builder["workflow_job"]) > 128
    ):
        raise CrossHostContainerEvidenceError("builder identity is invalid")
    root = Path(repo_root).resolve()
    recipe = _verify_recipe(claim.get("recipe"), root)
    tool = _validated_build_tool(claim.get("build_tool"))
    archive = _object(claim.get("archive"), "archive")
    name = archive.get("name")
    if (
        set(archive) != _ARCHIVE_FIELDS
        or not isinstance(name, str)
        or not name
        or Path(name).name != name
        or not isinstance(archive.get("sha256"), str)
        or _HEX.fullmatch(archive["sha256"]) is None
        or type(archive.get("size")) is not int
        or archive["size"] <= 0
    ):
        raise CrossHostContainerEvidenceError("archive identity is invalid")
    try:
        image = _verify_image_summary(
            claim.get("image"), recipe["source_date_epoch"]
        )
    except ContainerReproducibilityEvidenceError as exc:
        raise CrossHostContainerEvidenceError(str(exc)) from exc
    return {
        "archive_sha256": archive["sha256"],
        "archive_size": archive["size"],
        "builder": builder["runner_label"],
        "build_tool": tool,
        "image": image,
        "recipe": recipe,
        "source_commit": claim["source_commit"],
        "verdict": "PASS",
    }


def build_cross_host_container_evidence(
    repo_root: str | Path,
    *,
    claims: Sequence[Mapping[str, Any]],
    observed_at: int,
    workflow_ref: str,
    workflow_run: str,
) -> dict[str, Any]:
    """Require two distinct runner claims to describe the identical OCI archive."""
    if len(claims) != 2:
        raise CrossHostContainerEvidenceError("exactly two build claims are required")
    if type(observed_at) is not int or observed_at < 0:
        raise CrossHostContainerEvidenceError("observed_at is invalid")
    if (
        not isinstance(workflow_ref, str)
        or ".github/workflows/sigstore-evidence.yml@" not in workflow_ref
    ):
        raise CrossHostContainerEvidenceError("workflow ref is invalid")
    if (
        not isinstance(workflow_run, str)
        or _WORKFLOW_RUN.fullmatch(workflow_run) is None
    ):
        raise CrossHostContainerEvidenceError("workflow run is invalid")
    root = Path(repo_root).resolve()
    ordered = sorted(
        ((_object(claim, "claim"), verify_container_build_claim(claim, root)) for claim in claims),
        key=lambda item: item[1]["builder"],
    )
    builders = [summary["builder"] for _, summary in ordered]
    if builders != list(EXPECTED_BUILDERS):
        raise CrossHostContainerEvidenceError(
            "build claims must come from two distinct approved runners"
        )
    first = ordered[0][1]
    second = ordered[1][1]
    comparable = (
        "archive_sha256",
        "archive_size",
        "build_tool",
        "image",
        "recipe",
        "source_commit",
    )
    if any(first[field] != second[field] for field in comparable):
        raise CrossHostContainerEvidenceError(
            "cross-host OCI archives are not byte-identical"
        )
    image = first["image"]
    return {
        "schema_version": 1,
        "verdict": "PASS",
        "mode": EVIDENCE_MODE,
        "observed_at": observed_at,
        "source_commit": first["source_commit"],
        "workflow_ref": workflow_ref,
        "workflow_run": workflow_run,
        "builders": builders,
        "archive_byte_identical": True,
        "archive_sha256": first["archive_sha256"],
        "archive_size": first["archive_size"],
        "manifest_digest": image["manifest_digest"],
        "config_digest": image["config_digest"],
        "recipe": first["recipe"],
        "build_tool": first["build_tool"],
        "image": image,
        "claim_sha256": {
            summary["builder"]: claim["claim_sha256"]
            for claim, summary in ordered
        },
        "limitations": list(LIMITATIONS),
    }


def verify_cross_host_container_evidence(
    document: Mapping[str, Any], repo_root: str | Path
) -> dict[str, Any]:
    """Verify a cross-host aggregate without treating it as signed external proof."""
    evidence = _object(document, "cross-host container evidence")
    if (
        set(evidence) != _EVIDENCE_FIELDS
        or evidence.get("schema_version") != 1
        or evidence.get("verdict") != "PASS"
        or evidence.get("mode") != EVIDENCE_MODE
        or type(evidence.get("observed_at")) is not int
        or evidence["observed_at"] < 0
        or not isinstance(evidence.get("source_commit"), str)
        or _COMMIT.fullmatch(evidence["source_commit"]) is None
        or not isinstance(evidence.get("workflow_ref"), str)
        or ".github/workflows/sigstore-evidence.yml@"
        not in evidence["workflow_ref"]
        or not isinstance(evidence.get("workflow_run"), str)
        or _WORKFLOW_RUN.fullmatch(evidence["workflow_run"]) is None
        or evidence.get("builders") != list(EXPECTED_BUILDERS)
        or evidence.get("archive_byte_identical") is not True
        or not isinstance(evidence.get("archive_sha256"), str)
        or _HEX.fullmatch(evidence["archive_sha256"]) is None
        or type(evidence.get("archive_size")) is not int
        or evidence["archive_size"] <= 0
        or evidence.get("limitations") != list(LIMITATIONS)
    ):
        raise CrossHostContainerEvidenceError(
            "cross-host container evidence is invalid"
        )
    root = Path(repo_root).resolve()
    recipe = _verify_recipe(evidence.get("recipe"), root)
    _validated_build_tool(evidence.get("build_tool"))
    try:
        image = _verify_image_summary(
            evidence.get("image"), recipe["source_date_epoch"]
        )
    except ContainerReproducibilityEvidenceError as exc:
        raise CrossHostContainerEvidenceError(str(exc)) from exc
    claims = _object(evidence.get("claim_sha256"), "claim hashes")
    if (
        list(claims) != list(EXPECTED_BUILDERS)
        or any(
            not isinstance(claims[builder], str)
            or _HEX.fullmatch(claims[builder]) is None
            for builder in EXPECTED_BUILDERS
        )
        or evidence.get("manifest_digest") != image["manifest_digest"]
        or evidence.get("config_digest") != image["config_digest"]
    ):
        raise CrossHostContainerEvidenceError(
            "cross-host container evidence bindings are invalid"
        )
    return {
        "archive_sha256": evidence["archive_sha256"],
        "builders": list(EXPECTED_BUILDERS),
        "manifest_digest": image["manifest_digest"],
        "observed_at": evidence["observed_at"],
        "source_commit": evidence["source_commit"],
        "verdict": "PASS",
    }


__all__ = [
    "CLAIM_MODE",
    "EVIDENCE_MODE",
    "CrossHostContainerEvidenceError",
    "build_container_build_claim",
    "build_cross_host_container_evidence",
    "verify_container_build_claim",
    "verify_cross_host_container_evidence",
]
