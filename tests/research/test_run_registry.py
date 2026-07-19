"""run_registry: durable, fail-closed store of research-run records (research-ops console source of truth).

Pure stdlib sqlite3; deterministic (timestamps injected, no wall-clock); only whitelisted research kinds
may be stored (a non-research kind like an order is rejected at the registry boundary)."""
from dataclasses import replace
import json
import sqlite3
import threading

import pytest

from services.research.run_registry.contract import (
    RUN_OWNER_RUNNER_INVOCATION,
    VALID_STATUS,
    InvocationConflictError,
    ResearchRun,
    RunIdentityError,
    make_request_hash,
    make_run_id,
    make_run_identity_hash,
)
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry


def _run(**kw):
    base = dict(run_id="r1", kind="carry_strategy", title="t", params={"q": 0.3}, status="done",
                created_ts=1000, finished_ts=1010, metrics={"net_sharpe": 2.03},
                series={"per_year": [4.4, 2.8]}, verdict="DEPLOYABLE", caveats=["survivorship"],
                provenance="tool:carry", error=None)
    base.update(kw)
    return ResearchRun(**base)


def _runner_run(invocation_id="inv-1", **kw):
    base = _run(**kw)
    run_id = make_run_id(
        base.kind, base.params, base.created_ts, invocation_id=invocation_id
    )
    request_hash = make_request_hash(base.kind, base.params, base.operator)
    identity_hash = make_run_identity_hash(
        run_id=run_id,
        kind=base.kind,
        params=base.params,
        created_ts=base.created_ts,
        operator=base.operator,
        code_version=base.code_version,
        invocation_id=invocation_id,
        owner=RUN_OWNER_RUNNER_INVOCATION,
    )
    return replace(
        base,
        run_id=run_id,
        invocation_id=invocation_id,
        owner=RUN_OWNER_RUNNER_INVOCATION,
        request_hash=request_hash,
        identity_hash=identity_hash,
    )


def test_run_id_is_deterministic():
    a = make_run_id("carry_strategy", {"q": 0.3}, 1000)
    assert a == make_run_id("carry_strategy", {"q": 0.3}, 1000)
    assert a != make_run_id("carry_strategy", {"q": 0.3}, 1001)
    assert a.startswith("run_")


def test_run_id_binds_unique_invocation_identity():
    first = make_run_id(
        "carry_strategy", {"q": 0.3}, 1000, invocation_id="invocation-a"
    )
    assert first == make_run_id(
        "carry_strategy", {"q": 0.3}, 1000, invocation_id="invocation-a"
    )
    assert first != make_run_id(
        "carry_strategy", {"q": 0.3}, 1000, invocation_id="invocation-b"
    )
    assert first == make_run_id(
        "oi_research", {"different": True}, 9999, invocation_id="invocation-a"
    )
    with pytest.raises(ValueError, match="invocation_id"):
        make_run_id("carry_strategy", {}, 1000, invocation_id="")


def test_store_creates_missing_parent_dir(tmp_path):
    # CI / fresh-checkout: the db lives under a gitignored dir (data/cache) that does NOT exist yet.
    # The store must create its parent dir, not crash with "unable to open database file".
    nested = tmp_path / "data" / "cache" / "research_runs.db"
    assert not nested.parent.exists()
    s = ResearchRunStore(str(nested))                 # must not raise
    assert nested.parent.is_dir()
    s.upsert(_run())
    assert s.get("r1") is not None


def test_store_in_memory_ok():
    # an in-memory db has no parent path; the dir-creation guard must skip it cleanly
    s = ResearchRunStore(":memory:")
    s.upsert(_run())
    assert s.get("r1") is not None


def test_store_round_trip(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run())
    got = s.get("r1")
    assert got.kind == "carry_strategy" and got.metrics["net_sharpe"] == 2.03
    assert got.series["per_year"] == [4.4, 2.8] and got.caveats == ["survivorship"]


def test_insert_rejects_collision_without_replacing_existing_run(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    original = _runner_run("inv-a", operator="alice")
    s.insert(original)
    with pytest.raises(ValueError, match="already exists"):
        s.insert(original)
    retained = s.get(original.run_id)
    assert retained.operator == "alice"
    assert retained.invocation_id == "inv-a"


def test_terminal_replace_and_status_transitions_are_cas_guarded(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    queued = _runner_run("inv-cas", status="queued")
    s.insert(queued)
    assert s.compare_and_set_status(
        queued.run_id, expected_statuses=("queued",), status="running"
    ) is True
    assert s.compare_and_set_status(
        queued.run_id, expected_statuses=("queued",), status="cancelled"
    ) is False
    completed = replace(queued, status="done", verdict="DONE")
    assert s.replace_if_status(completed, expected_status="queued") is False
    assert s.replace_if_status(completed, expected_status="running") is True
    assert s.get(queued.run_id).status == "done"


def test_same_invocation_is_idempotent_across_seconds_and_connections(tmp_path):
    path = str(tmp_path / "idempotent.db")
    first_store = ResearchRunStore(path)
    second_store = ResearchRunStore(path)
    first = _runner_run("stable", status="queued", created_ts=1000, operator="alice")
    retry = _runner_run("stable", status="queued", created_ts=1001, operator="alice")

    persisted, inserted = first_store.insert_or_get(first)
    assert inserted is True and persisted.created_ts == 1000
    persisted, inserted = second_store.insert_or_get(retry)
    assert inserted is False
    assert persisted.run_id == first.run_id
    assert persisted.created_ts == 1000


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "oi_research"},
        {"params": {"q": 0.4}},
        {"operator": "bob"},
    ],
)
def test_same_invocation_conflicting_request_fails_closed(tmp_path, changes):
    store = ResearchRunStore(str(tmp_path / "conflict.db"))
    original = _runner_run("stable", status="queued", operator="alice")
    store.insert_or_get(original)
    conflicting = _runner_run(
        "stable", status="queued", operator=changes.get("operator", "alice"),
        kind=changes.get("kind", original.kind),
        params=changes.get("params", original.params),
    )
    with pytest.raises(InvocationConflictError, match="different kind, params, or operator"):
        store.insert_or_get(conflicting)
    assert store.get(original.run_id).operator == "alice"


