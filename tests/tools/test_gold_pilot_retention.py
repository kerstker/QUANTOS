"""Original pilot sampling and complete immutable payload retention."""

from copy import deepcopy
import json
import os
import sqlite3

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_pilot_original_deadline import enrolled as enrolled, NOW, SOURCE


def bundle():
    return dict(
        schema="GOLD_BAR_AUTO_EPISODE_INPUT_V1",
        source_commit=SOURCE,
        trial_manifest_sha256="a" * 64,
        archive_source_commit="b" * 40,
        candidate="channel",
        seconds=30,
        episode_id="e" * 64,
    )


def original(value):
    return dict(request=value.request, configuration=value.config)


def store(value, *, role="executor", root=None):
    from tools.gold_pilot_retention_store import Retention

    return Retention(root or value.root, original=original(value), base_runtime=bundle(), uid=os.getuid(), role=role)


def decision(value, at):
    return dict(
        schema="GOLD_STRATEGY_CYCLE_DECISION_V1",
        status="HOLDING",
        source_commit=SOURCE,
        bundle_sha256=_digest(bundle()),
        account_ref_sha256=value.account,
        observed_at_ms=at,
        origin_sha256=_digest({"at": at}),
        position_flat=False,
        submission_enabled=False,
        economic_authority=False,
        pilot_control=dict(status="ACTIVE", configuration_sha256=_digest(value.config), submission_enabled=False),
    )


def packet(value, at):
    from tests.tools.test_gold_pilot_original_deadline import window

    item = decision(value, at)
    return dict(
        schema="GOLD_STRATEGY_WORKER_RESULT_V1",
        status="DECISION_RETAINED",
        source_commit=SOURCE,
        bundle_sha256=_digest(bundle()),
        decision_sha256=_digest(item),
        decision=item,
        original_pilot_window=window(value),
        submission_enabled=False,
        economic_authority=False,
    )


def complete(value, db, at):
    claim = db.claim(bundle=bundle(), now_ms=at)
    row = packet(value, at)
    db.put(claim, "decision", row["decision"])
    db.put(claim, "result", row)
    return claim, row


def test_more_than_1000_distinct_frames_keep_original_hashes(enrolled):
    with store(enrolled) as db:
        for i in range(1002):
            _, row = complete(enrolled, db, NOW + i * 1000)
        assert db.summary()["frames"] == 1002
        assert json.loads(db.read("result", _digest(row))) == row
    with store(enrolled) as reopened:
        assert reopened.summary()["frames"] == 1002
        assert json.loads(reopened.read("decision", row["decision_sha256"])) == row["decision"]


def test_short_sessions_and_restart_share_original_cadence_and_window(enrolled):
    with store(enrolled) as db:
        complete(enrolled, db, NOW + 1000)
    with store(enrolled) as db:
        for at in (NOW, NOW + 1999):
            with pytest.raises(ValueError, match="RETENTION_CADENCE"):
                db.claim(bundle=bundle(), now_ms=at)
        complete(enrolled, db, NOW + 2000)
        before = db.summary()
        with pytest.raises(ValueError, match="RETENTION_WINDOW"):
            db.claim(bundle=bundle(), now_ms=enrolled.config["ends_at_ms"] + 900000)
        assert db.summary() == before


def test_partial_frame_never_silently_resamples_after_restart(enrolled):
    with store(enrolled) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        db.put(claim, "decision", decision(enrolled, NOW))
    with store(enrolled) as db:
        with pytest.raises(ValueError, match="RETENTION_INCOMPLETE_FRAME"):
            db.claim(bundle=bundle(), now_ms=NOW + 1000)
        row = packet(enrolled, NOW)
        db.put(claim, "result", row)
        assert json.loads(db.read("result", _digest(row))) == row
        complete(enrolled, db, NOW + 1000)


def test_original_identity_and_content_cannot_be_rebound(enrolled):
    with store(enrolled) as db:
        claim, row = complete(enrolled, db, NOW)
        changed = deepcopy(row)
        changed["decision"]["status"] = "NO_ENTRY_SIGNAL"
        with pytest.raises(ValueError, match="RETENTION_RECORD_CONFLICT"):
            db.put(claim, "result", changed)
    from tools.gold_pilot_retention_store import Retention

    changed = original(enrolled)
    changed = deepcopy(changed)
    changed["configuration"]["initial_equity"] = "6000"
    with pytest.raises(ValueError, match="RETENTION_BINDING"):
        Retention(enrolled.root, original=changed, base_runtime=bundle(), uid=os.getuid(), role="executor")


