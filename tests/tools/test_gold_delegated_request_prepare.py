"""Actual private preparer pipe uses the original retained delegated frame."""

from contextlib import ExitStack, closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import stat

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from tests.live.test_demo_gold_delegated_issuer import native_issuer_case
from tests.live.test_gold_delegated_pipeline import task_material
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tools import demo_request_prepare as preparer


@pytest.fixture
def prepared_case(case, original_evidence, tmp_path, monkeypatch):
    from live.execution import demo_gold_delegation_enable as enabling
    from live.execution.order_event_store import SqliteOrderEventStore
    from services.research.demo_user_stream import UserStreamJournal
    from tests.tools.test_gold_strategy_entry import origin_at
    from services.research import demo_execution_origin as origins

    # The independent synthetic registry owner is selected before signing;
    # production role/custody/retention checks are not replaced.
    f = native_issuer_case(
        case,
        original_evidence,
        tmp_path,
        monkeypatch,
        configure_registry=lambda registry: registry.update(executor_uid=os.getuid(), research_uids=[3001]),
    )
    f.clock = [f.now_ms]
    f.captures = []
    f.gets = []
    f.task, f.market, _ = task_material(f)
    for name in ("dispatch", "recovery", "oms"):
        (f.root / (name + ".db")).rename(f.root / (name + ".sqlite3"))
    with closing(SqliteOrderEventStore(str(f.root / "order-events.sqlite3"), create=True)):
        pass
    with UserStreamJournal(f.root / "user-stream.sqlite3", account_ref_sha256=f.base_policy["account_ref_sha256"]):
        pass
    for name in ("evidence", "resident-recovery"):
        (f.root / name).mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setattr(preparer, "verify_source", lambda source: source == "c" * 40 or pytest.fail("wrong source"))
    monkeypatch.setattr(preparer, "fixed_account", lambda: (f.root, f.base_policy["account_ref_sha256"]))
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid())
    monkeypatch.setattr(os, "getegid", lambda: os.getuid())
    monkeypatch.setattr(os, "getgroups", lambda: [os.getuid()])
    monkeypatch.setattr(preparer, "now_ms", lambda: f.clock[0])

    def private(fd):
        info = os.fstat(fd)
        assert stat.S_ISFIFO(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077

    monkeypatch.setattr(preparer, "private_pipe", private)

    def enabled(**kwargs):
        assert kwargs.pop("role") == "executor"
        return deepcopy(enabling.validate(f.current_enable, **kwargs))

    monkeypatch.setattr(enabling, "load_enabled", enabled)

    def account_capture(*, symbol, source_commit):
        assert symbol == "XAUUSDT" and source_commit == "c" * 40
        f.captures.append(1)
        origin = origin_at(f.now_ms + 10)
        path = origins.save(f.root / "execution-origins" / symbol, origin)
        return dict(status="CAPTURED", path=str(path))

    monkeypatch.setattr(preparer.demo_executor_origin, "run", account_capture)

    def market_capture(*, symbol, protected):
        assert symbol == "XAUUSDT" and protected is True
        start = f.now_ms + 1
        ticks = iter(range(start, start + 100))
        original = {row["kind"]: json.loads(row["body"]) for row in f.market["reads"]}

        def get(kind, **kwargs):
            f.gets.append(kind)
            value = deepcopy(original[kind])
            if kind.startswith("clock"):
                value = {"serverTime": start + (6 if kind == "clock_after" else 0)}
            elif kind == "ticker":
                value.update(openTime=start - 86400000, closeTime=start)
            return json.dumps(value).encode()

        report = preparer.binance_demo_market_inputs.collect(symbol=symbol, get=get, clock_ms=lambda: next(ticks))
        assert report["status"] == "CAPTURED"
        f.clock[0] = f.now_ms + 10
        if hasattr(f, "after_market"):
            f.after_market()
        return report

    monkeypatch.setattr(preparer.binance_demo_market_inputs, "collect_bounded", market_capture)
    with ExitStack() as stack:
        f.stores = {
            name: stack.enter_context(closing(factory(str(f.root / (name + ".sqlite3")))))
            for name, factory in (
                ("dispatch", DemoDispatchStore),
                ("recovery", SqliteOrderRecoveryStore),
                ("oms", LiveLedger),
            )
        }
        yield f


def call(f):
    incoming_read, incoming_write = os.pipe()
    outgoing_read, outgoing_write = os.pipe()
    for fd in (incoming_read, incoming_write, outgoing_read, outgoing_write):
        os.fchmod(fd, 0o600)
    bundle = json.dumps(dict(task=f.task, policy=f.base_policy)).encode() + b"\n"

    def feed():
        with os.fdopen(incoming_write, "wb") as channel:
            channel.write(bundle)

    def drain():
        with os.fdopen(outgoing_read, "rb") as channel:
            return channel.read()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            sending = pool.submit(feed)
            receiving = pool.submit(drain)
            with os.fdopen(outgoing_write, "w") as output:
                status = preparer.run(source_commit=f.base_policy["executor_release_commit"],
                    input_fd=incoming_read, output=output)
            sending.result(timeout=3)
            return status, json.loads(receiving.result(timeout=3))
    finally:
        os.close(incoming_read)


def test_native_private_preparer_uses_retained_origin_and_emits_valid_session(prepared_case):
    from live.execution.demo_execution_session import ExecutionSession
    from tools.demo_task import same_declarations

    f = prepared_case
    before = {
        name: store.snapshot()
        if name == "dispatch"
        else store.latest_records()
        if name == "recovery"
        else store.orders()
        for name, store in f.stores.items()
    }
    status, value = call(f)
    assert status == 0, value
    assert value["request"]["origin"] == f.proof["origin"]
    assert value["request"]["delegated_admission"] == f.proof
    assert f.captures == []
    assert f.gets == ["clock_before", "ticker", "klines", "clock_after"]
    assert value["precheck_status"] == "RISK_CHECKS_PASSED"
    same_declarations(value["request"], f.task)
    session = ExecutionSession(**f.stores, evidence_directory=f.root / "session-proof")
    try:
        handoff = session.propose(json.dumps(value["request"]).encode(), now_ms=f.clock[0])
        assert handoff["proposal"]["origin"] == f.proof["origin"]
        assert handoff["proposal"]["context"]["delegated_admission_sha256"] == _digest(f.proof)
    finally:
        session.close()
    after = {
        name: store.snapshot()
        if name == "dispatch"
        else store.latest_records()
        if name == "recovery"
        else store.orders()
        for name, store in f.stores.items()
    }
    assert before == after


def original_files(root):
    return {
        p: (p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_ctime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file()
    }


def assert_preserved(before):
    for path, facts in before.items():
        assert path.exists()
        assert (path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_ctime_ns, path.read_bytes()) == facts, path


@pytest.mark.parametrize("damage", ["missing_database", "missing_result", "tamper_origin", "claim", "state", "pilot"])
def test_missing_or_changed_original_frame_fails_before_public_gets(prepared_case, damage):
    import sqlite3
    from tools.gold_pilot_retention_store import AREA
    from quantos.contracts.trading import OrderSide

    f = prepared_case
    if damage == "missing_database":
        (f.root / "recovery.sqlite3").rename(f.root / "recovery-unavailable.sqlite3")
    elif damage in ("missing_result", "tamper_origin"):
        with sqlite3.connect(f.root / AREA / "records.sqlite3") as db:
            trigger = "records_no_delete" if damage == "missing_result" else "records_no_update"
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (trigger,)).fetchone()[0]
            db.execute("DROP TRIGGER " + trigger)
            if damage == "missing_result":
                db.execute("DELETE FROM records WHERE kind='result'")
            else:
                raw = db.execute("SELECT payload FROM records WHERE kind='origin'").fetchone()[0]
                value = json.loads(raw)
                value["source_sha256"] = "f" * 64
                db.execute(
                    "UPDATE records SET payload=? WHERE kind='origin'",
                    ((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(),),
                )
            db.execute(sql)
    elif damage == "claim":
        f.task["delegated_admission"]["claim"]["ordinal"] += 1
    elif damage == "state":
        f.stores["oms"].record_submission(
            "QDparallel0001",
            "XAUUSDT",
            OrderSide.BUY,
            0.1,
            strategy_id="existing",
            submitted_at=f.now_ms,
            status="NEW",
            order_type="LIMIT",
            limit_price=100,
        )
    else:
        path = f.root / "gold-pilot" / "stopped.json"
        path.write_text("{}")
        path.chmod(0o600)
    before = original_files(f.root)
    status, value = call(f)
    assert status == 3 and value["status"] == "INPUTS_UNRESOLVED" and "request" not in value
    assert f.captures == f.gets == []
    assert_preserved(before)
    assert not (f.root / "execution-origins").exists()
    if damage == "missing_database":
        assert not (f.root / "recovery.sqlite3").exists()


@pytest.mark.parametrize(
    "change",
    ["expired", "state", "enable", "clock_during_build", "clock_rollback_during_build", "clock_during_final_replay"],
)
def test_changes_during_public_capture_or_risk_are_rejected_without_recapture(prepared_case, monkeypatch, change):
    from quantos.contracts.trading import OrderSide

    f = prepared_case
    if change == "clock_during_final_replay":
        original = preparer.demo_request_inputs.task_check
        checked = []

        def task_check(*args, **kwargs):
            value = original(*args, **kwargs)
            checked.append(1)
            if len(checked) == 3:
                f.clock[0] = f.task["valid_until_ms"]
            return value

        monkeypatch.setattr(preparer.demo_request_inputs, "task_check", task_check)
    elif change in ("clock_during_build", "clock_rollback_during_build"):
        original = preparer.demo_request_inputs.build

        def build(**kwargs):
            value = original(**kwargs)
            f.clock[0] = (
                f.now_ms + 5
                if change == "clock_rollback_during_build"
                else f.task["execution_constraints"]["valid_until_ms"]
            )
            return value

        monkeypatch.setattr(preparer.demo_request_inputs, "build", build)
    else:

        def changed():
            if change == "expired":
                f.clock[0] = f.task["execution_constraints"]["valid_until_ms"]
            elif change == "enable":
                f.current_enable["consumer_enabled"] = False
            else:
                f.stores["oms"].record_submission(
                    "QDparallel0002",
                    "XAUUSDT",
                    OrderSide.BUY,
                    0.1,
                    strategy_id="existing",
                    submitted_at=f.clock[0],
                    status="NEW",
                    order_type="LIMIT",
                    limit_price=100,
                )

        f.after_market = changed
    status, value = call(f)
    assert status == 3 and value["status"] == "INPUTS_UNRESOLVED" and "request" not in value
    assert f.captures == [] and f.gets == ["clock_before", "ticker", "klines", "clock_after"]
    assert f.stores["dispatch"].snapshot() == [] and f.stores["recovery"].latest_records() == ()
    assert not (f.root / "execution-origins").exists()


def test_original_dispatch_lease_blocks_parallel_preparer_without_gets(prepared_case):
    from live.execution.demo_pre_submit import _preparation_lease

    f = prepared_case
    before = original_files(f.root)
    with _preparation_lease(f.stores["dispatch"]):
        status, value = call(f)
    assert status == 3 and "request" not in value
    assert f.captures == f.gets == []
    assert_preserved(before)
