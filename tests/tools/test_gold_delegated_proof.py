"""Native archived source and frozen market replay, with no capture or authority."""

from copy import deepcopy
import base64
import json
import os

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tests.live.test_gold_strategy_constraints import case as case


@pytest.fixture(params=["fresh", "retained"])
def frozen(original_evidence, request):
    from tools.gold_delegated_proof import freeze_prefix
    from tools.gold_bar_worker_inputs import load

    original = original_evidence
    runtime = deepcopy(original[request.param])
    decision = dict(bar_signal_sha256=original["signal_hash"], observed_at_ms=original["entry_ms"] + 1)
    if request.param == "fresh":
        decision["bar_continuation_sha256"] = original["prefix"]
    engine, _ = load(original["root"], original["retained"], source_commit="c" * 40, uid=os.getuid())
    prefix = freeze_prefix(
        root=original["root"], runtime=runtime, decision=decision, source_commit="c" * 40, uid=os.getuid()
    )
    return original, runtime, engine.verified_definition(), prefix


def test_full_native_prefix_is_replayed_independently_and_excludes_later_tail(frozen):
    from live.execution.demo_gold_delegated_admission import replay_prefix

    original, runtime, definition, prefix = frozen
    checked = replay_prefix(prefix, runtime=runtime, definition=definition, observed_at_ms=original["entry_ms"] + 1)
    assert _digest(checked["signal"]) == original["signal_hash"]
    assert checked["last_received_ms"] <= original["entry_ms"] + 1
    assert checked["entry_decision_ms"] == original["entry_ms"]
    assert checked["signal"]["entry_side"] == "BUY"


@pytest.mark.parametrize("damage", ["missing", "messages", "decisions", "future", "definition", "snapshot"])
def test_prefix_content_replay_rejects_damage_without_network(frozen, damage):
    from live.execution.demo_gold_delegated_admission import replay_prefix

    original, runtime, definition, prefix = frozen
    prefix = deepcopy(prefix)
    definition = deepcopy(definition)
    observed = original["entry_ms"] + 1
    if damage == "missing":
        prefix["files"].pop("journal/messages.jsonl")
    elif damage in ("messages", "decisions"):
        name = "journal/" + damage + ".jsonl"
        raw = base64.b64decode(prefix["files"][name])
        prefix["files"][name] = base64.b64encode(raw + b"{}\n").decode()
    elif damage == "future":
        observed = original["entry_ms"] - 1
    elif damage == "definition":
        definition["candidate"] = "reversion"
    else:
        if prefix["tail"] is None:
            prefix["tail"] = {"snapshot": {}}
        else:
            prefix["tail"]["snapshot"]["prefix"].append("a" * 64)
    with pytest.raises((ValueError, KeyError, TypeError)):
        replay_prefix(prefix, runtime=runtime, definition=definition, observed_at_ms=observed)


def test_export_reads_exact_native_claim_without_capture_or_mutation(case, original_evidence, tmp_path, monkeypatch):
    from tests.live.test_gold_delegated_admission import native_case
    from tests.tools.test_gold_runtime_evidence import evidence_snapshot
    from tools.gold_delegated_proof import export
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger

    f = native_case(case, original_evidence, tmp_path, monkeypatch)
    native = dict(
        dispatch=DemoDispatchStore(f.root / "dispatch.db", read_only=True),
        recovery=SqliteOrderRecoveryStore(str(f.root / "recovery.db"), read_only=True),
        oms=LiveLedger(str(f.root / "oms.db"), read_only=True),
    )
    before = evidence_snapshot(f.root)
    try:
        exported = export(
            root=f.root,
            runtime=f.proof["runtime"],
            result_sha256=_digest(f.proof["result"]),
            enable=f.enable,
            registry=f.registry,
            source_commit="c" * 40,
            uid=os.getuid(),
            now_ms=f.now_ms,
            stores=native,
        )
        assert exported == f.proof
        assert evidence_snapshot(f.root) == before
        with pytest.raises(ValueError):
            export(
                root=f.root,
                runtime=f.proof["runtime"],
                result_sha256="f" * 64,
                enable=f.enable,
                registry=f.registry,
                source_commit="c" * 40,
                uid=os.getuid(),
                now_ms=f.now_ms,
                stores=native,
            )
        assert evidence_snapshot(f.root) == before
    finally:
        for store in native.values():
            store.close()