@pytest.mark.parametrize("damage", ["database", "audit", "marker", "row", "payload"])
def test_missing_or_changed_history_never_restores_capacity(enrolled, damage):
    from tools import gold_pilot_retention_store as retained

    with store(enrolled) as db:
        _, row = complete(enrolled, db, NOW)
    if damage == "database":
        (enrolled.root / retained.AREA / "records.sqlite3").unlink()
    elif damage == "audit":
        next((enrolled.root / retained.AUDIT / "events").iterdir()).unlink()
    elif damage == "marker":
        (enrolled.root / retained.MARKER).unlink()
    else:
        with sqlite3.connect(enrolled.root / retained.AREA / "records.sqlite3") as conn:
            # Simulate damaged storage beneath the new SQL immutability gate.
            # Restore the exact reviewed trigger so the original row/payload
            # rejection remains exercised independently of schema checking.
            name = "records_no_delete" if damage == "row" else "records_no_update"
            trigger = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
            conn.execute("DROP TRIGGER " + name)
            if damage == "row":
                conn.execute("DELETE FROM records WHERE kind='result'")
            else:
                conn.execute("UPDATE records SET payload=? WHERE kind='result'", (b"{}",))
            conn.execute(trigger)
    with pytest.raises((ValueError, FileNotFoundError)):
        with store(enrolled) as db:
            db.read("result", _digest(row))


def test_single_record_and_aggregate_limits_retain_prior_evidence(enrolled, monkeypatch):
    from tools import gold_pilot_retention_store as retained

    monkeypatch.setattr(retained, "MAX_TOTAL_BYTES", 12000)
    with store(enrolled) as db:
        claim, row = complete(enrolled, db, NOW)
        snapshot = db.read("result", _digest(row))
        later = db.claim(bundle=bundle(), now_ms=NOW + 1000)
        too_big = decision(enrolled, NOW + 1000)
        too_big["diagnostic"] = "x" * 12000
        with pytest.raises(ValueError, match="RETENTION_BYTE_BUDGET"):
            db.put(later, "decision", too_big)
        assert db.read("result", _digest(row)) == snapshot
        monkeypatch.setattr(retained, "MAX_RECORD_BYTES", 100)
        with pytest.raises(ValueError, match="RETENTION_RECORD_BUDGET"):
            db.put(later, "decision", decision(enrolled, NOW + 1000))


def test_shared_readers_exclude_writer_and_writer_excludes_readers(enrolled):
    from tools.gold_pilot_retention_store import Retention

    with store(enrolled) as writer:
        complete(enrolled, writer, NOW)
        with pytest.raises(ValueError, match="RETENTION_BUSY"):
            Retention(
                enrolled.root,
                original=original(enrolled),
                base_runtime=bundle(),
                uid=os.getuid(),
                role="executor",
                read_only=True,
            )
    with Retention(
        enrolled.root,
        original=original(enrolled),
        base_runtime=bundle(),
        uid=os.getuid(),
        role="executor",
        read_only=True,
    ) as first:
        with Retention(
            enrolled.root,
            original=original(enrolled),
            base_runtime=bundle(),
            uid=os.getuid(),
            role="executor",
            read_only=True,
        ):
            with pytest.raises(ValueError, match="RETENTION_BUSY"):
                store(enrolled)
        assert first.summary()["frames"] == 1


def test_real_reader_process_holds_shared_lease_until_exit(enrolled):
    import subprocess
    import sys

    with store(enrolled) as writer:
        complete(enrolled, writer, NOW)
    arguments = dict(
        root=str(enrolled.root),
        original=original(enrolled),
        base_runtime=bundle(),
        uid=os.getuid(),
        role="executor",
        read_only=True,
        verify_history=False,
    )
    code = (
        "import json,sys; from tools.gold_pilot_retention_store import Retention; "
        'd=Retention(**json.loads(sys.argv[1])); print("READY",flush=True); '
        "sys.stdin.readline(); d.close()"
    )
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", code, json.dumps(arguments)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline() == "READY\n"
        with pytest.raises(ValueError, match="RETENTION_BUSY"):
            store(enrolled)
        child.communicate("\n", timeout=5)
        assert child.returncode == 0
        with store(enrolled) as writer:
            assert writer.metadata()["frames"] == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_claim_witness_content_is_verified_before_new_admission(enrolled):
    from tools import gold_pilot_retention_store as retained

    with store(enrolled) as db:
        complete(enrolled, db, NOW)
    for path in (enrolled.root / retained.AUDIT / "events").iterdir():
        if json.loads(path.read_bytes())["kind"] == "claim":
            path.write_bytes(b"")
    with pytest.raises(ValueError):
        store(enrolled)


