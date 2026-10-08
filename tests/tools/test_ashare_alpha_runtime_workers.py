from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from quantos.contracts.hashing import stable_hash
from tools import ashare_alpha_search_worker as search_worker
from tools import ashare_forward_observation as forward_worker
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.ashare_alpha_search_technical import MarketPanel, TECHNICAL_STATUS


_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _shanghai_ms(date: dt.date, hour: int, minute: int) -> int:
    return int(
        dt.datetime.combine(
            date,
            dt.time(hour, minute),
            tzinfo=_SHANGHAI,
        ).timestamp()
        * 1000
    )


def _prefix_binding(cutoff: int, *, rows: int, salt: str = "source") -> dict:
    return {
        "schema_version": 1,
        "algorithm": search_worker.SOURCE_PREFIX_ALGORITHM,
        "cutoff": cutoff,
        "rows": rows,
        "sha256": stable_hash({"salt": salt, "cutoff": cutoff, "rows": rows}),
    }


def _freeze_document(database: Path, candidate: dict, *, cutoff: int = 20260070) -> dict:
    prefix = _prefix_binding(
        cutoff,
        rows=(cutoff - 20260000) * 4,
    )
    candidate_set_hash = stable_hash([candidate])
    artifact = {
        "source": {
            "sha256": search_worker._sha256(database),
            "last_date": cutoff,
        },
        "untouched_forward_observation": {
            "status": "ARMED_AWAITING_NEW_SESSIONS",
            "freeze_cutoff": cutoff,
            "starts_after": cutoff,
            "minimum_sessions": 60,
            "candidate_specs": [candidate],
            "candidate_set_hash": candidate_set_hash,
            "selection_locked": True,
            "promotion_allowed": False,
        },
    }
    encoded_artifact = json.dumps(
        artifact, ensure_ascii=False, indent=2, allow_nan=False
    ).encode("utf-8")
    artifact_sha256 = hashlib.sha256(encoded_artifact).hexdigest()
    artifact_dir = database.parent / "ashare_alpha_search_artifacts"
    artifact_dir.mkdir(exist_ok=True)
    (artifact_dir / f"{artifact_sha256}.json").write_bytes(encoded_artifact)
    return {
        "schema_version": 1,
        "artifact_kind": "ASHARE_UNTOUCHED_FORWARD_FREEZE_V1",
        "evidence_status": TECHNICAL_STATUS,
        "freeze_cutoff": cutoff,
        "starts_after": cutoff,
        "minimum_sessions": 60,
        "candidate_specs": [candidate],
        "candidate_set_hash": candidate_set_hash,
        "source_database_sha256_at_freeze": search_worker._sha256(database),
        "source_rows_at_freeze": prefix["rows"],
        "source_prefix": prefix,
        "selection_locked": True,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "search_artifact_sha256": artifact_sha256,
    }


def _search_report(database: Path, *, cutoff: int, name: str) -> dict:
    candidate = {
        "name": name,
        "family": "reversal",
        "window": 5,
        "skip": 0,
        "hold_days": 5,
    }
    return {
        "schema_version": 2,
        "artifact_kind": "ASHARE_ALPHA_SEARCH_INSTITUTIONAL_SURFACES_V2",
        "evidence_status": TECHNICAL_STATUS,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "candidate_usefulness": "NOT_VALIDATED",
        "source": {
            "last_date": cutoff,
            "rows": 100,
            "instruments": 4,
            "dates": 70,
            "sha256": search_worker._sha256(database),
        },
        "trial_accounting": {"candidate_specs": 1},
        "champion_selected_without_confirmation": candidate,
        "champion_confirmation_cost_sensitivity": {},
        "untouched_forward_observation": {
            "freeze_cutoff": cutoff,
            "minimum_sessions": 60,
            "candidate_specs": [candidate],
        },
    }


