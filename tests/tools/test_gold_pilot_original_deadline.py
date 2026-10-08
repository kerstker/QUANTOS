"""Original executor enrollment bounds observation; it never extends permission."""

from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_demo_execution_origin import capture
from tests.live.test_demo_pre_submit import stores, NOW

SOURCE = "c" * 40


@pytest.fixture
def enrolled(tmp_path, monkeypatch):
    from live.execution import demo_authority, demo_gold_pilot as pilot
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution import demo_position_context as position, demo_market_partition_runtime as partition

    root = tmp_path / "executor"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(position, "_ROOT", root)
    monkeypatch.setattr(demo_authority, "load_registry_for_executor", lambda: {"executor_uid": os.getuid()})
    # Only ancestor custody is virtualized. Real native root/file UID, type,
    # mode, no-follow reads and completed enrollment checks remain in use.
    monkeypatch.setattr(demo_authority, "_require_protected", lambda info, **kw: None)
    for name in ("EXECUTOR_RECORD", "ISSUER_RECORD", "ACTIVATION"):
        monkeypatch.setattr(partition, name, tmp_path / name)
    origin, _ = capture()
    account = origin["account_ref_sha256"]
    request = dict(
        schema="GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1",
        source_commit=SOURCE,
        account_ref_sha256=account,
        duration_seconds=86400,
        max_loss_fraction="0.05",
        submission_enabled=False,
    )
    pilot.retain_once(root / "gold-pilot-request.json", request)
    dispatch, recovery, oms = stores(root)
    try:
        config = enrollment.enroll_requested(
            root=root, source_commit=SOURCE, origin=origin, dispatch=dispatch, recovery=recovery, oms=oms, now_ms=NOW
        )
        assert enrollment.completed_configuration(root / "gold-pilot", request=request) == config
        yield SimpleNamespace(
            root=root,
            config=config,
            request=request,
            account=account,
            origin=origin,
            pilot=pilot,
            dispatch=dispatch,
            recovery=recovery,
            oms=oms,
        )
        assert dispatch.snapshot() == [] and dispatch.wire_snapshot() == []
        assert recovery.latest_records() == () and oms.orders() == []
    finally:
        for store in (dispatch, recovery, oms):
            store.close()


def window(value):
    return dict(
        schema="GOLD_PILOT_ORIGINAL_WINDOW_V1",
        source_commit=SOURCE,
        account_ref_sha256=value.account,
        request_sha256=_digest(value.request),
        configuration_sha256=_digest(value.config),
        started_at_ms=value.config["started_at_ms"],
        ends_at_ms=value.config["ends_at_ms"],
        submission_enabled=False,
    )


def observation(value, *, now_ms, flat=True, history_empty=True, equity="5000"):
    control = value.pilot.observe(
        dict(account_ref_sha256=value.account, equity=equity),
        now_ms=now_ms,
        origin_sha256=_digest(value.origin),
        state_root=value.root,
    )
    decision = dict(
        schema="GOLD_STRATEGY_CYCLE_DECISION_V1",
        source_commit=SOURCE,
        bundle_sha256="b" * 64,
        account_ref_sha256=value.account,
        status="PILOT_STOPPED" if control["status"] == "STOPPED" else "NO_ENTRY_SIGNAL",
        position_flat=flat,
        execution_history_empty=history_empty,
        pilot_control=control,
        submission_enabled=False,
        economic_authority=False,
    )
    return dict(
        schema="GOLD_STRATEGY_WORKER_RESULT_V1",
        status="DECISION_RETAINED",
        source_commit=SOURCE,
        bundle_sha256="b" * 64,
        decision_sha256=_digest(decision),
        decision=decision,
        original_pilot_window=window(value),
        submission_enabled=False,
        economic_authority=False,
    )


def clocked(monkeypatch, *, wall_ms):
    from tools import gold_pilot_run as runner

    clock = SimpleNamespace(mono=0.0, wall=wall_ms)

    def advance(seconds):
        clock.mono += seconds
        clock.wall += int(seconds * 1000)

    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(monotonic=lambda: clock.mono, time_ns=lambda: clock.wall * 1000000, sleep=advance),
    )
    return clock, advance


def native_tick(monkeypatch, value, clock, *, before=None):
    from tools import gold_pilot_run as runner

    calls = []

    def controller(**kwargs):
        if before is not None:
            before(len(calls))
        result = observation(value, now_ms=clock.wall)
        runner.tick.controller.validate_result(
            result, source_commit=SOURCE, bundle_sha256="b" * 64, account=value.account
        )
        calls.append(deepcopy(result))
        kwargs["on_decision"](result)
        return result

    monkeypatch.setattr(runner.tick.controller, "run", controller)
    monkeypatch.setattr(runner.tick.dispatcher, "run", lambda **kw: pytest.fail("no signal may dispatch"))
    return calls


