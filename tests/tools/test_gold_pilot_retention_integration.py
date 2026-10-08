"""Native enrollment/retention bridges, with explicit local custody fixtures."""

from copy import deepcopy
import json
import os

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_pilot_retention import enrolled as enrolled, bundle, packet, NOW, SOURCE


@pytest.fixture
def roots(enrolled, tmp_path, monkeypatch):
    from tools import demo_operating_bundle as installer, gold_strategy_controller as controller

    root = tmp_path / "controller"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(installer, "EXECUTOR", enrolled.root)
    monkeypatch.setattr(
        controller,
        "observation_configuration",
        lambda source: ({"executor_uid": os.getuid()}, {"account_ref_sha256": enrolled.account}),
    )
    return enrolled, root


def retained(value, at=NOW):
    from tools import gold_pilot_retention as bridge

    db = bridge.executor_retention(value.root, bundle(), source_commit=SOURCE, account=value.account, uid=os.getuid())
    claim = db.claim(bundle=bundle(), now_ms=at)
    row = packet(value, at)
    db.put(claim, "decision", row["decision"])
    db.put(claim, "result", row)
    db.close()
    return row


def test_exact_native_copy_and_read_keep_worker_bytes_and_original_claim(roots):
    from tools import gold_pilot_retention as bridge

    value, root = roots
    row = retained(value)
    bridge.controller_retain(
        root, row, base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
    )
    raw = bridge.read_controller_result(root, _digest(row), source_commit=SOURCE, account=value.account)
    assert json.loads(raw) == row
    assert not (root / "strategy-decisions").exists()
    assert not (value.root / "strategy-decisions").exists()


def test_missing_executor_original_rejects_even_with_same_legacy_copy(roots):
    from tools import gold_pilot_retention as bridge

    value, root = roots
    row = retained(value)
    (value.root / bridge.AREA / "records.sqlite3").unlink()
    target = root / "strategy-decisions"
    target.mkdir(mode=0o700)
    (target / (_digest(row) + ".json")).write_text(json.dumps(row))
    with pytest.raises((ValueError, FileNotFoundError)):
        bridge.controller_retain(
            root, row, base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
        )
    assert not (root / bridge.MARKER).exists()


def test_controller_restart_recovers_only_original_complete_results_before_capture(roots):
    from tools import gold_pilot_retention as bridge

    value, root = roots
    first = retained(value)
    second = retained(value, NOW + 1000)
    with bridge.ControllerRetention(
        root, base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
    ) as session:
        assert session.store.metadata()["frames"] == 2
        assert json.loads(session.store.read("result", _digest(first))) == first
        assert json.loads(session.store.read("result", _digest(second))) == second
    assert not (root / "tasks").exists()


def test_controller_attachment_uses_original_frame_without_new_quota(roots):
    from tools import gold_pilot_retention as bridge

    value, root = roots
    row = retained(value)
    with bridge.ControllerRetention(
        root, base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
    ) as session:
        session.retain(row)
        request = dict(schema="TEST_APPROVAL_REFERENCE", source_commit=SOURCE, result_sha256=_digest(row))
        session.attach(row, "approval", request)
        assert session.store.metadata()["frames"] == 1
    assert (
        json.loads(bridge.read_controller_approval(root, _digest(request), source_commit=SOURCE, account=value.account))
        == request
    )


def test_changed_original_configuration_rejects_native_reader(roots):
    from tools import gold_pilot_retention as bridge

    value, root = roots
    row = retained(value)
    bridge.controller_retain(
        root, row, base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
    )
    path = value.root / "gold-pilot" / "configuration.json"
    changed = deepcopy(json.loads(path.read_bytes()))
    changed["initial_equity"] = "6000"
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError):
        bridge.read_controller_result(root, _digest(row), source_commit=SOURCE, account=value.account)


from tests.tools.test_gold_pilot_initial_capture import pending as pending, invoke, auto_fixture


