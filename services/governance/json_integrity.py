"""Small standard-library JSON trust primitives shared by governance gates."""
from __future__ import annotations

import json
import math
from typing import Any


class DuplicateJsonKeyError(ValueError):
    """A trust document contains an ambiguous duplicate object member."""


def load_json_without_duplicate_keys(content: bytes | str) -> Any:
    """Parse unambiguous, finite, Unicode-safe standards-compliant JSON."""
    if isinstance(content, bytes):
        text = content.decode("utf-8")
    elif isinstance(content, str):
        text = content
    else:
        raise TypeError("JSON content must be bytes or text")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJsonKeyError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite JSON number: {value}")
        return parsed

    parsed = json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )

    pending = [parsed]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and any(
            0xD800 <= ord(character) <= 0xDFFF for character in value
        ):
            raise ValueError("JSON contains a surrogate code point")
    return parsed


__all__ = ["DuplicateJsonKeyError", "load_json_without_duplicate_keys"]
