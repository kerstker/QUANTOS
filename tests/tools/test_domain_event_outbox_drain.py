import json
import sqlite3

from services.execution.dry_run_store import (
    DryRunOutboxEvent,
    DryRunRun,
    DryRunStore,
)
from services.execution.events import dry_run_completed_event
from services.monitoring.event_bus import DurableEventBus
from services.monitoring.event_projection import DomainEventProjection
from tools.domain_event_bus import dispatch_dry_run_outbox
from tools.domain_event_outbox_drain import (
    OutboxSource,
    default_outbox_sources,
    drain_domain_event_outboxes,
)


class _Store:
    def __init__(self, name: str):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


class _Bus:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_default_sources_cover_all_eight_transactional_outboxes():
    sources = default_outbox_sources()

    assert [source.name for source in sources] == [
        "dry_run",
        "experiment_registry",
        "alpha_maintenance",
        "kill_switch",
        "deployment",
        "data_refresh",
        "data_quality_admission",
        "shadow",
    ]
    assert len({source.path for source in sources}) == 8


def test_drain_attempts_all_sources_then_projects_once(tmp_path):
    calls = []
    stores = {}
    sources = []
    for name in ("dry_run", "data_refresh"):
        path = tmp_path / f"{name}.db"
        path.write_bytes(b"present")

        def opener(_path, *, source_name=name):
            store = _Store(source_name)
            stores[source_name] = store
            return store

        def dispatcher(store, bus, *, at_ms, source_name=name):
            calls.append((source_name, at_ms, bus))
            return {"dispatched": 1, "pending": 0}

        sources.append(
            OutboxSource(
                name=name,
                path=str(path),
                opener=opener,
                dispatcher=dispatcher,
            )
        )

    bus = _Bus()
    projections = []

    def project(event_bus, **kwargs):
        projections.append((event_bus, kwargs))
        return {"projected": 2, "offset": 2}

    output = tmp_path / "drain.json"
    document = drain_domain_event_outboxes(
        now_ms=100,
        event_path=str(tmp_path / "events.db"),
        projection_path=str(tmp_path / "projection.db"),
        output_path=str(output),
        sources=tuple(sources),
        event_bus_factory=lambda path, *, create: bus,
        projector=project,
    )

    assert document["verdict"] == "PASS"
    assert list(document["sources"]) == ["dry_run", "data_refresh"]
    assert all(item["status"] == "PASS" for item in document["sources"].values())
    assert [item[:2] for item in calls] == [("dry_run", 100), ("data_refresh", 100)]
    assert len(projections) == 1
    assert projections[0][1]["now_ms"] == 100
    assert projections[0][1]["worker_id"] == "domain-event-outbox-recovery"
    assert projections[0][1]["limit"] == 1_000
    assert document["projection"]["batches"] == 1
    assert document["projection"]["caught_up"] is True
    assert all(store.closed for store in stores.values())
    assert bus.closed
    assert json.loads(output.read_text(encoding="utf-8")) == document


def test_projection_catches_up_across_bounded_batches(tmp_path):
    event_path = tmp_path / "events.db"
    event_path.write_bytes(b"present")
    counts = iter((1_000, 1_000, 5))
    calls = []

    def project(_bus, **kwargs):
        calls.append(kwargs)
        return {"projected": next(counts), "offset": len(calls) * 1_000}

    document = drain_domain_event_outboxes(
        now_ms=150,
        event_path=str(event_path),
        projection_path=str(tmp_path / "projection.db"),
        output_path=str(tmp_path / "drain.json"),
        sources=(),
        event_bus_factory=lambda _path, *, create: _Bus(),
        projector=project,
    )

    assert len(calls) == 3
    assert document["projection"]["projected"] == 2_005
    assert document["projection"]["batches"] == 3
    assert document["projection"]["caught_up"] is True


