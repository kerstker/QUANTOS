# Exact-Byte Evidence Portability Implementation Plan

> **For agentic workers:** Use the repository's subagent-driven implementation workflow (or its plan-execution alternative) task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every current supply-chain manifest input, portable trial-ledger seal input, and container recipe input materialize and generate identically on Windows and Unix, while preserving fail-closed evidence and truthful live-versus-portable ledger schema reporting.

**Architecture:** Git attributes own checkout byte semantics; runtime verifiers continue to hash exact bytes and never normalize content. A repository governance test checks both attribute metadata and an isolated `core.autocrlf=true` materialization. The live ledger preflight reports the actual schema version separately from the minimum supported version, while the portable snapshot remains frozen at schema v2.

**Tech Stack:** Python 3.13, pytest, Git attributes, SQLite, PowerShell/RTK launch hygiene.

## Global Constraints

- Readiness remains `L2_DRY_RUN_ONLY`; do not add broker, order, credential, network, or live-trading behavior.
- Keep exact-byte SHA-256 verification; do not normalize bytes inside a verifier and do not accept multiple platform hashes.
- Pin every current `_MANIFESTS` text path plus `.dockerignore`, `evidence/alpha_trial_ledger_history.json`, and `evidence/alpha_trial_ledger_snapshot_seal.json` to LF through the reviewed type-scoped rules.
- Make the supply-chain manifest self-bind `.gitattributes` and `tests/governance/test_evidence_checkout_portability.py`.
- Keep `evidence/alpha_trial_ledger_snapshot.db` binary.
- Live ledger policy is schema v2-or-later with semantic verification; portable snapshot policy is schema v2 exactly.
- Do not rewrite retained npm/Python audit, OCI/Trivy, or supply-chain evidence merely to rebind hashes. npm/Python require real audits against LF manifests; container evidence requires a real two-build/scan refresh; the derived baseline follows only after all upstream inputs are current.
- Every raw-hashed evidence writer must emit canonical UTF-8/LF bytes directly; Git checkout attributes cannot repair bytes between generation and immediate verification.
- Do not stage, commit, push, refresh external backup evidence, run Docker/Trivy, or mutate external services without separate authorization.
- Prefix every shell command with `rtk`; use `python scripts/validate.py` as the canonical repository gate.

---

### Task 1: Prove the checkout-byte contract fails before adding attributes

**Files:**
- Create: `tests/governance/test_evidence_checkout_portability.py`
- Inspect: `.gitattributes`

**Interfaces:**
- Consumes: repository root, Git's `check-attr` and `checkout-index` behavior.
- Produces: regression coverage for LF text paths, the binary SQLite path, and a fresh Windows-style checkout.

- [ ] **Step 1: Write the failing governance test**

```python
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
TEXT_PATHS = tuple(dict.fromkeys((*supply_chain._MANIFESTS,
    ".dockerignore",
    "evidence/alpha_trial_ledger_history.json",
    "evidence/alpha_trial_ledger_snapshot_seal.json",
)))
BINARY_PATH = "evidence/alpha_trial_ledger_snapshot.db"
WRITERS = (
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
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
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
```

- [ ] **Step 2: Run the new test and verify the intended red state**

Run:

```powershell
rtk python -m pytest tests/governance/test_evidence_checkout_portability.py -q -p no:cacheprovider
```

Expected: the governed text-attribute/raw-byte cases fail because `.gitattributes` currently defines only the binary SQLite path and the Windows worktree contains CRLF/mixed files. The five `write_text` evidence writers also fail the LF-byte assertion on Windows.

- [ ] **Step 3: Confirm the failure is not caused by missing Git or a temp-directory error**

Run:

```powershell
rtk git --version
```

Expected: exit 0. If pytest reports a `tmp_path` permission failure, rerun the same test with an explicit writable `--basetemp`; do not alter the assertion.

---

### Task 2: Enforce LF checkout and generation without weakening exact-byte verification

**Files:**
- Modify: `.gitattributes`
- Mechanically normalize: every path in `scripts.generate_supply_chain_evidence._MANIFESTS`
- Mechanically normalize: `.dockerignore`
- Verify/normalize: `evidence/alpha_trial_ledger_history.json`
- Verify/normalize: `evidence/alpha_trial_ledger_snapshot_seal.json`
- Modify: `scripts/generate_actions_audit_evidence.py`
- Modify: `scripts/generate_container_audit_evidence.py`
- Modify: `scripts/generate_container_reproducibility_evidence.py`
- Modify: `scripts/generate_npm_audit_evidence.py`
- Modify: `scripts/generate_python_audit_evidence.py`
- Modify: `scripts/generate_supply_chain_evidence.py`
- Test: `tests/governance/test_supply_chain_evidence.py`
- Test: `tests/governance/test_evidence_checkout_portability.py`

**Interfaces:**
- Consumes: the failing tests from Task 1.
- Produces: one exact LF checkout identity on every supported Git checkout and an unchanged binary snapshot.

- [ ] **Step 1: Add the exact Git attribute rules**