def test_search_worker_freezes_first_candidate_set_and_attests_fixed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "ashare.sqlite"
    panel = tmp_path / "panel.npz"
    output = tmp_path / "search.json"
    freeze = tmp_path / "freeze.json"
    database.write_bytes(b"immutable-db")
    panel.write_bytes(b"bound-panel")
    monkeypatch.setattr(search_worker, "DEFAULT_FREEZE", freeze)
    monkeypatch.setattr(
        search_worker, "_database_latest_trade_date", lambda _database: 20260828
    )
    monkeypatch.setattr(
        search_worker,
        "_ensure_bound_panel_cache",
        lambda *_args, **_kwargs: "BOUND_CACHE_HIT",
    )
    monkeypatch.setattr(
        search_worker,
        "_source_prefix_bindings",
        lambda _database, cutoffs: {
            cutoff: _prefix_binding(cutoff, rows=100) for cutoff in cutoffs
        },
    )
    reports = [
        _search_report(database, cutoff=20260828, name="reversal_w5_h5"),
        _search_report(database, cutoff=20260831, name="momentum_w5_h5"),
    ]
    monkeypatch.setattr(search_worker, "run_search", lambda *_args, **_kwargs: reports.pop(0))
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "a" * 40)

    first = search_worker.run_cycle(
        database=database,
        panel_cache=panel,
        output=output,
        lock_path=tmp_path / "runtime.lock",
    )
    frozen_first = json.loads(freeze.read_text(encoding="utf-8"))
    second = search_worker.run_cycle(
        database=database,
        panel_cache=panel,
        output=output,
        lock_path=tmp_path / "runtime.lock",
    )
    frozen_second = json.loads(freeze.read_text(encoding="utf-8"))

    assert first["scheduled_worker"]["forward_freeze_created"] is True
    assert second["scheduled_worker"]["forward_freeze_created"] is False
    assert frozen_first == frozen_second
    assert frozen_first["freeze_cutoff"] == 20260828
    assert frozen_first["source_prefix"] == _prefix_binding(20260828, rows=100)
    assert frozen_first["selection_locked"] is True
    assert frozen_first["promotion_allowed"] is False
    assert frozen_first["execution_allowed"] is False
    first_artifact = (
        output.parent
        / "ashare_alpha_search_artifacts"
        / f"{frozen_first['search_artifact_sha256']}.json"
    )
    assert first_artifact.is_file()
    assert search_worker._sha256(first_artifact) == frozen_first["search_artifact_sha256"]
    assert second["untouched_forward_observation"]["freeze_cutoff"] == 20260828
    assert second["untouched_forward_observation"]["candidate_specs"] == [
        frozen_first["candidate_specs"][0]
    ]
    attestation = search_worker.release_run_attestation()
    assert attestation is not None
    assert attestation["module"] == "ashare_alpha_search_worker"
    assert attestation["complete"] is True
    assert attestation["safety"]["order_submission_allowed"] is False


def test_search_does_not_publish_frontend_mirror_when_freeze_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "ashare.sqlite"
    panel = tmp_path / "panel.npz"
    output = tmp_path / "search.json"
    database.write_bytes(b"immutable-db")
    panel.write_bytes(b"bound-panel")
    output.write_text('{"previous":true}', encoding="utf-8")
    monkeypatch.setattr(
        search_worker, "_database_latest_trade_date", lambda _database: 20260828
    )
    monkeypatch.setattr(
        search_worker,
        "_ensure_bound_panel_cache",
        lambda *_args, **_kwargs: "BOUND_CACHE_HIT",
    )
    monkeypatch.setattr(
        search_worker,
        "run_search",
        lambda *_args, **_kwargs: _search_report(
            database, cutoff=20260828, name="reversal_w5_h5"
        ),
    )
    monkeypatch.setattr(
        search_worker,
        "_freeze_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            search_worker.AShareSearchWorkerError("freeze rejected")
        ),
    )

    with pytest.raises(search_worker.AShareSearchWorkerError, match="freeze rejected"):
        search_worker.run_cycle(
            database=database,
            panel_cache=panel,
            output=output,
            lock_path=tmp_path / "runtime.lock",
        )

    assert output.read_text(encoding="utf-8") == '{"previous":true}'


