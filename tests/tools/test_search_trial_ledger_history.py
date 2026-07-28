from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from quantos.contracts.hashing import stable_hash
from services.research.experiment_registry.store import SqliteExperimentStore
from tools import search_trial_ledger_history as history


def _evaluation_row(
    index: int,
    *,
    recorded_at: int | None = None,
) -> tuple[object, ...]:
    timestamp = index * 10 if recorded_at is None else recorded_at
    dataset_version = f"dataset-v{index}"
    reproducibility_hash = f"repro-{index}"
    verdict = "REQUIRES_HUMAN_REVIEW"
    evaluation = {
        "alpha_id": f"alpha-{index}",
        "catalog_trial_count": index,
        "reproducibility_hash": reproducibility_hash,
        "verdict": verdict,
    }
    run_id = stable_hash(
        {
            "alpha_id": evaluation["alpha_id"],
            "repro": reproducibility_hash,
            "dataset_version": dataset_version,
        }
    )[:16]
    return (
        run_id,
        f"alpha-{index}",
        dataset_version,
        timestamp,
        verdict,
        stable_hash({"evaluation": evaluation, "dataset_version": dataset_version}),
        json.dumps(evaluation, sort_keys=True),
    )


def _append_session(path: Path, index: int, *, recorded_at: int) -> None:
    store = SqliteExperimentStore(str(path), create=False)
    search_id = f"real-session-{index}"
    row = _evaluation_row(index, recorded_at=recorded_at)
    try:
        store.begin_search_attempt(
            search_id=search_id,
            search_kind="TEST_SELECTION_SEARCH",
            source="tests.tools.test_search_trial_ledger_history",
            dataset_version=f"dataset-v{index}",
            recorded_at=recorded_at,
        )
        store.register_search_candidates(
            search_id,
            [str(index) * 64],
            registered_at=recorded_at,
        )
        store.insert_evaluation(
            run_id=str(row[0]),
            alpha_id=str(row[1]),
            dataset_version=str(row[2]),
            recorded_ts=int(row[3]),
            verdict=str(row[4]),
            content_hash=str(row[5]),
            evaluation_json=str(row[6]),
            search_id=search_id,
            config_hash=str(index) * 64,
        )
        store.finish_search_attempt(
            search_id,
            status="COMPLETED",
            recorded_at=recorded_at,
        )
    finally:
        store.close()


def _create_ledger(path: Path, *, count: int = 1) -> None:
    path.parent.mkdir(parents=True)
    SqliteExperimentStore(str(path), create=True).close()
    for index in range(1, count + 1):
        _append_session(path, index, recorded_at=index * 10)


def _append_ledger(path: Path, index: int) -> None:
    _append_session(path, index, recorded_at=index * 100)


def _paths(tmp_path: Path) -> tuple[Path, Path, str]:
    ledger = tmp_path / "data" / "cache" / "experiment_registry.db"
    output = tmp_path / "evidence" / "alpha_trial_ledger_history.json"
    return ledger, output, "data/cache/experiment_registry.db"


