"""Frozen v1 contract for the read-only MFT execution console."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from services.monitoring.mft_read_model import (
    MFT_CONSOLE_SCHEMA,
    MFT_READ_MODEL_SECTIONS,
    MftFreshnessPolicy,
    MftReadModelError,
    MftReadModelStore,
    apply_mft_delta,
)


POLICY = MftFreshnessPolicy(
    policy_id="mft-console-test",
    version="1",
    max_age_ms=1_000,
)


def _sections() -> dict[str, object]:
    return {
        "market_gap": {
            "status": "CONTIGUOUS",
            "latest_closed_bar_ms": 10_000,
            "missing_intervals": [],
        },
        "scheduler": {
            "status": "LEADER",
            "last_success_ms": 10_001,
            "next_due_ms": 13_600,
        },
        "intent_funnel": {
            "received": 3,
            "risk_allowed": 1,
            "blocked": 2,
        },
        "oms": {
            "status": "RECONCILED",
            "orders": [{"client_order_id": "cid-1", "state": "FILLED"}],
        },
        "fills": {
            "count": 1,
            "items": [{"fill_id": "fill-1", "quantity": 0.01}],
        },
        "positions": {
            "status": "RECONCILED",
            "items": [{"instrument": "BTCUSDT", "quantity": 0.01}],
        },
        "risk": {
            "status": "ALLOWED_TESTNET",
            "new_risk_allowed": True,
            "kill_switch_latched": False,
        },
        "reconciliation": {
            "status": "IN_SYNC",
            "requires_manual_review": False,
        },
        "tca": {
            "status": "INSUFFICIENT_EVIDENCE",
            "fill_ratio": 1.0,
            "slippage_bps": None,
        },
        "mandate_expiry": {
            "status": "VALID_TESTNET_ONLY",
            "expires_at_ms": 20_000,
        },
    }


def _build_store(path) -> None:
    with MftReadModelStore(str(path), create=True) as store:
        for offset, (section, payload) in enumerate(_sections().items()):
            assert store.append_section(
                section,
                payload,
                event_at_ms=10_000 + offset,
                event_id=f"event-{offset}",
            ) is True


def test_snapshot_is_complete_real_read_only_and_l2_only(tmp_path):
    path = tmp_path / "mft.db"
    _build_store(path)

    with MftReadModelStore(str(path), read_only=True) as store:
        snapshot = store.snapshot(as_of_ms=10_500, freshness_policy=POLICY)
        with pytest.raises(MftReadModelError, match="read-only") as exc_info:
            store.append_section("risk", {}, event_at_ms=10_500, event_id="blocked")

    assert exc_info.value.code == "READ_ONLY"
    assert snapshot["schema_version"] == MFT_CONSOLE_SCHEMA
    assert snapshot["status"] == "AVAILABLE"
    assert snapshot["snapshot_status"] == "REAL"
    assert snapshot["read_only"] is True
    assert snapshot["freshness"]["status"] == "CURRENT"
    assert snapshot["watermark"]["sequence"] == len(MFT_READ_MODEL_SECTIONS)
    assert set(MFT_READ_MODEL_SECTIONS).issubset(snapshot)
    assert snapshot["safety"] == {
        "read_only": True,
        "readiness": "L2_DRY_RUN_ONLY",
        "testnet_only": True,
        "live_trading_enabled": False,
        "real_broker_enabled": False,
        "allow_order_submission": False,
        "allow_ai_direct_trading": False,
        "require_human_approval": True,
        "submit_capability": False,
        "reset_capability": False,
        "approve_capability": False,
        "microstructure_scope": "TESTNET_MICROSTRUCTURE_NOT_PRODUCTION",
    }


def test_delta_rebuilds_the_next_snapshot_exactly(tmp_path):
    path = tmp_path / "mft.db"
    _build_store(path)
    with MftReadModelStore(str(path), read_only=True) as reader:
        before = reader.snapshot(as_of_ms=10_500, freshness_policy=POLICY)

    with MftReadModelStore(str(path)) as writer:
        writer.append_section(
            "risk",
            {
                "status": "BLOCKED",
                "new_risk_allowed": False,
                "kill_switch_latched": True,
            },
            event_at_ms=10_600,
            event_id="risk-latched",
        )
        writer.append_section(
            "reconciliation",
            {"status": "DIVERGED", "requires_manual_review": True},
            event_at_ms=10_601,
            event_id="reconciliation-diverged",
        )

    with MftReadModelStore(str(path), read_only=True) as reader:
        delta = reader.delta(
            before["cursor"],
            as_of_ms=10_700,
            freshness_policy=POLICY,
        )
        after = reader.snapshot(as_of_ms=10_700, freshness_policy=POLICY)

    assert delta["status"] == "OK"
    assert delta["events"]
    assert apply_mft_delta(before, delta) == after

    tampered = copy.deepcopy(delta)
    tampered["result_metadata"]["safety"]["submit_capability"] = True
    with pytest.raises(MftReadModelError) as exc_info:
        apply_mft_delta(before, tampered)
    assert exc_info.value.code == "DELTA_INVALID"


@pytest.mark.parametrize(
    ("cursor", "reason"),
    [
        ("not-a-cursor", "MALFORMED_CURSOR"),
        ("999:" + "0" * 64, "CURSOR_GAP"),
    ],
)
def test_invalid_cursor_requires_a_full_snapshot(tmp_path, cursor, reason):
    path = tmp_path / "mft.db"
    _build_store(path)
    with MftReadModelStore(str(path), read_only=True) as reader:
        delta = reader.delta(cursor, as_of_ms=10_500, freshness_policy=POLICY)

    assert delta == {
        "schema_version": MFT_CONSOLE_SCHEMA,
        "status": "RESNAPSHOT_REQUIRED",
        "reason": reason,
        "refetch": "/api/v1/console/mft-execution",
        "read_only": True,
    }


def test_cursor_hash_mismatch_requires_a_full_snapshot(tmp_path):
    path = tmp_path / "mft.db"
    _build_store(path)
    with MftReadModelStore(str(path), read_only=True) as reader:
        snapshot = reader.snapshot(as_of_ms=10_500, freshness_policy=POLICY)
        sequence = snapshot["watermark"]["sequence"]
        delta = reader.delta(
            f"{sequence}:" + "0" * 64,
            as_of_ms=10_500,
            freshness_policy=POLICY,
        )

    assert delta["status"] == "RESNAPSHOT_REQUIRED"
    assert delta["reason"] == "CURSOR_MISMATCH"


def test_stale_snapshot_is_visible_but_blocked(tmp_path):
    path = tmp_path / "mft.db"
    _build_store(path)
    with MftReadModelStore(str(path), read_only=True) as reader:
        snapshot = reader.snapshot(as_of_ms=20_000, freshness_policy=POLICY)

    assert snapshot["snapshot_status"] == "UNAVAILABLE"
    assert snapshot["status"] == "BLOCKED"
    assert snapshot["freshness"] == {
        "status": "STALE",
        "age_ms": 9_991,
        "policy_id": POLICY.policy_id,
        "policy_version": POLICY.version,
        "max_age_ms": POLICY.max_age_ms,
    }
    assert snapshot["risk"]["new_risk_allowed"] is True


def test_missing_corrupt_and_unknown_schema_stores_fail_closed_without_mock_data(tmp_path):
    from services.console.api.mft_execution_views import build_mft_execution_snapshot

    missing = tmp_path / "private-token-missing.db"
    corrupt = tmp_path / "private-token-corrupt.db"
    corrupt.write_text("not a sqlite database", encoding="utf-8")
    wrong_schema = tmp_path / "private-token-wrong.db"
    with sqlite3.connect(wrong_schema) as connection:
        connection.execute("CREATE TABLE mft_read_model_meta(schema_version TEXT)")
        connection.execute("INSERT INTO mft_read_model_meta VALUES ('future.v999')")

    for path in (missing, corrupt, wrong_schema):
        snapshot = build_mft_execution_snapshot(path=path, as_of_ms=10_500)
        encoded = json.dumps(snapshot, sort_keys=True)
        assert snapshot["status"] == "BLOCKED"
        assert snapshot["snapshot_status"] == "UNAVAILABLE"
        assert snapshot["data_provenance"] == "UNAVAILABLE"
        assert snapshot["freshness"]["status"] == "UNAVAILABLE"
        assert all(snapshot[name] == {"status": "UNAVAILABLE"} for name in MFT_READ_MODEL_SECTIONS)
        assert str(path) not in encoded
        assert "private-token" not in encoded
        assert "SYNTHETIC" not in encoded and "MOCK" not in encoded


def test_event_identity_is_idempotent_and_payloads_cannot_leak_sensitive_paths(tmp_path):
    path = tmp_path / "mft.db"
    with MftReadModelStore(str(path), create=True) as store:
        assert store.append_section(
            "risk", {"status": "BLOCKED"}, event_at_ms=1, event_id="same"
        ) is True
        assert store.append_section(
            "risk", {"status": "BLOCKED"}, event_at_ms=1, event_id="same"
        ) is False
        with pytest.raises(MftReadModelError, match="reuse"):
            store.append_section(
                "risk", {"status": "ALLOWED"}, event_at_ms=1, event_id="same"
            )
        with pytest.raises(MftReadModelError, match="unknown section"):
            store.append_section("credentials", {}, event_at_ms=2, event_id="unknown")
        with pytest.raises(MftReadModelError, match="sensitive"):
            store.append_section(
                "risk",
                {"api_key": "do-not-store"},
                event_at_ms=2,
                event_id="sensitive",
            )
        with pytest.raises(MftReadModelError, match="paths"):
            store.append_section(
                "risk",
                {"diagnostic": r"C:\private\mft.db"},
                event_at_ms=2,
                event_id="local-path",
            )
        with pytest.raises(ValueError, match="safe identifier"):
            store.append_section(
                "risk",
                {"status": "BLOCKED"},
                event_at_ms=2,
                event_id=r"C:\private\event",
            )


def test_append_only_guards_and_hash_chain_corruption_fail_closed(tmp_path):
    path = tmp_path / "mft.db"
    _build_store(path)
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE mft_control_events SET section='risk' WHERE sequence=1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM mft_control_events WHERE sequence=1")

    unguarded = tmp_path / "unguarded.db"
    _build_store(unguarded)
    with sqlite3.connect(unguarded) as connection:
        connection.execute("DROP TRIGGER mft_control_events_no_delete")
    with pytest.raises(MftReadModelError) as exc_info:
        MftReadModelStore(str(unguarded), read_only=True)
    assert exc_info.value.code == "STORE_SCHEMA_INCOMPLETE"

    corrupt = tmp_path / "corrupt-chain.db"
    _build_store(corrupt)
    with sqlite3.connect(corrupt) as connection:
        connection.execute("DROP TRIGGER mft_control_events_no_update")
        connection.execute(
            "UPDATE mft_control_events SET payload_hash=? WHERE sequence=1",
            ("0" * 64,),
        )
        connection.execute(
            "CREATE TRIGGER mft_control_events_no_update "
            "BEFORE UPDATE ON mft_control_events "
            "BEGIN SELECT RAISE(ABORT, 'mft_control_events is append-only'); END"
        )
    with MftReadModelStore(str(corrupt), read_only=True) as reader:
        with pytest.raises(MftReadModelError) as exc_info:
            reader.snapshot(as_of_ms=10_500, freshness_policy=POLICY)
    assert exc_info.value.code == "STORE_INTEGRITY"


def test_http_contract_is_get_only_and_never_exposes_mutation_capabilities(tmp_path, monkeypatch):
    from services.console.main import app

    path = tmp_path / "mft.db"
    _build_store(path)
    monkeypatch.setenv("QUANTOS_MFT_READ_MODEL_DB", str(path))
    client = TestClient(app)

    snapshot_response = client.get("/api/v1/console/mft-execution")
    snapshot = snapshot_response.json()
    delta_response = client.get(
        "/api/v1/console/mft-execution/delta",
        params={"cursor": snapshot["cursor"]},
    )

    assert snapshot_response.status_code == 200
    assert delta_response.status_code == 200
    assert snapshot["schema_version"] == MFT_CONSOLE_SCHEMA
    assert snapshot["read_only"] is True
    assert delta_response.json()["read_only"] is True
    assert snapshot["safety"]["submit_capability"] is False
    assert snapshot["safety"]["reset_capability"] is False
    assert snapshot["safety"]["approve_capability"] is False

    mft_routes = [
        route
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/console/mft-execution")
    ]
    assert {method for route in mft_routes for method in route.methods} == {"GET"}
