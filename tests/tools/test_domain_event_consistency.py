from services.execution.events import dry_run_started_event
from services.monitoring.domain_event_runtime import project_pending_domain_events
from services.monitoring.event_bus import DurableEventBus
from services.monitoring.event_projection import DomainEventProjection
from tools.domain_event_consistency import run_domain_event_consistency


def _event(event_id: str, timestamp: int):
    return dry_run_started_event(
        run_id=event_id,
        strategy_id="test",
        timestamp_ms=timestamp,
        mode="DRY_RUN",
    )


def test_aligned_authority_and_projection_pass_read_only(tmp_path):
    event_path = str(tmp_path / "events.db")
    projection_path = str(tmp_path / "projection.db")
    bus = DurableEventBus(event_path)
    bus.publish(_event("r1", 1), correlation_id="r1")
    project_pending_domain_events(
        bus,
        now_ms=2,
        projection_path=projection_path,
        worker_id="test-consistency",
    )
    bus.close()

    document = run_domain_event_consistency(
        event_path=event_path,
        projection_path=projection_path,
        output_path=str(tmp_path / "consistency.json"),
    )

    assert document["verdict"] == "PASS"
    assert document["reasons"] == []
    assert document["comparison"]["authority_event_records"] == 1
    assert document["comparison"]["projection_event_records"] == 1
    assert document["comparison"]["consumer_offset"] == 1
    assert document["safety"]["database_writes"] is False


def test_projection_from_another_authority_is_a_hard_breach(tmp_path):
    event_path = str(tmp_path / "events.db")
    stale_event_path = str(tmp_path / "stale-events.db")
    projection_path = str(tmp_path / "projection.db")

    current = DurableEventBus(event_path)
    current.publish(_event("current", 1), correlation_id="current")
    current.close()

    stale = DurableEventBus(stale_event_path)
    stale.publish(_event("stale-1", 1), correlation_id="stale-1")
    stale.publish(_event("stale-2", 2), correlation_id="stale-2")
    project_pending_domain_events(
        stale,
        now_ms=3,
        projection_path=projection_path,
        worker_id="test-consistency-stale",
    )
    stale.close()

    document = run_domain_event_consistency(
        event_path=event_path,
        projection_path=projection_path,
        output_path=str(tmp_path / "consistency.json"),
    )

    assert document["verdict"] == "BREACH"
    assert "PROJECTION_AHEAD_OF_AUTHORITY" in document["reasons"]
    assert "PROJECTION_PREFIX_MISMATCH" in document["reasons"]
    assert document["safety"]["order_submission"] is False
