"""Small, strict binding contract for canonical readiness evidence."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
from collections.abc import Mapping
from typing import Any


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True).strip()


def validation_binding(*, generated_at: int, run_id: str | None = None, source_commit: str | None = None) -> dict[str, object]:
    source = source_commit or os.environ.get("QUANTOS_VALIDATION_SOURCE_COMMIT") or _head()
    run = run_id or os.environ.get("QUANTOS_VALIDATION_RUN_ID")
    if not run:
        run = "diagnostic-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    if generated_at == 0:
        generated_at = int(os.environ.get("QUANTOS_VALIDATION_STARTED_AT_MS", "0"))
    return {"run_id": run, "source_commit": source, "generated_at": generated_at, "current": True}


def validation_binding_from_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    value = payload.get("validation")
    if value is None and isinstance(payload.get("commit"), str) and isinstance(payload.get("checks_manifest_sha256"), str):
        value = {
            "run_id": payload["checks_manifest_sha256"],
            "source_commit": payload["commit"],
            "current": True,
        }
    if not isinstance(value, Mapping):
        raise ValueError("evidence is missing canonical validation binding")
    required = ("run_id", "source_commit")
    if any(not isinstance(value.get(key), str) or not value[key] for key in required):
        raise ValueError("canonical validation binding is incomplete")
    return dict(value)


def assert_current_binding(payload: Mapping[str, Any], *, head: str) -> None:
    binding = validation_binding_from_payload(payload)
    if binding["source_commit"] != head:
        raise ValueError(f"validation source commit {binding['source_commit']} != HEAD {head}")
    if binding.get("current") is not True:
        raise ValueError("validation binding is not current")
    expected_run = os.environ.get("QUANTOS_VALIDATION_RUN_ID")
    if expected_run and binding["run_id"] != expected_run:
        raise ValueError(f"validation run {binding['run_id']} != expected {expected_run}")
