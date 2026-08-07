"""Strict canonical binding contract for readiness evidence."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
from collections.abc import Mapping
from typing import Any

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMAND_TIMEOUT_SECONDS = 10

# This is deliberately a finite, versioned list.  Widening it changes the
# identity domain and must be reviewed as a new boundary policy.
CANONICAL_GENERATED_ARTIFACT_ALLOWLIST_VERSION = "v1"
CANONICAL_GENERATED_ARTIFACT_ALLOWLIST = frozenset({
    "docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md",
    "docs/reports/QUANTOS_AUTONOMY_FINAL_AUDIT.md",
    "evidence/alpha_lifecycle_status.json",
    "evidence/autonomy_readiness.json",
    "evidence/chaos_recovery_status.json",
    "evidence/factor_garden_status.json",
    "evidence/l2_backup_latest.json",
    "evidence/mexc_integration_status.json",
    "evidence/paper_shadow_status.json",
    "evidence/reproducibility/container_build_latest.json",
    "evidence/sbom.cdx.json",
    "evidence/supply_chain_baseline.json",
    "evidence/top_tier_assessment.json",
    "evidence/trial_ledger_integrity.json",
    "evidence/validation_report.json",
    "evidence/vulnerability/container_trivy_latest.json",
    "evidence/vulnerability/python_audit_latest.json",
})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo,
        text=text,
        timeout=GIT_COMMAND_TIMEOUT_SECONDS,
    )


def _head(repo: Path | None = None) -> str:
    root = repo or _repo_root()
    return str(_git(root, "rev-parse", "HEAD")).strip()


def _source(value: object) -> str:
    if not isinstance(value, str) or not _SHA40.fullmatch(value):
        raise ValueError("canonical validation source_commit must be a full lowercase git SHA")
    return value


def _run(value: object) -> str:
    if not isinstance(value, str) or not _SHA64.fullmatch(value):
        raise ValueError("canonical validation run_id must be a 64-character lowercase hex digest")
    return value


def _timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("canonical validation generated_at must be a non-negative integer")
    return value


def _current(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("canonical validation current must be boolean")
    return value


def _allowlisted(path: str) -> bool:
    return path in CANONICAL_GENERATED_ARTIFACT_ALLOWLIST


def _assert_regular_artifact(repo: Path, path: str) -> None:
    try:
        mode = os.lstat(repo / path).st_mode
    except OSError as exc:
        raise ValueError(f"canonical artifact path is not a readable regular file: {path!r}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"canonical artifact path is not a regular file: {path!r}")


def _check_clean_boundary(repo: Path) -> None:
    for path in CANONICAL_GENERATED_ARTIFACT_ALLOWLIST:
        try:
            mode = os.lstat(repo / path).st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError(f"canonical artifact path cannot be inspected: {path!r}") from exc
        if not stat.S_ISREG(mode):
            raise ValueError(f"canonical artifact path is not a regular file: {path!r}")
    raw = _git(repo, "status", "--porcelain=v2", "--untracked-files=all", "-z", text=False)
    assert isinstance(raw, bytes)
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            line = record.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("git status contains an undecodable path") from exc
        kind = line[:1]
        if kind == "?":
            path = line[2:] if line.startswith("? ") else ""
            if not _allowlisted(path):
                raise ValueError(f"dirty path is outside canonical artifact allowlist: {path!r}")
            _assert_regular_artifact(repo, path)
            continue
        # Unmerged, rename/copy, and any future porcelain record are
        # intentionally rejected rather than guessed at. Ignored paths are not
        # reported by this status query and are outside this dirty boundary.
        if kind != "1":
            raise ValueError("git status contains unsupported or non-allowlisted change")
        fields = line.split(" ", 8)
        if len(fields) != 9:
            raise ValueError("git status ordinary record is malformed")
        xy, path = fields[1], fields[8]
        if len(xy) != 2 or any(flag not in ".AM" for flag in xy) or not _allowlisted(path):
            raise ValueError(f"dirty path is outside canonical artifact allowlist: {path!r}")
        _assert_regular_artifact(repo, path)


def assert_canonical_working_tree(repo_root: Path | str | None = None) -> None:
    """Accept only clean or canonical-generated-artifact-only working trees.

    The canonical artifact allowlist is the single dirty-boundary policy used
    by source-anchor resolution.  Callers that report a boolean dirty flag can
    treat ``ValueError`` as unsafe source dirtiness while preserving this
    resolver's fail-closed behavior.
    """
    repo = Path(repo_root) if repo_root is not None else _repo_root()
    _check_clean_boundary(repo)


def _artifact_commit(repo: Path, commit: str) -> bool:
    parents = str(_git(repo, "rev-list", "--parents", "-n", "1", commit)).split()
    if len(parents) != 2:
        return False
    raw = _git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--raw",
        "-z",
        "-r",
        "--find-copies-harder",
        "-C100%",
        commit,
        text=False,
    )
    assert isinstance(raw, bytes)
    records = [record for record in raw.split(b"\0") if record]
    if not records:
        return False
    entries: list[tuple[bytes, bytes]] = []
    if all(b"\t" in record for record in records):
        for record in records:
            metadata, path_bytes = record.split(b"\t", 1)
            entries.append((metadata, path_bytes))
    elif len(records) % 2 == 0:
        entries = list(zip(records[0::2], records[1::2]))
    else:
        return False
    for metadata, path_bytes in entries:
        try:
            fields = metadata.decode("utf-8").split()
            path = path_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("commit diff contains an undecodable path") from exc
        if len(fields) != 5:
            return False
        old_mode, new_mode, _old_sha, _new_sha, status = fields
        old_mode = old_mode.lstrip(":")
        if new_mode not in {"100644", "100755"}:
            return False
        if status not in {"A", "M"} or not _allowlisted(path):
            return False
        if status == "M" and old_mode not in {"100644", "100755"}:
            return False
        if status == "A" and old_mode != "000000":
            return False
    return True


def resolve_canonical_source_anchor(repo_root: Path | str | None = None) -> str:
    """Return HEAD's stable source anchor, rejecting unsafe dirty state."""
    repo = Path(repo_root) if repo_root is not None else _repo_root()
    _check_clean_boundary(repo)
    current = _head(repo)
    while _artifact_commit(repo, current):
        parents = str(_git(repo, "rev-list", "--parents", "-n", "1", current)).split()
        current = parents[1]
    return _source(current)


