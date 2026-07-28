from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

from scripts import generate_actions_audit_evidence as actions_audit
from scripts import generate_container_audit_evidence as container_audit
from scripts import generate_container_reproducibility_evidence as container_reproducibility
from scripts import generate_event_schema_catalog as event_schema_catalog
from scripts import generate_npm_audit_evidence as npm_audit
from scripts import generate_python_audit_evidence as python_audit
from scripts import generate_service_import_graph as service_import_graph
from scripts import generate_supply_chain_evidence as supply_chain


ROOT = Path(__file__).resolve().parents[2]
TEXT_PATHS = tuple(
    dict.fromkeys(
        (
            *supply_chain._MANIFESTS,
            ".dockerignore",
            "evidence/alpha_trial_ledger_history.json",
            "evidence/alpha_trial_ledger_snapshot_seal.json",
        )
    )
)
BINARY_PATH = "evidence/alpha_trial_ledger_snapshot.db"
WRITERS: tuple[tuple[Callable[[Path, dict[str, Any]], None], bool], ...] = (
    (actions_audit._write, False),
    (container_audit._write, False),
    (container_reproducibility._write, False),
    (event_schema_catalog.write_event_schema_catalog, True),
    (npm_audit._write, False),
    (python_audit._write, False),
    (service_import_graph._write, False),
    (supply_chain._write, False),
)


def _git(cwd: Path, *args: str) -> str:
    environment = dict(os.environ)
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _attributes(path: str, *names: str) -> dict[str, str]:
    output = _git(ROOT, "check-attr", *names, "--", path)
    values: dict[str, str] = {}
    for line in output.splitlines():
        _reported_path, name, value = line.split(": ", 2)
        values[name] = value
    return values


@pytest.mark.parametrize("path", TEXT_PATHS)
def test_exact_byte_text_evidence_is_lf_pinned(path: str) -> None:
    assert _attributes(path, "text", "eol") == {"text": "set", "eol": "lf"}
    raw = (ROOT / path).read_bytes()
    assert b"\r" not in raw


def test_portable_sqlite_snapshot_remains_binary() -> None:
    assert _attributes(BINARY_PATH, "binary", "text") == {
        "binary": "set",
        "text": "unset",
    }


def test_autocrlf_checkout_reproduces_exact_governed_bytes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    checkout = tmp_path / "checkout"
    repository.mkdir()
    checkout.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "core.autocrlf", "true")

    paths = tuple(dict.fromkeys((".gitattributes", *TEXT_PATHS, BINARY_PATH)))
    for relative in paths:
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)

    _git(repository, "add", "--", *paths)
    prefix = checkout.resolve().as_posix() + "/"
    _git(repository, "checkout-index", "--all", "--force", f"--prefix={prefix}")

    for relative in (*TEXT_PATHS, BINARY_PATH):
        assert (checkout / relative).read_bytes() == (ROOT / relative).read_bytes()


@pytest.mark.parametrize(("writer", "ensure_ascii"), WRITERS)
def test_governed_evidence_writers_emit_canonical_lf_bytes(
    tmp_path: Path,
    writer: Callable[[Path, dict[str, Any]], None],
    ensure_ascii: bool,
) -> None:
    output = tmp_path / "evidence.json"
    document = {"non_ascii": "量化", "value": 1}
    writer(output, document)

    raw = output.read_bytes()
    expected = (
        json.dumps(document, ensure_ascii=ensure_ascii, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")
    assert raw == expected
    assert json.loads(raw.decode("utf-8")) == document
