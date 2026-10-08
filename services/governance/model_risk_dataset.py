"""Read-only verification for versioned model-risk PIT dataset bundles."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


class DatasetBundleError(ValueError):
    """A retained evaluation dataset bundle cannot be trusted."""


_MANIFEST_FIELDS = {
    "bundle_ref",
    "bundle_sha256",
    "keys",
    "manifest_sha256",
    "max_event_time",
    "min_event_time",
    "row_count",
    "schema_version",
    "source_db_ref",
    "source_db_sha256",
    "source_query",
}
_ROW_FIELDS = {"event_time", "key", "knowledge_time", "payload_json"}


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
    if (
        not isinstance(manifest, dict)
        or set(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema_version") != 1
    ):
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

    source_ref = manifest.get("source_db_ref")
    source_sha = manifest.get("source_db_sha256")
    source_query = manifest.get("source_query")
    if not isinstance(source_ref, str) or not source_ref:
        raise DatasetBundleError("dataset manifest source_db_ref is invalid")
    manifest_source = _safe_relative(source_ref, root=root)
    if source_ref != manifest_source.relative_to(root).as_posix():
        raise DatasetBundleError("dataset manifest source_db_ref must be canonical")
    if (
        not isinstance(source_sha, str)
        or len(source_sha) != 64
        or any(character not in "0123456789abcdef" for character in source_sha)
    ):
        raise DatasetBundleError("dataset manifest source_db_sha256 is invalid")
    if not isinstance(source_query, str) or not source_query.strip():
        raise DatasetBundleError("dataset manifest source_query is invalid")

    expected_keys = manifest.get("keys")
    if (
        not isinstance(expected_keys, list)
        or not expected_keys
        or any(not isinstance(key, str) or not key for key in expected_keys)
        or len(set(expected_keys)) != len(expected_keys)
        or expected_keys != sorted(expected_keys)
    ):
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
                if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
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
                try:
                    payload = json.loads(
                        payload_json,
                        parse_constant=lambda value: (_ for _ in ()).throw(
                            ValueError(f"non-finite JSON value: {value}")
                        ),
                    )
                except (ValueError, json.JSONDecodeError) as exc:
                    raise DatasetBundleError(
                        "dataset bundle payload_json is invalid"
                    ) from exc
                if not isinstance(payload, dict):
                    raise DatasetBundleError(
                        "dataset bundle payload_json must encode an object"
                    )
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
        if source_ref != source.relative_to(root).as_posix():
            raise DatasetBundleError("source database identity mismatch")
        if source.is_file():
            if source_sha != _sha256(source):
                raise DatasetBundleError("source database digest mismatch")
        else:
            raise DatasetBundleError("source database digest mismatch")
    return manifest


__all__ = ["DatasetBundleError", "verify_pit_bundle"]