def test_two_connections_concurrently_insert_one_global_invocation(tmp_path):
    path = str(tmp_path / "concurrent.db")
    stores = (ResearchRunStore(path), ResearchRunStore(path))
    run = _runner_run("one-global-key", status="queued", operator="alice")
    barrier = threading.Barrier(2)
    results = []
    failures = []

    def insert(store):
        try:
            barrier.wait()
            results.append(store.insert_or_get(run))
        except Exception as exc:  # pragma: no cover - asserted below with useful diagnostics
            failures.append(exc)

    threads = [threading.Thread(target=insert, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert failures == []
    assert sorted(inserted for _row, inserted in results) == [False, True]
    assert {row.run_id for row, _inserted in results} == {run.run_id}
    assert len(stores[0].list()) == 1


def test_database_rejects_same_invocation_under_a_different_run_id(tmp_path):
    store = ResearchRunStore(str(tmp_path / "database-unique.db"))
    run = _runner_run("database-unique", status="queued", operator="alice")
    store.insert(run)
    with pytest.raises(sqlite3.IntegrityError):
        store._c.execute(
            "INSERT INTO runs (run_id,kind,title,status,created_ts,finished_ts,verdict,"
            "provenance,error,payload_json,owner,invocation_id,request_hash,identity_hash) "
            "SELECT ?,kind,title,status,created_ts,finished_ts,verdict,provenance,error,"
            "payload_json,owner,invocation_id,request_hash,identity_hash FROM runs WHERE run_id=?",
            ("run_different_primary_key", run.run_id),
        )
    store._c.rollback()
    assert len(store.list()) == 1


def _rehashed(run, **changes):
    proposed = replace(run, **changes)
    request_hash = make_request_hash(proposed.kind, proposed.params, proposed.operator)
    identity_hash = make_run_identity_hash(
        run_id=proposed.run_id,
        kind=proposed.kind,
        params=proposed.params,
        created_ts=proposed.created_ts,
        operator=proposed.operator,
        code_version=proposed.code_version,
        invocation_id=proposed.invocation_id,
        owner=proposed.owner,
    )
    return replace(proposed, request_hash=request_hash, identity_hash=identity_hash)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "oi_research"},
        {"params": {"q": 0.99}},
        {"created_ts": 9999},
        {"operator": "mallory"},
        {"code_version": "deadbeefcafe"},
    ],
)
def test_terminal_publish_cannot_rewrite_any_immutable_identity(tmp_path, changes):
    store = ResearchRunStore(str(tmp_path / "immutable.db"))
    queued = _runner_run("immutable", status="queued", operator="alice")
    store.insert(queued)
    assert store.compare_and_set_status(
        queued.run_id, expected_statuses=("queued",), status="running"
    )
    forged = _rehashed(replace(queued, status="done"), **changes)

    with pytest.raises(RunIdentityError, match="immutable run identity"):
        store.replace_if_status(forged, expected_status="running")
    retained = store.get(queued.run_id)
    assert retained.status == "running"
    assert retained.params == queued.params
    assert retained.operator == "alice"
    assert retained.created_ts == queued.created_ts


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "run_forged"},
        {"invocation_id": "different-invocation"},
        {"owner": "LEGACY_MIRROR"},
        {"request_hash": "0" * 64},
        {"identity_hash": "0" * 64},
    ],
)
def test_terminal_publish_rejects_forged_identity_tokens(tmp_path, changes):
    store = ResearchRunStore(str(tmp_path / "identity-token.db"))
    queued = _runner_run("identity-token", status="queued", operator="alice")
    store.insert(queued)
    assert store.compare_and_set_status(
        queued.run_id, expected_statuses=("queued",), status="running"
    )
    forged = replace(queued, status="done", **changes)

    with pytest.raises(RunIdentityError):
        store.replace_if_status(forged, expected_status="running")
    assert store.get(queued.run_id).status == "running"


