import json
import sqlite3

from services.execution.events import dry_run_started_event
from services.monitoring.event_bus import DurableEventBus
from tools.domain_event_projection_rebuild import rebuild_projection_candidate


def test_rebuild_creates_noncanonical_candidate_without_mutating_source(tmp_path):
    event_path = str(tmp_path / "events.db")
    candidate_path = str(tmp_path / "candidate.db")
    report_path = str(tmp_path / "rebuild.json")
    bus = DurableEventBus(event_path)
    bus.publish(
        dry_run_started_event(
            run_id="r1",
            strategy_id="test",
            timestamp_ms=1,
            mode="DRY_RUN",
        ),
        correlation_id="r1",
    )
    bus.close()
    source_before = (tmp_path / "events.db").read_bytes()

    document = rebuild_projection_candidate(
        event_path=event_path,
        output_projection_path=candidate_path,
        report_path=report_path,
    )

    assert document["verdict"] == "PASS"
    assert document["promotion"] == "CANDIDATE_CREATED_REQUIRES_HUMAN_REVIEW"
    assert (tmp_path / "candidate.db").is_file()
    check = sqlite3.connect(candidate_path)
    try:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        check.close()
    assert (tmp_path / "events.db").read_bytes() == source_before
    assert json.loads((tmp_path / "rebuild.json").read_text()) == document


def test_rebuild_refuses_to_overwrite_existing_candidate(tmp_path):
    event_path = str(tmp_path / "events.db")
    candidate_path = tmp_path / "candidate.db"
    candidate_path.write_bytes(b"retain")
    bus = DurableEventBus(event_path)
    bus.close()

    document = rebuild_projection_candidate(
        event_path=event_path,
        output_projection_path=str(candidate_path),
        report_path=str(tmp_path / "rebuild.json"),
    )

    assert document["verdict"] == "BLOCKED"
    assert document["reason"] == "OUTPUT_ALREADY_EXISTS"
    assert candidate_path.read_bytes() == b"retain"
