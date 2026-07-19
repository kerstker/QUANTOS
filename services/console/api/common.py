"""Small deterministic helpers shared by console HTTP routers."""

from __future__ import annotations

import hashlib


def short_hash(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