@pytest.mark.parametrize("names", [("area", "marker"), ("database", "audit")])
def test_paired_loss_keeps_independent_witness(enrolled, names):
    import shutil
    from tools import gold_pilot_retention_store as retained

    with store(enrolled) as db:
        complete(enrolled, db, NOW)
    paths = dict(
        area=enrolled.root / retained.AREA,
        marker=enrolled.root / retained.MARKER,
        database=enrolled.root / retained.AREA / "records.sqlite3",
        audit=enrolled.root / retained.AUDIT,
    )
    for name in names:
        path = paths[name]
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        store(enrolled)


def test_database_page_limit_and_immutable_sql_rows(enrolled):
    with store(enrolled) as db:
        complete(enrolled, db, NOW)
        assert db.conn.execute("PRAGMA max_page_count").fetchone()[0] < 2**24
        for table in ("frames", "records"):
            with pytest.raises(sqlite3.DatabaseError, match="RETENTION_IMMUTABLE"):
                db.conn.execute("DELETE FROM " + table)


def test_partial_checkpoint_is_detected_and_never_renews_claim(enrolled, monkeypatch):
    from tools import gold_pilot_retention_store as retained

    original_write = retained._write
    with store(enrolled) as db:

        def interrupted(path, body):
            if path.name == "head.next":
                raise OSError("synthetic checkpoint interruption")
            return original_write(path, body)

        monkeypatch.setattr(retained, "_write", interrupted)
        with pytest.raises(OSError, match="checkpoint"):
            db.claim(bundle=bundle(), now_ms=NOW)
    monkeypatch.setattr(retained, "_write", original_write)
    with pytest.raises(ValueError, match="RETENTION_HEAD"):
        store(enrolled)


def test_hot_commits_use_transaction_counters_not_full_payload_scans(enrolled):
    with store(enrolled) as db:
        statements = []
        db.conn.set_trace_callback(statements.append)
        complete(enrolled, db, NOW)
        assert not any(
            "SUM(" in s.upper() or "COUNT(*) FROM RECORDS" in s.upper() and "WHERE ORDINAL=" not in s.upper()
            for s in statements
        )


def test_per_frame_record_limit_uses_an_ordinal_lookup(enrolled):
    with store(enrolled) as db:
        plan = db.conn.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM records WHERE ordinal=?", (1,)).fetchall()
        assert all("SCAN " not in row[3] for row in plan), plan


def test_adopt_cannot_skip_an_incomplete_prior_frame(enrolled):
    with store(enrolled) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        later = dict(claim, ordinal=2, admitted_at_ms=NOW + 1000)
        with pytest.raises(ValueError, match="RETENTION_INCOMPLETE_FRAME"):
            db.adopt(later)


def test_released_reader_reacquires_exact_append_and_rejects_missing_witness(enrolled):
    from tools import gold_pilot_retention_store as retained

    with store(enrolled) as writer:
        _, first = complete(enrolled, writer, NOW)
    reader = retained.Retention(
        enrolled.root,
        original=original(enrolled),
        base_runtime=bundle(),
        uid=os.getuid(),
        role="executor",
        read_only=True,
    )
    try:
        reader.release()
        with store(enrolled) as writer:
            _, last = complete(enrolled, writer, NOW + 1000)
        reader.acquire()
        assert json.loads(reader.read("result", _digest(last))) == last
        reader.release()
        next((enrolled.root / retained.AUDIT / "events").iterdir()).unlink()
        with pytest.raises(ValueError, match="RETENTION_AUDIT"):
            reader.acquire()
    finally:
        reader.close()


def test_exact_reader_checks_selected_claim_and_payload_without_quota_scan(enrolled, monkeypatch):
    from tools import gold_pilot_retention_store as retained

    with store(enrolled) as writer:
        claim, row = complete(enrolled, writer, NOW)

    def no_history(*a, **kw):
        pytest.fail("exact reader must not claim full history verification")

    monkeypatch.setattr(retained.Retention, "_validate", no_history)
    with retained.Retention(
        enrolled.root,
        original=original(enrolled),
        base_runtime=bundle(),
        uid=os.getuid(),
        role="executor",
        read_only=True,
        verify_history=False,
    ) as reader:
        assert reader.reference("result", _digest(row)) == claim
        assert json.loads(reader.read("result", _digest(row))) == row
        assert reader.metadata()["frames"] == 1
        with pytest.raises(ValueError, match="RETENTION_WRITE_LEASE"):
            reader.adopt(claim)
    with pytest.raises(ValueError, match="RETENTION_READER_SCOPE"):
        retained.Retention(
            enrolled.root,
            original=original(enrolled),
            base_runtime=bundle(),
            uid=os.getuid(),
            role="executor",
            verify_history=False,
        )