def test_search_safe_blocks_todays_unfinished_daily_bar_without_freezing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = dt.date(2026, 9, 2)
    latest = 20260902
    database = tmp_path / "ashare.sqlite"
    panel = tmp_path / "panel.npz"
    output = tmp_path / "search.json"
    freeze = tmp_path / "freeze.json"
    database.write_bytes(b"intraday-source")
    panel.write_bytes(b"old-bound-panel")
    monkeypatch.setattr(search_worker, "DEFAULT_FREEZE", freeze)
    monkeypatch.setattr(
        search_worker, "_database_latest_trade_date", lambda _database: latest
    )
    monkeypatch.setattr(
        search_worker,
        "_ensure_bound_panel_cache",
        lambda *_args, **_kwargs: pytest.fail("intraday source reached panel mutation"),
    )
    monkeypatch.setattr(
        search_worker,
        "run_search",
        lambda *_args, **_kwargs: pytest.fail("intraday source reached search"),
    )
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "e" * 40)

    report = search_worker.run_cycle(
        database=database,
        panel_cache=panel,
        output=output,
        lock_path=tmp_path / "runtime.lock",
        source_observed_at_ms=_shanghai_ms(today, 14, 59),
    )

    assert report["status"] == "SAFE_BLOCKED_SOURCE_NOT_FINAL"
    assert report["source_finality"]["latest_date"] == latest
    assert report["source_finality"]["is_final"] is False
    assert report["state_mutated"] is False
    assert not freeze.exists()
    assert not output.exists()
    attestation = search_worker.release_run_attestation()
    assert attestation is not None
    assert attestation["complete"] is True
    assert attestation["module"] == "ashare_alpha_search_worker"


def _panel(length: int) -> MarketPanel:
    instruments = tuple(f"S{index:02d}" for index in range(4))
    rows = np.arange(length, dtype=float)[:, None]
    columns = np.arange(len(instruments), dtype=float)[None, :]
    closes = 10.0 + rows * (0.01 + columns * 0.001) + columns
    opens = closes * (1.0 + 0.0005 * (columns + 1.0))
    shape = closes.shape
    return MarketPanel(
        dates=np.arange(20260001, 20260001 + length, dtype=np.int64),
        instruments=instruments,
        opens=opens,
        highs=closes * 1.01,
        lows=closes * 0.99,
        closes=closes,
        volumes=np.full(shape, 1_000_000.0),
        amounts=np.full(shape, 50_000_000.0),
        base_eligible=np.ones(shape, dtype=bool),
        trailing_adv20=np.full(shape, 50_000_000.0),
        bad_bars=np.zeros(shape, dtype=bool),
    )


def test_source_prefix_binding_allows_append_and_detects_history_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "source.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE daily_prices("
            "instrument TEXT NOT NULL,trade_date INTEGER NOT NULL,"
            "knowledge_time INTEGER NOT NULL,open REAL NOT NULL,high REAL NOT NULL,"
            "low REAL NOT NULL,close REAL NOT NULL,amount REAL NOT NULL,"
            "volume INTEGER NOT NULL,reserved INTEGER NOT NULL,"
            "PRIMARY KEY(instrument,trade_date)) WITHOUT ROWID"
        )
        connection.executemany(
            "INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                ("000001.SZ", 20260001, 20260001, 10, 11, 9, 10.5, 1000, 100, 0),
                ("600000.SH", 20260001, 20260001, 8, 9, 7, 8.5, 900, 90, 0),
            ),
        )
        connection.commit()
    frozen = search_worker._source_prefix_bindings(database, [20260001])[20260001]

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("000001.SZ", 20260002, 20260002, 11, 12, 10, 11.5, 1100, 105, 0),
        )
        connection.commit()
    appended = search_worker._source_prefix_bindings(
        database, [20260001, 20260002]
    )
    assert appended[20260001] == frozen
    assert appended[20260002]["rows"] == frozen["rows"] + 1

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE daily_prices SET close=10.6 "
            "WHERE instrument='000001.SZ' AND trade_date=20260001"
        )
        connection.commit()
    revised = search_worker._source_prefix_bindings(database, [20260001])[20260001]
    assert revised["sha256"] != frozen["sha256"]