def _create_v1_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA user_version = 1;
        CREATE TABLE trials(config_hash TEXT PRIMARY KEY, first_seen_ts INTEGER NOT NULL);
        CREATE TABLE evaluations(
            run_id TEXT PRIMARY KEY,
            alpha_id TEXT NOT NULL,
            dataset_version TEXT NOT NULL,
            recorded_ts INTEGER NOT NULL,
            verdict TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            evaluation_json TEXT NOT NULL
        );
        CREATE TRIGGER trials_no_update BEFORE UPDATE ON trials
        BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
        CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
        BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
        CREATE TRIGGER evaluations_no_update BEFORE UPDATE ON evaluations
        BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
        CREATE TRIGGER evaluations_no_delete BEFORE DELETE ON evaluations
        BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
        """
    )
    connection.execute(
        "INSERT INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
        ("1" * 64, 10),
    )
    connection.execute(
        "INSERT INTO evaluations VALUES (?,?,?,?,?,?,?)",
        _evaluation_row(1),
    )
    connection.commit()
    connection.close()


def test_capture_appends_one_session_and_check_is_read_only(tmp_path: Path) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)

    document = history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )

    assert document == json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 2
    assert document["mode"] == "ALPHA_TRIAL_LEDGER_APPEND_ONLY_HISTORY"
    assert document["ledger_contract_version"] == 2
    assert document["ledger_path"] == label
    assert len(document["records"]) == 1
    assert document["records"][0]["session_id"] == "real-session-1"
    assert document["records"][0]["session_provenance"]["search_kind"] == ("TEST_SELECTION_SEARCH")
    assert len(document["records"][0]["session_provenance"]["evaluation_links"]) == 1
    assert document["records"][0]["session_provenance"]["evaluation_links"][0]["config_hash"] == "1" * 64
    before = output.read_bytes()
    assert (
        history.check_history(
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
        )
        == document
    )
    assert output.read_bytes() == before
    assert not output.with_name(f"{output.name}.lock").exists()


def test_inspect_lists_only_exactly_bound_completed_sessions_read_only(
    tmp_path: Path,
) -> None:
    ledger, output, _label = _paths(tmp_path)
    _create_ledger(ledger)
    before = ledger.read_bytes()

    inspection = history.inspect_ledger(ledger_path=ledger)

    assert inspection["status"] == "verified"
    assert inspection["ledger_contract_version"] >= history.LEDGER_CONTRACT_VERSION
    assert inspection["capture_ready"] is True
    assert inspection["attempts"] == [
        {
            "search_id": "real-session-1",
            "search_kind": "TEST_SELECTION_SEARCH",
            "source": "tests.tools.test_search_trial_ledger_history",
            "dataset_version": "dataset-v1",
            "started_at": 10,
            "completed_at": 10,
            "candidate_count": 1,
            "exact_evaluation_count": 1,
            "capture_status": "CAPTURABLE",
        }
    ]
    assert ledger.read_bytes() == before
    assert not output.exists()


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


def test_inspect_accepts_global_chain_order_with_per_session_activity_clocks(
    tmp_path: Path,
) -> None:
    ledger, _output, _label = _paths(tmp_path)
    ledger.parent.mkdir(parents=True)
    store = SqliteExperimentStore(str(ledger), create=True)
    try:
        store.begin_search_attempt(
            search_id="session-a",
            search_kind="TEST_SELECTION_SEARCH",
            source="tests.tools.test_search_trial_ledger_history",
            dataset_version="dataset-v1",
            recorded_at=100,
        )
        store.begin_search_attempt(
            search_id="session-b",
            search_kind="TEST_SELECTION_SEARCH",
            source="tests.tools.test_search_trial_ledger_history",
            dataset_version="dataset-v2",
            recorded_at=200,
        )
        for search_id, index, timestamp in (
            ("session-a", 1, 150),
            ("session-b", 2, 250),
        ):
            row = _evaluation_row(index, recorded_at=timestamp)
            store.register_search_candidates(
                search_id,
                [str(index) * 64],
                registered_at=timestamp,
            )
            store.insert_evaluation(
                run_id=str(row[0]),
                alpha_id=str(row[1]),
                dataset_version=str(row[2]),
                recorded_ts=timestamp,
                verdict=str(row[4]),
                content_hash=str(row[5]),
                evaluation_json=str(row[6]),
                search_id=search_id,
                config_hash=str(index) * 64,
            )
            store.finish_search_attempt(
                search_id,
                status="COMPLETED",
                recorded_at=timestamp,
            )
    finally:
        store.close()

    inspection = history.inspect_ledger(ledger_path=ledger)

    assert inspection["capture_ready"] is True
    assert [item["search_id"] for item in inspection["attempts"]] == [
        "session-a",
        "session-b",
    ]


def test_v1_inspection_is_honest_and_capture_fails_closed(tmp_path: Path) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_v1_ledger(ledger)
    before = ledger.read_bytes()

    inspection = history.inspect_ledger(ledger_path=ledger)

    assert inspection["status"] == "verified_not_capture_ready"
    assert inspection["ledger_contract_version"] == 1
    assert inspection["capture_ready"] is False
    assert inspection["reason"] == "EXACT_SEARCH_EVALUATION_BINDING_UNAVAILABLE"
    with pytest.raises(history.TrialLedgerHistoryError, match="schema version"):
        history.capture_history(
            session_id="invented-session",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=100,
        )
    assert ledger.read_bytes() == before
    assert not output.exists()


def test_arbitrary_session_label_is_rejected_without_history_write(
    tmp_path: Path,
) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)

    with pytest.raises(history.TrialLedgerHistoryError, match="not present"):
        history.capture_history(
            session_id="operator-supplied-label",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=100,
        )

    assert not output.exists()


def test_completed_candidate_session_without_exact_evaluation_is_not_capturable(
    tmp_path: Path,
) -> None:
    ledger, output, label = _paths(tmp_path)
    ledger.parent.mkdir(parents=True)
    store = SqliteExperimentStore(str(ledger), create=True)
    try:
        store.begin_search_attempt(
            search_id="unbound-session",
            search_kind="TEST_SELECTION_SEARCH",
            source="tests.tools.test_search_trial_ledger_history",
            dataset_version="dataset-v1",
            recorded_at=10,
        )
        store.register_search_candidates(
            "unbound-session",
            ["1" * 64],
            registered_at=10,
        )
        row = _evaluation_row(1)
        store.insert_evaluation(
            run_id=str(row[0]),
            alpha_id=str(row[1]),
            dataset_version=str(row[2]),
            recorded_ts=int(row[3]),
            verdict=str(row[4]),
            content_hash=str(row[5]),
            evaluation_json=str(row[6]),
        )
        store.finish_search_attempt(
            "unbound-session",
            status="COMPLETED",
            recorded_at=10,
        )
    finally:
        store.close()

    inspection = history.inspect_ledger(ledger_path=ledger)

    assert inspection["capture_ready"] is False
    assert inspection["attempts"][0]["capture_status"] == ("NOT_CAPTURABLE_NO_EXACT_EVALUATION_BINDING")
    with pytest.raises(history.TrialLedgerHistoryError, match="no exact"):
        history.capture_history(
            session_id="unbound-session",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=100,
        )
    assert not output.exists()


def test_second_capture_is_monotonic_and_hash_chained(tmp_path: Path) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)
    first = history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )
    _append_ledger(ledger, 2)

    second = history.capture_history(
        session_id="real-session-2",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=200,
    )

    assert len(second["records"]) == 2
    assert second["records"][1]["previous_record_hash"] == (first["records"][0]["record_hash"])
    assert second["records"][1]["record_hash"] == history._record_hash(second["records"][1])
    assert len(second["records"][1]["trials"]) == 2
    assert len(second["records"][1]["evaluations"]) == 2
    history.check_history(
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
    )


def test_second_record_must_be_a_session_started_after_first_snapshot(
    tmp_path: Path,
) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger, count=2)
    history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )
    before = output.read_bytes()

    with pytest.raises(history.TrialLedgerHistoryError, match="did not start after"):
        history.capture_history(
            session_id="real-session-2",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=200,
        )

    assert output.read_bytes() == before


def test_real_rerun_can_link_the_same_immutable_evaluation_in_a_new_session(
    tmp_path: Path,
) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)
    first = history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )
    row = _evaluation_row(1)
    store = SqliteExperimentStore(str(ledger), create=False)
    try:
        store.begin_search_attempt(
            search_id="real-rerun-session",
            search_kind="TEST_SELECTION_SEARCH",
            source="tests.tools.test_search_trial_ledger_history",
            dataset_version="dataset-v1",
            recorded_at=200,
        )
        store.register_search_candidates(
            "real-rerun-session",
            ["1" * 64],
            registered_at=200,
        )
        store.insert_evaluation(
            run_id=str(row[0]),
            alpha_id=str(row[1]),
            dataset_version=str(row[2]),
            recorded_ts=200,
            verdict=str(row[4]),
            content_hash=str(row[5]),
            evaluation_json=str(row[6]),
            search_id="real-rerun-session",
            config_hash="1" * 64,
        )
        store.finish_search_attempt(
            "real-rerun-session",
            status="COMPLETED",
            recorded_at=200,
        )
    finally:
        store.close()

    second = history.capture_history(
        session_id="real-rerun-session",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=300,
    )

    assert len(first["records"][0]["evaluations"]) == 1
    assert len(second["records"][1]["evaluations"]) == 1
    first_link = first["records"][0]["session_provenance"]["evaluation_links"][0]
    second_link = second["records"][1]["session_provenance"]["evaluation_links"][0]
    assert first_link["run_id"] == second_link["run_id"]
    assert first_link["link_hash"] != second_link["link_hash"]


def test_duplicate_session_and_clock_rollback_leave_history_unchanged(
    tmp_path: Path,
) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)
    history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )
    _append_session(ledger, 2, recorded_at=110)
    before = output.read_bytes()

    with pytest.raises(history.TrialLedgerHistoryError, match="already retained"):
        history.capture_history(
            session_id="real-session-1",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=200,
        )
    with pytest.raises(history.TrialLedgerHistoryError, match="increase strictly"):
        history.capture_history(
            session_id="real-session-2",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=99,
        )

    assert output.read_bytes() == before


def test_corrupt_history_chain_is_rejected_without_repair(tmp_path: Path) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)
    history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    document["records"][0]["record_hash"] = "f" * 64
    output.write_text(json.dumps(document), encoding="utf-8")
    corrupt = output.read_bytes()

    with pytest.raises(history.TrialLedgerHistoryError, match="hash is invalid"):
        history.capture_history(
            session_id="real-session-2",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=200,
        )

    assert output.read_bytes() == corrupt


def test_history_link_must_reference_a_candidate_from_the_same_session(
    tmp_path: Path,
) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)
    history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    record = document["records"][0]
    record["session_provenance"]["evaluation_links"][0]["config_hash"] = "f" * 64
    record["record_hash"] = history._record_hash(record)
    output.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(history.TrialLedgerHistoryError, match="evaluation link"):
        history.check_history(
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
        )


def test_trial_deletion_from_live_ledger_is_rejected(tmp_path: Path) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger, count=2)
    history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )
    connection = sqlite3.connect(ledger)
    connection.executescript(
        """
        DROP TRIGGER trials_no_delete;
        DELETE FROM trials WHERE config_hash = '22222222222222222222222222222222'
            || '22222222222222222222222222222222';
        CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
        BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(history.TrialLedgerHistoryError, match="invalid provenance"):
        history.capture_history(
            session_id="real-session-2",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=200,
        )


