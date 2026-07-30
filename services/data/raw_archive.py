"""Immutable, content-addressed source-response archive for market-data lineage."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter

from pydantic import HttpUrl, TypeAdapter, ValidationError


class RawArchiveError(RuntimeError):
    pass


DEFAULT_RAW_ARCHIVE = os.path.join("data", "cache", "raw_market_data")
_REPRESENTATIONS = frozenset({"HTTP_BODY_BYTES", "CANONICAL_JSON_UTF8"})
_PARSE_STATUSES = frozenset({"ACCEPTED", "REJECTED", "UNPARSED"})
_SENSITIVE_QUERY_PARTS = (
    "apikey",
    "api_key",
    "credential",
    "passphrase",
    "secret",
    "signature",
    "token",
)
_HTTPS_URL = TypeAdapter(HttpUrl)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RawArchiveError(f"{name} must be a non-empty string")
    return value.strip()


def _validate_source_uri(source_uri: str) -> str:
    source_uri = _require_text("source_uri", source_uri)
    try:
        parsed = _HTTPS_URL.validate_python(source_uri)
    except ValidationError as exc:
        raise RawArchiveError("source_uri must be an absolute HTTPS URI") from exc
    if parsed.scheme.lower() != "https" or not parsed.host:
        raise RawArchiveError("source_uri must be an absolute HTTPS URI")
    if parsed.username or parsed.password or parsed.fragment:
        raise RawArchiveError("source_uri must not contain credentials or fragments")
    for key, _value in parsed.query_params():
        normalized = key.lower().replace("-", "_")
        compact = normalized.replace("_", "")
        if any(part in normalized or part.replace("_", "") in compact
               for part in _SENSITIVE_QUERY_PARTS):
            raise RawArchiveError(
                f"source_uri contains a sensitive query parameter: {key}"
            )
    return source_uri


def _json_bytes(value, *, label: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RawArchiveError(
            f"{label} must be JSON serializable without non-finite values"
        ) from exc


def _relative(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _publish_immutable(path: str, payload: bytes) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(path):
        try:
            existing = open(path, "rb").read()
        except OSError as exc:
            raise RawArchiveError(f"archive object is unreadable: {path}") from exc
        if existing != payload:
            raise RawArchiveError(f"immutable archive collision or tampering: {path}")
        return

    fd, temporary = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = open(path, "rb").read()
            if existing != payload:
                raise RawArchiveError(
                    f"immutable archive collision or tampering: {path}"
                )
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    else:
        os.remove(temporary)


def archive_response(
    *,
    root: str,
    payload: bytes,
    source_uri: str,
    provider: str,
    dataset_id: str,
    instrument_id: str,
    observed_at_ms: int,
    representation: str,
    parse_diagnostics: dict | None = None,
) -> dict:
    """Archive one response body and one immutable observation sidecar."""
    root = os.path.abspath(_require_text("root", root))
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise RawArchiveError("payload must be bytes-like")
    raw = bytes(payload)
    source_uri = _validate_source_uri(source_uri)
    provider = _require_text("provider", provider)
    dataset_id = _require_text("dataset_id", dataset_id)
    instrument_id = _require_text("instrument_id", instrument_id)
    if isinstance(observed_at_ms, bool) or not isinstance(observed_at_ms, int) \
            or observed_at_ms < 0:
        raise RawArchiveError("observed_at_ms must be a non-negative integer")
    if representation not in _REPRESENTATIONS:
        raise RawArchiveError(f"unsupported representation: {representation}")
    diagnostics = dict(parse_diagnostics or {})
    status = diagnostics.get("status")
    if status is not None and status not in _PARSE_STATUSES:
        raise RawArchiveError(f"unsupported parse status: {status}")
    _json_bytes(diagnostics, label="parse_diagnostics")

    artifact_sha = _sha256(raw)
    object_path = os.path.join(
        root, "objects", "sha256", artifact_sha[:2], artifact_sha + ".bin"
    )
    _publish_immutable(object_path, raw)

    observation = {
        "schema_version": 1,
        "artifact_sha256": artifact_sha,
        "size_bytes": len(raw),
        "representation": representation,
        "provider": provider,
        "dataset_id": dataset_id,
        "instrument_id": instrument_id,
        "source_uri": source_uri,
        "observed_at_ms": observed_at_ms,
        "object_path": _relative(object_path, root),
        "parse_diagnostics": diagnostics,
    }
    observation_bytes = _json_bytes(observation, label="observation")
    observation_sha = _sha256(observation_bytes)
    observation_path = os.path.join(
        root,
        "observations",
        "sha256",
        observation_sha[:2],
        observation_sha + ".json",
    )
    _publish_immutable(observation_path, observation_bytes)
    return {
        "artifact_sha256": artifact_sha,
        "observation_sha256": observation_sha,
        "size_bytes": len(raw),
        "representation": representation,
        "object_path": _relative(object_path, root),
        "observation_path": _relative(observation_path, root),
    }


def archive_json_response(*, payload, **kwargs) -> dict:
    """Archive a decoded public JSON response with an honest representation label."""
    raw = _json_bytes(payload, label="JSON response")
    return archive_response(
        payload=raw,
        representation="CANONICAL_JSON_UTF8",
        **kwargs,
    )


def _load_observation(path: str) -> tuple[dict | None, str | None]:
    try:
        raw = open(path, "rb").read()
        expected_sha = os.path.splitext(os.path.basename(path))[0]
        if _sha256(raw) != expected_sha:
            return None, "OBSERVATION_DIGEST_MISMATCH"
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        ), None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None, "OBSERVATION_UNREADABLE"


def _validate_observation_schema(observation: dict) -> None:
    if not isinstance(observation, dict) or observation.get("schema_version") != 1:
        raise RawArchiveError("unsupported observation schema")
    artifact_sha = observation.get("artifact_sha256")
    if (not isinstance(artifact_sha, str) or len(artifact_sha) != 64
            or artifact_sha.lower() != artifact_sha
            or any(char not in "0123456789abcdef" for char in artifact_sha)):
        raise RawArchiveError("artifact_sha256 is invalid")
    size = observation.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise RawArchiveError("size_bytes is invalid")
    if observation.get("representation") not in _REPRESENTATIONS:
        raise RawArchiveError("representation is invalid")
    _require_text("provider", observation.get("provider"))
    _require_text("dataset_id", observation.get("dataset_id"))
    _require_text("instrument_id", observation.get("instrument_id"))
    _validate_source_uri(observation.get("source_uri"))
    observed_at = observation.get("observed_at_ms")
    if isinstance(observed_at, bool) or not isinstance(observed_at, int) \
            or observed_at < 0:
        raise RawArchiveError("observed_at_ms is invalid")
    expected_object = f"objects/sha256/{artifact_sha[:2]}/{artifact_sha}.bin"
    if observation.get("object_path") != expected_object:
        raise RawArchiveError("object_path is invalid")
    diagnostics = observation.get("parse_diagnostics")
    if not isinstance(diagnostics, dict):
        raise RawArchiveError("parse_diagnostics is invalid")
    status = diagnostics.get("status")
    if status is not None and status not in _PARSE_STATUSES:
        raise RawArchiveError("parse status is invalid")
    _json_bytes(diagnostics, label="parse_diagnostics")


def load_archived_response(
    root: str,
    *,
    dataset_id: str,
    instrument_id: str,
    event_time_ms: int,
) -> dict | None:
    """Load the latest verified accepted response for one dataset/instrument/event."""
    root = os.path.abspath(root)
    dataset_id = _require_text("dataset_id", dataset_id)
    instrument_id = _require_text("instrument_id", instrument_id)
    if isinstance(event_time_ms, bool) or not isinstance(event_time_ms, int):
        raise RawArchiveError("event_time_ms must be an integer")
    observation_root = os.path.join(root, "observations", "sha256")
    if not os.path.isdir(observation_root):
        return None
    matches = []
    for directory, _subdirs, names in os.walk(observation_root):
        for name in names:
            if not name.endswith(".json") or name.startswith(".tmp_"):
                continue
            path = os.path.join(directory, name)
            observation, error = _load_observation(path)
            if error is not None:
                continue
            try:
                _validate_observation_schema(observation)
            except (RawArchiveError, TypeError):
                continue
            diagnostics = observation["parse_diagnostics"]
            if (observation["dataset_id"] != dataset_id
                    or observation["instrument_id"] != instrument_id
                    or diagnostics.get("status") != "ACCEPTED"
                    or diagnostics.get("event_time_ms") != event_time_ms):
                continue
            object_path = os.path.join(
                root, *observation["object_path"].split("/")
            )
            try:
                payload = open(object_path, "rb").read()
            except OSError as exc:
                raise RawArchiveError("archived response object is missing") from exc
            if (_sha256(payload) != observation["artifact_sha256"]
                    or len(payload) != observation["size_bytes"]):
                raise RawArchiveError("archived response object failed verification")
            observation_sha = os.path.splitext(name)[0]
            matches.append((
                int(observation["observed_at_ms"]), observation_sha,
                payload, observation,
            ))
    if not matches:
        return None
    _observed_at, observation_sha, payload, observation = max(matches)
    return {
        "payload": payload,
        "observation_sha256": observation_sha,
        "observation": observation,
    }


def summarize_archive(
    root: str,
    *,
    dataset_ids: set[str] | frozenset[str] | None = None,
    instrument_ids: set[str] | frozenset[str] | None = None,
) -> dict:
    """Verify observations and return a compact full-archive or scoped manifest."""
    root = os.path.abspath(root)
    if dataset_ids is not None:
        dataset_ids = frozenset(
            _require_text("dataset_id", value) for value in dataset_ids
        )
        if not dataset_ids:
            raise RawArchiveError("dataset_ids must not be empty")
    if instrument_ids is not None:
        instrument_ids = frozenset(
            _require_text("instrument_id", value) for value in instrument_ids
        )
        if not instrument_ids:
            raise RawArchiveError("instrument_ids must not be empty")
    scoped = dataset_ids is not None or instrument_ids is not None
    observation_root = os.path.join(root, "observations", "sha256")
    paths = []
    if os.path.isdir(observation_root):
        for directory, _subdirs, names in os.walk(observation_root):
            paths.extend(
                os.path.join(directory, name)
                for name in names
                if name.endswith(".json") and not name.startswith(".tmp_")
            )
    paths.sort()
    if not paths:
        return {
            "schema_version": 1,
            "status": "MISSING",
            "coverage_scope": "OBSERVED_FETCH_RESPONSES_ONLY",
            "observation_count": 0,
            "invalid_observation_count": 0,
            "object_count": 0,
            "datasets": {},
            "providers": [],
            "representations": [],
            "latest_observed_at_ms": None,
            "aggregate_sha256": None,
            "errors": [],
        }

    errors = []
    invalid = set()
    datasets = Counter()
    providers = set()
    representations = set()
    objects = set()
    observed_at = []
    observation_ids = []
    for path in paths:
        observation_id = os.path.splitext(os.path.basename(path))[0]
        if not scoped:
            observation_ids.append(observation_id)
        observation, load_error = _load_observation(path)
        if load_error is not None:
            if scoped:
                # The unfiltered admission summary remains load-bearing for
                # malformed observations that cannot be attributed safely.
                continue
            errors.append({"observation_sha256": observation_id, "kind": load_error})
            invalid.add(observation_id)
            continue

        try:
            _validate_observation_schema(observation)
        except (RawArchiveError, TypeError):
            if scoped:
                continue
            errors.append({
                "observation_sha256": observation_id,
                "kind": "OBSERVATION_SCHEMA_INVALID",
            })
            invalid.add(observation_id)
            continue

        if (
            dataset_ids is not None
            and observation["dataset_id"] not in dataset_ids
        ) or (
            instrument_ids is not None
            and observation["instrument_id"] not in instrument_ids
        ):
            continue
        if scoped:
            observation_ids.append(observation_id)

        artifact_sha = observation.get("artifact_sha256")
        expected_object = (
            f"objects/sha256/{str(artifact_sha)[:2]}/{artifact_sha}.bin"
        )
        object_relative = observation.get("object_path")
        if object_relative != expected_object:
            errors.append({
                "observation_sha256": observation_id,
                "kind": "OBJECT_PATH_INVALID",
            })
            invalid.add(observation_id)
            continue
        object_path = os.path.join(root, *object_relative.split("/"))
        try:
            body = open(object_path, "rb").read()
        except OSError:
            errors.append({
                "observation_sha256": observation_id,
                "kind": "OBJECT_MISSING",
            })
            invalid.add(observation_id)
            continue
        if _sha256(body) != artifact_sha:
            kind = "OBJECT_DIGEST_MISMATCH"
        elif len(body) != observation.get("size_bytes"):
            kind = "OBJECT_SIZE_MISMATCH"
        else:
            kind = None
        if kind is not None:
            errors.append({"observation_sha256": observation_id, "kind": kind})
            invalid.add(observation_id)

        objects.add(str(artifact_sha))
        datasets[str(observation.get("dataset_id"))] += 1
        providers.add(str(observation.get("provider")))
        representations.add(str(observation.get("representation")))
        at = observation.get("observed_at_ms")
        if isinstance(at, int) and not isinstance(at, bool):
            observed_at.append(at)

    if not observation_ids:
        return {
            "schema_version": 1,
            "status": "MISSING",
            "coverage_scope": "OBSERVED_FETCH_RESPONSES_ONLY",
            "observation_count": 0,
            "invalid_observation_count": 0,
            "object_count": 0,
            "datasets": {},
            "providers": [],
            "representations": [],
            "latest_observed_at_ms": None,
            "aggregate_sha256": None,
            "errors": [],
        }
    aggregate = _sha256(_json_bytes(sorted(observation_ids), label="observation index"))
    return {
        "schema_version": 1,
        "status": "BLOCK" if errors else "PASS",
        "coverage_scope": "OBSERVED_FETCH_RESPONSES_ONLY",
        "observation_count": len(observation_ids),
        "invalid_observation_count": len(invalid),
        "object_count": len(objects),
        "datasets": dict(sorted(datasets.items())),
        "providers": sorted(providers),
        "representations": sorted(representations),
        "latest_observed_at_ms": max(observed_at) if observed_at else None,
        "aggregate_sha256": aggregate,
        "errors": errors,
    }