def test_legacy_mirror_writes_cannot_overwrite_or_restate_runner_row(tmp_path):
    store = ResearchRunStore(str(tmp_path / "ownership.db"))
    runner = _runner_run("owned", status="queued", operator="alice")
    store.insert(runner)
    mirror = _run(run_id=runner.run_id, status="done", operator="mallory")

    with pytest.raises(RunIdentityError, match="cannot overwrite"):
        store.upsert(mirror)
    with pytest.raises(RunIdentityError, match="cannot mutate"):
        store.set_status(runner.run_id, "done")
    retained = store.get(runner.run_id)
    assert retained.status == "queued"
    assert retained.operator == "alice"


def test_old_schema_is_migrated_and_payload_invocation_becomes_unique(tmp_path):
    path = str(tmp_path / "old.db")
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY,kind TEXT,title TEXT,status TEXT,"
        "created_ts INTEGER,finished_ts INTEGER,verdict TEXT,provenance TEXT,error TEXT,"
        "payload_json TEXT)"
    )
    payload = {
        "params": {"q": 0.3},
        "metrics": {},
        "series": {},
        "caveats": [],
        "log": ["[queued]"],
        "code_version": "abc123def456",
        "operator": "alice",
        "invocation_id": "pre-schema-key",
    }
    connection.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "old-run-id",
            "carry_strategy",
            "old",
            "queued",
            10,
            None,
            "",
            "legacy",
            None,
            json.dumps(payload),
        ),
    )
    connection.commit()
    connection.close()

    store = ResearchRunStore(path)
    migrated = store.get_by_invocation("pre-schema-key")
    assert migrated is not None
    assert migrated.run_id == "old-run-id"
    assert migrated.owner == RUN_OWNER_RUNNER_INVOCATION
    assert migrated.request_hash == make_request_hash(
        migrated.kind, migrated.params, migrated.operator
    )
    index_sql = store._c.execute(
        "SELECT sql FROM sqlite_master WHERE name='ux_runs_runner_invocation'"
    ).fetchone()[0]
    assert "UNIQUE INDEX" in index_sql and "invocation_id" in index_sql


def test_two_connection_cancel_vs_terminal_publish_has_one_winner(tmp_path):
    path = str(tmp_path / "terminal-race.db")
    completing_store = ResearchRunStore(path)
    cancelling_store = ResearchRunStore(path)
    queued = _runner_run("terminal-race", status="queued", operator="alice")
    completing_store.insert(queued)
    assert completing_store.compare_and_set_status(
        queued.run_id, expected_statuses=("queued",), status="running"
    )
    completed = replace(queued, status="done", verdict="DONE", metrics={"score": 1})
    barrier = threading.Barrier(2)
    results = []

    def complete():
        barrier.wait()
        results.append(
            ("done", completing_store.replace_if_status(completed, expected_status="running"))
        )

    def cancel():
        barrier.wait()
        results.append(
            (
                "cancelled",
                cancelling_store.compare_and_set_status(
                    queued.run_id,
                    expected_statuses=("running",),
                    status="cancelled",
                ),
            )
        )

    threads = [threading.Thread(target=complete), threading.Thread(target=cancel)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(won for _status, won in results) == [False, True]
    winner = next(status for status, won in results if won)
    assert completing_store.get(queued.run_id).status == winner


def test_list_is_newest_first_and_filterable(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(run_id="a", kind="carry_strategy", created_ts=1))
    s.upsert(_run(run_id="b", kind="oi_research", created_ts=2))
    assert [r.run_id for r in s.list()] == ["b", "a"]
    assert [r.run_id for r in s.list(kind="oi_research")] == ["b"]


def test_set_status(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(status="queued", run_id="q1"))
    s.set_status("q1", "running")
    assert s.get("q1").status == "running"
    s.set_status("q1", "error", error="boom")
    assert s.get("q1").error == "boom" and s.get("q1").status == "error"


def test_empty_store_returns_none_and_empty(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    assert s.get("nope") is None
    assert s.list() == []


def test_registry_rejects_bad_status(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    with pytest.raises(ValueError):
        reg.upsert(_run(status="bogus"))


def test_registry_rejects_unknown_kind(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    with pytest.raises(ValueError):
        reg.upsert(_run(kind="place_order"))      # only whitelisted research kinds allowed


def test_valid_status_set():
    assert VALID_STATUS == {"queued", "running", "done", "error", "cancelled"}


def test_log_and_code_version_round_trip(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(run_id="L1", log=["[seed] start", "[gen 1] best 0.5"], code_version="abc1234"))
    got = s.get("L1")
    assert got.log == ["[seed] start", "[gen 1] best 0.5"] and got.code_version == "abc1234"


def test_append_log_is_live(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(run_id="P1", status="running", log=[]))
    s.append_log("P1", "[gen 1] done")
    s.append_log("P1", "[gen 2] done")
    assert s.get("P1").log == ["[gen 1] done", "[gen 2] done"]


def test_cancelled_status_is_valid(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    reg.upsert(_run(run_id="C1", status="queued"))
    reg.set_status("C1", "cancelled")
    assert reg.get("C1").status == "cancelled"
