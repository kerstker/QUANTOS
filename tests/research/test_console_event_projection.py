from quantos.contracts.events import Event
from services.data.events import data_quality_admission_decided_event
from services.deployment.events import deployment_recorded_event
from services.monitoring.event_bus import EventDelivery
from services.monitoring.event_projection import DomainEventProjection


def test_console_events_prefers_real_domain_event_projection(tmp_path, monkeypatch):
    from services.console.api import system_views

    path = str(tmp_path / "projection.db")
    event = Event(
        event_id="dry-run-1",
        event_type="execution.dry_run.completed",
        timestamp=1_000_000,
        source="services.execution.dry_run_pipeline",
        domain="execution",
        payload={"run_id": "run-1", "n_fills": 2, "api_key": "must-not-leak"},
    )
    delivery = EventDelivery(
        consumer_id="domain-event-projection-v1",
        worker_id="test",
        event_seq=7,
        event=event,
        correlation_id="run-1",
        causation_id="dry-run-start-1",
        attempt=1,
        claim_journal_seq=1,
        claimed_at_ms=1,
        lease_until_ms=2,
    )
    projection = DomainEventProjection(path)
    projection.apply(delivery)
    projection.close()
    monkeypatch.setattr(system_views, "_DOMAIN_EVENT_PROJECTION_DB", path)
    monkeypatch.setattr(
        system_views,
        "_DOMAIN_EVENT_DB",
        str(tmp_path / "missing-events.db"),
    )

    payload = system_views._events_payload()

    assert payload["data_provenance"] == "DOMAIN_EVENT_PROJECTION"
    assert payload["event_bus_health"]["verdict"] == "UNAVAILABLE"
    assert payload["events"] == [
        {
            "event_id": "dry-run-1",
            "event_type": "execution.dry_run.completed",
            "timestamp": 1,
            "source": "services.execution.dry_run_pipeline",
            "correlation_id": "run-1",
            "payload": {
                "causation_id": "dry-run-start-1",
                "domain": "execution",
                "event_seq": 7,
                "n_fills": 2,
                "read_only": True,
                "run_id": "run-1",
            },
        }
    ]


def test_console_surfaces_safe_deployment_state_fields(tmp_path, monkeypatch):
    from services.console.api import system_views

    path = str(tmp_path / "projection.db")
    at_ms = 1_784_016_000_000
    event = deployment_recorded_event(
        environment="LIVE",
        subject="release-7",
        actor_id="multi_party_grant",
        allowed=False,
        reasons=("READINESS_BELOW_LIVE", "LIVE_FLAGS_DISABLED"),
        version_ids=("cfg", "strat"),
        at_ms=at_ms,
    )
    projection = DomainEventProjection(path)
    projection.apply(
        EventDelivery(
            consumer_id="domain-event-projection-v1",
            worker_id="test",
            event_seq=8,
            event=event,
            correlation_id="release-7",
            causation_id="approval-7",
            attempt=1,
            claim_journal_seq=1,
            claimed_at_ms=1,
            lease_until_ms=2,
        )
    )
    projection.close()
    monkeypatch.setattr(system_views, "_DOMAIN_EVENT_PROJECTION_DB", path)
    monkeypatch.setattr(
        system_views,
        "_DOMAIN_EVENT_DB",
        str(tmp_path / "missing-events.db"),
    )

    payload = system_views._events_payload()

    assert payload["data_provenance"] == "DOMAIN_EVENT_PROJECTION"
    assert payload["events"][0]["timestamp"] == at_ms // 1_000
    assert payload["events"][0]["payload"] == {
        "actor_id": "multi_party_grant",
        "allowed": False,
        "causation_id": "approval-7",
        "domain": "governance",
        "environment": "LIVE",
        "event_seq": 8,
        "read_only": True,
        "reasons": ["READINESS_BELOW_LIVE", "LIVE_FLAGS_DISABLED"],
        "subject": "release-7",
        "version_ids": ["cfg", "strat"],
    }


def test_console_surfaces_only_redacted_data_admission_summary(tmp_path, monkeypatch):
    from services.console.api import system_views

    path = str(tmp_path / "projection.db")
    at_ms = 1_784_016_000_000
    event = data_quality_admission_decided_event(
        admission_id="data-admission-1",
        verdict="BLOCK",
        quality_verdict="BLOCK",
        lineage_verdict="BLOCK",
        evidence_schema_version=2,
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        instrument_count=24,
        block_reason_count=2,
        price_basis="PERP_MARK_PRICE",
        readiness="L2_DRY_RUN_ONLY",
        purpose="FORWARD_EVIDENCE_DATA_ADMISSION",
        at_ms=at_ms,
    )
    projection = DomainEventProjection(path)
    projection.apply(
        EventDelivery(
            consumer_id="domain-event-projection-v1",
            worker_id="test",
            event_seq=9,
            event=event,
            correlation_id="data-admission-1",
            causation_id="refresh-1",
            attempt=1,
            claim_journal_seq=1,
            claimed_at_ms=1,
            lease_until_ms=2,
        )
    )
    projection.close()
    monkeypatch.setattr(system_views, "_DOMAIN_EVENT_PROJECTION_DB", path)
    monkeypatch.setattr(
        system_views,
        "_DOMAIN_EVENT_DB",
        str(tmp_path / "missing-events.db"),
    )

    payload = system_views._events_payload()

    assert payload["data_provenance"] == "DOMAIN_EVENT_PROJECTION"
    event_payload = payload["events"][0]["payload"]
    assert event_payload["admission_id"] == "data-admission-1"
    assert event_payload["verdict"] == "BLOCK"
    assert event_payload["block_reason_count"] == 2
    assert event_payload["causation_id"] == "refresh-1"
    assert "block_reasons" not in event_payload
    assert "sources" not in event_payload
