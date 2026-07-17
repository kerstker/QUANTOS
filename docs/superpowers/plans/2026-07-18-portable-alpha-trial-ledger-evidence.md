# Portable Alpha Trial-Ledger Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ALPHA.TRIAL.GLOBAL_LEDGER` verifiably `M4/PASS` in both the originating worktree and a fresh checkout without committing the mutable production registry.

**Architecture:** Keep `data/cache/experiment_registry.db` as the ignored mutable authority. Export only the five load-bearing ledger tables into a tracked SQLite snapshot, bind the exact history and snapshot bytes with a strict JSON seal, and make the repository assessment verify that portable bundle. Preserve the existing live `--check` as a separate operational-health assertion.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`/`hashlib`/`tempfile`, pytest, existing QUANTOS top-tier assessment framework, Git binary attributes.

## Global Constraints

- `LIVE_TRADING_ENABLED=false`, `REAL_BROKER_ENABLED=false`, `ALLOW_ORDER_SUBMISSION=false`, `ALLOW_AI_DIRECT_TRADING=false`, and `REQUIRE_HUMAN_APPROVAL=true` remain unchanged.
- No broker SDK, network client, credential access, or live order path may be added.
- `data/cache/` remains Git-ignored; never force-add `data/cache/experiment_registry.db`.
- Preserve the two existing history records and their hashes exactly.
- The portable snapshot contains only `trials`, `evaluations`, `search_attempt_events`, `search_candidates`, `search_evaluations`, plus SQLite-owned `sqlite_sequence`.
- Missing portable evidence is `PARTIAL/M3`; present invalid evidence is `FAILED/M2`; only a fully verified bundle is `PASS/M4`.
- Every repository command begins with `rtk`; use `rtk python scripts/validate.py` for the canonical gate.
- Existing user changes in the worktree are preserved and folded into the task that owns them; no reset, checkout, or destructive cleanup is permitted.

---

## File responsibility map

- `tools/search_trial_ledger_history.py`: create, seal, and verify portable evidence; retain live capture/check behavior.
- `tests/tools/test_search_trial_ledger_history.py`: API, CLI, tamper, crash-window, and data-minimization tests.
- `services/governance/top_tier_assessment.py`: read-only repository verifier; never opens the ignored live registry for maturity assessment.
- `tests/governance/test_top_tier_assessment.py`: maturity semantics and fresh-checkout behavior.
- `config/top_tier_requirements.json`: declare runtime label, portable snapshot, and seal paths.
- `.gitattributes`: new file marking only the tracked evidence SQLite file as binary.
- `evidence/alpha_trial_ledger_history.json`: existing two-record append-only history.
- `evidence/alpha_trial_ledger_snapshot.db`: generated five-table portable snapshot.
- `evidence/alpha_trial_ledger_snapshot_seal.json`: generated strict cryptographic binding.
- `evidence/top_tier_assessment.json` and `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`: generated maturity result.
- `docs/engineering/specs/2026-07-17-paper-to-quantos-gap-analysis.md`: honest paper comparison after the portable seal exists.

---

### Task 1: Portable snapshot and seal API

**Files:**
- Modify: `tools/search_trial_ledger_history.py:25-73,745-936`
- Modify: `tests/tools/test_search_trial_ledger_history.py:1-127,718-766`

**Interfaces:**
- Consumes: existing `check_history`, `_read_stable_ledger`, `_exclusive_output_lock`, and `_atomic_publish`.
- Produces:
  - `DEFAULT_SNAPSHOT_PATH = "evidence/alpha_trial_ledger_snapshot.db"`
  - `DEFAULT_SEAL_PATH = "evidence/alpha_trial_ledger_snapshot_seal.json"`
  - `seal_snapshot(*, ledger_path, history_path, snapshot_path, seal_path, ledger_path_label, history_path_label, snapshot_path_label, sealed_at_ms=None) -> dict[str, Any]`
  - `check_snapshot(*, history_path, snapshot_path, seal_path, ledger_path_label, history_path_label, snapshot_path_label) -> dict[str, Any]`

- [ ] **Step 1: Write the failing happy-path and minimization tests**

Add imports for `hashlib` if absent, then add these tests to `tests/tools/test_search_trial_ledger_history.py`:

```python
def _capture_two_sessions(ledger: Path, output: Path, label: str) -> None:
    _create_ledger(ledger)
    history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )
    _append_session(ledger, 2, recorded_at=200)
    history.capture_history(
        session_id="real-session-2",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=300,
    )


def test_seal_snapshot_is_portable_and_data_minimized(tmp_path: Path) -> None:
    ledger, output, label = _paths(tmp_path)
    snapshot = tmp_path / "evidence" / "alpha_trial_ledger_snapshot.db"
    seal = tmp_path / "evidence" / "alpha_trial_ledger_snapshot_seal.json"
    _capture_two_sessions(ledger, output, label)

    document = history.seal_snapshot(
        ledger_path=ledger,
        history_path=output,
        snapshot_path=snapshot,
        seal_path=seal,
        ledger_path_label=label,
        history_path_label="evidence/alpha_trial_ledger_history.json",
        snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
        sealed_at_ms=400,
    )
    ledger.unlink()
    verified = history.check_snapshot(
        history_path=output,
        snapshot_path=snapshot,
        seal_path=seal,
        ledger_path_label=label,
        history_path_label="evidence/alpha_trial_ledger_history.json",
        snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
    )

    assert verified == document
    connection = sqlite3.connect(snapshot)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    connection.close()
    assert tables == {
        "sqlite_sequence",
        "trials",
        "evaluations",
        "search_attempt_events",
        "search_candidates",
        "search_evaluations",
    }
    assert document["record_count"] == 2
    assert document["trial_count"] == 2
    assert document["evaluation_count"] == 2