def test_worker_claims_before_capture_and_releases_before_result_publication(pending, monkeypatch):
    import builtins
    from types import SimpleNamespace
    from tools import gold_strategy_worker as worker, gold_pilot_retention as bridge
    from live.execution import demo_gold_pilot_enrollment as enrollment

    runtime = auto_fixture(pending, monkeypatch)
    seen = []

    def frame(root, bundle, **kwargs):
        assert (root / bridge.MARKER).exists(), "native claim must precede fresh capture"
        seen.append("capture")
        return object(), {"definition_hash": "f" * 64}, NOW, lambda: {"definition_hash": "f" * 64}

    def prepare(**kwargs):
        config = enrollment.completed_configuration(pending.root / "gold-pilot", request=pending.request)
        return dict(
            schema="GOLD_STRATEGY_CYCLE_DECISION_V1",
            status="NO_ENTRY_SIGNAL",
            submission_enabled=False,
            economic_authority=False,
            pilot_control=dict(status="ACTIVE", configuration_sha256=_digest(config), submission_enabled=False),
        )

    monkeypatch.setattr(worker.gold_bar_demo_cycle, "prepare", prepare)

    def emit(*args, **kwargs):
        value = json.loads(args[0])
        if value["status"] == "DECISION_RETAINED":
            original = dict(
                request=pending.request,
                configuration=enrollment.completed_configuration(pending.root / "gold-pilot", request=pending.request),
            )
            with bridge.Retention(
                pending.root, original=original, base_runtime=runtime, uid=pending.uid, role="executor", read_only=True
            ) as db:
                assert json.loads(db.read("result", _digest(value))) == value
            seen.append("published_unlocked")
        return builtins.print(*args, **kwargs)

    monkeypatch.setattr(worker, "print", emit, raising=False)
    import os

    read, write = os.pipe()
    out_read, out_write = os.pipe()
    with os.fdopen(write, "wb") as channel:
        channel.write(json.dumps(runtime).encode() + b"\n")
    try:
        with os.fdopen(out_write, "w") as output:
            code = worker.run(
                source_commit=SOURCE, input_fd=read, output=output, bar_sessions=SimpleNamespace(frame=frame)
            )
        with os.fdopen(out_read, "rb") as output:
            result = json.loads(output.read())
    finally:
        os.close(read)
    assert code == 0, result
    assert seen == ["capture", "published_unlocked"]
    assert not (pending.root / "strategy-decisions").exists()


def test_controller_prepares_retention_before_child_and_releases_for_handoff(roots, monkeypatch):
    from tools import gold_strategy_controller as controller, gold_pilot_retention as bridge
    from tools import gold_bar_rotation
    from tests.tools.test_demo_supervisor import child

    value, root = roots
    row = retained(value)
    runtime = bundle()
    monkeypatch.setattr(controller.supervisor, "STATE", root)
    monkeypatch.setattr(controller.supervisor, "now_ms", lambda: NOW)
    monkeypatch.setattr(controller.supervisor, "_root_private_directory", lambda _: None)
    monkeypatch.setattr(controller.supervisor, "_read_root_owned_json", lambda _: json.dumps(runtime).encode())
    monkeypatch.setattr(
        controller.demo_task, "preparation_command", lambda **kw: ["tools.demo_request_prepare", "RuntimeMaxSec=155s"]
    )
    monkeypatch.setattr(gold_bar_rotation, "load", lambda **kw: dict(runtime))
    monkeypatch.setattr(gold_bar_rotation, "advance", lambda **kw: dict(runtime))
    process = child(
        "import sys,json\nassert json.loads(sys.stdin.buffer.readline())=="
        + repr(runtime)
        + "\nprint("
        + repr(json.dumps(row))
        + ",flush=True)\n"
    )
    seen = []

    def start(*args, **kwargs):
        assert (root / bridge.MARKER).exists(), "cold history review must precede child capture"
        seen.append("child")
        return process

    monkeypatch.setattr(controller.subprocess, "Popen", start)
    monkeypatch.setattr(controller.subprocess, "run", lambda *a, **kw: None)

    def handoff(result):
        assert (
            json.loads(bridge.read_controller_result(root, _digest(row), source_commit=SOURCE, account=value.account))
            == row
        )
        seen.append("handoff_unlocked")
        return False

    try:
        actual = controller.run(source_commit=SOURCE, ticks=1, on_decision=handoff)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        process.stdin.close()
        process.stdout.close()
    assert actual == row
    assert seen == ["child", "handoff_unlocked"]
    assert not (root / "strategy-decisions").exists()


