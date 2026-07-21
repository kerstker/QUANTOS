"""Small, dependency-free helpers for publishing local research cache artifacts."""
from __future__ import annotations

import json
import os
import tempfile
import time


_REPLACE_RETRY_DELAYS_S = (0.05, 0.10, 0.20, 0.40)


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
        try:
            os.replace(temporary, path)
        except PermissionError:
            # Windows readers (including antivirus/indexer hooks) can hold the
            # destination for a short interval. Retry only this transient
            # failure; a persistent denial remains fail-closed and is raised.
            for delay in _REPLACE_RETRY_DELAYS_S:
                time.sleep(delay)
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    continue
            else:
                raise
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