def test_portable_snapshot_does_not_change_when_live_ledger_advances(
    tmp_path: Path,
) -> None:
    ledger, output, label = _paths(tmp_path)
    snapshot = tmp_path / "evidence" / "alpha_trial_ledger_snapshot.db"
    seal = tmp_path / "evidence" / "alpha_trial_ledger_snapshot_seal.json"
    _capture_two_sessions(ledger, output, label)
    history.seal_snapshot(
        ledger_path=ledger,
        history_path=output,
        snapshot_path=snapshot,
        seal_path=seal,
        ledger_path_label=label,
        history_path_label="evidence/alpha_trial_ledger_history.json",
        snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
        sealed_at_ms=400,
    )
    before = snapshot.read_bytes()

    _append_session(ledger, 3, recorded_at=500)

    assert snapshot.read_bytes() == before
    assert history.check_snapshot(
        history_path=output,
        snapshot_path=snapshot,
        seal_path=seal,
        ledger_path_label=label,
        history_path_label="evidence/alpha_trial_ledger_history.json",
        snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
    )["record_count"] == 2
    with pytest.raises(history.TrialLedgerHistoryError, match="history does not match"):
        history.check_history(
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
        )
```

- [ ] **Step 2: Run the new tests and verify the API is absent**

Run:

```powershell
rtk python -m pytest tests/tools/test_search_trial_ledger_history.py -k "seal_snapshot_is_portable or portable_snapshot_does_not_change" -q
```

Expected: FAIL with `AttributeError: module 'tools.search_trial_ledger_history' has no attribute 'seal_snapshot'`.

- [ ] **Step 3: Implement fixed-schema export and strict seal verification**

Add these constants near the current defaults in `tools/search_trial_ledger_history.py`:

```python
DEFAULT_SNAPSHOT_PATH = "evidence/alpha_trial_ledger_snapshot.db"
DEFAULT_SEAL_PATH = "evidence/alpha_trial_ledger_snapshot_seal.json"
PORTABLE_SEAL_MODE = "ALPHA_TRIAL_LEDGER_PORTABLE_SEAL"
PORTABLE_SEAL_SCHEMA_VERSION = 1
_PORTABLE_APPLICATION_TABLES = {
    "trials",
    "evaluations",
    "search_attempt_events",
    "search_candidates",
    "search_evaluations",
}
_PORTABLE_ALLOWED_TABLES = _PORTABLE_APPLICATION_TABLES | {"sqlite_sequence"}
_SEAL_FIELDS = {
    "schema_version",
    "mode",
    "ledger_path",
    "history_path",
    "snapshot_path",
    "history_sha256",
    "snapshot_sha256",
    "history_head_hash",
    "record_count",
    "trial_count",
    "evaluation_count",
    "sealed_at_ms",
}
```

Add this complete fixed schema; do not import mutable runtime schema strings:

```python
_PORTABLE_SCHEMA = """
CREATE TABLE trials (
    config_hash TEXT PRIMARY KEY,
    first_seen_ts INTEGER NOT NULL
);
CREATE TABLE evaluations (
    run_id TEXT PRIMARY KEY,
    alpha_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    recorded_ts INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    evaluation_json TEXT NOT NULL
);
CREATE TABLE search_attempt_events (
    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT NOT NULL,
    search_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('STARTED', 'FAILED', 'COMPLETED')),
    recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),
    prev_event_hash TEXT NOT NULL,
    event_hash TEXT UNIQUE NOT NULL
);
CREATE INDEX search_attempt_events_by_search
ON search_attempt_events(search_id, event_seq);
CREATE TABLE search_candidates (
    candidate_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'REGISTERED'),
    registered_at INTEGER NOT NULL CHECK(registered_at >= 0),
    prev_candidate_hash TEXT NOT NULL,
    candidate_hash TEXT UNIQUE NOT NULL,
    UNIQUE(search_id, config_hash)
);
CREATE INDEX search_candidates_by_search
ON search_candidates(search_id, candidate_seq);
CREATE TABLE search_evaluations (
    link_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    run_id TEXT NOT NULL,
    recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),
    evaluation_content_hash TEXT NOT NULL,
    prev_link_hash TEXT NOT NULL,
    link_hash TEXT UNIQUE NOT NULL,
    UNIQUE(search_id, run_id)
);
CREATE INDEX search_evaluations_by_search
ON search_evaluations(search_id, link_seq);
CREATE TRIGGER trials_no_update BEFORE UPDATE ON trials
BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
CREATE TRIGGER evaluations_no_update BEFORE UPDATE ON evaluations
BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
CREATE TRIGGER evaluations_no_delete BEFORE DELETE ON evaluations
BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
CREATE TRIGGER search_attempt_events_no_update BEFORE UPDATE ON search_attempt_events
BEGIN SELECT RAISE(ABORT, 'search_attempt_events is append-only'); END;
CREATE TRIGGER search_attempt_events_no_delete BEFORE DELETE ON search_attempt_events
BEGIN SELECT RAISE(ABORT, 'search_attempt_events is append-only'); END;
CREATE TRIGGER search_candidates_no_update BEFORE UPDATE ON search_candidates
BEGIN SELECT RAISE(ABORT, 'search_candidates is append-only'); END;
CREATE TRIGGER search_candidates_no_delete BEFORE DELETE ON search_candidates
BEGIN SELECT RAISE(ABORT, 'search_candidates is append-only'); END;
CREATE TRIGGER search_evaluations_no_update BEFORE UPDATE ON search_evaluations
BEGIN SELECT RAISE(ABORT, 'search_evaluations is append-only'); END;
CREATE TRIGGER search_evaluations_no_delete BEFORE DELETE ON search_evaluations
BEGIN SELECT RAISE(ABORT, 'search_evaluations is append-only'); END;
"""
```

Add the exact copy specification:

```python
_PORTABLE_COPY_COLUMNS = {
    "trials": ("config_hash", "first_seen_ts"),
    "evaluations": (
        "run_id", "alpha_id", "dataset_version", "recorded_ts", "verdict",
        "content_hash", "evaluation_json",
    ),
    "search_attempt_events": (
        "event_seq", "search_id", "search_kind", "source", "dataset_version",
        "status", "recorded_at", "prev_event_hash", "event_hash",
    ),
    "search_candidates": (
        "candidate_seq", "search_id", "config_hash", "status",
        "registered_at", "prev_candidate_hash", "candidate_hash",
    ),
    "search_evaluations": (
        "link_seq", "search_id", "config_hash", "run_id", "recorded_at",
        "evaluation_content_hash", "prev_link_hash", "link_hash",
    ),
}
```

Implement these helpers and public functions below `check_history`:

```python
def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _portable_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _build_portable_snapshot(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.execute("BEGIN")
        destination_connection.executescript(_PORTABLE_SCHEMA)
        for table, columns in _PORTABLE_COPY_COLUMNS.items():
            column_sql = ",".join(columns)
            rows = source_connection.execute(
                f"SELECT {column_sql} FROM {table} ORDER BY 1"
            ).fetchall()
            placeholders = ",".join("?" for _ in columns)
            destination_connection.executemany(
                f"INSERT INTO {table}({column_sql}) VALUES ({placeholders})",
                rows,
            )
        destination_connection.execute(f"PRAGMA user_version={LEDGER_CONTRACT_VERSION}")
        destination_connection.commit()
        integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok" or _portable_tables(destination_connection) != _PORTABLE_ALLOWED_TABLES:
            raise TrialLedgerHistoryError("portable snapshot contract failed")
    except sqlite3.DatabaseError as exc:
        raise TrialLedgerHistoryError(f"portable snapshot cannot be built: {exc}") from exc
    finally:
        source_connection.close()
        destination_connection.close()


def _validate_seal_document(
    document: Any,
    *,
    ledger_path_label: str,
    history_path_label: str,
    snapshot_path_label: str,
) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != _SEAL_FIELDS:
        raise TrialLedgerHistoryError("portable seal fields are invalid")
    expected = {
        "schema_version": PORTABLE_SEAL_SCHEMA_VERSION,
        "mode": PORTABLE_SEAL_MODE,
        "ledger_path": ledger_path_label,
        "history_path": history_path_label,
        "snapshot_path": snapshot_path_label,
    }
    if any(document.get(key) != value for key, value in expected.items()):
        raise TrialLedgerHistoryError("portable seal contract is invalid")
    if any(_DIGEST.fullmatch(document.get(key, "")) is None for key in (
        "history_sha256", "snapshot_sha256", "history_head_hash"
    )):
        raise TrialLedgerHistoryError("portable seal digest is invalid")
    for key in ("record_count", "trial_count", "evaluation_count", "sealed_at_ms"):
        if type(document.get(key)) is not int or document[key] < 0:
            raise TrialLedgerHistoryError(f"portable seal {key} is invalid")
    return document


def check_snapshot(
    *,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    seal_path: str | Path = DEFAULT_SEAL_PATH,
    ledger_path_label: str = DEFAULT_LEDGER_PATH,
    history_path_label: str = DEFAULT_HISTORY_PATH,
    snapshot_path_label: str = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    history_file, snapshot_file, seal_file = map(Path, (history_path, snapshot_path, seal_path))
    try:
        seal = _validate_seal_document(
            _load_json(seal_file.read_bytes()),
            ledger_path_label=ledger_path_label,
            history_path_label=history_path_label,
            snapshot_path_label=snapshot_path_label,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise TrialLedgerHistoryError(f"portable seal cannot be read: {exc}") from exc
    try:
        if _file_sha256(history_file) != seal["history_sha256"]:
            raise TrialLedgerHistoryError("portable seal history hash mismatch")
        if _file_sha256(snapshot_file) != seal["snapshot_sha256"]:
            raise TrialLedgerHistoryError("portable seal snapshot hash mismatch")
    except OSError as exc:
        raise TrialLedgerHistoryError(f"portable bundle file is unavailable: {exc}") from exc
    history_document = check_history(
        ledger_path=snapshot_file,
        history_path=history_file,
        ledger_path_label=ledger_path_label,
    )
    final = history_document["records"][-1]
    connection = sqlite3.connect(snapshot_file.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if _portable_tables(connection) != _PORTABLE_ALLOWED_TABLES:
            raise TrialLedgerHistoryError("portable snapshot table allowlist failed")
    finally:
        connection.close()
    expected_values = {
        "history_head_hash": final["record_hash"],
        "record_count": len(history_document["records"]),
        "trial_count": len(final["trials"]),
        "evaluation_count": len(final["evaluations"]),
    }
    if any(seal[key] != value for key, value in expected_values.items()):
        raise TrialLedgerHistoryError("portable seal counts or history head mismatch")
    if seal["sealed_at_ms"] < final["recorded_at_ms"]:
        raise TrialLedgerHistoryError("portable seal predates final history record")
    return seal
```

Implement the publisher exactly around the existing lock and atomic JSON writer:

```python
def seal_snapshot(
    *,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    seal_path: str | Path = DEFAULT_SEAL_PATH,
    ledger_path_label: str = DEFAULT_LEDGER_PATH,
    history_path_label: str = DEFAULT_HISTORY_PATH,
    snapshot_path_label: str = DEFAULT_SNAPSHOT_PATH,
    sealed_at_ms: int | None = None,
) -> dict[str, Any]:
    ledger_file = Path(ledger_path)
    history_file = Path(history_path)
    snapshot_file = Path(snapshot_path)
    seal_file = Path(seal_path)
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    seal_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_snapshot: Path | None = None
    with _exclusive_output_lock(history_file):
        history_bytes = _history_bytes(history_file)
        if history_bytes is None:
            raise TrialLedgerHistoryError("history does not exist for portable sealing")
        document = check_history(
            ledger_path=ledger_file,
            history_path=history_file,
            ledger_path_label=ledger_path_label,
        )
        live_snapshot = _read_stable_ledger(ledger_file)
        final = document["records"][-1]
        timestamp = time.time_ns() // 1_000_000 if sealed_at_ms is None else sealed_at_ms
        if type(timestamp) is not int or timestamp < final["recorded_at_ms"]:
            raise TrialLedgerHistoryError("portable seal predates final history record")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{snapshot_file.name}.",
            suffix=".tmp",
            dir=snapshot_file.parent,
        )
        os.close(descriptor)
        temporary_snapshot = Path(temporary_name)
        try:
            _build_portable_snapshot(ledger_file, temporary_snapshot)
            check_history(
                ledger_path=temporary_snapshot,
                history_path=history_file,
                ledger_path_label=ledger_path_label,
            )
            if _history_bytes(history_file) != history_bytes:
                raise TrialLedgerHistoryError("history changed during portable sealing")
            if _read_stable_ledger(ledger_file) != live_snapshot:
                raise TrialLedgerHistoryError("live ledger changed during portable sealing")
            seal = {
                "schema_version": PORTABLE_SEAL_SCHEMA_VERSION,
                "mode": PORTABLE_SEAL_MODE,
                "ledger_path": ledger_path_label,
                "history_path": history_path_label,
                "snapshot_path": snapshot_path_label,
                "history_sha256": hashlib.sha256(history_bytes).hexdigest(),
                "snapshot_sha256": _file_sha256(temporary_snapshot),
                "history_head_hash": final["record_hash"],
                "record_count": len(document["records"]),
                "trial_count": len(final["trials"]),
                "evaluation_count": len(final["evaluations"]),
                "sealed_at_ms": timestamp,
            }
            _validate_seal_document(
                seal,
                ledger_path_label=ledger_path_label,
                history_path_label=history_path_label,
                snapshot_path_label=snapshot_path_label,
            )
            previous_seal = _history_bytes(seal_file)
            os.replace(temporary_snapshot, snapshot_file)

            def verify_inputs_unchanged() -> None:
                if _history_bytes(history_file) != history_bytes:
                    raise TrialLedgerHistoryError("history changed before seal publish")
                if _read_stable_ledger(ledger_file) != live_snapshot:
                    raise TrialLedgerHistoryError("live ledger changed before seal publish")
                if _file_sha256(snapshot_file) != seal["snapshot_sha256"]:
                    raise TrialLedgerHistoryError("snapshot changed before seal publish")

            _atomic_publish(
                seal_file,
                seal,
                expected_previous=previous_seal,
                before_replace=verify_inputs_unchanged,
            )
            return check_snapshot(
                history_path=history_file,
                snapshot_path=snapshot_file,
                seal_path=seal_file,
                ledger_path_label=ledger_path_label,
                history_path_label=history_path_label,
                snapshot_path_label=snapshot_path_label,
            )
        finally:
            if temporary_snapshot is not None:
                try:
                    temporary_snapshot.unlink()
                except FileNotFoundError:
                    pass
```

This order may leave a new snapshot with an old seal after a process failure,
but the hashes then fail closed. It never deletes an existing seal or snapshot.

- [ ] **Step 4: Run the focused tests**

Run:

```powershell
rtk python -m pytest tests/tools/test_search_trial_ledger_history.py -k "seal_snapshot_is_portable or portable_snapshot_does_not_change" -q
```

Expected: `2 passed`.

- [ ] **Step 5: Run the complete tool suite and commit**

Run:

```powershell
rtk python -m pytest tests/tools/test_search_trial_ledger_history.py -q
rtk git add -- tools/search_trial_ledger_history.py tests/tools/test_search_trial_ledger_history.py
rtk git commit -m "feat(evidence): add portable trial-ledger seal"
```

Expected: all tests in the file pass; commit contains only the tool API and its tests.

---

### Task 2: Fail-closed CLI and tamper matrix

**Files:**
- Modify: `tools/search_trial_ledger_history.py:939-990`
- Modify: `tests/tools/test_search_trial_ledger_history.py:767-end`

**Interfaces:**
- Consumes: `seal_snapshot` and `check_snapshot` from Task 1.
- Produces: mutually exclusive CLI modes `--seal-snapshot` and `--check-snapshot`; existing `--check`, `--inspect-ledger`, and `--session-id` behavior remains compatible.

- [ ] **Step 1: Write failing CLI and tamper tests**

Add tests that call `history.main` with explicit argument lists:

```python
def test_cli_seals_and_checks_without_live_ledger(tmp_path: Path, monkeypatch) -> None:
    ledger, output, label = _paths(tmp_path)
    snapshot = tmp_path / "evidence" / "alpha_trial_ledger_snapshot.db"
    seal = tmp_path / "evidence" / "alpha_trial_ledger_snapshot_seal.json"
    _capture_two_sessions(ledger, output, label)
    monkeypatch.chdir(tmp_path)

    assert history.main(["--seal-snapshot"]) == 0
    ledger.unlink()
    assert history.main(["--check-snapshot"]) == 0
    with pytest.raises(SystemExit) as conflict:
        history.main(["--check", "--check-snapshot"])
    assert conflict.value.code == 2


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("history_hash", "history hash mismatch"),
        ("snapshot_hash", "snapshot hash mismatch"),
        ("count", "counts or history head mismatch"),
        ("extra_table", "table allowlist"),
        ("missing_trigger", "append-only trigger"),
    ],
)
def test_portable_bundle_tampering_fails_closed(
    tmp_path: Path,
    mutation: str,
    match: str,
) -> None:
    ledger, output, label = _paths(tmp_path)
    snapshot = tmp_path / "evidence" / "alpha_trial_ledger_snapshot.db"
    seal = tmp_path / "evidence" / "alpha_trial_ledger_snapshot_seal.json"
    _capture_two_sessions(ledger, output, label)
    history.seal_snapshot(
        ledger_path=ledger,
        history_path=output,
        snapshot_path=snapshot,
        seal_path=seal,
        ledger_path_label=label,
        history_path_label="evidence/alpha_trial_ledger_history.json",
        snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
        sealed_at_ms=400,
    )
    document = json.loads(seal.read_text(encoding="utf-8"))
    if mutation == "history_hash":
        document["history_sha256"] = "0" * 64
        seal.write_text(json.dumps(document), encoding="utf-8")
    elif mutation == "snapshot_hash":
        document["snapshot_sha256"] = "0" * 64
        seal.write_text(json.dumps(document), encoding="utf-8")
    elif mutation == "count":
        document["trial_count"] += 1
        seal.write_text(json.dumps(document), encoding="utf-8")
    else:
        connection = sqlite3.connect(snapshot)
        if mutation == "extra_table":
            connection.execute("CREATE TABLE forbidden_runtime_state(value TEXT)")
        else:
            connection.execute("DROP TRIGGER evaluations_no_delete")
        connection.commit()
        connection.close()
        document["snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        seal.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(history.TrialLedgerHistoryError, match=match):
        history.check_snapshot(
            history_path=output,
            snapshot_path=snapshot,
            seal_path=seal,
            ledger_path_label=label,
            history_path_label="evidence/alpha_trial_ledger_history.json",
            snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
        )
```

Add this publication-failure test:

```python
def test_seal_publish_failure_leaves_detectable_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, output, label = _paths(tmp_path)
    snapshot = tmp_path / "evidence" / "alpha_trial_ledger_snapshot.db"
    seal = tmp_path / "evidence" / "alpha_trial_ledger_snapshot_seal.json"
    _capture_two_sessions(ledger, output, label)
    history.seal_snapshot(
        ledger_path=ledger,
        history_path=output,
        snapshot_path=snapshot,
        seal_path=seal,
        ledger_path_label=label,
        history_path_label="evidence/alpha_trial_ledger_history.json",
        snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
        sealed_at_ms=400,
    )
    old_seal = seal.read_bytes()
    old_snapshot = snapshot.read_bytes()
    _append_session(ledger, 3, recorded_at=500)
    history.capture_history(
        session_id="real-session-3",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=600,
    )
    original = history._atomic_publish

    def reject_seal(path, document, *, expected_previous, before_replace):
        if Path(path) == seal:
            before_replace()
            raise OSError("simulated seal replace failure")
        return original(
            path,
            document,
            expected_previous=expected_previous,
            before_replace=before_replace,
        )

    monkeypatch.setattr(history, "_atomic_publish", reject_seal)
    with pytest.raises(OSError, match="simulated seal replace failure"):
        history.seal_snapshot(
            ledger_path=ledger,
            history_path=output,
            snapshot_path=snapshot,
            seal_path=seal,
            ledger_path_label=label,
            history_path_label="evidence/alpha_trial_ledger_history.json",
            snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
            sealed_at_ms=700,
        )

    assert seal.read_bytes() == old_seal
    assert snapshot.exists()
    assert old_snapshot != b""
    with pytest.raises(history.TrialLedgerHistoryError, match="hash mismatch"):
        history.check_snapshot(
            history_path=output,
            snapshot_path=snapshot,
            seal_path=seal,
            ledger_path_label=label,
            history_path_label="evidence/alpha_trial_ledger_history.json",
            snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
        )
```

- [ ] **Step 2: Run the tests and verify CLI modes are rejected**

Run:

```powershell
rtk python -m pytest tests/tools/test_search_trial_ledger_history.py -k "cli_seals or portable_bundle_tampering or publication_failure" -q
```

Expected: FAIL because the parser does not recognize the two new flags and the full tamper matrix is not yet enforced.

- [ ] **Step 3: Add mutually exclusive CLI dispatch**

Replace `_parser()` and the mode validation at the top of `main()` with:

```python
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append, seal, or verify the production alpha trial-ledger history"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="verify against the live authority")
    modes.add_argument("--inspect-ledger", action="store_true", help="inspect live capture readiness")
    modes.add_argument("--seal-snapshot", action="store_true", help="publish portable snapshot evidence")
    modes.add_argument("--check-snapshot", action="store_true", help="verify portable evidence only")
    parser.add_argument("--session-id", help="completed search ID to retain")
    return parser
```

In `main()`, reject a `session_id` combined with any mode and require it when no
mode is selected. Dispatch in this exact order:

```python
if args.inspect_ledger:
    result = inspect_ledger()
elif args.check:
    result = check_history(ledger_path_label=DEFAULT_LEDGER_PATH)
elif args.seal_snapshot:
    result = seal_snapshot(
        ledger_path_label=DEFAULT_LEDGER_PATH,
        history_path_label=DEFAULT_HISTORY_PATH,
        snapshot_path_label=DEFAULT_SNAPSHOT_PATH,
    )
elif args.check_snapshot:
    result = check_snapshot(
        ledger_path_label=DEFAULT_LEDGER_PATH,
        history_path_label=DEFAULT_HISTORY_PATH,
        snapshot_path_label=DEFAULT_SNAPSHOT_PATH,
    )
else:
    result = capture_history(
        session_id=args.session_id,
        ledger_path_label=DEFAULT_LEDGER_PATH,
    )
```

Keep `TrialLedgerHistoryError` mapped to exit code 1 and argument conflicts mapped
to exit code 2. Print only counts/status; never print evaluation payloads.

- [ ] **Step 4: Run the tamper, CLI, and legacy regression tests**

Run:

```powershell
rtk python -m pytest tests/tools/test_search_trial_ledger_history.py -q
```

Expected: all existing and new tests pass.

- [ ] **Step 5: Commit the fail-closed CLI**

Run:

```powershell
rtk git add -- tools/search_trial_ledger_history.py tests/tools/test_search_trial_ledger_history.py
rtk git commit -m "test(evidence): harden portable trial-ledger checks"
```

---

### Task 3: Repository top-tier checker uses portable evidence

**Files:**
- Modify: `config/top_tier_requirements.json:64-90`
- Modify: `services/governance/top_tier_assessment.py:248-1250`
- Modify: `tests/governance/test_top_tier_assessment.py:1940-2420`

**Interfaces:**
- Consumes: history schema v2, portable snapshot schema v2, and seal schema v1.
- Produces: deterministic `alpha_trial_ledger_evidence` assessment with new checker arguments `snapshot_path` and `seal_path`.

- [ ] **Step 1: Change tests first to require portable paths and no live database**

Update `_write_alpha_trial_wiring()` to return:

```python
"snapshot_path": "evidence/alpha_trial_ledger_snapshot.db",
"seal_path": "evidence/alpha_trial_ledger_snapshot_seal.json",
```

Update `_write_valid_alpha_trial_history()` after the second capture with:

```python
search_trial_ledger_history.seal_snapshot(
    ledger_path=ledger_path,
    history_path=evidence_path,
    snapshot_path=tmp_path / checker_args["snapshot_path"],
    seal_path=tmp_path / checker_args["seal_path"],
    ledger_path_label=checker_args["ledger_path"],
    history_path_label=checker_args["evidence_path"],
    snapshot_path_label=checker_args["snapshot_path"],
    sealed_at_ms=400,
)
return evidence_path
```

Then replace the current live-store PASS test with:

```python
def test_alpha_trial_ledger_checker_passes_from_portable_bundle_without_live_store(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    (tmp_path / checker_args["ledger_path"]).unlink()
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PASS", result["detail"]
    assert result["current_maturity"] == "M4"
    assert checker_args["snapshot_path"] in result["evidence"]
    assert checker_args["seal_path"] in result["evidence"]
```

Add the failure-semantics tests:

```python
@pytest.mark.parametrize("missing_key", ["snapshot_path", "seal_path"])
def test_alpha_trial_ledger_checker_is_partial_without_portable_bundle_file(
    tmp_path,
    missing_key,
):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    (tmp_path / checker_args[missing_key]).unlink()
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "PARTIAL"
    assert result["current_maturity"] == "M3"
    assert "portable snapshot evidence is unavailable" in result["detail"]


def test_alpha_trial_ledger_checker_rejects_bad_portable_seal_hash(tmp_path):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    seal_path = tmp_path / checker_args["seal_path"]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["snapshot_sha256"] = "0" * 64
    seal_path.write_text(json.dumps(seal), encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert result["current_maturity"] == "M2"
    assert "portable seal failed" in result["detail"]


@pytest.mark.parametrize(
    "mutation,detail",
    [
        ("extra_table", "ledger_tables"),
        ("missing_trigger", "evaluations_no_delete_trigger"),
    ],
)
def test_alpha_trial_ledger_checker_rejects_nonminimal_or_mutable_snapshot(
    tmp_path,
    mutation,
    detail,
):
    checker_args = _write_alpha_trial_wiring(tmp_path)
    _write_valid_alpha_trial_history(tmp_path, checker_args)
    snapshot_path = tmp_path / checker_args["snapshot_path"]
    connection = sqlite3.connect(snapshot_path)
    if mutation == "extra_table":
        connection.execute("CREATE TABLE forbidden_runtime_state(value TEXT)")
    else:
        connection.execute("DROP TRIGGER evaluations_no_delete")
    connection.commit()
    connection.close()
    seal_path = tmp_path / checker_args["seal_path"]
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["snapshot_sha256"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    seal_path.write_text(json.dumps(seal), encoding="utf-8")
    registry = _write_registry(tmp_path, [_alpha_trial_requirement(checker_args)])

    result = assess_repository(tmp_path, registry)["requirements"][0]

    assert result["status"] == "FAILED"
    assert result["current_maturity"] == "M2"
    assert detail in result["detail"]
```

All database mutations target `checker_args["snapshot_path"]`, never the ignored
live path.

Update the committed requirement assertion to require both new paths and rename
the root test to `test_committed_alpha_trial_requirement_passes_with_portable_snapshot`.

- [ ] **Step 2: Run the governance tests and verify the checker still depends on the live path**

Run:

```powershell
rtk python -m pytest tests/governance/test_top_tier_assessment.py -k "alpha_trial" -q
```

Expected: new tests fail because `snapshot_path`/`seal_path` are unrecognized and removing the live database downgrades the result.

- [ ] **Step 3: Extend the config and checker argument contract**

Add to the `ALPHA.TRIAL.GLOBAL_LEDGER.checker_args` object in
`config/top_tier_requirements.json`:

```json
"snapshot_path": "evidence/alpha_trial_ledger_snapshot.db",
"seal_path": "evidence/alpha_trial_ledger_snapshot_seal.json"
```

Add both names to `path_fields` in `_alpha_trial_ledger_evidence`. Keep
`ledger_path` as the label verified in history and factory-helper AST checks.

After the existing history record validation and before opening SQLite, add:

```python
snapshot_path = repo_root / paths["snapshot_path"]
seal_path = repo_root / paths["seal_path"]
if not snapshot_path.is_file() or not seal_path.is_file():
    return _result(
        "PARTIAL",
        "M3",
        evidence,
        "versioned trial-ledger history is valid, but portable snapshot evidence is unavailable",
    )
evidence.extend([paths["snapshot_path"], paths["seal_path"]])
```

Add the file hash helper near `_alpha_trial_search_hash`:

```python
def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
```

Parse and validate the seal before opening SQLite:

```python
seal_failures: list[str] = []
try:
    seal = load_json_without_duplicate_keys(seal_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, ValueError) as exc:
    return _result(
        "FAILED",
        "M2",
        evidence,
        f"alpha trial-ledger portable seal failed: {exc}",
    )
seal_fields = {
    "schema_version", "mode", "ledger_path", "history_path", "snapshot_path",
    "history_sha256", "snapshot_sha256", "history_head_hash", "record_count",
    "trial_count", "evaluation_count", "sealed_at_ms",
}
if not isinstance(seal, dict) or set(seal) != seal_fields:
    return _result(
        "FAILED",
        "M2",
        evidence,
        "alpha trial-ledger portable seal failed: seal_fields",
    )
expected_seal_header = {
    "schema_version": 1,
    "mode": "ALPHA_TRIAL_LEDGER_PORTABLE_SEAL",
    "ledger_path": paths["ledger_path"],
    "history_path": paths["evidence_path"],
    "snapshot_path": paths["snapshot_path"],
}
if any(seal.get(key) != value for key, value in expected_seal_header.items()):
    seal_failures.append("seal_header")
for key in ("history_sha256", "snapshot_sha256", "history_head_hash"):
    if not isinstance(seal.get(key), str) or digest_pattern.fullmatch(seal[key]) is None:
        seal_failures.append(key)
for key in ("record_count", "trial_count", "evaluation_count", "sealed_at_ms"):
    if type(seal.get(key)) is not int or seal[key] < 0:
        seal_failures.append(key)
if not seal_failures:
    if _sha256_file(evidence_path) != seal["history_sha256"]:
        seal_failures.append("history_sha256")
    if _sha256_file(snapshot_path) != seal["snapshot_sha256"]:
        seal_failures.append("snapshot_sha256")
    expected_counts = {
        "history_head_hash": previous_record_hash,
        "record_count": len(records),
        "trial_count": len(final_trials),
        "evaluation_count": len(final_evaluations),
    }
    if any(seal[key] != value for key, value in expected_counts.items()):
        seal_failures.append("seal_counts_or_head")
    if seal["sealed_at_ms"] < records[-1]["recorded_at_ms"]:
        seal_failures.append("sealed_at_ms")
if seal_failures:
    return _result(
        "FAILED",
        "M2",
        evidence,
        "alpha trial-ledger portable seal failed: "
        + ", ".join(sorted(set(seal_failures))),
    )
```

Set the physical database path used by the existing SQLite validation block to:

```python
ledger_path = snapshot_path
```

After reading `tables`, require exact equality with:

```python
{
    "sqlite_sequence",
    "trials",
    "evaluations",
    "search_attempt_events",
    "search_candidates",
    "search_evaluations",
}
```

Remove `paths["ledger_path"]` from the returned evidence list. Change the PASS
detail to state that a portable sealed snapshot, two-session monotonic history,
exact completed-search bindings, and append-only database guards were verified.

- [ ] **Step 4: Run all top-tier assessment tests**

Run:

```powershell
rtk python -m pytest tests/governance/test_top_tier_assessment.py tests/governance/test_top_tier_boundary.py -q
```

Expected: all tests pass, including the no-live-registry case.

- [ ] **Step 5: Commit the repository verifier**

Run:

```powershell
rtk git add -- config/top_tier_requirements.json services/governance/top_tier_assessment.py tests/governance/test_top_tier_assessment.py
rtk git commit -m "feat(governance): verify portable trial-ledger evidence"
```

---

### Task 4: Generate and commit the real portable evidence bundle

**Files:**
- Create: `.gitattributes`
- Add: `evidence/alpha_trial_ledger_history.json`
- Add: `evidence/alpha_trial_ledger_snapshot.db`
- Add: `evidence/alpha_trial_ledger_snapshot_seal.json`
- Modify: `evidence/top_tier_assessment.json`
- Modify: `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`
- Modify: `tests/governance/test_top_tier_assessment.py`

**Interfaces:**
- Consumes: the real ignored 40-trial/10-evaluation live authority and Tasks 1-3.
- Produces: a tracked portable evidence bundle and stable 15/22 assessment.

- [ ] **Step 1: Verify the live authority before publication**

Run:

```powershell
rtk python -m tools.search_trial_ledger_history --check
rtk python -m tools.search_trial_ledger_history --inspect-ledger
```

Expected: history has two verified records; the live authority has 40 trials and 10 evaluations; the official `tools.crypto_factory_run` session remains completed and exactly bound.

- [ ] **Step 2: Publish and verify the minimized portable bundle**

Run:

```powershell
rtk python -m tools.search_trial_ledger_history --seal-snapshot
rtk python -m tools.search_trial_ledger_history --check-snapshot
```

Expected: both commands exit 0; the seal reports 2 records, 40 trials, and 10 evaluations.

- [ ] **Step 3: Prove the snapshot surface and Git treatment**

Append exactly this line to `.gitattributes` if it is not already present:

```gitattributes
evidence/alpha_trial_ledger_snapshot.db binary
```

Add the committed-evidence test:

```python
def test_committed_alpha_trial_snapshot_has_minimal_table_surface():
    snapshot = ROOT / "evidence/alpha_trial_ledger_snapshot.db"
    connection = sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()
    assert tables == {
        "sqlite_sequence",
        "trials",
        "evaluations",
        "search_attempt_events",
        "search_candidates",
        "search_evaluations",
    }
```

Then run:

```powershell
rtk git check-ignore -v data/cache/experiment_registry.db
rtk git check-ignore -q evidence/alpha_trial_ledger_snapshot.db
```

Expected: the runtime database is ignored; the second command exits nonzero
because the evidence snapshot is not ignored.

- [ ] **Step 4: Recompute maturity artifacts and check them**

Run:

```powershell
rtk python scripts/assess_top_tier.py
rtk python scripts/assess_top_tier.py --check
```

Expected: `NOT_TOP_TIER - 15/22 pass`; `ALPHA.TRIAL.GLOBAL_LEDGER` is
`M4/PASS`; three red lines remain.

- [ ] **Step 5: Run the focused portable-evidence regression**

Run:

```powershell
rtk python -m pytest tests/tools/test_search_trial_ledger_history.py tests/governance/test_top_tier_assessment.py tests/governance/test_top_tier_boundary.py -q
```

Expected: all focused tests pass without reading or mutating the live authority.

- [ ] **Step 6: Commit only the portable evidence closure**

Run:

```powershell
rtk git add -- .gitattributes evidence/alpha_trial_ledger_history.json evidence/alpha_trial_ledger_snapshot.db evidence/alpha_trial_ledger_snapshot_seal.json evidence/top_tier_assessment.json docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md tests/governance/test_top_tier_assessment.py
rtk git commit -m "chore(evidence): seal global alpha trial ledger"
```

Before committing, inspect `rtk git diff --cached --stat` and confirm
`data/cache/experiment_registry.db` is absent.

---

### Task 5: Documentation, canonical verification, and independent review

**Files:**
- Modify: `docs/engineering/specs/2026-07-17-paper-to-quantos-gap-analysis.md`
- Modify: `docs/runbooks/l2-research-operations.md`

**Interfaces:**
- Consumes: verified portable evidence and the generated 15/22 assessment.
- Produces: accurate operator instructions and final evidence-backed gap report.

- [ ] **Step 1: Correct the paper-gap wording and operating runbook**

In the paper-gap report, describe the closure as a “two-session portable sealed
authority snapshot with 40 trials and 10 immutable evaluations.” Do not call it
an eternally current live database. Keep the remaining gaps explicit:
natural forward evidence 0/8, container/Trivy supply-chain evidence, PIT
multi-asset data, White/SPA/publication-bias controls, factor/IPCA depth, and
event/NLP.

Add this command sequence to `docs/runbooks/l2-research-operations.md`:

```powershell
rtk python -m tools.search_trial_ledger_history --inspect-ledger
$sessionId = Read-Host 'Paste one CAPTURABLE search_id from the inspection output'
if ([string]::IsNullOrWhiteSpace($sessionId)) { throw 'search_id is required' }
rtk python -m tools.search_trial_ledger_history --session-id $sessionId
rtk python -m tools.search_trial_ledger_history --check
rtk python -m tools.search_trial_ledger_history --seal-snapshot
rtk python -m tools.search_trial_ledger_history --check-snapshot
```

Explain that a capture changes history, a seal publishes repository evidence,
and CI checks the seal while operators check the live registry.

- [ ] **Step 2: Run focused domain and safety verification**

Run:

```powershell
rtk python -m pytest tests/research/test_experiment_store.py tests/research/test_search_trial_provenance.py tests/intelligence/test_registered_candidate_batch.py tests/tools/test_search_trial_ledger_history.py tests/governance/test_experiment_registry_isolation.py tests/governance/test_top_tier_assessment.py tests/governance/test_top_tier_boundary.py -q
rtk python apps/research-console/scripts/run_paper_to_live_governance_check.py
rtk python scripts/assess_top_tier.py --check
```

Expected: all focused tests pass, governance scanner reports 0 violations, and the assessment remains 15/22.

- [ ] **Step 3: Run the canonical gate**

Run:

```powershell
rtk python scripts/validate.py
```

Expected: no new failures. The only accepted failures are the already documented
container-Trivy/reproducibility/supply-chain chain that requires a running Docker
daemon, two current no-cache BuildKit OCI archives, and current Trivy database
evidence. Record the exact pass/fail count rather than claiming a green full gate.

- [ ] **Step 4: Request independent review**

Ask a fresh reviewer to inspect:

```text
Verify that the portable snapshot is data-minimized, the seal binds exact bytes,
fresh checkout does not require data/cache, invalid present evidence fails M2,
missing evidence stays M3, live --check remains independent, and no L2 safety
boundary was weakened. Report P0/P1/P2 findings and do not modify files.
```

Resolve every P0/P1 finding with a failing test before changing implementation,
then rerun Steps 2 and 3.

- [ ] **Step 5: Commit documentation and final corrections**

Run:

```powershell
rtk git add -- docs/engineering/specs/2026-07-17-paper-to-quantos-gap-analysis.md docs/runbooks/l2-research-operations.md
rtk git commit -m "docs(audit): record portable trial-ledger closure"
rtk git status --short --branch
```

Expected: the branch is clean except for intentionally preserved user-owned
changes identified before execution; every implementation/evidence change is in
an auditable task commit.

---

## Plan self-review record

- Spec coverage: Tasks 1-5 cover the artifact contracts, publication flow,
  verification flow, failure semantics, code boundaries, migration, tests, and
  all six acceptance criteria from the approved design.
- Scope: one subsystem only—the portable global alpha trial-ledger evidence
  closure. Container evidence and later P1 research work remain separate plans.
- Interface consistency: Task 1 defines `seal_snapshot`/`check_snapshot`; Tasks
  2-5 consume those exact names. Path labels remain repository-relative while
  physical test paths may be absolute `Path` objects.
- Safety: the plan never commits or creates a production registry, never imports
  network/broker code, and retains the live fail-closed check.
- Portability: the top-tier checker reads only tracked history/snapshot/seal
  artifacts; no test requires the ignored live database.