def immutable_records(value):
    paths = [value.root / "gold-pilot-request.json"] + [
        value.root / "gold-pilot" / name for name in ("configuration.json", "enrollment.json", "enrollment-intent.json")
    ]
    return {str(p): (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in paths}


def test_late_original_enrollment_reaches_native_stop_after_process_day(enrolled, monkeypatch):
    from tools import gold_pilot_run as runner

    before = immutable_records(enrolled)
    clock, advance = clocked(monkeypatch, wall_ms=NOW - 300000)
    calls = native_tick(monkeypatch, enrolled, clock, before=lambda n: advance(86400) if n == 0 else None)
    result = runner.run(source_commit=SOURCE, execute_approved=True, interval_seconds=60)
    assert result["status"] == "PILOT_STOPPED" and result["pilot_complete"]
    assert clock.mono == 86700 and len(calls) == 6
    assert calls[-1]["decision"]["pilot_control"]["reason"] == "DURATION_COMPLETE"
    assert immutable_records(enrolled) == before


def test_restart_observes_original_end_without_new_day(enrolled, monkeypatch):
    from tools import gold_pilot_run as runner

    before = immutable_records(enrolled)
    clock, _ = clocked(monkeypatch, wall_ms=enrolled.config["ends_at_ms"] - 1000)
    native_tick(monkeypatch, enrolled, clock)
    result = runner.run(source_commit=SOURCE, interval_seconds=1)
    assert result["pilot_complete"] and clock.mono == 1
    assert immutable_records(enrolled) == before


@pytest.mark.parametrize("late_seconds", [100, 900, 901])
def test_late_restart_never_renews_original_closing_budget(enrolled, monkeypatch, late_seconds):
    from tools import gold_pilot_run as runner

    before = immutable_records(enrolled)
    clock, _ = clocked(monkeypatch, wall_ms=enrolled.config["ends_at_ms"] + late_seconds * 1000)
    calls = []

    def controller(**kwargs):
        row = observation(enrolled, now_ms=clock.wall, history_empty=False)
        row["decision"].update(status="RECOVERY_REQUIRED", action="REDUCE_LONG")
        row["decision_sha256"] = _digest(row["decision"])
        row["recovery_observation"] = dict(status="WAITING_QUERY_INTERVAL", new_order_submission_enabled=False)
        runner.tick.controller.validate_result(
            row, source_commit=SOURCE, bundle_sha256="b" * 64, account=enrolled.account
        )
        calls.append(row)
        kwargs["on_decision"](row)
        return row

    monkeypatch.setattr(runner.tick.controller, "run", controller)
    monkeypatch.setattr(runner.tick.dispatcher, "run", lambda **kw: pytest.fail("recovery cannot submit"))
    result = runner.run(source_commit=SOURCE, execute_approved=True, interval_seconds=60)
    assert result["status"] == "PILOT_CLOSING_BUDGET_EXHAUSTED" and not result["pilot_complete"]
    assert clock.mono == max(0, 900 - late_seconds)
    elapsed, observed = clock.mono, len(calls)
    restarted = runner.run(source_commit=SOURCE, execute_approved=True, interval_seconds=60)
    assert restarted["status"] == "PILOT_CLOSING_BUDGET_EXHAUSTED" and not restarted["pilot_complete"]
    assert clock.mono == elapsed and len(calls) == observed + 1
    assert immutable_records(enrolled) == before


def test_wall_clock_rollback_cannot_extend_original_monotonic_bound(enrolled, monkeypatch):
    from tools import gold_pilot_run as runner

    clock, _ = clocked(monkeypatch, wall_ms=enrolled.config["ends_at_ms"] - 1000)

    def rollback(n):
        assert n < 20, "clock rollback extended the original closing budget"
        if n == 1:
            clock.wall -= 3600000

    calls = native_tick(monkeypatch, enrolled, clock, before=rollback)
    result = runner.run(source_commit=SOURCE, interval_seconds=60)
    assert result["status"] == "PILOT_CLOSING_BUDGET_EXHAUSTED" and not result["pilot_complete"]
    assert clock.mono == 901 and len(calls) == 16
    assert not (enrolled.root / "gold-pilot" / "stopped.json").exists()


def test_original_loss_stop_is_not_delayed_until_duration_end(enrolled, monkeypatch):
    from tools import gold_pilot_run as runner

    clock, _ = clocked(monkeypatch, wall_ms=NOW)
    row = observation(enrolled, now_ms=NOW, equity="4750")

    def controller(**kw):
        kw["on_decision"](row)
        return row

    monkeypatch.setattr(runner.tick.controller, "run", controller)
    result = runner.run(source_commit=SOURCE)
    assert result["pilot_complete"] and result["status"] == "PILOT_STOPPED" and clock.mono == 0
    assert row["decision"]["pilot_control"]["reason"] == "LOSS_LIMIT"


def test_original_authority_expiry_attention_is_never_retried(enrolled, monkeypatch):
    from tools import gold_pilot_run as runner

    clock, _ = clocked(monkeypatch, wall_ms=enrolled.config["ends_at_ms"] - 1000)
    rows = iter([observation(enrolled, now_ms=clock.wall), dict(status="INPUTS_UNRESOLVED", phase="CONFIGURATION")])
    calls = []

    def controller(**kw):
        row = next(rows)
        calls.append(row)
        if row["status"] == "DECISION_RETAINED":
            kw["on_decision"](row)
        return row

    monkeypatch.setattr(runner.tick.controller, "run", controller)
    result = runner.run(source_commit=SOURCE, execute_approved=True, interval_seconds=1)
    assert result["status"] == "OBSERVATION_REQUIRES_ATTENTION" and not result["pilot_complete"]
    assert len(calls) == 2 and clock.mono == 1


def test_short_explicit_budget_stays_diagnostic(enrolled, monkeypatch):
    from tools import gold_pilot_run as runner

    clock, _ = clocked(monkeypatch, wall_ms=NOW)
    calls = native_tick(monkeypatch, enrolled, clock)
    result = runner.run(source_commit=SOURCE, max_seconds=3, interval_seconds=2)
    assert result["status"] == "SUPERVISOR_BUDGET_EXHAUSTED" and not result["pilot_complete"]
    assert clock.mono == 3 and len(calls) == 2
    assert not (enrolled.root / "gold-pilot" / "stopped.json").exists()


@pytest.mark.parametrize("flat", [True, False, None])
def test_original_closing_has_finite_budget_and_never_claims_completion(enrolled, monkeypatch, flat):
    from tools import gold_pilot_run as runner

    clock, advance = clocked(monkeypatch, wall_ms=enrolled.config["ends_at_ms"])
    row = observation(enrolled, now_ms=clock.wall, flat=flat, history_empty=False)
    row["decision"]["status"] = "RECOVERY_REQUIRED"
    row["decision_sha256"] = _digest(row["decision"])
    row["recovery_observation"] = dict(status="WAITING_QUERY_INTERVAL", new_order_submission_enabled=False)
    calls = []

    def controller(**kw):
        calls.append(1)
        assert len(calls) <= 10, "closing escaped its bounded observation budget"
        advance(300)
        kw["on_decision"](row)
        return row

    # Native tick preserves its exit attention when a stopped position is not
    # known flat. Only the existing reconciliation status may continue.
    if flat is True:
        row["decision"]["action"] = "REDUCE_LONG"
    monkeypatch.setattr(runner.tick.controller, "run", controller)
    result = runner.run(source_commit=SOURCE, interval_seconds=60)
    expected = "PILOT_CLOSING_BUDGET_EXHAUSTED" if flat is True else "PILOT_EXIT_REQUIRES_ATTENTION"
    assert result["status"] == expected and not result["pilot_complete"]
    assert clock.mono <= 1500


def test_closing_can_complete_only_through_original_settled_feedback(enrolled, monkeypatch):
    from tools import gold_pilot_run as runner

    clock, _ = clocked(monkeypatch, wall_ms=enrolled.config["ends_at_ms"])
    calls = []

    def controller(**kw):
        row = observation(enrolled, now_ms=clock.wall, history_empty=False)
        row["decision"]["status"] = "STRATEGY_EPISODE_SETTLED"
        row["decision_sha256"] = _digest(row["decision"])
        if calls:
            row["research_feedback"] = {"status": "RESEARCH_FEEDBACK_DELIVERED"}
        calls.append(row)
        kw["on_decision"](row)
        return row

    monkeypatch.setattr(runner.tick.controller, "run", controller)
    result = runner.run(source_commit=SOURCE, interval_seconds=1)
    assert result["pilot_complete"] and result["status"] == "PILOT_STOPPED" and len(calls) == 2


def test_full_day_missing_original_window_fails_closed(enrolled, monkeypatch):
    from tools import gold_pilot_run as runner

    row = observation(enrolled, now_ms=NOW)
    row.pop("original_pilot_window")
    monkeypatch.setattr(
        runner.tick, "run_loop", lambda **kw: dict(status="OBSERVATION_COMPLETE", ticks=[{"observation": row}])
    )
    monkeypatch.setattr(runner.time, "sleep", lambda _: pytest.fail("missing original window must not continue"))
    result = runner.run(source_commit=SOURCE)
    assert result["status"] == "PILOT_ORIGINAL_WINDOW_REQUIRED" and not result["pilot_complete"]


def test_original_window_change_never_rebinds_or_extends(enrolled, monkeypatch):
    from tools import gold_pilot_run as runner

    clock, _ = clocked(monkeypatch, wall_ms=NOW)
    first = observation(enrolled, now_ms=NOW)
    second = deepcopy(first)
    second["original_pilot_window"]["request_sha256"] = "f" * 64
    rows = iter((first, second))
    calls = []

    def run_loop(**kw):
        calls.append(1)
        row = next(rows)
        return dict(status="OBSERVATION_COMPLETE", ticks=[{"observation": row}])

    monkeypatch.setattr(runner.tick, "run_loop", run_loop)
    result = runner.run(source_commit=SOURCE, interval_seconds=1)
    assert result["status"] == "PILOT_ORIGINAL_WINDOW_CHANGED" and not result["pilot_complete"]
    assert len(calls) == 2 and clock.mono == 1


def test_worker_exports_original_native_enrollment_without_changing_decision(enrolled):
    from tools import gold_strategy_worker as worker

    row = observation(enrolled, now_ms=NOW)
    before = immutable_records(enrolled)
    decision = deepcopy(row["decision"])
    result = worker.original_pilot_window(
        enrolled.root, decision=decision, source_commit=SOURCE, account=enrolled.account
    )
    assert result == window(enrolled) and decision == row["decision"]
    assert _digest(decision) == row["decision_sha256"] and immutable_records(enrolled) == before


def test_worker_preserves_actual_executor_uid_gate(enrolled, monkeypatch):
    from tools import gold_strategy_worker as worker
    from live.execution import demo_authority

    row = observation(enrolled, now_ms=NOW)
    monkeypatch.setattr(demo_authority, "load_registry_for_executor", lambda: {"executor_uid": os.getuid() + 1})
    with pytest.raises(ValueError, match="CUSTODY"):
        worker.original_pilot_window(
            enrolled.root, decision=row["decision"], source_commit=SOURCE, account=enrolled.account
        )


@pytest.mark.parametrize(
    "damage", ["missing_receipt", "configuration", "request_source", "request_account", "decision_digest"]
)
def test_worker_window_rejects_missing_or_changed_originals(enrolled, damage):
    from tools import gold_strategy_worker as worker

    row = observation(enrolled, now_ms=NOW)
    if damage == "decision_digest":
        row["decision"]["pilot_control"]["configuration_sha256"] = "f" * 64
    elif damage == "missing_receipt":
        (enrolled.root / "gold-pilot" / "enrollment.json").unlink()
    else:
        path = (
            enrolled.root / "gold-pilot-request.json"
            if damage.startswith("request")
            else enrolled.root / "gold-pilot" / "configuration.json"
        )
        value = json.loads(path.read_bytes())
        if damage == "configuration":
            value["started_at_ms"] += 1
            value["ends_at_ms"] += 1
        else:
            value["source_commit" if damage == "request_source" else "account_ref_sha256"] = "f" * (
                40 if damage == "request_source" else 64
            )
        path.write_text(json.dumps(value))
    with pytest.raises((ValueError, FileNotFoundError)):
        worker.original_pilot_window(
            enrolled.root, decision=row["decision"], source_commit=SOURCE, account=enrolled.account
        )


@pytest.mark.parametrize(
    "damage", ["source_commit", "account_ref_sha256", "configuration_sha256", "request_sha256", "ends_at_ms", "extra"]
)
def test_controller_rejects_window_identity_and_shape_changes(enrolled, damage):
    from tools import gold_strategy_controller as controller

    row = observation(enrolled, now_ms=NOW)
    if damage == "ends_at_ms":
        row["original_pilot_window"][damage] += 1
    elif damage == "extra":
        row["original_pilot_window"]["renewed"] = True
    else:
        row["original_pilot_window"][damage] = "bad"
    with pytest.raises(ValueError, match="ORIGINAL_WINDOW"):
        controller.validate_result(row, source_commit=SOURCE, bundle_sha256="b" * 64, account=enrolled.account)
