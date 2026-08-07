from __future__ import annotations

import sqlite3

import pytest

from quantos.contracts.alpha import ActorKind
from services.orchestration.mft_runtime import (
    MFT_PHASES,
    MftRuntimeBlocked,
    MftTaskResult,
    open_mft_runtime,
)


T0 = 1_700_000_000_000


def _complete(_context):
    return MftTaskResult.completed({"ok": True}, consumed_units=1)


def _handlers(calls=None):
    output = {}
    for phase in MFT_PHASES[:-1]:
        def handler(context, *, _phase=phase):
            if calls is not None:
                calls.append(_phase)
            return _complete(context)

        output[phase] = handler
    return output


def test_ordered_pipeline_checkpoints_and_pre_task32_dispatch_block(tmp_path):
    calls = []
    runtime = open_mft_runtime(
        str(tmp_path / "mft.db"),
        create=True,
        handlers=_handlers(calls),
    )
    try:
        runtime.register_tasks(
            interval_ms=1_000,
            first_due_ms=T0,
            now_ms=T0 - 1_000,
        )
        result = runtime.run_once(
            owner_id="node-a",
            now_ms=T0,
            lease_ms=60_000,
        )
        assert calls == list(MFT_PHASES[:-1])
        assert result["runtime_status"] == "BLOCKED"
        assert result["completed"] == 5
        assert result["failed"] == 1
        dispatch = runtime.checkpoints.latest(T0, "execution_dispatch")
        assert dispatch is not None
        assert dispatch.outcome == "BLOCKED"
        assert dispatch.reason_code == "EXECUTION_DISPATCH_DISABLED_BEFORE_TASK_32"
        assert runtime.checkpoints.chain_valid()
    finally:
        runtime.close()


def test_failure_blocks_all_downstream_handlers_and_never_claims_complete(tmp_path):
    calls = []
    handlers = _handlers(calls)
    handlers["materialize"] = lambda _context: MftTaskResult.blocked("TIMEOUT")
    runtime = open_mft_runtime(
        str(tmp_path / "mft.db"), create=True, handlers=handlers
    )
    try:
        runtime.register_tasks(
            interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
        )
        result = runtime.run_once(
            owner_id="node-a", now_ms=T0, lease_ms=60_000
        )
        assert calls == ["market_health"]
        assert result["runtime_status"] == "BLOCKED"
        assert runtime.checkpoints.latest(T0, "materialize").reason_code == "TIMEOUT"
        for phase in MFT_PHASES[2:-1]:
            checkpoint = runtime.checkpoints.latest(T0, phase)
            assert checkpoint is not None
            assert checkpoint.outcome == "BLOCKED"
            assert checkpoint.reason_code == "UPSTREAM_NOT_COMPLETED"
        assert runtime.checkpoints.latest(
            T0, "execution_dispatch"
        ).reason_code == "EXECUTION_DISPATCH_DISABLED_BEFORE_TASK_32"
    finally:
        runtime.close()


def test_second_instance_is_skipped_and_executes_nothing(tmp_path):
    path = str(tmp_path / "mft.db")
    first = open_mft_runtime(path, create=True, handlers=_handlers())
    first.register_tasks(
        interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
    )
    first.run_once(owner_id="node-a", now_ms=T0, lease_ms=60_000)
    first.close()

    calls = []
    second = open_mft_runtime(path, handlers=_handlers(calls))
    try:
        result = second.run_once(
            owner_id="node-b", now_ms=T0 + 1_000, lease_ms=60_000
        )
        assert result["runtime_status"] == "SKIPPED_NOT_LEADER"
        assert result["skipped_not_leader"] == len(MFT_PHASES) - 1
        assert calls == []
    finally:
        second.close()


def test_crash_leaves_in_flight_unknown_and_reopen_preserves_checkpoints(tmp_path):
    class SimulatedCrash(BaseException):
        pass

    path = str(tmp_path / "mft.db")
    handlers = _handlers()
    handlers["materialize"] = lambda _context: (_ for _ in ()).throw(
        SimulatedCrash()
    )
    runtime = open_mft_runtime(path, create=True, handlers=handlers)
    runtime.register_tasks(
        interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
    )
    with pytest.raises(SimulatedCrash):
        runtime.run_once(owner_id="node-a", now_ms=T0, lease_ms=60_000)
    assert runtime.checkpoints.latest(T0, "market_health").outcome == "COMPLETED"
    runtime.close()

    reopened = open_mft_runtime(path, handlers=_handlers())
    try:
        report = reopened.kernel.recovery_report(now_ms=T0 + 1)
        assert len(report.in_flight_commands) == 1
        assert report.in_flight_commands[0].command_type.endswith("materialize")
        assert reopened.checkpoints.chain_valid()
    finally:
        reopened.close()