Replace `.gitattributes` with:

```gitattributes
.gitattributes text eol=lf
*.py text eol=lf
*.json text eol=lf
*.toml text eol=lf
*.txt text eol=lf
*.yml text eol=lf
*.ps1 text eol=lf
deploy/console.Dockerfile text eol=lf
.dockerignore text eol=lf
evidence/alpha_trial_ledger_snapshot.db binary
```

- [ ] **Step 2: Normalize only line endings for every governed text input**

For each `_MANIFESTS` path and the three extra text paths, compare the worktree bytes after CRLF/lone-CR normalization with the LF Git index blob. Abort if any path differs for a reason other than EOL. Only then write the verified LF index bytes back to the worktree. Verify every resolved target remains inside the repository root.

- [ ] **Step 2a: Make the manifest self-bind the controls**

Add `.gitattributes` and `tests/governance/test_evidence_checkout_portability.py` to `_MANIFESTS`. A focused governance test must fail before this edit and pass afterward. This prevents a later deletion or weakening of the exact-byte policy from leaving a previously green supply-chain baseline apparently current.

- [ ] **Step 3: Make the five platform-dependent writers emit bytes**

Replace each default-newline `temporary.write_text(...)` call with the byte-equivalent form:

```python
temporary.write_bytes(
    (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
)
```

Do not change JSON key ordering, indentation, Unicode policy, final-LF count, temp-file replacement, or any verifier. The event-schema, service-import-graph, and supply-chain writers already use `write_bytes` and require no production edit.

- [ ] **Step 4: Run the governance test and verify green**

Run:

```powershell
rtk python -m pytest tests/governance/test_evidence_checkout_portability.py -q -p no:cacheprovider
```

Expected: every governed path, binary attribute, isolated checkout, and writer fixture passes.

- [ ] **Step 5: Prove the portable bundle still verifies exact bytes**

Run:

```powershell
rtk python -m tools.search_trial_ledger_history --check-snapshot
```

Expected: JSON with `"status":"verified"`, `"record_count":2`, and exit 0. Do not regenerate the history, SQLite snapshot, or seal.

- [ ] **Step 6: Prove stale external bindings remain honestly red**

Run:

```powershell
rtk python scripts/generate_npm_audit_evidence.py --check
rtk python scripts/generate_python_audit_evidence.py --check
rtk python scripts/generate_actions_audit_evidence.py --check
rtk python scripts/generate_container_audit_evidence.py --check
rtk python scripts/generate_container_reproducibility_evidence.py --check
rtk python scripts/generate_supply_chain_evidence.py --check
```

Expected: npm/Python fail on LF-manifest digest mismatch; Actions OSV remains current; container audit/reproducibility fail on recipe binding; supply-chain fails on the first stale upstream input. These red states are required until actual advisory audits and external builds/scans exist.

---

### Task 3: Report actual live schema separately from the supported minimum

**Files:**
- Modify: `tests/tools/test_search_trial_ledger_history.py`
- Modify: `tools/search_trial_ledger_history.py:868,1197-1202`
- Modify: `docs/runbooks/l2-research-operations.md`

**Interfaces:**
- Consumes: `inspect_ledger(ledger_path=...)` and `LEDGER_CONTRACT_VERSION == 2`.
- Produces: `ledger_contract_version=<actual PRAGMA user_version>` and `required_ledger_contract_version=2` for every inspection result.

- [ ] **Step 1: Write the failing actual-version test**

```python
def test_inspect_reports_actual_forward_compatible_schema_version(
    tmp_path: Path,
) -> None:
    ledger, _output, _label = _paths(tmp_path)
    _create_ledger(ledger)
    connection = sqlite3.connect(ledger)
    connection.execute("PRAGMA user_version=3")
    connection.close()

    inspection = history.inspect_ledger(ledger_path=ledger)

    assert inspection["status"] == "verified"
    assert inspection["ledger_contract_version"] == 3
    assert inspection["required_ledger_contract_version"] == 2
    assert inspection["capture_ready"] is True
```

- [ ] **Step 1a: Write the failing exact-portable-version test**

Create a valid sealed snapshot, change only its `PRAGMA user_version` from 2 to
3, recompute `snapshot_sha256` in the seal so the raw hash is again internally
consistent, and assert `check_snapshot` raises
`TrialLedgerHistoryError("portable snapshot schema version...")`. Also add a
live CLI fixture proving the same schema-v3 authority remains accepted by
`--check` when its v2 subset is valid.

- [ ] **Step 2: Run only the new test and verify the intended red state**

Run:

```powershell
rtk python -m pytest tests/tools/test_search_trial_ledger_history.py -k "actual_forward_compatible or rehashed_unknown_portable or live_forward_compatible" -q -p no:cacheprovider
```

Expected before production edits: actual-version reporting fails because the
ready branch returns constant `2`, and the rehashed portable-v3 snapshot is not
rejected. The live-v3 compatibility fixture stays green.

- [ ] **Step 3: Make the minimal production change**

In the ready return value of `inspect_ledger`, use:

```python
"ledger_contract_version": first["schema_version"],
"required_ledger_contract_version": LEDGER_CONTRACT_VERSION,
```

Keep the existing live `schema_version < LEDGER_CONTRACT_VERSION`
forward-compatibility rule. In `check_snapshot`, read `PRAGMA user_version` and
raise unless it equals `LEDGER_CONTRACT_VERSION` before validating the table
allowlist/triggers. This is a new exact-v2 portable guard, not an existing
behavior to preserve.

- [ ] **Step 4: Run the focused ledger tests**

Run:

```powershell
rtk python -m pytest tests/tools/test_search_trial_ledger_history.py -q -p no:cacheprovider
```

Expected: all tests pass, including the newly added live-v3 acceptance,
actual-version reporting, and rehashed portable-v3 rejection cases.

- [ ] **Step 5: Correct operator language**

Document the distinction exactly as:

```text
The live authority must report schema v2-or-later and pass the v2 subset's semantic checks. The portable repository snapshot must report schema v2 exactly. A live v3 result proves only v2-subset readability. The separately reviewed White/SPA Phase A must add an exact family-authority verifier before any v3-only family table can authorize an assessment.
```

---

### Task 4: Correct audit documentation without predicting a greener gate

**Files:**
- Modify: `docs/engineering/specs/2026-07-18-portable-alpha-trial-ledger-evidence-design.md`
- Modify: `docs/engineering/specs/2026-07-17-paper-to-quantos-gap-analysis.md`
- Modify: `docs/engineering/specs/2026-07-18-evidence-byte-portability-design.md`
- Modify: `docs/engineering/plans/2026-07-18-portable-alpha-trial-ledger-evidence.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: reviewed Git history and the latest 14/19 canonical-gate evidence.
- Produces: internally consistent repository documentation and no banned legacy workflow marker.

- [ ] **Step 1: Correct the portable-ledger closure baseline**

Use `a38c8d5` as the closure baseline. Describe `98df8cb` only as the earlier paper-gap audit commit, not as a parent containing the ledger closure.

- [ ] **Step 2: Correct the `.gitignore` statement**

Replace the claim that `.gitignore` stayed unchanged with the factual statement that the obsolete legacy-workflow marker was removed while `data/cache/` remains excluded.

- [ ] **Step 3: Correct full-gate causality**

State that the latest recorded pre-migration full gate is 14/19, not a current-worktree result. Record the current focused npm/Python manifest failures, Actions OSV pass, three supply-chain-test failures at the npm binding, and the locally regenerated event-schema/import-graph/top-tier derivatives that now pass their self-checks; do not claim an independently reproduced legacy-path failure and do not predict a new full-gate count.

- [ ] **Step 4: Run the legacy-boundary tests**

Run:

```powershell
rtk python -m pytest tests/governance/test_no_legacy_workflow_dependency.py -q -p no:cacheprovider
```

Expected: 2 passed.

---

### Task 5: Verification and independent review

**Files:**
- Verify all files changed in Tasks 1-4.

**Interfaces:**
- Consumes: the completed local portability change.
- Produces: fresh evidence for the local claim and an explicit list of external red states.

- [ ] **Step 1: Run the focused regression set**

Run:

```powershell
rtk python -m pytest tests/tools/test_search_trial_ledger_history.py tests/governance/test_evidence_checkout_portability.py tests/governance/test_no_legacy_workflow_dependency.py -q -p no:cacheprovider
```

Expected: exit 0 with no failed tests.

- [ ] **Step 2: Run portable and top-tier artifact checks**

Run:

```powershell
rtk python -m tools.search_trial_ledger_history --check-snapshot
rtk python scripts/assess_top_tier.py --check
```

Expected: portable snapshot verifies; assessment artifact is current and still reports `NOT_TOP_TIER — 15/22 pass`.

- [ ] **Step 3: Run the governance scanner**

Run:

```powershell
rtk python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

Expected: 0 violations.

- [ ] **Step 4: Request an independent code review**

Give the reviewer the exact-byte design, this implementation plan, the current diff, and the focused command output. Require review of Windows materialization, exact-byte security semantics, schema reporting, documentation truthfulness, and preservation of L2 safety boundaries.

- [ ] **Step 5: Run the canonical gate when focused review is clean**

Run:

```powershell
rtk python scripts/validate.py
```

Expected: record the actual 19-group result. npm/Python audit, container audit/reproducibility, supply-chain, and stale external backup may remain red until their independent external evidence is refreshed; do not relabel those failures as local-code success. Actions OSV should remain independently current.

## Self-review

- Spec coverage: Tasks 1-5 cover all current supply-chain manifest paths, the portable-ledger seal, direct recipe inputs, explicit LF evidence writers, and truthful schema reporting.
- Placeholder scan: the plan contains no undefined implementation placeholder; external rebuild work is explicitly excluded rather than deferred ambiguously.
- Type consistency: both schema fields retain integer values; the live field is actual and the required field is the minimum.
- Safety: no step adds or enables a trading, broker, credential, network, backup, Docker, or external mutation path.
