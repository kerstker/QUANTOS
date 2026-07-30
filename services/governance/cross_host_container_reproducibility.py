"""Fail-closed evidence for deployable-container reproduction across CI hosts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

from cryptography.hazmat.primitives.serialization import Encoding
from sigstore.errors import VerificationError
from sigstore.models import Bundle, InvalidBundle, TrustedRoot
from sigstore.verify import Verifier
from sigstore.verify.policy import Identity

from services.governance.artifact_admission import (
    load_json_without_duplicate_keys,
)
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
CERTIFICATE_IDENTITY = (
    "https://github.com/kerstker/QUANTOS/.github/workflows/"
    "sigstore-evidence.yml@refs/heads/codex/top-tier-wave0"
)
CERTIFICATE_ISSUER = "https://token.actions.githubusercontent.com"
SIGNED_RECEIPT_STATUS = (
    "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_CROSS_HOST_CONTAINER"
)
SIGNED_LIMITATIONS = {
    "external_receipt_authority": "NOT_PROVIDED",
    "independent_admission_boundary": "NOT_PROVIDED",
    "worm_retention": "GITHUB_ARTIFACT_RETENTION_ONLY",
}
NO_AUTHORITY = {
    "credential_access": False,
    "deployment": False,
    "execution": False,
    "live_trading": False,
    "order_submission": False,
}

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
_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_CONTAINER_INPUT_TREES = ("quantos", "services")
_CONTAINER_INPUT_FILES = (
    ".dockerignore",
    "deploy/console.Dockerfile",
    "requirements.txt",
)
_CLAIM_FIELDS = {
    "schema_version",
    "verdict",
    "mode",
    "source_commit",
    "builder",
    "container_input_sha256",
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
    "container_input_sha256",
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


def _source_file_record(path: Path, root: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise CrossHostContainerEvidenceError(
            f"symlink container input is not accepted: {path.relative_to(root)}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise CrossHostContainerEvidenceError(
                    f"container input is not a regular file: {path.relative_to(root)}"
                )
            size = 0
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
            after = path.stat()
    except OSError as exc:
        raise CrossHostContainerEvidenceError(
            f"container input is unavailable: {path.relative_to(root)}"
        ) from exc
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or size != before.st_size
    ):
        raise CrossHostContainerEvidenceError(
            f"container input changed while reading: {path.relative_to(root)}"
        )
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": digest.hexdigest(),
        "size": size,
    }


def _container_input_sha256(root: Path) -> str:
    """Hash every file that can affect the deployable console image."""
    paths = [root / relative for relative in _CONTAINER_INPUT_FILES]
    for tree_name in _CONTAINER_INPUT_TREES:
        tree = root / tree_name
        if tree.is_symlink() or not tree.is_dir():
            raise CrossHostContainerEvidenceError(
                f"container input tree is unavailable: {tree_name}"
            )
        for directory, directory_names, file_names in os.walk(
            tree, followlinks=False
        ):
            base = Path(directory)
            for name in tuple(directory_names):
                candidate = base / name
                if candidate.is_symlink():
                    raise CrossHostContainerEvidenceError(
                        "symlink container input is not accepted: "
                        f"{candidate.relative_to(root)}"
                    )
            paths.extend(base / name for name in file_names)
    records = [
        _source_file_record(path, root)
        for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix())
    ]
    relative_paths = [record["path"] for record in records]
    if len(relative_paths) != len(set(relative_paths)):
        raise CrossHostContainerEvidenceError(
            "container input paths are not unique"
        )
    return _canonical_sha256(
        {
            "schema_version": 1,
            "mode": "DEPLOYABLE_CONSOLE_COMPLETE_INPUT_SET",
            "files": records,
        }
    )


def _read_regular(path: Path) -> bytes:
    if path.is_symlink():
        raise CrossHostContainerEvidenceError(
            f"symlink evidence is not accepted: {path.name}"
        )
    try:
        with path.open("rb") as handle:
            before = path.stat()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size > _MAX_EVIDENCE_BYTES
            ):
                raise CrossHostContainerEvidenceError(
                    f"evidence file is invalid: {path.name}"
                )
            content = handle.read(_MAX_EVIDENCE_BYTES + 1)
            after = path.stat()
    except OSError as exc:
        raise CrossHostContainerEvidenceError(
            f"evidence file is unavailable: {path.name}"
        ) from exc
    if (
        len(content) > _MAX_EVIDENCE_BYTES
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != before.st_size
    ):
        raise CrossHostContainerEvidenceError(
            f"evidence file changed while reading: {path.name}"
        )
    return content


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        document = load_json_without_duplicate_keys(content)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise CrossHostContainerEvidenceError(
            f"{label} is not valid JSON"
        ) from exc
    return _object(document, label)


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
        "schema_version": 2,
        "verdict": "PASS",
        "mode": CLAIM_MODE,
        "source_commit": source_commit,
        "builder": {
            "runner_label": runner_label,
            "workflow_job": workflow_job,
        },
        "container_input_sha256": _container_input_sha256(root),
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
        or claim.get("schema_version") != 2
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
    container_input_sha256 = claim.get("container_input_sha256")
    if (
        not isinstance(container_input_sha256, str)
        or _HEX.fullmatch(container_input_sha256) is None
        or container_input_sha256 != _container_input_sha256(root)
    ):
        raise CrossHostContainerEvidenceError(
            "container input content root is inconsistent"
        )
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
        "container_input_sha256": container_input_sha256,
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
        "container_input_sha256",
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
        "schema_version": 2,
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
        "container_input_sha256": first["container_input_sha256"],
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
        or evidence.get("schema_version") != 2
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
        or not isinstance(evidence.get("container_input_sha256"), str)
        or _HEX.fullmatch(evidence["container_input_sha256"]) is None
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
    if evidence["container_input_sha256"] != _container_input_sha256(root):
        raise CrossHostContainerEvidenceError(
            "container input content root is inconsistent"
        )
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
        "container_input_sha256": evidence["container_input_sha256"],
        "manifest_digest": image["manifest_digest"],
        "observed_at": evidence["observed_at"],
        "source_commit": evidence["source_commit"],
        "verdict": "PASS",
    }


def verify_signed_cross_host_container_evidence(
    evidence_directory: str | Path,
    *,
    repo_root: str | Path,
    trusted_root_path: str | Path,
) -> dict[str, Any]:
    """Cryptographically verify the retained OIDC/Sigstore container proof."""
    root = Path(evidence_directory)
    if root.is_symlink() or not root.is_dir():
        raise CrossHostContainerEvidenceError(
            "signed cross-host evidence directory is unavailable"
        )
    expected_names = {
        "cross-host-container-receipt.json",
        "quantos-console-cross-host.json",
        "quantos-console-cross-host.sigstore.json",
    }
    try:
        observed_names = {
            item.name for item in root.iterdir() if item.is_file()
        }
    except OSError as exc:
        raise CrossHostContainerEvidenceError(
            "signed cross-host evidence directory cannot be read"
        ) from exc
    if observed_names != expected_names:
        raise CrossHostContainerEvidenceError(
            "signed cross-host evidence file set is invalid"
        )
    evidence_bytes = _read_regular(
        root / "quantos-console-cross-host.json"
    )
    bundle_bytes = _read_regular(
        root / "quantos-console-cross-host.sigstore.json"
    )
    receipt_bytes = _read_regular(
        root / "cross-host-container-receipt.json"
    )
    evidence = _json_object(evidence_bytes, "cross-host evidence")
    bundle_document = _json_object(bundle_bytes, "Sigstore bundle")
    receipt = _json_object(receipt_bytes, "cross-host receipt")
    summary = verify_cross_host_container_evidence(evidence, repo_root)
    evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
    bundle_sha256 = hashlib.sha256(bundle_bytes).hexdigest()

    if (
        set(receipt)
        != {
            "schema_version",
            "status",
            "source_commit",
            "workflow_run",
            "certificate_identity",
            "certificate_oidc_issuer",
            "sigstore_version",
            "rekor_version",
            "mode",
            "builders",
            "archive_sha256",
            "container_input_sha256",
            "manifest_digest",
            "evidence_sha256",
            "bundle_sha256",
            "limitations",
            "authority",
        }
        or receipt.get("schema_version") != 1
        or receipt.get("status") != SIGNED_RECEIPT_STATUS
        or receipt.get("source_commit") != summary["source_commit"]
        or receipt.get("workflow_run") != evidence.get("workflow_run")
        or receipt.get("certificate_identity") != CERTIFICATE_IDENTITY
        or receipt.get("certificate_oidc_issuer") != CERTIFICATE_ISSUER
        or receipt.get("sigstore_version") != "4.4.0"
        or receipt.get("rekor_version") != 1
        or receipt.get("mode") != EVIDENCE_MODE
        or receipt.get("builders") != list(EXPECTED_BUILDERS)
        or receipt.get("archive_sha256") != summary["archive_sha256"]
        or receipt.get("container_input_sha256")
        != summary["container_input_sha256"]
        or receipt.get("manifest_digest") != summary["manifest_digest"]
        or receipt.get("evidence_sha256") != evidence_sha256
        or receipt.get("bundle_sha256") != bundle_sha256
        or receipt.get("limitations") != SIGNED_LIMITATIONS
        or receipt.get("authority") != NO_AUTHORITY
    ):
        raise CrossHostContainerEvidenceError(
            "signed cross-host receipt is inconsistent"
        )
    try:
        bundle = Bundle.from_json(bundle_bytes)
        trusted_root = TrustedRoot.from_file(str(trusted_root_path))
        Verifier(trusted_root=trusted_root).verify_artifact(
            evidence_bytes,
            bundle,
            Identity(
                identity=CERTIFICATE_IDENTITY,
                issuer=CERTIFICATE_ISSUER,
            ),
        )
    except (InvalidBundle, VerificationError, OSError, TypeError, ValueError) as exc:
        raise CrossHostContainerEvidenceError(
            "cross-host Sigstore signature verification failed"
        ) from exc
    except Exception as exc:
        raise CrossHostContainerEvidenceError(
            "cross-host Sigstore verifier failed closed"
        ) from exc
    if (
        bundle_document.get("mediaType")
        != "application/vnd.dev.sigstore.bundle.v0.3+json"
    ):
        raise CrossHostContainerEvidenceError(
            "cross-host Sigstore bundle media type is invalid"
        )
    verification_material = bundle_document.get("verificationMaterial")
    entries = (
        verification_material.get("tlogEntries")
        if isinstance(verification_material, dict)
        else None
    )
    if not isinstance(entries, list) or len(entries) != 1:
        raise CrossHostContainerEvidenceError(
            "cross-host transparency entry count is invalid"
        )
    integrated_time = entries[0].get("integratedTime")
    if (
        not isinstance(integrated_time, str)
        or re.fullmatch(r"[1-9][0-9]*", integrated_time) is None
    ):
        raise CrossHostContainerEvidenceError(
            "cross-host Rekor integrated time is invalid"
        )
    certificate_sha256 = hashlib.sha256(
        bundle.signing_certificate.public_bytes(Encoding.DER)
    ).hexdigest()
    return {
        "status": SIGNED_RECEIPT_STATUS,
        "source_commit": summary["source_commit"],
        "archive_sha256": summary["archive_sha256"],
        "container_input_sha256": summary["container_input_sha256"],
        "manifest_digest": summary["manifest_digest"],
        "builders": list(EXPECTED_BUILDERS),
        "workflow_run": receipt["workflow_run"],
        "evidence_sha256": evidence_sha256,
        "bundle_sha256": bundle_sha256,
        "certificate_sha256": certificate_sha256,
        "certificate_identity": CERTIFICATE_IDENTITY,
        "certificate_oidc_issuer": CERTIFICATE_ISSUER,
        "transparency_integrated_at_ms": int(integrated_time) * 1_000,
        "transparency_log_verified": True,
        "signed_certificate_timestamp_verified": True,
        "archive_byte_identical": True,
        "limitations": dict(SIGNED_LIMITATIONS),
    }


__all__ = [
    "CLAIM_MODE",
    "EVIDENCE_MODE",
    "CrossHostContainerEvidenceError",
    "build_container_build_claim",
    "build_cross_host_container_evidence",
    "verify_container_build_claim",
    "verify_cross_host_container_evidence",
    "verify_signed_cross_host_container_evidence",
]