def test_retry_keeps_original_cycle_and_resumes_after_completed_upstream(tmp_path):
    calls = []
    attempts = {"materialize": 0}
    handlers = _handlers(calls)

    def materialize(_context):
        attempts["materialize"] += 1
        if attempts["materialize"] == 1:
            return MftTaskResult.blocked("TRANSIENT")
        calls.append("materialize")
        return MftTaskResult.completed({"resumed": True})

    handlers["materialize"] = materialize
    runtime = open_mft_runtime(
        str(tmp_path / "mft.db"),
        create=True,
        handlers=handlers,
        backoff_base_ms=1,
        backoff_cap_ms=1,
    )
    try:
        runtime.register_tasks(
            interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
        )
        runtime.run_once(owner_id="node-a", now_ms=T0, lease_ms=60_000)
        second = runtime.run_once(
            owner_id="node-a", now_ms=T0 + 1, lease_ms=60_000
        )
        assert second["cycle_slot_ms"] == T0
        assert calls.count("market_health") == 1
        assert calls.count("materialize") == 1
        assert runtime.checkpoints.latest(T0, "materialize").outcome == "COMPLETED"
    finally:
        runtime.close()


def test_completed_checkpoint_short_circuits_recovery_attempt(tmp_path):
    calls = []
    runtime = open_mft_runtime(
        str(tmp_path / "mft.db"), create=True, handlers=_handlers(calls)
    )
    try:
        runtime.register_tasks(
            interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
        )
        lease = runtime.kernel.leases.acquire(
            "autonomy-kernel",
            owner_id="node-a",
            now_ms=T0 - 1,
            lease_ms=60_000,
        )
        runtime.checkpoints.append(
            cycle_slot_ms=T0,
            phase="market_health",
            attempt=0,
            command_id=f"sched:mft.00.market_health:{T0}:0",
            outcome="COMPLETED",
            reason_code="",
            consumed_units=1,
            result_hash="a" * 64,
            fencing_token=lease.fencing_token,
            recorded_at_ms=T0 - 1,
        )
        runtime.run_once(owner_id="node-a", now_ms=T0, lease_ms=60_000)
        assert "market_health" not in calls
        assert calls[:4] == [
            "materialize",
            "hourly_alpha",
            "paper",
            "intent_projection",
        ]
        assert runtime.checkpoints.event_count() == len(MFT_PHASES)
    finally:
        runtime.close()


def test_missed_cycles_are_coalesced_by_existing_scheduler(tmp_path):
    runtime = open_mft_runtime(
        str(tmp_path / "mft.db"), create=True, handlers=_handlers()
    )
    try:
        runtime.register_tasks(
            interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
        )
        result = runtime.run_once(
            owner_id="node-a", now_ms=T0 + 3_500, lease_ms=60_000
        )
        assert result["coalesced"] == 3 * len(MFT_PHASES)
        assert result["cycle_slot_ms"] == T0 + 3_000
        assert result["runtime_status"] == "BLOCKED"
    finally:
        runtime.close()


def test_stale_fencing_token_cannot_write_checkpoint(tmp_path):
    path = str(tmp_path / "mft.db")
    runtime = None

    def steal(context):
        current = runtime.kernel.leases.current(
            "autonomy-kernel", now_ms=context.now_ms
        )
        assert current is not None
        runtime.kernel.leases.release(current, now_ms=context.now_ms)
        runtime.kernel.leases.acquire(
            "autonomy-kernel",
            owner_id="node-b",
            now_ms=context.now_ms,
            lease_ms=60_000,
        )
        return MftTaskResult.completed({"should_not_commit": True})

    handlers = _handlers()
    handlers["market_health"] = steal
    runtime = open_mft_runtime(path, create=True, handlers=handlers)
    try:
        runtime.register_tasks(
            interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
        )
        result = runtime.run_once(
            owner_id="node-a", now_ms=T0, lease_ms=60_000
        )
        assert result["runtime_status"] == "BLOCKED"
        assert runtime.checkpoints.latest(T0, "market_health") is None
        assert runtime.checkpoints.event_count() == 0
    finally:
        runtime.close()


