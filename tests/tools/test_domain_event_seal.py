from __future__ import annotations

import json
from pathlib import Path

import pytest

from quantos.contracts.events import Event
from services.monitoring.event_bus import DurableEventBus
from tools.domain_event_seal import (
    DomainEventSealError,
    export_event_authority_seal,
    verify_event_authority_seal,
)


def _seed_authority(path: Path) -> None:
    bus = DurableEventBus(str(path))
    bus.publish(
        Event(
            event_id="event-1",
            event_type="research.test.event",
            timestamp=1,
            source="test",
            domain="research",
            payload={"value": 1},
        ),
        correlation_id="correlation-1",
    )
    bus.close()


def test_external_seal_anchor_is_create_once_idempotent_and_non_mutating(tmp_path):
    repo = tmp_path / "repo"
    authority = repo / "data" / "cache" / "domain_events.db"
    authority.parent.mkdir(parents=True)
    _seed_authority(authority)
    before = authority.read_bytes()
    destination = tmp_path / "external-seals"
    evidence = repo / "evidence" / "domain_event_seal_latest.json"

    first = export_event_authority_seal(
        repo_root=repo,
        event_path=authority,
        destination_root=destination,
        evidence_path=evidence,
        now_ms=1_784_044_800_000,
        allow_local_destination=True,
    )
    second = export_event_authority_seal(
        repo_root=repo,
        event_path=authority,
        destination_root=destination,
        evidence_path=evidence,
        now_ms=1_784_044_801_000,
        allow_local_destination=True,
    )

    assert authority.read_bytes() == before
    assert first["operation_verdict"] == second["operation_verdict"] == "PASS"
    assert first["institutional_seal_status"] == "PARTIAL_EXTERNAL_WORM_UNVERIFIED"
    assert first["anchor_created"] is True
    assert second["anchor_created"] is False
    assert first["commitment"] == second["commitment"]
    anchor = Path(first["anchor_path"])
    assert anchor.is_file()
    assert anchor.parent == destination.resolve()
    payload = json.loads(anchor.read_text(encoding="utf-8"))
    assert payload["commitment"]["overall_hash"] == first["commitment"]["overall_hash"]
    assert "observed_at_ms" not in payload
    assert json.loads(evidence.read_text(encoding="utf-8")) == second

    bus = DurableEventBus(str(authority), create=False)
    bus.publish(
        Event(
            event_id="event-2",
            event_type="research.test.event",
            timestamp=2,
            source="test",
            domain="research",
            payload={"value": 2},
        ),
        correlation_id="correlation-1",
        causation_id="event-1",
    )
    bus.close()
    verification = verify_event_authority_seal(
        event_path=authority, anchor_path=anchor
    )
    assert verification["verdict"] == "PASS"
    assert verification["authority_extends_anchor"] is True

    replacement = tmp_path / "replacement.db"
    replacement_bus = DurableEventBus(str(replacement))
    replacement_bus.publish(
        Event(
            event_id="replacement",
            event_type="research.test.event",
            timestamp=1,
            source="test",
            domain="research",
            payload={"value": "replacement"},
        )
    )
    replacement_bus.close()
    replacement_result = verify_event_authority_seal(
        event_path=replacement, anchor_path=anchor
    )
    assert replacement_result["verdict"] == "BREACH"
    assert replacement_result["authority_extends_anchor"] is False


def test_external_seal_refuses_to_overwrite_tampered_existing_anchor(tmp_path):
    repo = tmp_path / "repo"
    authority = repo / "data" / "cache" / "domain_events.db"
    authority.parent.mkdir(parents=True)
    _seed_authority(authority)
    destination = tmp_path / "external-seals"
    evidence = repo / "evidence.json"
    result = export_event_authority_seal(
        repo_root=repo,
        event_path=authority,
        destination_root=destination,
        evidence_path=evidence,
        now_ms=1,
        allow_local_destination=True,
    )
    anchor = Path(result["anchor_path"])
    anchor.write_text("tampered", encoding="utf-8")

    with pytest.raises(DomainEventSealError, match="collision or tampering"):
        export_event_authority_seal(
            repo_root=repo,
            event_path=authority,
            destination_root=destination,
            evidence_path=evidence,
            now_ms=2,
            allow_local_destination=True,
        )
    assert anchor.read_text(encoding="utf-8") == "tampered"
    with pytest.raises(DomainEventSealError, match="unreadable"):
        verify_event_authority_seal(event_path=authority, anchor_path=anchor)


def test_external_seal_missing_authority_does_not_create_destination(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    destination = tmp_path / "external-seals"
    with pytest.raises(DomainEventSealError, match="authority is unavailable"):
        export_event_authority_seal(
            repo_root=repo,
            event_path=repo / "data" / "cache" / "missing.db",
            destination_root=destination,
            evidence_path=repo / "evidence.json",
            now_ms=1,
            allow_local_destination=True,
        )
    assert not destination.exists()


def test_external_seal_requires_unc_or_separate_volume_by_default(tmp_path):
    repo = tmp_path / "repo"
    authority = repo / "data" / "cache" / "domain_events.db"
    authority.parent.mkdir(parents=True)
    _seed_authority(authority)

    with pytest.raises(DomainEventSealError, match="separate volume or UNC"):
        export_event_authority_seal(
            repo_root=repo,
            event_path=authority,
            destination_root=tmp_path / "same-volume",
            evidence_path=repo / "evidence.json",
            now_ms=1,
        )
