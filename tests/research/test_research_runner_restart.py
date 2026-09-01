"""Runner restart reconciliation closes work that no longer has a process-local future."""

import json

import pytest
from fastapi.testclient import TestClient

import services.research_runner.main as runner_main
from services.monitoring.audit_store import DurableAuditLog
from services.research.run_registry.contract import (
    RUN_OWNER_RUNNER_INVOCATION,
    RUNNER_RESTART_INTERRUPTED,
    ResearchRun,
    RunIdentityError,
    make_request_hash,
    make_run_id,
    make_run_identity_hash,
)
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore


def _runner_run(invocation_id: str, *, status: str, created_ts: int) -> ResearchRun:
    kind = "ashare_technical_search"
    params = {}
    operator = "restart-test"
    code_version = "0123456789ab"
    run_id = make_run_id(kind, params, created_ts, invocation_id=invocation_id)
    return ResearchRun(
        run_id=run_id,
        kind=kind,
        title="A-share technical search",
        params=params,
        status=status,
        created_ts=created_ts,
        finished_ts=created_ts + 1 if status in {"done", "error", "cancelled"} else None,
        metrics={},
        series={},
        verdict="DONE" if status == "done" else "",
        caveats=[],
        provenance="test",
        error=None,
        log=[f"[{status}]"],
        code_version=code_version,
        operator=operator,
        invocation_id=invocation_id,
        owner=RUN_OWNER_RUNNER_INVOCATION,
        request_hash=make_request_hash(kind, params, operator),
        identity_hash=make_run_identity_hash(
            run_id=run_id,
            kind=kind,
            params=params,
            created_ts=created_ts,
            operator=operator,
            code_version=code_version,
            invocation_id=invocation_id,
            owner=RUN_OWNER_RUNNER_INVOCATION,
        ),
    )


def test_service_startup_terminalizes_only_abandoned_runner_work_once(
    tmp_path, monkeypatch
):
    registry = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    queued = _runner_run("restart-queued", status="queued", created_ts=10)
    running = _runner_run("restart-running", status="running", created_ts=20)
    completed = _runner_run("restart-done", status="done", created_ts=30)
    for run in (queued, running, completed):
        registry.insert(run)
    legacy = ResearchRun(
        run_id="legacy-running",
        kind="data_refresh",
        title="external indexed work",
        params={},
        status="running",
        created_ts=40,
        finished_ts=None,
        metrics={},
        series={},
        verdict="",
        caveats=[],
        provenance="legacy",
        log=["[running elsewhere]"],
    )
    registry.upsert(legacy)

    audit = DurableAuditLog(str(tmp_path / "audit.db"))
    monkeypatch.setattr(runner_main, "_registry", registry)
    monkeypatch.setattr(runner_main, "_audit", audit)
    monkeypatch.setattr(runner_main.time, "time", lambda: 1_000)

    with TestClient(runner_main.app) as client:
        assert client.get("/health").status_code == 200

    for original in (queued, running):
        reconciled = registry.get(original.run_id)
        assert reconciled.status == "error"
        assert reconciled.finished_ts == 1_000
        assert reconciled.error == RUNNER_RESTART_INTERRUPTED
        assert reconciled.verdict == ""
        assert reconciled.log == [
            f"[{original.status}]",
            f"[error] {RUNNER_RESTART_INTERRUPTED}",
        ]
    assert registry.get(completed.run_id) == completed
    assert registry.get(legacy.run_id) == legacy

    events = audit.events
    assert [event.target for event in events] == [queued.run_id, running.run_id]
    assert all(event.action == "research.run.restart_interrupted" for event in events)
    assert [event.metadata["previous_status"] for event in events] == [
        "queued",
        "running",
    ]
    assert all(event.metadata["error"] == RUNNER_RESTART_INTERRUPTED for event in events)
    assert audit.chain_valid()

    # A second service lifetime is a no-op: terminal rows stay terminal, logs and audit do not duplicate.
    with TestClient(runner_main.app) as client:
        assert client.get("/health").status_code == 200
    assert len(audit.events) == 2
    assert registry.get(queued.run_id).log.count(
        f"[error] {RUNNER_RESTART_INTERRUPTED}"
    ) == 1


def test_reconciliation_is_all_or_nothing_if_a_runner_row_is_corrupt(tmp_path):
    store = ResearchRunStore(str(tmp_path / "atomic.db"))
    first = _runner_run("valid-stale", status="queued", created_ts=10)
    corrupt = _runner_run("corrupt-stale", status="running", created_ts=20)
    store.insert(first)
    store.insert(corrupt)
    payload = json.loads(
        store._c.execute(
            "SELECT payload_json FROM runs WHERE run_id=?", (corrupt.run_id,)
        ).fetchone()[0]
    )
    payload["log"] = "not-a-list"
    store._c.execute(
        "UPDATE runs SET payload_json=? WHERE run_id=?",
        (json.dumps(payload), corrupt.run_id),
    )
    store._c.commit()

    with pytest.raises(RunIdentityError, match="invalid durable log"):
        store.reconcile_interrupted_runner_runs(finished_ts=1_000)

    # The valid first row was not partially committed before the corrupt second row failed validation.
    retained = store.get(first.run_id)
    assert retained.status == "queued"
    assert retained.finished_ts is None
    assert retained.error is None
    assert retained.log == ["[queued]"]


def test_restart_reconciliation_rejects_invalid_completion_clock(tmp_path):
    registry = RunRegistry(ResearchRunStore(str(tmp_path / "clock.db")))
    registry.insert(_runner_run("clock", status="queued", created_ts=10))

    for invalid in (-1, True, 1.5):
        with pytest.raises(ValueError, match="finished_ts"):
            registry.reconcile_interrupted_runner_runs(finished_ts=invalid)