def test_projection_does_not_treat_idempotent_replay_as_end_of_backlog(tmp_path):
    event_path = tmp_path / "events.db"
    event_path.write_bytes(b"present")
    batches = iter(
        (
            {"projected": 999, "idempotent_replays": 1, "offset": 1_000},
            {"projected": 2, "idempotent_replays": 0, "offset": 1_002},
        )
    )
    calls = []

    def project(_bus, **kwargs):
        calls.append(kwargs)
        return next(batches)

    document = drain_domain_event_outboxes(
        now_ms=175,
        event_path=str(event_path),
        projection_path=str(tmp_path / "projection.db"),
        output_path=str(tmp_path / "drain.json"),
        sources=(),
        event_bus_factory=lambda _path, *, create: _Bus(),
        projector=project,
    )

    assert len(calls) == 2
    assert document["projection"]["projected"] == 1_001
    assert document["projection"]["idempotent_replays"] == 1
    assert document["projection"]["processed"] == 1_002
    assert document["projection"]["batches"] == 2
    assert document["projection"]["caught_up"] is True


def test_corrupt_source_is_redacted_and_does_not_block_other_sources(tmp_path):
    bad_path = tmp_path / "bad.db"
    good_path = tmp_path / "good.db"
    bad_path.write_bytes(b"present")
    good_path.write_bytes(b"present")
    good_calls = []

    def bad_dispatch(store, bus, *, at_ms):
        raise RuntimeError("credential=must-not-leak")

    def good_dispatch(store, bus, *, at_ms):
        good_calls.append(at_ms)
        return {"dispatched": 1, "pending": 0}

    document = drain_domain_event_outboxes(
        now_ms=200,
        event_path=str(tmp_path / "events.db"),
        projection_path=str(tmp_path / "projection.db"),
        output_path=str(tmp_path / "drain.json"),
        sources=(
            OutboxSource("bad", str(bad_path), lambda path: _Store("bad"), bad_dispatch),
            OutboxSource("good", str(good_path), lambda path: _Store("good"), good_dispatch),
        ),
        event_bus_factory=lambda path, *, create: _Bus(),
        projector=lambda *args, **kwargs: {"projected": 1},
    )

    assert document["verdict"] == "BREACH"
    assert document["sources"]["bad"] == {
        "status": "BREACH",
        "error_type": "RuntimeError",
        "path": str(bad_path),
    }
    assert "must-not-leak" not in json.dumps(document)
    assert document["sources"]["good"]["status"] == "PASS"
    assert good_calls == [200]


def test_all_missing_sources_and_bus_do_not_create_empty_authorities(tmp_path):
    opened = []
    event_path = tmp_path / "events.db"
    output = tmp_path / "drain.json"

    document = drain_domain_event_outboxes(
        now_ms=300,
        event_path=str(event_path),
        projection_path=str(tmp_path / "projection.db"),
        output_path=str(output),
        sources=(
            OutboxSource(
                "missing",
                str(tmp_path / "missing.db"),
                lambda path: opened.append(path),
                lambda *args, **kwargs: None,
            ),
        ),
        event_bus_factory=lambda path, *, create: opened.append((path, create)),
        projector=lambda *args, **kwargs: None,
    )

    assert document["verdict"] == "UNAVAILABLE"
    assert document["projection"] == {"status": "UNAVAILABLE"}
    assert document["sources"]["missing"]["status"] == "UNAVAILABLE"
    assert opened == []
    assert not event_path.exists()
    assert output.is_file()


def test_missing_source_with_existing_bus_is_partial_not_full_pass(tmp_path):
    event_path = tmp_path / "events.db"
    event_path.write_bytes(b"present")

    document = drain_domain_event_outboxes(
        now_ms=400,
        event_path=str(event_path),
        projection_path=str(tmp_path / "projection.db"),
        output_path=str(tmp_path / "drain.json"),
        sources=(
            OutboxSource(
                "missing",
                str(tmp_path / "missing.db"),
                lambda path: _Store("missing"),
                lambda *args, **kwargs: {},
            ),
        ),
        event_bus_factory=lambda path, *, create: _Bus(),
        projector=lambda *args, **kwargs: {"projected": 0},
    )

    assert document["verdict"] == "PARTIAL"
    assert document["sources"]["missing"]["status"] == "UNAVAILABLE"
    assert document["projection"]["status"] == "PASS"


