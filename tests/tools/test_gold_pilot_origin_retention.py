"""Native account captures consume the same durable original-pilot frame."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_pilot_retention import bundle, packet, store
from tests.tools.test_gold_pilot_original_deadline import enrolled as enrolled, NOW, SOURCE
from tools import demo_executor_origin as worker


@pytest.fixture
def capture_child(enrolled, monkeypatch):
    clock = [NOW]
    calls = []
    monkeypatch.setattr(worker, "fixed_account", lambda: (enrolled.root, enrolled.account))
    monkeypatch.setattr(worker, "now_ms", lambda: clock[0])
    monkeypatch.setenv("HTTP_PROXY", "http://not-inherited.invalid")

    def child(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[-3:] == ["--capture-worker", "--symbol", "XAUUSDT"]
        assert "--credential-file" not in argv and "HTTP_PROXY" not in kwargs["env"]
        assert kwargs["timeout"] == 115 and kwargs["check"] is True
        assert kwargs["capture_output"] is True and kwargs["env"]["NO_PROXY"] == "*"

        def shift(value):
            if type(value) is dict:
                return {k: shift(v) for k, v in value.items()}
            if type(value) is list:
                return [shift(v) for v in value]
            if type(value) is int and NOW - 86400000 <= value <= NOW + 86400000:
                return value + clock[0] - NOW
            return value

        raw = shift(enrolled.origin)
        raw["source_sha256"] = worker.source_hash()
        return SimpleNamespace(stdout=json.dumps(raw).encode())

    monkeypatch.setattr(worker.subprocess, "run", child)
    return clock, calls


def capture(db, claim):
    return worker.capture_for_pilot(symbol="XAUUSDT", source_commit=SOURCE, retention=db, claim=claim)


def test_native_origins_cross_legacy_1000_limit_without_replacing_old_files(enrolled, capture_child):
    clock, calls = capture_child
    old = worker.original.save(enrolled.root / "execution-origins" / "XAUUSDT", enrolled.origin)
    old_files = {p.name: p.read_bytes() for p in old.parent.iterdir()}
    hashes = []
    with store(enrolled) as db:
        for i in range(1002):
            clock[0] = NOW + i * 1000
            claim = db.claim(bundle=bundle(), now_ms=clock[0])
            origin = capture(db, claim)
            assert origin["status"] == "CAPTURED" and len(origin["reads"]) == 16
            assert json.loads(db.read_claim(claim, "origin")) == origin
            assert capture(db, claim) == origin
            hashes.append(_digest(origin))
            row = packet(enrolled, clock[0])
            row["decision"]["origin_sha256"] = _digest(origin)
            row["decision_sha256"] = _digest(row["decision"])
            db.put(claim, "decision", row["decision"])
            db.put(claim, "result", row)
        assert db.metadata()["records"] == 3006
        assert len(set(hashes)) == len(calls) == 1002
    assert {p.name: p.read_bytes() for p in old.parent.iterdir()} == old_files
    with store(enrolled) as db:
        assert capture(db, claim) == origin and len(calls) == 1002
        assert json.loads(db.read("origin", hashes[0]))["observed_at_ms"] == NOW


@pytest.mark.parametrize("wrong", ["claim", "unpersisted", "root", "account", "source", "role", "readonly", "released"])
def test_wrong_pilot_identity_rejects_before_network(enrolled, tmp_path, monkeypatch, capture_child, wrong):
    clock, calls = capture_child
    db = store(enrolled)
    try:
        claim = db.claim(bundle=bundle(), now_ms=clock[0])
        if wrong == "claim":
            claim = dict(claim, bundle_sha256="f" * 64)
        elif wrong == "unpersisted":
            claim = dict(claim, ordinal=2, admitted_at_ms=clock[0] + 1000)
        elif wrong == "root":
            elsewhere = tmp_path / "elsewhere"
            elsewhere.mkdir(mode=0o700)
            monkeypatch.setattr(worker, "fixed_account", lambda: (elsewhere, enrolled.account))
        elif wrong == "account":
            monkeypatch.setattr(worker, "fixed_account", lambda: (enrolled.root, "f" * 64))
        elif wrong == "source":
            with pytest.raises(ValueError):
                worker.capture_for_pilot(symbol="XAUUSDT", source_commit="f" * 40, retention=db, claim=claim)
            assert not calls
            return
        elif wrong == "role":
            db.role = "controller"
        elif wrong == "readonly":
            db.read_only = True
        else:
            db.release()
        with pytest.raises(ValueError):
            capture(db, claim)
        assert not calls
    finally:
        db.close()


@pytest.mark.parametrize("failure", ["timeout", "private_error", "wrong_account", "oversize"])
def test_failed_capture_retains_original_failure_and_never_retries_same_claim(
    enrolled, monkeypatch, capture_child, failure
):
    _, calls = capture_child
    native_child = worker.subprocess.run

    def failed(argv, **kwargs):
        if failure == "wrong_account":
            result = native_child(argv, **kwargs)
            raw = json.loads(result.stdout)
            raw["account_ref_sha256"] = "f" * 64
            for read in raw["reads"]:
                if read["account_ref_sha256"] is not None:
                    read["account_ref_sha256"] = "f" * 64
            return SimpleNamespace(stdout=json.dumps(raw).encode())
        calls.append((argv, kwargs))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 115)
        if failure == "oversize":
            return SimpleNamespace(stdout=b"x" * (worker.original.MAX_BYTES + 1))
        raise RuntimeError("SYNTHETIC_PRIVATE_RESPONSE_MUST_NOT_LEAK")

    monkeypatch.setattr(worker.subprocess, "run", failed)
    with store(enrolled) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        raw = capture(db, claim)
        assert raw["status"] == "FAILED" and raw["schema"] == "DEMO_GOLD_EXECUTION_ORIGIN_V1"
        assert "SYNTHETIC_PRIVATE" not in json.dumps(raw)
        assert json.loads(db.read_claim(claim, "origin")) == raw
        assert db.metadata()["records"] == 1
    with store(enrolled) as db:
        assert capture(db, claim) == raw and len(calls) == 1
        with pytest.raises(ValueError, match="RETENTION_INCOMPLETE_FRAME"):
            db.claim(bundle=bundle(), now_ms=NOW + 1000)


def test_pilot_capture_shares_original_writer_lock(enrolled, monkeypatch, capture_child):
    from tools.binance_demo_user_stream import writer_lock
    from tools import binance_demo_user_stream as locking

    with store(enrolled) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        with writer_lock(enrolled.root / "origin-capture.lock"):
            clock = iter([0, 35])
            monkeypatch.setattr(locking.time, "monotonic", lambda: next(clock))
            with pytest.raises(ValueError, match="STREAM_WRITER_ALREADY_RUNNING"):
                capture(db, claim)
        assert not capture_child[1] and db.metadata()["records"] == 0


def test_role_caps_and_physical_witness_budgets_follow_third_executor_payload(enrolled, tmp_path):
    with store(enrolled) as db:
        binding = dict(db.binding)
        assert binding["role_records_per_frame"] == {"executor": 3, "controller": 2}
        assert binding["max_audit_events"] == 4 * binding["max_frames"]
        claim = db.claim(bundle=bundle(), now_ms=NOW)
    controller = tmp_path / "controller"
    controller.mkdir(mode=0o700)
    with store(enrolled, role="controller", root=controller) as db:
        assert db.binding == binding
        db.adopt(claim)
        db.put(claim, "result", packet(enrolled, NOW))
        db.put(claim, "approval", {"schema": "synthetic-approval"})
        with pytest.raises(ValueError, match="RETENTION_FRAME_RECORD_BUDGET"):
            db.put(claim, "delivery", {"schema": "synthetic-delivery"})
        with pytest.raises(ValueError, match="RETENTION_ROLE"):
            db.put(claim, "origin", enrolled.origin)


def test_claim_lookup_checks_durable_original_and_exact_audit(enrolled):
    with store(enrolled) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        assert db.read_claim(claim, "origin") is None
        bad = dict(claim, bundle_sha256="f" * 64)
        with pytest.raises(ValueError, match="RETENTION_CLAIM"):
            db.read_claim(bad, "origin")
        db.put(claim, "origin", enrolled.origin)
        path = next(p for p in (db.audit / "events").iterdir() if json.loads(p.read_bytes())["kind"] == "origin")
        path.unlink()
        with pytest.raises((ValueError, FileNotFoundError)):
            db.read_claim(claim, "origin")


def test_interrupted_before_origin_commit_cannot_allocate_another_frame(enrolled, monkeypatch, capture_child):
    # A GET and a local transaction cannot be made atomic. The worker always
    # allocates its next frame before capture, so an unrecorded interrupted
    # origin leaves an incomplete claim and prevents automatic resampling.
    from tools.gold_pilot_retention_store import Retention

    native_put = Retention.put

    def interrupted(self, claim, kind, value):
        if kind == "origin":
            raise RuntimeError("synthetic interruption before origin transaction")
        return native_put(self, claim, kind, value)

    with store(enrolled) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        monkeypatch.setattr(Retention, "put", interrupted)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            capture(db, claim)
        assert db.read_claim(claim, "origin") is None
        assert db.metadata()["frames"] == 1 and db.metadata()["records"] == 0
    monkeypatch.setattr(Retention, "put", native_put)
    with store(enrolled) as db:
        with pytest.raises(ValueError, match="RETENTION_INCOMPLETE_FRAME"):
            db.claim(bundle=bundle(), now_ms=NOW + 1000)
    assert len(capture_child[1]) == 1


def test_full_default_day_native_origins_decisions_results(enrolled, tmp_path, capture_child):
    """8732 real durable frames, native validated account shape, no network."""
    import statistics
    import time
    from tools.gold_pilot_retention_store import Retention, AREA, AUDIT, canonical
    from tests.tools.test_gold_pilot_retention import original

    clock, calls = capture_child
    timestamps = [NOW] + [NOW + 1000 + i * 10000 for i in range(8730)] + [NOW + 87300000 - 1]
    elapsed = []
    origin_bytes = []
    started = time.perf_counter()
    with store(enrolled) as db:
        for at in timestamps:
            before = time.perf_counter()
            clock[0] = at
            claim = db.claim(bundle=bundle(), now_ms=at)
            origin = capture(db, claim)
            assert origin["status"] == "CAPTURED"
            row = packet(enrolled, at)
            row["decision"]["origin_sha256"] = _digest(origin)
            row["decision_sha256"] = _digest(row["decision"])
            db.put(claim, "decision", row["decision"])
            db.put(claim, "result", row)
            elapsed.append(time.perf_counter() - before)
            origin_bytes.append(len(canonical(origin)))
        metadata = db.metadata()
        assert metadata["frames"] == len(calls) == 8732 and metadata["records"] == 26196
        assert json.loads(db.read_claim(claim, "origin")) == origin
        assert json.loads(db.read("result", _digest(row))) == row
        binding = dict(db.binding)
    write_seconds = time.perf_counter() - started
    before = time.perf_counter()
    with store(enrolled) as db:
        assert db.metadata() == metadata
    cold_seconds = time.perf_counter() - before
    before = time.perf_counter()
    with Retention(
        enrolled.root,
        original=original(enrolled),
        base_runtime=bundle(),
        uid=enrolled.root.stat().st_uid,
        role="executor",
        read_only=True,
        verify_history=False,
    ) as reader:
        assert json.loads(reader.read_claim(claim, "origin")) == origin
        assert json.loads(reader.read("result", _digest(row))) == row
    exact_seconds = time.perf_counter() - before
    paths = [p for name in (AREA, AUDIT) for p in (enrolled.root / name).rglob("*")]
    report = dict(
        frames=len(elapsed),
        records=metadata["records"],
        payload_bytes=metadata["bytes"],
        native_origin_min_bytes=min(origin_bytes),
        native_origin_max_bytes=max(origin_bytes),
        write_seconds=write_seconds,
        cold_writer_seconds=cold_seconds,
        exact_origin_and_result_reader_seconds=exact_seconds,
        first_100_median_seconds=statistics.median(elapsed[:100]),
        last_100_median_seconds=statistics.median(elapsed[-100:]),
        p99_seconds=sorted(elapsed)[int(len(elapsed) * 0.99)],
        max_seconds=max(elapsed),
        allocated_file_blocks_bytes=sum(p.stat().st_blocks * 512 for p in paths),
        database_bytes=(enrolled.root / AREA / "records.sqlite3").stat().st_size,
        audit_files=sum(1 for p in (enrolled.root / AUDIT / "events").iterdir()),
        binding=binding,
        scope="NATIVE_VALIDATED_ORIGIN_SYNTHETIC_HOLDING_REAL_DURABLE_STORAGE_NO_NETWORK",
    )
    (tmp_path / "origin-performance.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(
        "ORIGIN_RETENTION_PERFORMANCE "
        + json.dumps({k: v for k, v in report.items() if k != "binding"}, sort_keys=True)
    )
