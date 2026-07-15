"""Small, dependency-free helpers for publishing local research cache artifacts."""
from __future__ import annotations

import json
import os
import tempfile


def atomic_write_json(path: str, payload, *, indent=None) -> None:
    """Publish one complete JSON document or leave the previous file untouched."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, temporary = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                indent=indent,
                allow_nan=False,
                ensure_ascii=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
