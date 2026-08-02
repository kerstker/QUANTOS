"""Licensed vendor delivery decisions form a durable append-only authority."""
from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

from services.data.vendor_delivery_history import (
    VENDOR_DELIVERY_HISTORY_KIND,
    VENDOR_DELIVERY_HISTORY_SCHEMA_VERSION,
    VendorDeliveryHistoryClockRollback,
    VendorDeliveryHistoryConflict,
    VendorDeliveryHistoryError,
    VendorDeliveryHistoryStore,
    VendorDeliveryRecord,
    vendor_delivery_event_id,
)


def _record(
    delivery_id: str = "delivery-1",
    *,
    recorded_at_ms: int = 1_000,
    verdict: str = "ARCHIVE_PENDING",
) -> VendorDeliveryRecord:
    admitted = verdict == "ADMITTED"
    pending = verdict == "ARCHIVE_PENDING"
    return VendorDeliveryRecord(
        event_id=vendor_delivery_event_id(delivery_id, verdict),
        delivery_id=delivery_id,
        recorded_at_ms=recorded_at_ms,
        vendor_id="vendor-1",
        dataset_id="orderbook-l2",
        instrument_id="BTC-USDT",
        grant_id="grant-1",
        contract_id="contract-1",
        usage="RESEARCH",
        content_sha256="a" * 64,
        grant_sha256="b" * 64,
        contract_sha256="c" * 64,
        policy_sha256="d" * 64,
        verdict=verdict,
        reasons=() if admitted or pending else ("DELIVERY_LAG_SLA_BREACH",),
        coverage_ratio="1",
        missing_ratio="0",
        duplicate_ratio="0",
        delivery_lag_ms=10,
        history_span_ms=100,
        archive_artifact_sha256="a" * 64 if admitted else "",
    )


def test_persists_reopens_and_uses_full_durability(tmp_path) -> None:
    path = str(tmp_path / "vendor-history.db")
    store = VendorDeliveryHistoryStore(path, create=True)
    record = _record()
    retained, inserted = store.append(record, expected_head=None)

    assert (retained, inserted) == (record, True)
    assert store.records() == (record,)
    assert store.head().event_seq == 1
    assert store.chain_valid() is True
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    store.close()

    reopened = VendorDeliveryHistoryStore(path, create=False)
    assert reopened.records() == (record,)
    assert reopened.chain_valid() is True
    reopened.close()


def test_exact_replay_is_idempotent_and_changed_license_conflicts(tmp_path) -> None:
    store = VendorDeliveryHistoryStore(
        str(tmp_path / "vendor-history.db"),
        create=True,
    )
    record = _record()
    store.append(record, expected_head=None)
    replay, inserted = store.append(record, expected_head=None)
    assert (replay, inserted) == (record, False)

    with pytest.raises(VendorDeliveryHistoryConflict):
        store.append(
            replace(record, grant_sha256="e" * 64),
            expected_head=store.head(),
        )
    assert store.records() == (record,)
    store.close()


def test_global_head_cas_and_clock_floor_hold_across_connections(tmp_path) -> None:
    path = str(tmp_path / "vendor-history.db")
    first = VendorDeliveryHistoryStore(path, create=True)
    second = VendorDeliveryHistoryStore(path, create=False)
    stale_head = second.head()
    first.append(_record(), expected_head=None)

    with pytest.raises(
        VendorDeliveryHistoryError,
        match="VENDOR_DELIVERY_HISTORY_HEAD_CHANGED",
    ):
        second.append(
            _record("delivery-2", recorded_at_ms=1_001),
            expected_head=stale_head,
        )
    with pytest.raises(VendorDeliveryHistoryClockRollback):
        second.append(
            _record("delivery-2", recorded_at_ms=999),
            expected_head=second.head(),
        )
    first.close()
    second.close()


def test_denied_sla_delivery_is_retained_without_archive_claim(tmp_path) -> None:
    store = VendorDeliveryHistoryStore(
        str(tmp_path / "vendor-history.db"),
        create=True,
    )
    denied = _record(verdict="DENIED")
    store.append(denied, expected_head=None)

    assert store.records()[0].reasons == ("DELIVERY_LAG_SLA_BREACH",)
    assert store.records()[0].archive_artifact_sha256 == ""
    store.close()


def test_admitted_terminal_requires_matching_pending_event(tmp_path) -> None:
    store = VendorDeliveryHistoryStore(
        str(tmp_path / "vendor-history.db"),
        create=True,
    )
    with pytest.raises(VendorDeliveryHistoryError, match="lifecycle"):
        store.append(_record(verdict="ADMITTED"), expected_head=None)
    assert store.records() == ()

    pending = _record()
    admitted = _record(verdict="ADMITTED")
    store.append(pending, expected_head=None)
    store.append(admitted, expected_head=store.head())
    assert store.open_deliveries() == ()
    assert [record.verdict for record in store.records()] == [
        "ARCHIVE_PENDING",
        "ADMITTED",
    ]
    store.close()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE vendor_delivery_history_events SET payload_sha256='f' || substr(payload_sha256,2)",
        "DELETE FROM vendor_delivery_history_events",
    ],
)
def test_tamper_or_truncation_is_detected_on_reopen(tmp_path, statement) -> None:
    path = str(tmp_path / "vendor-history.db")
    store = VendorDeliveryHistoryStore(path, create=True)
    store.append(_record(), expected_head=None)
    store.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER vendor_delivery_history_events_no_update")
    connection.execute("DROP TRIGGER vendor_delivery_history_events_no_delete")
    connection.execute(statement)
    connection.commit()
    connection.close()

    with pytest.raises(VendorDeliveryHistoryError):
        VendorDeliveryHistoryStore(path, create=False)


def test_schema_injection_and_trigger_removal_fail_closed(tmp_path) -> None:
    path = str(tmp_path / "vendor-history.db")
    store = VendorDeliveryHistoryStore(path, create=True)
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER vendor_delivery_history_events_no_update")
    connection.execute("CREATE TABLE injected(value TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(VendorDeliveryHistoryError, match="schema|triggers"):
        VendorDeliveryHistoryStore(path, create=False)


def test_same_name_noop_trigger_is_rejected(tmp_path) -> None:
    path = str(tmp_path / "vendor-history.db")
    store = VendorDeliveryHistoryStore(path, create=True)
    store.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER vendor_delivery_history_events_no_update")
    connection.execute(
        "CREATE TRIGGER vendor_delivery_history_events_no_update "
        "BEFORE UPDATE ON vendor_delivery_history_events BEGIN SELECT 1; END"
    )
    connection.commit()
    connection.close()

    with pytest.raises(VendorDeliveryHistoryError, match="trigger contract"):
        VendorDeliveryHistoryStore(path, create=False)


def test_missing_or_unrelated_store_is_not_initialized_by_reader(tmp_path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(VendorDeliveryHistoryError):
        VendorDeliveryHistoryStore(str(missing), create=False)

    unrelated = tmp_path / "unrelated.db"
    connection = sqlite3.connect(unrelated)
    connection.execute("CREATE TABLE marker(value TEXT)")
    connection.commit()
    connection.close()
    before = unrelated.read_bytes()
    with pytest.raises(VendorDeliveryHistoryError):
        VendorDeliveryHistoryStore(str(unrelated), create=False)
    assert unrelated.read_bytes() == before


def test_store_identity_is_pinned() -> None:
    assert VENDOR_DELIVERY_HISTORY_KIND == (
        "QUANTOS_VENDOR_DELIVERY_HISTORY_AUTHORITY"
    )
    assert VENDOR_DELIVERY_HISTORY_SCHEMA_VERSION == 1
