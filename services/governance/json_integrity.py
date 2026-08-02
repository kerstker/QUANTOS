"""Small standard-library JSON trust primitives shared by governance gates."""
from __future__ import annotations

import json
from typing import Any


class DuplicateJsonKeyError(ValueError):
    """A trust document contains an ambiguous duplicate object member."""


def load_json_without_duplicate_keys(content: bytes | str) -> Any:
    """Parse standards-compliant JSON while rejecting duplicate object keys."""
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

    return json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )


__all__ = ["DuplicateJsonKeyError", "load_json_without_duplicate_keys"]