def test_deadline_budget_and_checkpoint_tamper_fail_closed(tmp_path):
    path = str(tmp_path / "mft.db")
    handlers = _handlers()
    handlers["market_health"] = lambda _context: MftTaskResult.completed(
        {"too_much": True}, consumed_units=10_000_000
    )
    runtime = open_mft_runtime(path, create=True, handlers=handlers)
    try:
        runtime.register_tasks(
            interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
        )
        result = runtime.run_once(
            owner_id="node-a", now_ms=T0, lease_ms=60_000
        )
        assert result["runtime_status"] == "BLOCKED"
        assert runtime.checkpoints.latest(T0, "market_health").reason_code == (
            "BUDGET_EXCEEDED"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            runtime.checkpoints._conn.execute(  # noqa: SLF001
                "DELETE FROM mft_runtime_checkpoints"
            )
    finally:
        runtime.close()


def test_deadline_and_dlq_are_deterministic(tmp_path):
    deadline_runtime = open_mft_runtime(
        str(tmp_path / "deadline.db"), create=True, handlers=_handlers()
    )
    try:
        deadline_runtime.register_tasks(
            interval_ms=100_000,
            first_due_ms=T0,
            now_ms=T0 - 100_000,
        )
        result = deadline_runtime.run_once(
            owner_id="node-a",
            now_ms=T0 + 60_001,
            lease_ms=120_000,
        )
        assert result["runtime_status"] == "BLOCKED"
        assert deadline_runtime.checkpoints.latest(
            T0, "market_health"
        ).reason_code == "DEADLINE_EXCEEDED"
    finally:
        deadline_runtime.close()

    handlers = _handlers()
    handlers["market_health"] = lambda _context: MftTaskResult.blocked("TIMEOUT")
    dlq_runtime = open_mft_runtime(
        str(tmp_path / "dlq.db"),
        create=True,
        handlers=handlers,
        backoff_base_ms=1,
        backoff_cap_ms=1,
    )
    try:
        dlq_runtime.register_tasks(
            interval_ms=1_000,
            first_due_ms=T0,
            now_ms=T0 - 1_000,
            max_attempts=2,
        )
        first = dlq_runtime.run_once(
            owner_id="node-a", now_ms=T0, lease_ms=60_000
        )
        assert first["dlq"] == 0
        second = dlq_runtime.run_once(
            owner_id="node-a", now_ms=T0 + 1, lease_ms=60_000
        )
        assert second["dlq"] == len(MFT_PHASES)
        assert len(dlq_runtime.kernel.tasks.dead_tasks()) == len(MFT_PHASES)
    finally:
        dlq_runtime.close()


def test_foreign_due_task_is_never_consumed(tmp_path):
    calls = []
    runtime = open_mft_runtime(
        str(tmp_path / "mft.db"), create=True, handlers=_handlers(calls)
    )
    try:
        runtime.register_tasks(
            interval_ms=1_000, first_due_ms=T0, now_ms=T0 - 1_000
        )
        runtime.kernel.tasks.register(
            "foreign",
            kind="ONE_SHOT",
            run_at_ms=T0,
            payload={},
            now_ms=T0 - 1,
        )
        with pytest.raises(MftRuntimeBlocked, match="foreign"):
            runtime.run_once(owner_id="node-a", now_ms=T0, lease_ms=60_000)
        assert calls == []
        assert runtime.kernel.tasks.runs("foreign") == ()
    finally:
        runtime.close()


def test_runtime_rejects_human_actor_and_unregistered_tasks(tmp_path):
    runtime = open_mft_runtime(
        str(tmp_path / "mft.db"), create=True, handlers=_handlers()
    )
    try:
        idle = runtime.run_once(
            owner_id="node-a", now_ms=T0, lease_ms=60_000
        )
        assert idle["runtime_status"] == "IDLE"
        assert runtime.actor.kind is ActorKind.SYSTEM
        with pytest.raises(MftRuntimeBlocked):
            runtime.register_tasks(
                interval_ms=1_000,
                first_due_ms=T0 - 2_000,
                now_ms=T0 - 1_000,
            )
    finally:
        runtime.close()
