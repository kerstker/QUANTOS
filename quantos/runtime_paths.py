"""Resolve mutable QUANTOS runtime state independently from release code.

Scheduled production-paper workers receive ``QUANTOS_STATE_ROOT`` from the
immutable launcher.  Direct developer invocations retain the historical
working-directory behavior, while an explicit override must always be an
absolute path so path identity cannot depend on a caller's current directory.
"""
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path


STATE_ROOT_ENV = "QUANTOS_STATE_ROOT"


class RuntimePathError(RuntimeError):
    """Mutable runtime state has no trustworthy absolute root."""


def runtime_state_root(
    *,
    environ: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> Path:
    """Return the absolute mutable state root for this process."""
    source = os.environ if environ is None else environ
    configured = source.get(STATE_ROOT_ENV, "").strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            raise RuntimePathError(f"{STATE_ROOT_ENV} must be absolute")
    else:
        candidate = Path.cwd() if cwd is None else Path(cwd)
    return candidate.resolve()


__all__ = ["RuntimePathError", "STATE_ROOT_ENV", "runtime_state_root"]