def validation_binding(*, generated_at: int, run_id: str | None = None, source_commit: str | None = None,
                       current: bool = True) -> dict[str, object]:
    if "QUANTOS_VALIDATION_SOURCE_COMMIT" in os.environ:
        env_source = _source(os.environ["QUANTOS_VALIDATION_SOURCE_COMMIT"])
        if source_commit is not None and source_commit != env_source:
            raise ValueError("explicit source_commit disagrees with canonical environment")
        source = env_source
    else:
        source = source_commit if source_commit is not None else resolve_canonical_source_anchor()
    source = _source(source)

    if "QUANTOS_VALIDATION_RUN_ID" in os.environ:
        env_run = _run(os.environ["QUANTOS_VALIDATION_RUN_ID"])
        if run_id is not None and run_id != env_run:
            raise ValueError("explicit run_id disagrees with canonical environment")
        run = env_run
    else:
        run = run_id
    if run is None:
        run = hashlib.sha256(("diagnostic:" + CANONICAL_GENERATED_ARTIFACT_ALLOWLIST_VERSION + ":" + source).encode("utf-8")).hexdigest()
    run = _run(run)
    generated_at = _timestamp(generated_at)
    if generated_at == 0:
        raw = os.environ.get("QUANTOS_VALIDATION_STARTED_AT_MS")
        generated_at = int(raw) if raw is not None else 0
        generated_at = _timestamp(generated_at)
    return {"run_id": run, "source_commit": source, "generated_at": _timestamp(generated_at), "current": _current(current)}


def validation_binding_from_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("validation"), Mapping):
        raise ValueError("evidence is missing explicit canonical validation binding")
    value = payload["validation"]
    required = {"run_id", "source_commit", "generated_at", "current"}
    if set(value) != required:
        raise ValueError("canonical validation binding has an invalid shape")
    return {"run_id": _run(value["run_id"]), "source_commit": _source(value["source_commit"]), "generated_at": _timestamp(value["generated_at"]), "current": _current(value["current"])}


def validation_report_legacy_binding(payload: Mapping[str, Any]) -> dict[str, object]:
    """Read-only migration helper for pre-Task-5 reports; not a canonical accessor."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("commit"), str) or not isinstance(payload.get("checks_manifest_sha256"), str):
        raise ValueError("legacy validation report shape is invalid")
    return {"source_commit": _source(payload["commit"]), "run_id": _run(payload["checks_manifest_sha256"])}


def assert_current_binding(payload: Mapping[str, Any], *, head: str, repo_root: Path | str | None = None) -> None:
    binding = validation_binding_from_payload(payload)
    _source(head)
    anchor = resolve_canonical_source_anchor(repo_root)
    if binding["source_commit"] != anchor:
        raise ValueError(f"validation source commit {binding['source_commit']} != canonical source anchor {anchor}")
    if binding["current"] is not True:
        raise ValueError("validation binding is not current")
    if "QUANTOS_VALIDATION_SOURCE_COMMIT" in os.environ:
        expected_source = _source(os.environ["QUANTOS_VALIDATION_SOURCE_COMMIT"])
        if binding["source_commit"] != expected_source:
            raise ValueError(f"validation source commit {binding['source_commit']} != canonical environment")
    if "QUANTOS_VALIDATION_RUN_ID" in os.environ:
        expected_run = _run(os.environ["QUANTOS_VALIDATION_RUN_ID"])
        if binding["run_id"] != expected_run:
            raise ValueError(f"validation run {binding['run_id']} != canonical environment")
