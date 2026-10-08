"""Compatibility imports for the neutral, unchanged JSON trust primitives."""
from quantos.contracts.json_integrity import (
    DuplicateJsonKeyError,
    load_json_without_duplicate_keys,
)

__all__ = ["DuplicateJsonKeyError", "load_json_without_duplicate_keys"]
