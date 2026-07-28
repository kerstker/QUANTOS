"""Exact canonical JSON and SHA-256 primitives for v3 authorities."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any

from pydantic import BaseModel


class CanonicalJsonError(ValueError):
    """Raised when a value or payload is not QUANTOS_CANONICAL_JSON_V1."""


_FLOAT_HEX = re.compile(r"^[+-]?0[xX][0-9a-fA-F.]+[pP][+-]?\d+$")


def canonicalize_float64(value: float) -> float:
    if not isinstance(value, float) or not math.isfinite(value):
        raise CanonicalJsonError("authority floats must be finite float64 values")
    return 0.0 if value == 0.0 else value


def float64_hex(value: float) -> str:
    return canonicalize_float64(value).hex()


def parse_float64_hex(value: str) -> float:
    if not isinstance(value, str) or not _FLOAT_HEX.fullmatch(value):
        raise CanonicalJsonError("not a float64 hex literal")
    try:
        parsed = float.fromhex(value)
    except ValueError as exc:
        raise CanonicalJsonError("not a float64 hex literal") from exc
    canonical = float64_hex(parsed)
    if value != canonical:
        raise CanonicalJsonError("non-canonical float64 hex literal")
    return parsed


def _nfc(value: str, *, context: str) -> str:
    if unicodedata.normalize("NFC", value) != value:
        raise CanonicalJsonError(f"{context} must be NFC normalized")
    return value


def _validate_loaded_string(value: str) -> str:
    _nfc(value, context="strings")
    if _FLOAT_HEX.fullmatch(value):
        parse_float64_hex(value)
    return value


def _canonicalize(value: Any, *, loaded: bool) -> Any:
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if loaded:
            raise CanonicalJsonError("JSON numeric floats are forbidden")
        return float64_hex(value)
    if isinstance(value, str):
        return _validate_loaded_string(value) if loaded else _nfc(value, context="strings")
    if isinstance(value, BaseModel):
        if loaded:
            raise CanonicalJsonError("loaded payload cannot contain a model")
        return _canonicalize(value.model_dump(mode="python"), loaded=False)
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item, loaded=loaded) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJsonError("authority dictionary keys must be strings")
            _nfc(key, context="dictionary keys")
            normalized[key] = _canonicalize(item, loaded=loaded)
        return normalized
    raise CanonicalJsonError(f"unsupported authority value: {type(value).__name__}")


def canonicalize_authority_value(value: object) -> object:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise CanonicalJsonError("pre-serialized JSON is not an authority value")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        except RecursionError as exc:
            raise CanonicalJsonError("JSON text probe exceeded parser recursion limits") from exc
        except Exception as exc:
            raise CanonicalJsonError("JSON text probe failed closed") from exc
        if isinstance(parsed, (dict, list)):
            raise CanonicalJsonError("pre-serialized JSON is not an authority value")
    return _canonicalize(value, loaded=False)


def canonical_json_bytes(value: object) -> bytes:
    normalized = canonicalize_authority_value(value)
    try:
        text = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CanonicalJsonError("authority value cannot be serialized") from exc
    return (text + "\n").encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalJsonError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_float(_: str) -> Any:
    raise CanonicalJsonError("JSON numeric floats are forbidden")


def _reject_json_constant(_: str) -> Any:
    raise CanonicalJsonError("JSON constants are forbidden")


def load_canonical_json_bytes(payload: bytes) -> object:
    if not isinstance(payload, bytes):
        raise CanonicalJsonError("canonical payload must be bytes")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise CanonicalJsonError("UTF-8 BOM is forbidden")
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n") or b"\r" in payload:
        raise CanonicalJsonError("payload must end with exactly one LF")
    try:
        text = payload.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_pairs, parse_float=_reject_json_float, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanonicalJsonError("invalid canonical JSON") from exc
    normalized = _canonicalize(value, loaded=True)
    if canonical_json_bytes(normalized) != payload:
        raise CanonicalJsonError("payload is not canonical JSON")
    return normalized


def sha256_bytes(payload: bytes) -> str:
    if not isinstance(payload, bytes):
        raise CanonicalJsonError("hash input must be bytes")
    return hashlib.sha256(payload).hexdigest()


def canonical_sha256(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))