@pytest.mark.parametrize("damage", ["claim_witness", "same_size_payload"])
def test_exact_reader_rejects_selected_corruption(enrolled, damage):
    from tools import gold_pilot_retention_store as retained

    with store(enrolled) as db:
        _, row = complete(enrolled, db, NOW)
    if damage == "claim_witness":
        for path in (enrolled.root / retained.AUDIT / "events").iterdir():
            if json.loads(path.read_bytes())["kind"] == "claim":
                path.write_bytes(b"")
    else:
        with sqlite3.connect(enrolled.root / retained.AREA / "records.sqlite3") as conn:
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='records_no_update'").fetchone()[0]
            body = conn.execute("SELECT payload FROM records WHERE kind='result'").fetchone()[0]
            changed = body.replace(b"HOLDING", b"CORRUPT")
            assert len(changed) == len(body) and changed != body
            conn.execute("DROP TRIGGER records_no_update")
            conn.execute("UPDATE records SET payload=? WHERE kind='result'", (changed,))
            conn.execute(sql)
    with retained.Retention(
        enrolled.root,
        original=original(enrolled),
        base_runtime=bundle(),
        uid=os.getuid(),
        role="executor",
        read_only=True,
        verify_history=False,
    ) as reader:
        with pytest.raises(ValueError):
            reader.read("result", _digest(row))


@pytest.mark.parametrize("runtime", ["fresh", "retained"])
def test_original_other_bar_runtimes_remain_bound(enrolled, runtime):
    from tools import gold_pilot_retention_store as retained

    base = bundle()
    if runtime == "fresh":
        base["schema"] = "GOLD_BAR_FRESH_EPISODE_INPUT_V1"
        base["history_sha256"] = base.pop("episode_id")
        field = "history_sha256"
    else:
        base = dict(
            schema="GOLD_BAR_RETAINED_EPISODE_INPUT_V1",
            source_commit=SOURCE,
            trial_manifest_sha256="a" * 64,
            archive_source_commit="b" * 40,
            observation_sha256="c" * 64,
            entry_decision_ms=NOW - 1,
        )
        field = "observation_sha256"
    with retained.Retention(
        enrolled.root, original=original(enrolled), base_runtime=base, uid=os.getuid(), role="executor"
    ) as db:
        changed = dict(base)
        changed[field] = "f" * 64
        with pytest.raises(ValueError, match="RETENTION_RUNTIME_CHANGED"):
            db.claim(bundle=changed, now_ms=NOW)
        assert db.claim(bundle=base, now_ms=NOW)["bundle_sha256"] == _digest(base)


@pytest.mark.parametrize("damage", ["database", "lease"])
def test_reacquire_rejects_replaced_identity(enrolled, damage):
    from tools import gold_pilot_retention_store as retained

    with store(enrolled) as db:
        complete(enrolled, db, NOW)
        db.release()
        path = enrolled.root / retained.AREA / ("records.sqlite3" if damage == "database" else "lease.lock")
        raw = path.read_bytes()
        path.rename(path.with_suffix(".saved"))
        path.write_bytes(raw)
        path.chmod(0o600)
        # Keep the namespace valid while changing the underlying object.
        path.with_suffix(".saved").rename(enrolled.root / (damage + ".saved"))
        with pytest.raises(ValueError, match="RETENTION_.*CHANGED"):
            db.acquire()


def test_physical_database_growth_is_not_hidden_by_payload_counter(enrolled):
    with store(enrolled) as db:
        complete(enrolled, db, NOW)
        old = db.binding["max_database_bytes"]
        db.binding["max_database_bytes"] = 4096
        with pytest.raises(ValueError, match="RETENTION_DATABASE_PHYSICAL_BUDGET"):
            db._physical()
        db.binding["max_database_bytes"] = old
        assert db.metadata()["records"] == 2


def test_unknown_database_schema_and_leftover_journal_fail_closed(enrolled):
    from tools import gold_pilot_retention_store as retained

    with store(enrolled) as db:
        complete(enrolled, db, NOW)
    path = enrolled.root / retained.AREA / "records.sqlite3-journal"
    path.write_bytes(b"partial synthetic rollback journal")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="RETENTION_DATABASE_PARTIAL"):
        store(enrolled)
    assert path.read_bytes() == b"partial synthetic rollback journal"
    path.unlink()
    with sqlite3.connect(enrolled.root / retained.AREA / "records.sqlite3") as conn:
        conn.execute("CREATE TABLE unreviewed(value BLOB)")
    with pytest.raises(ValueError, match="RETENTION_DATABASE_SCHEMA"):
        store(enrolled)


