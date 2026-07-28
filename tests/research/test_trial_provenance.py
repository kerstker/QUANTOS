"""TrialRecord provenance (727 §7.1/§7.2) — the record behind the trial count.

Proves the invariants the doc names explicitly:
  * every ATTEMPT is ledgered, including duplicates and parameter-sweep points
  * a failed/eliminated trial cannot be recorded without a reason
  * physically deleting or editing a retained trial is impossible
  * the OOS-peek count is recorded rather than left unrecorded
  * a provenance row cannot exist for a config that escaped the trial population
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from quantos.contracts.alpha import AlphaDefinition
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.ports.persistence import TrialPersistenceError
from services.research.trial_provenance import (
    ActorType,
    TrialProvenance,
    TrialStatus,
    chain_head,
    trial_provenance_for_config,
    trial_provenance_for_definition,
)


def _registry(path: str = ":memory:") -> ExperimentRegistry:
    return ExperimentRegistry(SqliteExperimentStore(path, create=True))


def _record(**overrides) -> TrialProvenance:
    base = dict(
        trial_id="trial-1",
        research_session_id="session-1",
        config_hash="cfg-a",
        actor_id="agent-alpha-factory",
        actor_type=ActorType.AUTOMATED_SEARCH,
        status=TrialStatus.RECORDED,
        started_at=1_000,
        completed_at=1_500,
    )
    base.update(overrides)
    return TrialProvenance(**base)


# --- domain rules ---------------------------------------------------------


def test_eliminated_trial_cannot_be_recorded_without_a_reason() -> None:
    """§7.2/§22.11 — an elimination nobody can review is not an audit trail."""
    with pytest.raises(ValueError, match="failure_reasons"):
        _record(status=TrialStatus.ELIMINATED_POSTCOST)

    ok = _record(
        status=TrialStatus.ELIMINATED_POSTCOST,
        failure_reasons=("net_sharpe_below_floor", "turnover_cost_exceeds_gross"),
    )
    assert ok.eliminated is True
    # order is information: the funnel produced them in this sequence
    assert ok.failure_reasons == (
        "net_sharpe_below_floor",
        "turnover_cost_exceeds_gross",
    )


def test_passing_trial_needs_no_reason_and_is_not_eliminated() -> None:
    record = _record(status=TrialStatus.PASSED_TO_CATALOG)
    assert record.eliminated is False
    assert record.failure_reasons == ()


def test_definition_and_hash_helpers_preserve_only_known_provenance() -> None:
    definition = AlphaDefinition(
        alpha_id="helper-alpha",
        name="Helper Alpha",
        factor_ids=("close",),
        universe="cs-v1",
        params={"operator": "momentum", "window": "5"},
    )
    from_definition = trial_provenance_for_definition(
        definition,
        research_session_id="helper-session",
        actor_id="tests.provenance",
        dataset_version="dataset-v1",
        recorded_at=42,
    )
    assert from_definition.trial_id == (
        f"helper-session:{definition.definition_hash}"
    )
    assert from_definition.parameter_set == {"operator": "momentum", "window": "5"}
    assert from_definition.universe_version == "cs-v1"
    assert from_definition.alpha_expression == ""
    assert from_definition.canonical_expression == ""

    hash_only = trial_provenance_for_config(
        "hash-only",
        research_session_id="helper-session",
        actor_id="tests.provenance",
        recorded_at=42,
    )
    assert hash_only.config_hash == "hash-only"
    assert hash_only.parameter_set == {}
    assert hash_only.dataset_versions == ()


def test_canonical_payload_is_order_independent_and_hash_is_deterministic() -> None:
    a = _record(
        factor_ids=("f_b", "f_a", "f_b"),
        viewed_oos_boundaries=("2025-01-01", "2024-06-30"),
        parameter_set={"window": "14", "cap": "0.05"},
        metrics={"net_sharpe": 1.25, "turnover": 0.4},
    )
    b = _record(
        factor_ids=("f_a", "f_b"),
        viewed_oos_boundaries=("2024-06-30", "2025-01-01"),
        parameter_set={"cap": "0.05", "window": "14"},
        metrics={"turnover": 0.4, "net_sharpe": 1.25},
    )
    # set-like fields are canonicalized (sorted + de-duplicated) so a caller's
    # iteration order cannot change the identity of the same trial
    assert a.factor_ids == ("f_a", "f_b")
    assert a.canonical_payload() == b.canonical_payload()
    assert a.entry_hash("") == b.entry_hash("")
    assert len(a.entry_hash("")) == 64


def test_oos_peek_count_counts_distinct_boundaries() -> None:
    """§0.4.7 forbids calling repeatedly-viewed OOS data 'unseen'. The count is
    what makes that visible, so duplicates must not inflate it."""
    record = _record(
        viewed_oos_boundaries=("2025-01-01", "2025-01-01", "2025-06-30")
    )
    assert record.oos_peek_count == 2


def test_artifact_paths_and_hashes_must_be_paired() -> None:
    with pytest.raises(ValueError, match="1:1"):
        _record(artifact_paths=("a.json", "b.json"), artifact_hashes=("h1",))


def test_parent_trial_id_cannot_be_self() -> None:
    with pytest.raises(ValueError, match="parent_trial_id"):
        _record(parent_trial_id="trial-1")


def test_non_finite_metric_is_rejected() -> None:
    """NaN/Inf would break canonical JSON round-tripping and make the retained
    entry_hash unverifiable after a reload."""
    with pytest.raises(ValueError, match="finite"):
        _record(metrics={"sharpe": float("nan")})


def test_chain_head_recomputes_from_scratch() -> None:
    records = [_record(trial_id=f"trial-{i}") for i in range(3)]
    expected = ""
    for record in records:
        expected = record.entry_hash(expected)
    assert chain_head(records) == expected
    # dropping the middle record changes the head — that is the tamper evidence
    assert chain_head([records[0], records[2]]) != expected


# --- persistence ----------------------------------------------------------


def test_record_roundtrips_and_admits_the_config_into_the_trial_population() -> None:
    """§7.2 — a real attempt must not escape the DSR/PBO/FDR denominator."""
    registry = _registry()
    record = _record(
        hypothesis_id="hyp-carry-1",
        model_name="claude-opus-5",
        model_version="2026-07",
        prompt_hash="p" * 64,
        alpha_expression="cs_rank(neg(global_ls))",
        canonical_expression="cs_rank(neg(global_ls))",
        expression_hash="e" * 64,
        parameter_set={"window": "7"},
        parameter_hash="h" * 64,
        dataset_versions=("crypto_daily_v3",),
        code_hash="c" * 64,
        viewed_oos_boundaries=("2025-01-01",),
        metrics={"net_sharpe": 1.21},
    )
    assert registry.record_trial_provenance(record) is True

    assert registry._store.all_trial_hashes() == ["cfg-a"]
    assert registry._store.trial_count() == 1

    stored = registry.trial_provenance("trial-1")
    assert stored is not None
    assert stored["record"] == record.canonical_payload()
    assert stored["prev_entry_hash"] == ""
    assert stored["entry_hash"] == record.entry_hash("")
    assert registry.trial_provenance_head() == record.entry_hash("")
    assert registry.trial_provenance_chain_valid() is True
    assert registry.oos_peek_totals() == {"cfg-a": 1}


def test_replaying_the_same_trial_is_idempotent_not_a_duplicate() -> None:
    registry = _registry()
    record = _record()
    assert registry.record_trial_provenance(record) is True
    assert registry.record_trial_provenance(record) is False
    assert len(registry.trial_provenance_for_config("cfg-a")) == 1
    assert registry.trial_provenance_chain_valid() is True


def test_two_attempts_at_one_config_are_two_records_but_one_trial() -> None:
    """§7.2 'repeated candidates must also be ledgered' and 'every parameter
    sweep configuration counts' pull in different directions; the honest reading
    is attempts-many, distinct-configs-one."""
    registry = _registry()
    first = _record(trial_id="trial-1", started_at=10, completed_at=20)
    second = _record(
        trial_id="trial-2",
        started_at=30,
        completed_at=40,
        status=TrialStatus.ELIMINATED_DUPLICATE,
        failure_reasons=("canonical_expression_already_tried",),
    )
    assert registry.record_trial_provenance(first) is True
    assert registry.record_trial_provenance(second) is True

    attempts = registry.trial_provenance_for_config("cfg-a")
    assert [a["trial_id"] for a in attempts] == ["trial-1", "trial-2"]
    assert registry._store.trial_count() == 1  # de-duplicated for the count
    assert registry.trial_provenance_chain_valid() is True
    # chain links the two attempts in insertion order
    assert attempts[1]["prev_entry_hash"] == attempts[0]["entry_hash"]


def test_retained_provenance_is_append_only() -> None:
    registry = _registry()
    registry.record_trial_provenance(_record())
    connection = registry._store._conn

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute(
            "UPDATE trial_provenance SET status='PASSED_TO_CATALOG'"
            " WHERE trial_id='trial-1'"
        )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("DELETE FROM trial_provenance WHERE trial_id='trial-1'")


def test_tampered_record_breaks_the_chain() -> None:
    """Defeating the trigger is not enough: the recomputed chain still rejects."""
    registry = _registry()
    registry.record_trial_provenance(_record(metrics={"net_sharpe": 0.1}))
    registry.record_trial_provenance(
        _record(trial_id="trial-2", config_hash="cfg-b", started_at=20, completed_at=25)
    )
    assert registry.trial_provenance_chain_valid() is True

    connection = registry._store._conn
    connection.execute("DROP TRIGGER trial_provenance_no_update")
    forged = json.loads(
        connection.execute(
            "SELECT record_json FROM trial_provenance WHERE trial_id='trial-1'"
        ).fetchone()["record_json"]
    )
    forged["metrics"]["net_sharpe"] = 9.9  # the metric a cheater would edit
    connection.execute(
        "UPDATE trial_provenance SET record_json=? WHERE trial_id='trial-1'",
        (json.dumps(forged, ensure_ascii=True, sort_keys=True, separators=(",", ":")),),
    )
    connection.commit()
    assert registry.trial_provenance_chain_valid() is False


def test_deleting_a_retained_trial_breaks_the_chain() -> None:
    registry = _registry()
    for index in range(3):
        registry.record_trial_provenance(
            _record(
                trial_id=f"trial-{index}",
                config_hash=f"cfg-{index}",
                started_at=index * 10,
                completed_at=index * 10 + 5,
            )
        )
    assert registry.trial_provenance_chain_valid() is True

    connection = registry._store._conn
    connection.execute("DROP TRIGGER trial_provenance_no_delete")
    connection.execute("DELETE FROM trial_provenance WHERE trial_id='trial-1'")
    connection.commit()
    assert registry.trial_provenance_chain_valid() is False


def test_provenance_for_a_config_absent_from_trials_is_rejected_by_verify() -> None:
    """The two writes are one transaction; if someone forces them apart, verify
    must not call the ledger consistent."""
    registry = _registry()
    registry.record_trial_provenance(_record())
    connection = registry._store._conn
    connection.execute("DROP TRIGGER trials_no_delete")
    connection.execute("DELETE FROM trials WHERE config_hash='cfg-a'")
    connection.commit()
    assert registry.trial_provenance_chain_valid() is False


def test_unknown_trial_reads_as_none_not_as_an_error() -> None:
    registry = _registry()
    assert registry.trial_provenance("nope") is None
    assert registry.trial_provenance_for_config("nope") == []
    assert registry.trial_provenance_head() == ""
    assert registry.trial_provenance_chain_valid() is True  # empty chain is valid


def test_oos_peek_totals_report_the_worst_attempt_per_config() -> None:
    registry = _registry()
    registry.record_trial_provenance(
        _record(trial_id="t1", viewed_oos_boundaries=("2025-01-01",))
    )
    registry.record_trial_provenance(
        _record(
            trial_id="t2",
            started_at=20,
            completed_at=25,
            viewed_oos_boundaries=("2025-01-01", "2025-06-30", "2025-12-31"),
        )
    )
    assert registry.oos_peek_totals() == {"cfg-a": 3}


def test_existing_v2_store_migrates_and_keeps_its_rows(tmp_path) -> None:
    """The lazy migration must add the table without touching retained history."""
    path = str(tmp_path / "registry.db")
    store = SqliteExperimentStore(path, create=True)
    store.begin_search_attempt(
        search_id="search-1",
        search_kind="ALPHA_FACTORY",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )
    store.register_search_candidates("search-1", ["legacy-cfg"], registered_at=11)
    store.finish_search_attempt("search-1", status="COMPLETED", recorded_at=12)
    store._conn.close()

    # Simulate a pre-provenance store: drop the new table and pin user_version=2.
    raw = sqlite3.connect(path)
    raw.execute("DROP TRIGGER trial_provenance_no_update")
    raw.execute("DROP TRIGGER trial_provenance_no_delete")
    raw.execute("DROP TABLE trial_provenance")
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()

    migrated = SqliteExperimentStore(path)
    assert migrated.record_trial_provenance(_record()) is True
    assert int(migrated._conn.execute("PRAGMA user_version").fetchone()[0]) == 4
    # pre-existing rows survived the migration untouched
    assert "legacy-cfg" in migrated.all_trial_hashes()
    assert migrated.search_history_valid() is True
    assert migrated.trial_provenance_chain_valid() is True


def test_actual_oos_access_is_append_only_chained_and_counted() -> None:
    registry = _registry()
    registry.record_trial_provenance(
        _record(viewed_oos_boundaries=("legacy-boundary",))
    )
    assert registry.record_search_oos_access(
        "session-1",
        {
            "CPCV_OOS_DISTRIBUTION": ("index:[0,10)", "index:[10,20)"),
            "WALK_FORWARD_NO_PEEK_SELECTION_OOS": ("index:[10,20)",),
        },
        viewed_at=2_000,
    ) == 3

    rows = registry.trial_oos_access("trial-1")
    assert len(rows) == 3
    assert rows[0]["prev_entry_hash"] == ""
    assert rows[1]["prev_entry_hash"] == rows[0]["entry_hash"]
    assert registry.trial_oos_access_head() == rows[-1]["entry_hash"]
    assert registry.trial_oos_access_chain_valid() is True
    # Method is part of access identity: the same index range inspected by two
    # validators is two distinct peeks, plus the immutable legacy boundary.
    assert registry.oos_peek_totals() == {"cfg-a": 4}

    connection = registry._store._conn
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute(
            "UPDATE trial_oos_access SET boundary='forged' WHERE access_seq=1"
        )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("DELETE FROM trial_oos_access WHERE access_seq=1")


def test_v3_store_migrates_oos_chain_without_rewriting_trial_record(tmp_path) -> None:
    path = str(tmp_path / "registry-v3.db")
    original = SqliteExperimentStore(path, create=True)
    original.record_trial_provenance(_record())
    original_record = original.trial_provenance("trial-1")
    original._conn.close()

    raw = sqlite3.connect(path)
    raw.execute("DROP TRIGGER trial_oos_access_no_update")
    raw.execute("DROP TRIGGER trial_oos_access_no_delete")
    raw.execute("DROP TABLE trial_oos_access")
    raw.execute("PRAGMA user_version = 3")
    raw.commit()
    raw.close()

    migrated = SqliteExperimentStore(path)
    assert migrated.record_search_oos_access(
        "session-1",
        {"WALK_FORWARD_NO_PEEK_SELECTION_OOS": ("index:[20,28)",)},
        viewed_at=2_000,
    ) == 1
    assert migrated.trial_provenance("trial-1") == original_record
    assert migrated.trial_oos_access_chain_valid() is True
    assert int(migrated._conn.execute("PRAGMA user_version").fetchone()[0]) == 4


def test_persistence_failure_surfaces_as_trial_persistence_error(tmp_path) -> None:
    """§22.12 — never swallow a persistence failure and carry on."""
    path = str(tmp_path / "registry.db")
    store = SqliteExperimentStore(path, create=True)
    store.record_trial_provenance(_record())
    # Inject a write failure WITHOUT disturbing the append-only guards the schema
    # check requires — otherwise the schema validation fires first and we would
    # not be testing the write path at all.
    store._conn.execute(
        "CREATE TRIGGER trial_provenance_reject_insert"
        " BEFORE INSERT ON trial_provenance"
        " BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END"
    )
    store._conn.commit()
    with pytest.raises(TrialPersistenceError, match="could not be retained"):
        store.record_trial_provenance(_record(trial_id="trial-2", config_hash="cfg-b"))
