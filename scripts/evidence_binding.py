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
    if "QUANTOS_VALIDATION_SOURCE_COMMIT" in os.environ:
        env_source = _source(os.environ["QUANTOS_VALIDATION_SOURCE_COMMIT"])
        if source_commit is not None and source_commit != env_source:
            raise ValueError("explicit source_commit disagrees with canonical environment")
        source = env_source
    else:
        source = source_commit if source_commit is not None else _head()
    source = _source(source)

    if "QUANTOS_VALIDATION_RUN_ID" in os.environ:
        env_run = _run(os.environ["QUANTOS_VALIDATION_RUN_ID"])
        if run_id is not None and run_id != env_run:
            raise ValueError("explicit run_id disagrees with canonical environment")
        run = env_run
    else:
        run = run_id
    if run is None:
        run = hashlib.sha256(("diagnostic:" + source).encode("utf-8")).hexdigest()
    run = _run(run)
    generated_at = _timestamp(generated_at)
    if generated_at == 0:
        raw = os.environ.get("QUANTOS_VALIDATION_STARTED_AT_MS")
        generated_at = int(raw) if raw is not None else 0
        generated_at = _timestamp(generated_at)
    return {
        "run_id": run,
        "source_commit": source,
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
    if "QUANTOS_VALIDATION_SOURCE_COMMIT" in os.environ:
        expected_source = _source(os.environ["QUANTOS_VALIDATION_SOURCE_COMMIT"])
        if binding["source_commit"] != expected_source:
            raise ValueError(f"validation source commit {binding['source_commit']} != canonical environment")
    if "QUANTOS_VALIDATION_RUN_ID" in os.environ:
        expected_run = _run(os.environ["QUANTOS_VALIDATION_RUN_ID"])
        if binding["run_id"] != expected_run:
            raise ValueError(f"validation run {binding['run_id']} != canonical environment")
