"""Original V1 worker enrollment must precede pilot AUTO capture capacity.

All account/source/custody identities and market captures are isolated fixtures.
Worker framing, enrollment, preparation flock and capacity readers stay native.
Synthetic captures finish FreshEpisode; the strategy decision boundary stops
before any order preparation.
"""

from copy import deepcopy
import json
import os
import shutil
import stat
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_pre_submit import NOW, stores
from tests.tools.test_gold_demo_execution_origin import capture
from tests.tools.test_gold_pilot_capacity import history_capture, write


@pytest.fixture
def pending(tmp_path, monkeypatch):
    from live.execution import demo_authority, demo_position_context as position
    from live.execution import demo_market_partition_runtime as partition
    from tools import gold_strategy_worker as worker

    root = tmp_path / "executor"
    root.mkdir(mode=0o700)
    uid = os.getuid()
    monkeypatch.setattr(position, "_ROOT", root)
    monkeypatch.setattr(demo_authority, "load_registry_for_executor", lambda: {"executor_uid": uid})
    # Virtualize ancestors only; native directory/file ownership and modes stay.
    monkeypatch.setattr(demo_authority, "_require_protected", lambda *a, **kw: None)
    for name in ("EXECUTOR_RECORD", "ISSUER_RECORD", "ACTIVATION"):
        monkeypatch.setattr(partition, name, tmp_path / name)
    origin, _ = capture()
    request = dict(
        schema="GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1",
        source_commit="c" * 40,
        account_ref_sha256=origin["account_ref_sha256"],
        duration_seconds=86400,
        max_loss_fraction="0.05",
        submission_enabled=False,
    )
    write(root / "gold-pilot-request.json", request)
    opened = stores(root)
    values = dict(zip(("dispatch", "recovery", "oms"), opened, strict=True))
    monkeypatch.setattr(worker, "verify_source", lambda _: None)
    monkeypatch.setattr(worker, "fixed_account", lambda: (root, request["account_ref_sha256"]))
    monkeypatch.setattr(worker.os, "geteuid", lambda: uid)
    monkeypatch.setattr(worker.os, "getegid", lambda: uid)
    monkeypatch.setattr(worker.os, "getgroups", lambda: [uid])

    # The developer owns these real pipes; production requires root ownership.
    def isolated_pipe(fd):
        info = os.fstat(fd)
        assert stat.S_ISFIFO(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o077

    monkeypatch.setattr(worker, "private_pipe", isolated_pipe)
    monkeypatch.setattr(worker, "require_custody", lambda: root)
    monkeypatch.setattr(worker, "_open", lambda *a: values)
    monkeypatch.setattr(worker, "now_ms", lambda: NOW)
    captures = []
    monkeypatch.setattr(worker, "capture_origin", lambda **kw: captures.append(kw) or deepcopy(origin))
    try:
        yield SimpleNamespace(
            root=root, uid=uid, request=request, origin=origin, stores=values, captures=captures, partition=partition
        )
        assert values["dispatch"].snapshot() == [] and values["dispatch"].wire_snapshot() == []
        assert values["recovery"].latest_records() == () and values["oms"].orders() == []
    finally:
        for store in opened:
            store.close()


def invoke(value, bundle):
    """Real bounded pipe reader and worker; response fits one private pipe."""
    from tools import gold_strategy_worker as worker

    read, write_fd = os.pipe()
    out_read, out_write = os.pipe()
    with os.fdopen(write_fd, "wb") as channel:
        channel.write(json.dumps(bundle).encode() + b"\n")
    try:
        with os.fdopen(out_write, "w") as output:
            code = worker.run(source_commit="c" * 40, input_fd=read, output=output)
        with os.fdopen(out_read, "rb") as output:
            result = json.loads(worker.read_frame(output.fileno(), maximum=32768, deadline=worker.time.monotonic() + 3))
        return code, result
    finally:
        os.close(read)


def auto_fixture(value, monkeypatch):
    from tools import gold_bar_fresh_inputs as fresh
    from tests.tools.test_gold_bar_worker_inputs import setup

    _, retained, template, _, _, _ = setup(value.root, monkeypatch)
    value.template = template
    bundle = dict(
        schema="GOLD_BAR_AUTO_EPISODE_INPUT_V1",
        source_commit="c" * 40,
        trial_manifest_sha256=retained["trial_manifest_sha256"],
        archive_source_commit=retained["archive_source_commit"],
        candidate="channel",
        seconds=30,
        episode_id="e" * 64,
    )
    definition = dict(
        definition_hash="d" * 64,
        archive_files={"manifest.json": bundle["trial_manifest_sha256"]},
        source={"commit": bundle["archive_source_commit"]},
    )
    monkeypatch.setattr(
        fresh, "PreparedBarDefinition", lambda *a, **kw: SimpleNamespace(definition=lambda *a, **kw: definition)
    )
    monkeypatch.setattr(fresh, "restore_engine", lambda *a, **kw: object())
    return bundle


def test_native_v1_worker_enrolls_before_auto_history_and_fresh_capture(pending, monkeypatch):
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from tools import gold_bar_fresh_inputs as fresh, gold_bar_cash, gold_bar_pilot_capacity as capacity
    from tools import gold_strategy_worker as worker

    bundle = auto_fixture(pending, monkeypatch)
    history = history_capture(monkeypatch)
    calls = []
    # Account transport is synthetic; no cash collector or network may run.
    monkeypatch.setattr(gold_bar_cash, "open_window", lambda *a, **kw: calls.append("cash"))
    # Account capture is explicitly isolated here; preserve this test's native
    # initial-enrollment/observation ordering scope. The recovery cash fixture
    # separately runs the real full account opening before AUTO observation.
    opening = {'origin': {'completed_at_ms': NOW-5001}}
    monkeypatch.setattr(gold_bar_cash, 'open_account_origin', lambda *a, **kw: opening)
    monkeypatch.setattr(gold_bar_cash, 'load_account_opening', lambda *a, **kw: opening)
    monkeypatch.setattr(gold_bar_cash, 'account_opening_usage', lambda *a, **kw: None)

    async def isolated_capture(*args, **kwargs):
        config = enrollment.completed_configuration(pending.root / "gold-pilot", request=pending.request)
        marker = json.loads((pending.root / capacity.MARKER).read_bytes())
        assert config["started_at_ms"] == NOW and config["ends_at_ms"] == NOW + 86400000
        assert config["initial_equity"] == "5000"
        assert marker["request_sha256"] == _digest(pending.request)
        assert marker["configuration_sha256"] == _digest(config)
        assert kwargs["output"].parent.name == "observations"
        assert kwargs["protected_network"] is True
        output = kwargs["output"]
        shutil.copytree(pending.template, output)
        write(
            output / "registration.json", dict(first_close=True, network_mode="PROTECTED_DEMO_NETWORK_V1", seconds=30)
        )
        write(output / "selected-decisions.jsonl", {"received_at_ms": NOW})
        for source, name in (("receipt.json", "warmup-receipt.json"), ("response.json", "warmup.json")):
            (output / "journal" / name).write_bytes((kwargs["history"] / source).read_bytes())
        calls.append("fresh")

    monkeypatch.setattr(fresh, "observe", isolated_capture)

    def stop_at_decision(**kwargs):
        assert kwargs["entry_decision_ms"] == NOW
        calls.append("decision_boundary")
        raise ValueError("ISOLATED_DECISION_BOUNDARY")

    monkeypatch.setattr(worker.gold_bar_demo_cycle, "prepare", stop_at_decision)
    code, result = invoke(pending, bundle)
    assert len(history) == 1 and calls == ["cash", "fresh", "decision_boundary"], result
    assert len(pending.captures) == 2
    assert code == 3 and result["phase"] == "STRATEGY_DECISION"
    reviewed = fresh.FreshEpisode(pending.root, bundle, source_commit="c" * 40, uid=pending.uid, read_only=True)
    assert reviewed.recheck() == reviewed.checked and reviewed.entry_decision_ms == NOW
    assert not (pending.root / "strategy-decisions").exists()
    from tools.gold_pilot_retention import executor_retention
    with executor_retention(pending.root,bundle,source_commit="c"*40,
            account=pending.request["account_ref_sha256"],uid=pending.uid) as retained:
        assert retained.metadata()["frames"]==1 and retained.metadata()["records"]==0


@pytest.mark.parametrize("damage", ["source", "account", "schema", "duration", "partial", "partition", "stale"])
def test_invalid_original_v1_inputs_stop_before_capacity_allocation(pending, monkeypatch, damage):
    from tools import gold_strategy_worker as worker, gold_bar_pilot_capacity as capacity

    request = deepcopy(pending.request)
    if damage == "source":
        request["source_commit"] = "a" * 40
    elif damage == "account":
        request["account_ref_sha256"] = "a" * 64
    elif damage == "schema":
        request["schema"] = "UNKNOWN_REQUEST"
    elif damage == "duration":
        request["duration_seconds"] = 86401
    elif damage == "partial":
        (pending.root / "gold-pilot").mkdir(mode=0o700)
    elif damage == "partition":
        write(pending.partition.ACTIVATION, {})
    else:
        monkeypatch.setattr(worker, "now_ms", lambda: NOW + 86400000)
    write(pending.root / "gold-pilot-request.json", request)
    bundle = auto_fixture(pending, monkeypatch)
    code, result = invoke(pending, bundle)
    assert code == 3 and result["phase"] == "SCOPE_PILOT_BEFORE_CAPTURE", result
    assert not (pending.root / capacity.MARKER).exists()
    assert not (pending.root / "gold-pilot" / "configuration.json").exists()


def test_original_preparation_lease_still_blocks_first_enrollment(pending):
    from live.execution.demo_pre_submit import _preparation_lease
    from tools import gold_strategy_worker as worker

    with _preparation_lease(pending.stores["dispatch"]):
        with pytest.raises(ValueError, match="DEMO_EXECUTOR_BUSY"):
            worker.start_scope_pilot_before_capture(
                pending.root, account=pending.request["account_ref_sha256"], source_commit="c" * 40
            )
    assert not (pending.root / "gold-pilot").exists()


def test_completed_v1_baseline_is_reused_without_account_capture(pending, monkeypatch):
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from tools import gold_strategy_worker as worker

    config = enrollment.enroll_requested(
        root=pending.root, source_commit="c" * 40, origin=pending.origin, **pending.stores, now_ms=NOW
    )
    originals = {
        p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
        for p in (pending.root / "gold-pilot").rglob("*")
        if p.is_file()
    }
    monkeypatch.setattr(worker, "capture_origin", lambda **kw: pytest.fail("completed baseline recaptured"))
    monkeypatch.setattr(worker, "now_ms", lambda: NOW + 86400001)
    worker.start_scope_pilot_before_capture(
        pending.root, account=pending.request["account_ref_sha256"], source_commit="c" * 40
    )
    assert enrollment.completed_configuration(pending.root / "gold-pilot", request=pending.request) == config
    assert originals == {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in originals}