def test_forward_observation_arms_then_records_before_realizing_append_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "ashare.sqlite"
    panel_cache = tmp_path / "panel.npz"
    freeze_path = tmp_path / "freeze.json"
    ledger = tmp_path / "forward.db"
    output = tmp_path / "forward.json"
    database.write_bytes(b"immutable-db")
    panel_cache.write_bytes(b"bound-panel")
    candidate = {
        "name": "reversal_w5_h5",
        "family": "reversal",
        "window": 5,
        "skip": 0,
        "hold_days": 5,
    }
    freeze_path.write_text(
        json.dumps(_freeze_document(database, candidate)),
        encoding="utf-8",
    )
    current_panel = [_panel(70)]
    monkeypatch.setattr(
        forward_worker,
        "_load_panel_cache",
        lambda *_args, **_kwargs: (
            current_panel[0],
            {
                "rows": current_panel[0].closes.size,
                "instruments": len(current_panel[0].instruments),
                "dates": len(current_panel[0].dates),
                "last_date": int(current_panel[0].dates[-1]),
                "sha256": forward_worker._file_hash(database),
            },
        ),
    )
    monkeypatch.setattr(
        forward_worker,
        "_database_latest_trade_date",
        lambda _database: int(current_panel[0].dates[-1]),
    )
    monkeypatch.setattr(
        forward_worker,
        "_ensure_bound_panel_cache",
        lambda *_args, **_kwargs: "BOUND_CACHE_HIT",
    )
    monkeypatch.setattr(
        forward_worker,
        "_source_prefix_bindings",
        lambda _database, cutoffs: {
            cutoff: _prefix_binding(
                cutoff,
                rows=(cutoff - 20260000) * 4,
            )
            for cutoff in cutoffs
        },
    )
    monkeypatch.setattr(
        forward_worker,
        "_decision_window_ms",
        lambda _session_date: (0, 10**15),
    )
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "b" * 40)

    armed = forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=1,
        lock_path=tmp_path / "runtime.lock",
    )
    assert armed["status"] == "ARMED_AWAITING_NEW_SESSIONS"
    assert armed["cycle"]["decision_count"] == 0

    current_panel[0] = _panel(71)
    decided = forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=2,
        lock_path=tmp_path / "runtime.lock",
    )
    assert decided["status"] == "OBSERVING"
    assert decided["cycle"]["new_decisions"] == 1
    assert decided["cycle"]["new_outcomes"] == 0
    assert decided["cycle"]["pending_count"] == 1

    current_panel[0] = _panel(77)
    realized = forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=3,
        lock_path=tmp_path / "runtime.lock",
    )
    assert realized["cycle"]["new_outcomes"] == 1
    assert realized["cycle"]["outcome_count"] == 1
    assert realized["cycle"]["pending_count"] == 0
    assert realized["chain_valid"] is True
    assert realized["research_contract"]["historical_backfill_allowed"] is False
    assert realized["execution_allowed"] is False

    with sqlite3.connect(ledger) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE decisions SET signal_date=signal_date")
        decision = connection.execute(
            "SELECT signal_date,recorded_at_ms FROM decisions"
        ).fetchone()
        outcome = connection.execute(
            "SELECT signal_date,recorded_at_ms FROM outcomes"
        ).fetchone()
        authority = connection.execute(
            "SELECT freeze_sha256,candidate_set_hash,freeze_cutoff "
            "FROM ledger_authority"
        ).fetchone()
        checkpoints = connection.execute(
            "SELECT through_date,prefix_rows FROM source_checkpoints ORDER BY sequence"
        ).fetchall()
    assert decision == (20260071, 2)
    assert outcome == (20260071, 3)
    assert authority == (
        forward_worker._file_hash(freeze_path),
        stable_hash([candidate]),
        20260070,
    )
    assert checkpoints == [(20260070, 280), (20260071, 284), (20260077, 308)]
    attestation = forward_worker.release_run_attestation()
    assert attestation is not None
    assert attestation["module"] == "ashare_forward_observation"
    assert attestation["complete"] is True
    assert attestation["safety"]["broker_connectivity"] is False