def test_tampered_evaluation_content_hash_is_recomputed_and_rejected(
    tmp_path: Path,
) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)
    connection = sqlite3.connect(ledger)
    connection.execute("DROP TRIGGER evaluations_no_update")
    connection.execute(
        "UPDATE evaluations SET evaluation_json=? WHERE run_id=?",
        ('{"alpha_id":"tampered"}', str(_evaluation_row(1)[0])),
    )
    connection.executescript(
        "CREATE TRIGGER evaluations_no_update BEFORE UPDATE ON evaluations "
        "BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;"
    )
    connection.commit()
    connection.close()

    with pytest.raises(history.TrialLedgerHistoryError, match="content_hash is invalid"):
        history.capture_history(
            session_id="real-session-1",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=100,
        )
    assert not output.exists()


def test_hash_valid_link_to_another_sessions_candidate_is_rejected(
    tmp_path: Path,
) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger, count=2)
    connection = sqlite3.connect(ledger)
    connection.row_factory = sqlite3.Row
    connection.execute("DROP TRIGGER search_evaluations_no_update")
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT link_seq,search_id,config_hash,run_id,recorded_at,"
            "evaluation_content_hash,prev_link_hash,link_hash "
            "FROM search_evaluations ORDER BY link_seq"
        )
    ]
    rows[0]["config_hash"] = "2" * 64
    previous_hash = ""
    for row in rows:
        row["prev_link_hash"] = previous_hash
        row["link_hash"] = history._search_evaluation_hash(row)
        connection.execute(
            "UPDATE search_evaluations SET config_hash=?,prev_link_hash=?,link_hash=? WHERE link_seq=?",
            (
                row["config_hash"],
                row["prev_link_hash"],
                row["link_hash"],
                row["link_seq"],
            ),
        )
        previous_hash = row["link_hash"]
    connection.executescript(
        """
        CREATE TRIGGER search_evaluations_no_update
        BEFORE UPDATE ON search_evaluations
        BEGIN SELECT RAISE(ABORT, 'search_evaluations is append-only'); END;
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(history.TrialLedgerHistoryError, match="invalid provenance"):
        history.capture_history(
            session_id="real-session-1",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=100,
        )
    assert not output.exists()


def test_concurrent_ledger_snapshot_change_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)
    original = history._read_verified_ledger_once
    calls = 0

    def changing_snapshot(path: Path) -> dict:
        nonlocal calls
        calls += 1
        snapshot = original(path)
        if calls == 2:
            snapshot = {
                **snapshot,
                "trials": [*snapshot["trials"], "f" * 64],
            }
        return snapshot

    monkeypatch.setattr(history, "_read_verified_ledger_once", changing_snapshot)

    with pytest.raises(history.TrialLedgerHistoryError, match="between read-only"):
        history.capture_history(
            session_id="real-session-1",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=100,
        )
    assert not output.exists()


def test_output_lock_rejects_concurrent_collector(tmp_path: Path) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)
    output.parent.mkdir(parents=True)
    lock_path = output.with_name(f"{output.name}.lock")
    lock_path.write_text("other-process", encoding="ascii")

    with pytest.raises(history.TrialLedgerHistoryError, match="output lock"):
        history.capture_history(
            session_id="real-session-1",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=100,
        )
    assert not output.exists()
    assert lock_path.read_text(encoding="ascii") == "other-process"


def test_atomic_replace_failure_preserves_previous_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, output, label = _paths(tmp_path)
    _create_ledger(ledger)
    history.capture_history(
        session_id="real-session-1",
        ledger_path=ledger,
        history_path=output,
        ledger_path_label=label,
        recorded_at_ms=100,
    )
    before = output.read_bytes()
    _append_ledger(ledger, 2)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(history.os, "replace", fail_replace)

    with pytest.raises(history.TrialLedgerHistoryError, match="atomically published"):
        history.capture_history(
            session_id="real-session-2",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=200,
        )
    assert output.read_bytes() == before
    assert list(output.parent.glob(f".{output.name}.*.tmp")) == []


def test_missing_ledger_is_not_created(tmp_path: Path) -> None:
    ledger, output, label = _paths(tmp_path)

    with pytest.raises(history.TrialLedgerHistoryError, match="does not exist"):
        history.capture_history(
            session_id="real-session-1",
            ledger_path=ledger,
            history_path=output,
            ledger_path_label=label,
            recorded_at_ms=100,
        )

    assert not ledger.exists()
    assert not output.exists()


def test_cli_check_requires_no_session_and_does_not_create_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert history.main(["--check"]) == 1
    assert not Path(history.DEFAULT_LEDGER_PATH).exists()
    assert not Path(history.DEFAULT_HISTORY_PATH).exists()
    assert history.main(["--check", "--session-id", "forbidden"]) == 2


def test_cli_inspection_is_read_only_and_rejects_mode_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    ledger = Path(history.DEFAULT_LEDGER_PATH)
    _create_v1_ledger(ledger)
    before = ledger.read_bytes()

    assert history.main(["--inspect-ledger"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["capture_ready"] is False
    assert output["reason"] == "EXACT_SEARCH_EVALUATION_BINDING_UNAVAILABLE"
    assert ledger.read_bytes() == before
    assert not Path(history.DEFAULT_HISTORY_PATH).exists()
    assert history.main(["--inspect-ledger", "--session-id", "forbidden"]) == 2
    assert history.main(["--inspect-ledger", "--check"]) == 2


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


def test_cli_seals_and_checks_without_live_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, output, label = _paths(tmp_path)
    snapshot = tmp_path / "evidence" / "alpha_trial_ledger_snapshot.db"
    seal = tmp_path / "evidence" / "alpha_trial_ledger_snapshot_seal.json"
    _capture_two_sessions(ledger, output, label)
    monkeypatch.chdir(tmp_path)

    assert history.main(["--seal-snapshot"]) == 0
    ledger.unlink()
    assert history.main(["--check-snapshot"]) == 0
    assert history.main(["--check", "--check-snapshot"]) == 2
    assert snapshot.exists()
    assert seal.exists()


def test_check_snapshot_rejects_rehashed_unknown_portable_schema_version(
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
    connection = sqlite3.connect(snapshot)
    connection.execute("PRAGMA user_version=3")
    connection.close()
    document = json.loads(seal.read_text(encoding="utf-8"))
    document["snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    seal.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        history.TrialLedgerHistoryError,
        match="portable snapshot schema version",
    ):
        history.check_snapshot(
            history_path=output,
            snapshot_path=snapshot,
            seal_path=seal,
            ledger_path_label=label,
            history_path_label="evidence/alpha_trial_ledger_history.json",
            snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
        )


def test_cli_check_keeps_live_forward_compatible_schema_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, output, _label = _paths(tmp_path)
    _capture_two_sessions(ledger, output, history.DEFAULT_LEDGER_PATH)
    connection = sqlite3.connect(ledger)
    connection.execute("PRAGMA user_version=3")
    connection.close()
    monkeypatch.chdir(tmp_path)

    assert history.main(["--check"]) == 0


@pytest.mark.parametrize(
    ("mutation", "match"),
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


def test_portable_bundle_rejects_extra_trigger_with_recomputed_hash(tmp_path: Path) -> None:
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
    connection = sqlite3.connect(snapshot)
    connection.execute(
        "CREATE TRIGGER unexpected_guard BEFORE UPDATE ON trials BEGIN SELECT 1; END"
    )
    connection.commit()
    connection.close()
    document = json.loads(seal.read_text(encoding="utf-8"))
    document["snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    seal.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(history.TrialLedgerHistoryError, match="trigger allowlist"):
        history.check_snapshot(
            history_path=output,
            snapshot_path=snapshot,
            seal_path=seal,
            ledger_path_label=label,
            history_path_label="evidence/alpha_trial_ledger_history.json",
            snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
        )


def test_portable_bundle_rejects_noop_guard_with_recomputed_hash(tmp_path: Path) -> None:
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
    connection = sqlite3.connect(snapshot)
    connection.executescript(
        """
        DROP TRIGGER trials_no_update;
        CREATE TRIGGER trials_no_update BEFORE UPDATE ON trials
        BEGIN SELECT 1; END;
        """
    )
    connection.commit()
    connection.close()
    document = json.loads(seal.read_text(encoding="utf-8"))
    document["snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    seal.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(history.TrialLedgerHistoryError, match="trigger definition"):
        history.check_snapshot(
            history_path=output,
            snapshot_path=snapshot,
            seal_path=seal,
            ledger_path_label=label,
            history_path_label="evidence/alpha_trial_ledger_history.json",
            snapshot_path_label="evidence/alpha_trial_ledger_snapshot.db",
        )
