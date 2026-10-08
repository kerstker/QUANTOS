"""Small standard-library JSON trust primitives shared by governance gates."""
from __future__ import annotations

import json
import math
import re
from threading import local
from typing import Any

_ASCII_JSON_ESCAPES = re.compile(r'[\x00-\x1f"\\]')
_SHORT_JSON_ESCAPES = '\b\t\n\f\r"\\'


def ascii_json_string_size(value: str, *, maximum_bytes: int) -> int | None:
    """Count exact ASCII JSON bytes, stopping once the caller's bound is exceeded.

    Non-ASCII strings and subclasses retain their caller's original path. No
    escaped string or byte buffer is allocated before its budget is checked.
    """
    if type(value) is not str or not value.isascii():
        return None
    size = len(value) + 2
    if size > maximum_bytes or _ASCII_JSON_ESCAPES.search(value) is None:
        return size
    for escaped in _ASCII_JSON_ESCAPES.finditer(value):
        size += 1 if escaped[0] in _SHORT_JSON_ESCAPES else 5
        if size > maximum_bytes:
            break
    return size


class DuplicateJsonKeyError(ValueError):
    """A trust document contains an ambiguous duplicate object member."""


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


_DECODER_STATE = local()


def _strict_decoder() -> json.JSONDecoder:
    # Reuse parser machinery within one thread, never parsed documents or a
    # scanner shared between concurrent readers. Every call decodes its input.
    decoder = getattr(_DECODER_STATE, "decoder", None)
    if decoder is None:
        decoder = json.JSONDecoder(
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
        _DECODER_STATE.decoder = decoder
    return decoder


def load_json_without_duplicate_keys(content: bytes | str) -> Any:
    """Parse unambiguous, finite, Unicode-safe standards-compliant JSON."""
    if isinstance(content, bytes):
        text = content.decode("utf-8")
    elif isinstance(content, str):
        text = content
    else:
        raise TypeError("JSON content must be bytes or text")

    # Keep json.loads' text-BOM rejection, including its original error and
    # position. Bytes still require strict UTF-8 above, as before.
    if text.startswith("\ufeff"):
        raise json.JSONDecodeError("Unexpected UTF-8 BOM (decode using utf-8-sig)", text, 0)
    parsed = _strict_decoder().decode(text)

    pending = [parsed]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and not value.isascii() and any(
            0xD800 <= ord(character) <= 0xDFFF for character in value
        ):
            raise ValueError("JSON contains a surrogate code point")
    return parsed


__all__ = ["DuplicateJsonKeyError", "load_json_without_duplicate_keys", "ascii_json_string_size"]