def _forward_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidate_name: str = "candidate_A",
    panel_length: int = 71,
) -> tuple[Path, Path, Path, Path, Path, Path, list[MarketPanel]]:
    database = tmp_path / "ashare.sqlite"
    panel_cache = tmp_path / "panel.npz"
    freeze_path = tmp_path / "freeze.json"
    ledger = tmp_path / "forward.db"
    output = tmp_path / "forward.json"
    lock = tmp_path / "runtime.lock"
    database.write_bytes(b"immutable-db")
    panel_cache.write_bytes(b"bound-panel")
    candidate = {
        "name": candidate_name,
        "family": "reversal",
        "window": 5,
        "skip": 0,
        "hold_days": 5,
    }
    freeze_path.write_text(
        json.dumps(_freeze_document(database, candidate)), encoding="utf-8"
    )
    current_panel = [_panel(panel_length)]
    monkeypatch.setattr(
        forward_worker,
        "_load_panel_cache",
        lambda *_args, **_kwargs: (
            current_panel[0],
            {
                "rows": current_panel[0].closes.size,
                "instruments": len(current_panel[0].instruments),
                "dates": len(current_panel[0].dates),
                "last_date": int(current_panel[0].dates[-1]),
                "sha256": forward_worker._file_hash(database),
            },
        ),
    )
    monkeypatch.setattr(
        forward_worker,
        "_database_latest_trade_date",
        lambda _database: int(current_panel[0].dates[-1]),
    )
    monkeypatch.setattr(
        forward_worker,
        "_ensure_bound_panel_cache",
        lambda *_args, **_kwargs: "BOUND_CACHE_HIT",
    )
    monkeypatch.setattr(
        forward_worker,
        "_source_prefix_bindings",
        lambda _database, cutoffs: {
            cutoff: _prefix_binding(
                cutoff,
                rows=(cutoff - 20260000) * 4,
            )
            for cutoff in cutoffs
        },
    )
    monkeypatch.setattr(
        forward_worker,
        "_decision_window_ms",
        lambda _session_date: (0, 10**15),
    )
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "d" * 40)
    return database, panel_cache, freeze_path, ledger, output, lock, current_panel


def test_intraday_forward_block_does_not_poison_same_day_final_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = dt.date(2026, 9, 2)
    latest = 20260902
    database, panel_cache, freeze_path, ledger, output, lock, panels = _forward_fixture(
        tmp_path, monkeypatch
    )
    panels[0].dates[-1] = latest
    prefix_calls: list[tuple[int, ...]] = []

    def final_prefixes(_database: Path, cutoffs) -> dict[int, dict]:
        requested = tuple(sorted(cutoffs))
        prefix_calls.append(requested)
        return {
            cutoff: _prefix_binding(
                cutoff,
                rows=(cutoff - 20260000) * 4,
                salt="source" if cutoff == 20260070 else "final-close",
            )
            for cutoff in requested
        }

    monkeypatch.setattr(forward_worker, "_source_prefix_bindings", final_prefixes)
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "f" * 40)

    blocked = forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=_shanghai_ms(today, 14, 59),
        source_observed_at_ms=_shanghai_ms(today, 14, 59),
        lock_path=lock,
    )
    assert blocked["status"] == "SAFE_BLOCKED_SOURCE_NOT_FINAL"
    assert blocked["state_mutated"] is False
    assert prefix_calls == []
    assert not ledger.exists()
    assert not output.exists()

    completed = forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=_shanghai_ms(today, 15, 1),
        source_observed_at_ms=_shanghai_ms(today, 15, 1),
        lock_path=lock,
    )
    assert completed["status"] == "OBSERVING"
    assert completed["cycle"]["new_decisions"] == 1
    assert prefix_calls == [(20260070, latest)]
    with sqlite3.connect(ledger) as connection:
        checkpoints = connection.execute(
            "SELECT through_date,observation_status,prefix_sha256 "
            "FROM source_checkpoints ORDER BY sequence"
        ).fetchall()
    assert checkpoints == [
        (20260070, "FREEZE_ANCHOR", _prefix_binding(20260070, rows=280)["sha256"]),
        (
            latest,
            "TIMELY_PRE_NEXT_OPEN",
            _prefix_binding(
                latest,
                rows=(latest - 20260000) * 4,
                salt="final-close",
            )["sha256"],
        ),
    ]