def test_event_authority_failure_is_redacted_into_breach_snapshot(tmp_path):
    source_path = tmp_path / "source.db"
    source_path.write_bytes(b"present")
    output = tmp_path / "drain.json"

    def broken_factory(path, *, create):
        raise OSError("token=must-not-leak")

    document = drain_domain_event_outboxes(
        now_ms=500,
        event_path=str(tmp_path / "events.db"),
        projection_path=str(tmp_path / "projection.db"),
        output_path=str(output),
        sources=(
            OutboxSource(
                "source",
                str(source_path),
                lambda path: _Store("source"),
                lambda *args, **kwargs: {},
            ),
        ),
        event_bus_factory=broken_factory,
        projector=lambda *args, **kwargs: {},
    )

    assert document["verdict"] == "BREACH"
    assert document["projection"] == {
        "status": "BREACH",
        "error_type": "OSError",
    }
    assert document["sources"]["source"]["status"] == "BLOCKED"
    assert "must-not-leak" not in json.dumps(document)


def test_existing_authority_without_schema_is_not_initialized_or_rewritten(tmp_path):
    source_path = tmp_path / "source.db"
    source_path.write_bytes(b"present")
    event_path = tmp_path / "events.db"
    connection = sqlite3.connect(event_path)
    connection.execute("CREATE TABLE unrelated_marker(value TEXT NOT NULL)")
    connection.execute("INSERT INTO unrelated_marker(value) VALUES ('preserve-me')")
    connection.commit()
    connection.close()
    before = event_path.read_bytes()
    opened = []

    document = drain_domain_event_outboxes(
        now_ms=600,
        event_path=str(event_path),
        projection_path=str(tmp_path / "projection.db"),
        output_path=str(tmp_path / "drain.json"),
        sources=(
            OutboxSource(
                "source",
                str(source_path),
                lambda path: opened.append(path),
                lambda *args, **kwargs: {},
            ),
        ),
        projector=lambda *args, **kwargs: {"projected": 0},
    )

    assert document["verdict"] == "BREACH"
    assert document["sources"]["source"]["status"] == "BLOCKED"
    assert document["projection"] == {
        "status": "BREACH",
        "error_type": "EventBusIntegrityError",
    }
    assert opened == []
    assert event_path.read_bytes() == before
    connection = sqlite3.connect(f"file:{event_path.as_posix()}?mode=ro", uri=True)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    connection.close()
    assert tables == {"unrelated_marker"}


def test_real_sqlite_outbox_recovers_into_shared_projection(tmp_path):
    source_path = str(tmp_path / "dry_run.db")
    event_path = str(tmp_path / "events.db")
    projection_path = str(tmp_path / "projection.db")
    run = DryRunRun(
        run_id="recovery-run",
        strategy_id="carry",
        timestamp=1_000,
        n_intents=1,
        n_approved=1,
        n_rejected=0,
        rejected_reasons=[],
        pnl={},
        audit_root="audit",
        detail={"n_fills": 1},
    )
    event = dry_run_completed_event(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        timestamp_ms=run.timestamp,
        n_intents=run.n_intents,
        n_approved=run.n_approved,
        n_rejected=run.n_rejected,
        n_fills=1,
        audit_root=run.audit_root,
        run_repro_hash=run.reproducibility_hash,
    )
    store = DryRunStore(source_path, create=True)
    store.save_run_with_outbox(
        run,
        [
            DryRunOutboxEvent(
                run_id=run.run_id,
                event=event,
                correlation_id=run.run_id,
                causation_id="started-recovery-run",
            )
        ],
    )
    store.close()

    document = drain_domain_event_outboxes(
        now_ms=2_000,
        event_path=event_path,
        projection_path=projection_path,
        output_path=str(tmp_path / "drain.json"),
        sources=(
            OutboxSource(
                "dry_run",
                source_path,
                lambda path: DryRunStore(path, create=False),
                dispatch_dry_run_outbox,
            ),
        ),
    )

    assert document["verdict"] == "PASS"
    assert document["sources"]["dry_run"] == {
        "status": "PASS",
        "path": source_path,
        "dispatched": 1,
        "pending": 0,
    }
    reopened = DryRunStore(source_path, create=False)
    assert reopened.pending_outbox() == ()
    reopened.close()
    bus = DurableEventBus(event_path, create=False)
    assert [record["event"].event_id for record in bus.records()] == [event.event_id]
    bus.close()
    projection = DomainEventProjection(projection_path, create=False)
    assert [record["event"].event_id for record in projection.records()] == [
        event.event_id
    ]
    projection.close()
