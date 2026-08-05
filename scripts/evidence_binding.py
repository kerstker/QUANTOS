"""Strict canonical binding contract for readiness evidence."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
from collections.abc import Mapping
from typing import Any

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    ).strip()


def _source(value: object) -> str:
    if not isinstance(value, str) or not _SHA40.fullmatch(value):
        raise ValueError("canonical validation source_commit must be a full lowercase git SHA")
    return value


def _run(value: object) -> str:
    if not isinstance(value, str) or not _SHA64.fullmatch(value):
        raise ValueError("canonical validation run_id must be a 64-character lowercase hex digest")
    return value


def _timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("canonical validation generated_at must be a non-negative integer")
    return value


def _current(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("canonical validation current must be boolean")
    return value


def validation_binding(
    *, generated_at: int, run_id: str | None = None, source_commit: str | None = None,
    current: bool = True,
) -> dict[str, object]:
    source_env = os.environ.get("QUANTOS_VALIDATION_SOURCE_COMMIT")
    source = source_commit if source_commit is not None else (source_env if source_env is not None else _head())
    _source(source)
    run_env = os.environ.get("QUANTOS_VALIDATION_RUN_ID")
    run = run_id if run_id is not None else run_env
    if run is None:
        run = hashlib.sha256(("diagnostic:" + source).encode("utf-8")).hexdigest()
    _run(run)
    if generated_at == 0:
        raw = os.environ.get("QUANTOS_VALIDATION_STARTED_AT_MS")
        generated_at = int(raw) if raw is not None else 0
    return {
        "run_id": _run(run),
        "source_commit": _source(source),
        "generated_at": _timestamp(generated_at),
        "current": _current(current),
    }


def validation_binding_from_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("validation"), Mapping):
        raise ValueError("evidence is missing explicit canonical validation binding")
    value = payload["validation"]
    required = {"run_id", "source_commit", "generated_at", "current"}
    if set(value) != required:
        raise ValueError("canonical validation binding has an invalid shape")
    return {
        "run_id": _run(value["run_id"]),
        "source_commit": _source(value["source_commit"]),
        "generated_at": _timestamp(value["generated_at"]),
        "current": _current(value["current"]),
    }


def validation_report_legacy_binding(payload: Mapping[str, Any]) -> dict[str, object]:
    """Read-only migration helper for pre-Task-5 reports; not a canonical accessor."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("commit"), str) or not isinstance(payload.get("checks_manifest_sha256"), str):
        raise ValueError("legacy validation report shape is invalid")
    return {"source_commit": _source(payload["commit"]), "run_id": _run(payload["checks_manifest_sha256"])}


def assert_current_binding(payload: Mapping[str, Any], *, head: str) -> None:
    binding = validation_binding_from_payload(payload)
    _source(head)
    if binding["source_commit"] != head:
        raise ValueError(f"validation source commit {binding['source_commit']} != HEAD {head}")
    if binding["current"] is not True:
        raise ValueError("validation binding is not current")
    expected_run = os.environ.get("QUANTOS_VALIDATION_RUN_ID")
    if expected_run is not None and binding["run_id"] != _run(expected_run):
        raise ValueError(f"validation run {binding['run_id']} != expected {expected_run}")