def test_forward_ledger_rejects_replaced_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, panel_cache, freeze_path, ledger, output, lock, _ = _forward_fixture(
        tmp_path, monkeypatch
    )
    forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=1,
        lock_path=lock,
    )
    replacement = {
        "name": "candidate_B",
        "family": "momentum",
        "window": 10,
        "skip": 0,
        "hold_days": 5,
    }
    freeze_path.write_text(
        json.dumps(_freeze_document(database, replacement)), encoding="utf-8"
    )

    with pytest.raises(
        forward_worker.AShareForwardObservationError,
        match="immutable ledger authority",
    ):
        forward_worker.run_cycle(
            database=database,
            panel_cache=panel_cache,
            freeze_path=freeze_path,
            ledger_path=ledger,
            output_path=output,
            recorded_at_ms=2,
            lock_path=lock,
        )

    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT candidate_id FROM decisions ORDER BY sequence"
        ).fetchall() == [("candidate_A",)]


def test_forward_ledger_rejects_historical_prefix_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, panel_cache, freeze_path, ledger, output, lock, _ = _forward_fixture(
        tmp_path, monkeypatch
    )
    forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=1,
        lock_path=lock,
    )
    monkeypatch.setattr(
        forward_worker,
        "_source_prefix_bindings",
        lambda _database, cutoffs: {
            cutoff: _prefix_binding(
                cutoff,
                rows=(cutoff - 20260000) * 4,
                salt="historically-revised",
            )
            for cutoff in cutoffs
        },
    )

    with pytest.raises(
        forward_worker.AShareForwardObservationError,
        match="observed forward prefix",
    ):
        forward_worker.run_cycle(
            database=database,
            panel_cache=panel_cache,
            freeze_path=freeze_path,
            ledger_path=ledger,
            output_path=output,
            recorded_at_ms=2,
            lock_path=lock,
        )


def test_forward_clock_counts_observed_checkpoints_not_offline_calendar_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, panel_cache, freeze_path, ledger, output, lock, _ = _forward_fixture(
        tmp_path,
        monkeypatch,
        panel_length=130,
    )

    report = forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=1,
        lock_path=lock,
    )

    assert report["calendar_sessions_since_freeze"] == 60
    assert report["sessions_observed"] == 1
    assert report["missed_observation_sessions"] == 59
    assert report["observation_gaps_detected"] is True
    assert report["minimum_sessions_met"] is False
    assert report["research_contract"]["observation_clock"] == (
        "DISTINCT_PRE_OUTCOME_SOURCE_CHECKPOINTS_AFTER_FREEZE"
    )
    assert report["research_contract"]["due_decisions_expected"] == 12
    assert report["research_contract"]["due_decisions_recorded"] == 0
    assert report["research_contract"]["due_decision_coverage_complete"] is False


def test_forward_missed_due_deadline_is_a_permanent_coverage_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, panel_cache, freeze_path, ledger, output, lock, _ = _forward_fixture(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        forward_worker,
        "_decision_window_ms",
        lambda _session_date: (0, 0),
    )

    report = forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=1,
        lock_path=lock,
    )

    assert report["sessions_observed"] == 0
    assert report["missed_observation_sessions"] == 1
    assert report["minimum_sessions_met"] is False
    assert report["research_contract"]["due_decisions_expected"] == 1
    assert report["research_contract"]["due_decisions_recorded"] == 0
    assert report["research_contract"]["required_decision_gaps"] == [
        {
            "candidate_id": "candidate_A",
            "session_date": 20260071,
            "status": "MISSED_NEXT_OPEN_DEADLINE",
        }
    ]


def test_forward_migrates_v1_checkpoints_without_claiming_timing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, panel_cache, freeze_path, ledger, output, lock, _ = _forward_fixture(
        tmp_path, monkeypatch
    )
    binding = _prefix_binding(20260070, rows=280)
    payload = {
        "sequence": 1,
        "through_date": 20260070,
        "prefix_algorithm": binding["algorithm"],
        "prefix_sha256": binding["sha256"],
        "prefix_rows": binding["rows"],
        "source_database_sha256": forward_worker._file_hash(database),
        "recorded_at_ms": 0,
    }
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            "CREATE TABLE source_checkpoints("
            "sequence INTEGER PRIMARY KEY,through_date INTEGER NOT NULL UNIQUE,"
            "prefix_algorithm TEXT NOT NULL,prefix_sha256 TEXT NOT NULL,"
            "prefix_rows INTEGER NOT NULL,source_database_sha256 TEXT NOT NULL,"
            "recorded_at_ms INTEGER NOT NULL,previous_hash TEXT NOT NULL,"
            "record_hash TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO source_checkpoints VALUES (?,?,?,?,?,?,?,?,?)",
            (
                *(payload[field] for field in forward_worker._LEGACY_CHECKPOINT_FIELDS),
                "0" * 64,
                forward_worker._record_hash(payload, "0" * 64),
            ),
        )
        connection.commit()

    report = forward_worker.run_cycle(
        database=database,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger,
        output_path=output,
        recorded_at_ms=1,
        lock_path=lock,
    )

    migration = report["ledger"]["legacy_checkpoint_migration"]
    assert migration["status"] == "LEGACY_UNATTESTED_DECISION_TIME_RESTARTED"
    assert migration["row_count"] == 1
    assert report["sessions_observed"] == 1
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM source_checkpoints_legacy_v1"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT migration_status FROM checkpoint_legacy_authority"
        ).fetchone() == ("LEGACY_UNATTESTED_DECISION_TIME_RESTARTED",)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM source_checkpoints_legacy_v1 WHERE sequence=1"
            )