def test_original_large_v1_result_is_retained_exactly_without_16k_cap(enrolled):
    with store(enrolled) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        row = packet(enrolled, NOW)
        # Native unresolved recovery can report every pending original ID:
        # 1000 original batches * 20 bounded client IDs, without truncation.
        row["recovery_observation"] = dict(
            status="INPUTS_UNRESOLVED",
            new_order_submission_enabled=False,
            pending_client_order_ids=["QD" + format(i, "034d") for i in range(20000)],
        )
        db.put(claim, "decision", row["decision"])
        identity = db.put(claim, "result", row)
        raw = db.read("result", identity)
        assert len(raw) > 256 * 1024 and json.loads(raw) == row and identity == _digest(row)


def test_full_default_day_and_closing_frames_keep_all_originals(enrolled, tmp_path):
    """Real durable API writes; 10s cadence plus opening/closing alignment."""
    import statistics
    import time
    from tools import gold_pilot_retention_store as retained

    times = []
    started = time.perf_counter()
    timestamps = [NOW] + [NOW + 1000 + i * 10000 for i in range(8730)] + [NOW + 87300000 - 1]
    assert len(timestamps) == 8732
    with store(enrolled) as db:
        for at in timestamps:
            before = time.perf_counter()
            _, last = complete(enrolled, db, at)
            times.append(time.perf_counter() - before)
        metadata = db.metadata()
        assert metadata["frames"] == 8732 and metadata["records"] == 17464
        assert json.loads(db.read("result", _digest(last))) == last
        binding = dict(db.binding)
    write_seconds = time.perf_counter() - started
    before = time.perf_counter()
    with store(enrolled) as reopened:
        assert reopened.metadata() == metadata
    cold_seconds = time.perf_counter() - before
    before = time.perf_counter()
    with retained.Retention(
        enrolled.root,
        original=original(enrolled),
        base_runtime=bundle(),
        uid=os.getuid(),
        role="executor",
        read_only=True,
        verify_history=False,
    ) as reader:
        assert json.loads(reader.read("result", _digest(last))) == last
    exact_seconds = time.perf_counter() - before
    physical = sum(
        p.stat().st_blocks * 512 for name in (retained.AREA, retained.AUDIT) for p in (enrolled.root / name).rglob("*")
    )
    report = dict(
        frames=len(times),
        records=metadata["records"],
        payload_bytes=metadata["bytes"],
        write_seconds=write_seconds,
        cold_writer_seconds=cold_seconds,
        exact_reader_seconds=exact_seconds,
        first_100_median_seconds=statistics.median(times[:100]),
        last_100_median_seconds=statistics.median(times[-100:]),
        p99_seconds=sorted(times)[int(len(times) * 0.99)],
        max_seconds=max(times),
        allocated_file_blocks_bytes=physical,
        binding=binding,
        scope="SYNTHETIC_HOLDING_PAYLOADS_REAL_DURABLE_STORAGE_NO_NETWORK",
    )
    (tmp_path / "performance.json").write_text(json.dumps(report, sort_keys=True, indent=2))
    print("RETENTION_PERFORMANCE " + json.dumps({k: v for k, v in report.items() if k != "binding"}, sort_keys=True))


def test_completed_frame_cannot_add_or_capture_a_new_origin(enrolled, monkeypatch):
    from tools import demo_executor_origin as worker

    monkeypatch.setattr(worker, "fixed_account", lambda: (enrolled.root, enrolled.account))
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **kw: pytest.fail("late network capture"))
    with store(enrolled) as db:
        claim, _ = complete(enrolled, db, NOW)
        with pytest.raises(ValueError, match="ORIGIN.*ORDER"):
            worker.capture_for_pilot(symbol="XAUUSDT", source_commit=SOURCE, retention=db, claim=claim)
        with pytest.raises(ValueError, match="ORIGIN.*ORDER"):
            db.put(claim, "origin", enrolled.origin)


def test_retained_origin_must_match_decision_digest(enrolled):
    with store(enrolled) as db:
        claim = db.claim(bundle=bundle(), now_ms=NOW)
        db.put(claim, "origin", enrolled.origin)
        with pytest.raises(ValueError, match="RETENTION_ORIGIN_HASH"):
            db.put(claim, "decision", decision(enrolled, NOW))
        row = decision(enrolled, NOW)
        row["origin_sha256"] = _digest(enrolled.origin)
        db.put(claim, "decision", row)