@pytest.mark.parametrize("field", ["request_sha256", "started_at_ms", "ends_at_ms"])
def test_controller_rejects_forged_window_even_with_original_configuration_digest(roots, field):
    from tools import gold_pilot_retention as bridge

    value, root = roots
    row = packet(value, NOW)
    row["original_pilot_window"][field] = (
        "a" * 64 if field == "request_sha256" else row["original_pilot_window"][field] + 1000
    )
    if field in ("started_at_ms", "ends_at_ms"):
        other = "ends_at_ms" if field == "started_at_ms" else "started_at_ms"
        row["original_pilot_window"][other] += 1000
    with bridge.executor_retention(
        value.root, bundle(), source_commit=SOURCE, account=value.account, uid=os.getuid()
    ) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        db.put(claim, "decision", row["decision"])
        db.put(claim, "result", row)
    with pytest.raises(ValueError, match="RETENTION_RESULT_WINDOW"):
        bridge.controller_retain(
            root, row, base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
        )


def test_missing_entire_executor_retention_stops_before_new_worker_claim(roots):
    import shutil
    from tools import gold_pilot_retention as bridge

    value, root = roots
    row = retained(value)
    bridge.controller_retain(
        root, row, base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
    )
    for name in (bridge.AREA, bridge.AUDIT):
        shutil.rmtree(value.root / name)
    (value.root / bridge.MARKER).unlink()
    with pytest.raises(ValueError, match="RETENTION_EXECUTOR_HISTORY_MISSING"):
        with bridge.ControllerRetention(
            root, base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
        ):
            pass
    assert not (value.root / bridge.MARKER).exists()


@pytest.mark.parametrize("side", ["executor", "controller"])
def test_existing_native_history_cannot_fall_back_to_legacy_runtime(roots, side):
    from tools import gold_pilot_retention as bridge

    value, root = roots
    row = retained(value)
    bridge.controller_retain(
        root, row, base_runtime=bundle(), source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
    )
    legacy = dict(
        schema="GOLD_STRATEGY_WORKER_INPUT_V1",
        source_commit=SOURCE,
        observation_sha256="a" * 64,
        trial_manifest_sha256="b" * 64,
    )
    with pytest.raises(ValueError, match="RETENTION_RUNTIME_CHANGED"):
        if side == "executor":
            bridge.executor_retention(value.root, legacy, source_commit=SOURCE, account=value.account, uid=os.getuid())
        else:
            with bridge.ControllerRetention(
                root, base_runtime=legacy, source_commit=SOURCE, account=value.account, executor_uid=os.getuid()
            ):
                pass
    assert not (root / "strategy-decisions").exists()
    assert not (value.root / "strategy-decisions").exists()


def test_invalid_retained_archive_cannot_start_original_pilot_or_claim(pending):
    from tools import gold_pilot_retention as bridge

    runtime = dict(
        schema="GOLD_BAR_RETAINED_EPISODE_INPUT_V1",
        source_commit=SOURCE,
        observation_sha256="a" * 64,
        trial_manifest_sha256="b" * 64,
        archive_source_commit="d" * 40,
        entry_decision_ms=NOW,
    )
    code, result = invoke(pending, runtime)
    assert code == 3 and result["status"] == "INPUTS_UNRESOLVED"
    assert pending.captures == []
    assert not (pending.root / "gold-pilot").exists()
    assert not (pending.root / bridge.MARKER).exists()


def test_worker_account_capture_uses_native_claim_and_retains_original_report(enrolled, monkeypatch):
    from types import SimpleNamespace
    from tools import demo_executor_origin as capture, gold_pilot_retention as bridge
    from tools.gold_strategy_worker import capture_origin
    from tests.tools.test_gold_demo_execution_origin import capture as account_fixture

    raw, _ = account_fixture()
    raw["source_sha256"] = capture.source_hash()
    monkeypatch.setattr(capture, "fixed_account", lambda: (enrolled.root, enrolled.account))
    monkeypatch.setattr(capture, "now_ms", lambda: NOW)
    calls = []

    def child(command, **kwargs):
        assert command[-2:] == ["--symbol", "XAUUSDT"]
        assert kwargs["timeout"] == 115 and kwargs["env"]["NO_PROXY"] == "*"
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps(raw).encode())

    monkeypatch.setattr(capture.subprocess, "run", child)
    with bridge.executor_retention(
        enrolled.root, bundle(), source_commit=SOURCE, account=enrolled.account, uid=os.getuid()
    ) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        result = capture_origin(root=enrolled.root, source_commit=SOURCE, retention=db, claim=claim)
        assert result == raw
        assert json.loads(db.read_claim(claim, "origin")) == raw
        assert capture_origin(root=enrolled.root, source_commit=SOURCE, retention=db, claim=claim) == raw
        assert len(calls) == 1
    assert not (enrolled.root / "execution-origins").exists()