def test_nonempty_legacy_forward_ledger_without_authority_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, panel_cache, freeze_path, ledger, output, lock, _ = _forward_fixture(
        tmp_path, monkeypatch
    )
    with sqlite3.connect(ledger) as connection:
        connection.executescript(forward_worker._SCHEMA)
        payload = {
            "sequence": 1,
            "candidate_id": "candidate_A",
            "signal_date": 20260071,
            "hold_days": 5,
            "factor_spec_json": "{}",
            "selected_json": "{}",
            "source_sha256": forward_worker._file_hash(database),
            "recorded_at_ms": 1,
        }
        connection.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                *(payload[field] for field in forward_worker._DECISION_FIELDS),
                "0" * 64,
                forward_worker._record_hash(payload, "0" * 64),
            ),
        )
        connection.commit()

    with pytest.raises(
        forward_worker.AShareForwardObservationError,
        match="lacks immutable freeze authority",
    ):
        forward_worker.run_cycle(
            database=database,
            panel_cache=panel_cache,
            freeze_path=freeze_path,
            ledger_path=ledger,
            output_path=output,
            recorded_at_ms=2,
            lock_path=lock,
        )


def test_search_and_forward_share_one_nonblocking_single_flight_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "runtime.lock"
    started = threading.Event()
    release = threading.Event()
    first_result: list[dict] = []

    def held_search(**_kwargs):
        started.set()
        assert release.wait(timeout=5)
        return {"status": "SEARCH_COMPLETE"}

    monkeypatch.setattr(search_worker, "_run_cycle_once", held_search)
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", "c" * 40)
    thread = threading.Thread(
        target=lambda: first_result.append(
            search_worker.run_cycle(lock_path=lock_path)
        )
    )
    thread.start()
    assert started.wait(timeout=5)
    try:
        busy = forward_worker.run_cycle(lock_path=lock_path)
    finally:
        release.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert first_result == [{"status": "SEARCH_COMPLETE"}]
    assert busy == {
        "schema_version": 1,
        "artifact_kind": "ASHARE_RUNTIME_SINGLE_FLIGHT_V1",
        "evidence_status": TECHNICAL_STATUS,
        "status": "SAFE_BLOCKED_RUNTIME_BUSY",
        "module": "ashare_forward_observation",
        "retry_allowed": True,
        "state_mutated": False,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "safety": {
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "legacy_v1_used": False,
        },
    }
    attestation = forward_worker.release_run_attestation()
    assert attestation is not None
    assert attestation["module"] == "ashare_forward_observation"
    assert attestation["complete"] is True


def test_runtime_lock_arbitrates_a_separate_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "runtime.lock"
    script = (
        "import sys\n"
        "from tools.ashare_alpha_runtime_lock import acquire_runtime_lock\n"
        "with acquire_runtime_lock(sys.argv[1]) as acquired:\n"
        " print('LOCKED' if acquired else 'BUSY', flush=True)\n"
        " sys.stdin.readline()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "LOCKED"
        with acquire_runtime_lock(lock_path) as acquired:
            assert acquired is False
    finally:
        if process.stdin is not None:
            process.stdin.write("\n")
            process.stdin.flush()
        stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, (stdout, stderr)