@pytest.mark.parametrize("expired", [False, True])
def test_real_private_pipe_native_export_rechecks_original_deadline(
    case, original_evidence, tmp_path, monkeypatch, expired
):
    from tests.live.test_gold_delegated_admission import native_case
    from tools import gold_delegated_proof as tool
    from live.execution import demo_authority
    import multiprocessing
    import stat
    import time
    from tools.demo_execution_session import read_frame

    f = native_case(
        case,
        original_evidence,
        tmp_path,
        monkeypatch,
        configure_registry=lambda registry: registry.update(executor_uid=os.getuid(), research_uids=[3001]),
    )
    for name in ("dispatch", "recovery", "oms"):
        (f.root / (name + ".db")).rename(f.root / (name + ".sqlite3"))
    # Map only deployment identity/independent public custody into this fixture.
    # All three stores, retention, preparation lease and proof replay stay native.
    monkeypatch.setattr(tool, "verify_source", lambda value: value == "c" * 40 or pytest.fail("source"))
    monkeypatch.setattr(tool, "fixed_account", lambda: (f.root, f.base_policy["account_ref_sha256"]))
    monkeypatch.setattr(tool, "require_custody", lambda: f.root)
    monkeypatch.setattr(demo_authority, "load_registry_for_executor", lambda: f.registry)
    monkeypatch.setattr(tool.os, "geteuid", lambda: os.getuid())
    monkeypatch.setattr(tool.os, "getegid", lambda: os.getuid())
    monkeypatch.setattr(tool.os, "getgroups", lambda: [os.getuid()])
    monkeypatch.setattr(tool, "load_enabled", lambda **kw: deepcopy(f.enable))
    calls = [0]

    def clock():
        calls[0] += 1
        return f.now_ms + (5000 if expired and calls[0] > 1 else 0)

    monkeypatch.setattr(tool, "now_ms", clock)

    def private(fd):
        info = os.fstat(fd)
        assert stat.S_ISFIFO(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077

    monkeypatch.setattr(tool, "private_pipe", private)
    read_in, write_in = os.pipe()
    read_out, write_out = os.pipe()
    for fd in (read_in, write_in, read_out, write_out):
        os.fchmod(fd, 0o600)
    payload = dict(
        schema="GOLD_DELEGATED_PROOF_INPUT_V1",
        source_commit="c" * 40,
        runtime=f.proof["runtime"],
        result_sha256=_digest(f.proof["result"]),
        enable_sha256=_digest(f.enable),
    )

    def worker():
        os.close(write_in)
        os.close(read_out)

        with os.fdopen(write_out, "w") as output:
            code = tool.run(source_commit="c" * 40, input_fd=read_in, output=output)
        os.close(read_in)
        raise SystemExit(code)

    process = multiprocessing.get_context("fork").Process(target=worker)
    try:
        process.start()
        os.close(read_in)
        os.close(write_out)
        os.write(write_in, json.dumps(payload).encode() + b"\n")
        os.close(write_in)
        raw = read_frame(read_out, maximum=16_000_000, deadline=time.monotonic() + 20)
        process.join(timeout=5)
        assert not process.is_alive()
        result = json.loads(raw)
        if expired:
            assert process.exitcode == 3 and result["status"] == "INPUTS_UNRESOLVED"
            assert result["submission_enabled"] is result["economic_authority"] is False
        else:
            assert process.exitcode == 0 and result == f.proof
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        os.close(read_out)


@pytest.mark.parametrize("gap", [False, True])
def test_full_nonempty_tail_replays_original_events_and_gap(original_evidence, tmp_path, gap):
    from dataclasses import asdict
    import shutil
    from tools.gold_bar_fresh_inputs import FreshEpisode
    from tools.gold_bar_episode_tail import EpisodeTail
    from tools.gold_delegated_proof import freeze_prefix
    from live.execution.demo_gold_delegated_admission import replay_prefix

    root = tmp_path / "executor"
    shutil.copytree(original_evidence["root"], root)
    runtime = original_evidence["fresh"]
    episode = FreshEpisode(root, runtime, source_commit="c" * 40, uid=os.getuid(), read_only=True)
    tail = EpisodeTail(
        root, bundle_sha256=_digest(runtime), engine=episode.engine, entry_decision_ms=episode.entry_decision_ms
    )
    if gap:
        tail.gap(received_at_ms=tail.engine._cursor.last_received + 1, reason="ISOLATED_TRANSPORT_GAP")
    rebuilt, digest, _ = tail.snapshot()
    at = rebuilt._cursor.last_received + 1
    decision = dict(
        bar_signal_sha256=original_evidence["signal_hash"], bar_continuation_sha256=digest, observed_at_ms=at
    )
    frozen = freeze_prefix(root=root, runtime=runtime, decision=decision, source_commit="c" * 40, uid=os.getuid())
    checked = replay_prefix(frozen, runtime=runtime, definition=episode.engine.verified_definition(), observed_at_ms=at)
    assert len(frozen["tail"]["events"]) == (2 if gap else 1)
    assert checked["cursor"].blocked is gap
    assert _digest([asdict(bar) for bar in checked["cursor"].bars]) == _digest(
        [asdict(bar) for bar in rebuilt._cursor.bars]
    )
    assert _digest(checked["signal"]) == original_evidence["signal_hash"]
