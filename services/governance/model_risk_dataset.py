"""Read-only verification for versioned model-risk PIT dataset bundles."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


class DatasetBundleError(ValueError):
    """A retained evaluation dataset bundle cannot be trusted."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(path: str, *, root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        if ".." in candidate.parts:
            raise DatasetBundleError("dataset bundle path must stay within the repository")
        resolved = (root / candidate).resolve()
    if root.resolve() not in resolved.parents and resolved != root.resolve():
        raise DatasetBundleError("dataset bundle path escapes the repository")
    return resolved


def verify_pit_bundle(
    bundle_path: str | Path,
    manifest_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    source_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify a deterministic gzip JSONL bundle and return its manifest.

    The bundle is intentionally a PIT source subset, not a generated feature
    matrix: each retained row keeps `key`, `event_time`, `knowledge_time`, and
    the original canonical payload so the feature panel can be rebuilt.
    """
    root = Path(repo_root or Path.cwd()).resolve()
    bundle = _safe_relative(str(bundle_path), root=root)
    manifest_file = _safe_relative(str(manifest_path), root=root)
    if not bundle.is_file() or not manifest_file.is_file():
        raise DatasetBundleError("dataset bundle or manifest is missing")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetBundleError(f"dataset manifest is invalid: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise DatasetBundleError("dataset manifest schema_version must be 1")
    if manifest.get("bundle_ref") != bundle.relative_to(root).as_posix():
        raise DatasetBundleError("dataset manifest bundle_ref does not match its file")
    if manifest.get("bundle_sha256") != _sha256(bundle):
        raise DatasetBundleError("dataset bundle digest mismatch")
    if manifest.get("manifest_sha256") != _sha256(manifest_file):
        # The manifest cannot hash itself; the writer stores the digest of the
        # canonical document with this field omitted.
        without_self = dict(manifest)
        without_self.pop("manifest_sha256", None)
        canonical = json.dumps(
            without_self, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        if manifest.get("manifest_sha256") != hashlib.sha256(canonical).hexdigest():
            raise DatasetBundleError("dataset manifest self-digest mismatch")

    expected_keys = manifest.get("keys")
    if not isinstance(expected_keys, list) or not expected_keys or expected_keys != sorted(expected_keys):
        raise DatasetBundleError("dataset manifest keys must be a sorted non-empty list")
    expected_rows = manifest.get("row_count")
    if type(expected_rows) is not int or expected_rows <= 0:
        raise DatasetBundleError("dataset manifest row_count is invalid")

    rows = 0
    previous: tuple[str, int, int] | None = None
    observed_keys: set[str] = set()
    observed_min: int | None = None
    observed_max: int | None = None
    try:
        with gzip.open(bundle, "rt", encoding="utf-8", newline="") as handle:
            for line in handle:
                if not line.endswith("\n"):
                    raise DatasetBundleError("dataset bundle has an unterminated row")
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise DatasetBundleError("dataset bundle rows must be objects")
                key = row.get("key")
                event_time = row.get("event_time")
                knowledge_time = row.get("knowledge_time")
                payload_json = row.get("payload_json")
                if (
                    not isinstance(key, str)
                    or type(event_time) is not int
                    or type(knowledge_time) is not int
                    or not isinstance(payload_json, str)
                ):
                    raise DatasetBundleError("dataset bundle row shape is invalid")
                current = (key, event_time, knowledge_time)
                if previous is not None and current <= previous:
                    raise DatasetBundleError("dataset bundle rows are not strictly ordered")
                previous = current
                observed_keys.add(key)
                observed_min = event_time if observed_min is None else min(observed_min, event_time)
                observed_max = event_time if observed_max is None else max(observed_max, event_time)
                rows += 1
    except (OSError, EOFError, json.JSONDecodeError) as exc:
        raise DatasetBundleError(f"dataset bundle cannot be read: {exc}") from exc
    if rows != expected_rows or sorted(observed_keys) != expected_keys:
        raise DatasetBundleError("dataset bundle row or key count mismatch")
    if manifest.get("min_event_time") != observed_min or manifest.get("max_event_time") != observed_max:
        raise DatasetBundleError("dataset bundle time range mismatch")
    if source_db_path is not None:
        source = _safe_relative(str(source_db_path), root=root)
        expected_source = manifest.get("source_db_sha256")
        if not source.is_file() or expected_source != _sha256(source):
            raise DatasetBundleError("source database digest mismatch")
    return manifest


__all__ = ["DatasetBundleError", "verify_pit_bundle"]
