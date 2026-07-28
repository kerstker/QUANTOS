import json
from pathlib import Path
import sqlite3

import pytest

from services.governance.artifact_admission_store import (
    POLICY_REGISTRY_KIND,
    RECEIPT_AUTHORITY_REGISTRY_KIND,
    ArtifactAdmissionStore,
    ArtifactAdmissionStoreError,
)
from tools.provision_artifact_admission_authority import (
    provision_artifact_admission_authority,
)


def _registries(tmp_path: Path) -> tuple[Path, Path]:
    policy = tmp_path / "policy-registry.json"
    receipt = tmp_path / "receipt-registry.json"
    policy.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": 0,
                "policies": {},
            }
        ),
        encoding="utf-8",
    )
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "readiness": "L2_DRY_RUN_ONLY",
                "generation": 0,
                "keys": {},
            }
        ),
        encoding="utf-8",
    )
    return policy, receipt


def test_provisioning_retains_exact_empty_registry_heads_and_survives_restart(
    tmp_path: Path,
):
    policy, receipt = _registries(tmp_path)
    store_path = tmp_path / "artifact-admission.db"
    first = provision_artifact_admission_authority(
        policy_registry_path=policy,
        receipt_authority_registry_path=receipt,
        store_path=store_path,
        observed_at_ms=1_000,
    )
    assert first["status"] == "PASS"
    assert first["inserted"] == {
        POLICY_REGISTRY_KIND: True,
        RECEIPT_AUTHORITY_REGISTRY_KIND: True,
    }
    assert first["decision_count"] == 0
    assert first["signing_capability_present"] is False
    assert first["live_trading_authority_granted"] is False

    checked = provision_artifact_admission_authority(
        policy_registry_path=policy,
        receipt_authority_registry_path=receipt,
        store_path=store_path,
        observed_at_ms=1_001,
        check_only=True,
    )
    assert checked["status"] == "PASS"
    assert checked["mode"] == "CHECK_ONLY"
    assert checked["inserted"] == {}


def test_provisioning_is_idempotent_and_rejects_same_generation_conflict(
    tmp_path: Path,
):
    policy, receipt = _registries(tmp_path)
    store_path = tmp_path / "artifact-admission.db"
    provision_artifact_admission_authority(
        policy_registry_path=policy,
        receipt_authority_registry_path=receipt,
        store_path=store_path,
        observed_at_ms=1_000,
    )
    replay = provision_artifact_admission_authority(
        policy_registry_path=policy,
        receipt_authority_registry_path=receipt,
        store_path=store_path,
        observed_at_ms=1_001,
    )
    assert replay["inserted"] == {
        POLICY_REGISTRY_KIND: False,
        RECEIPT_AUTHORITY_REGISTRY_KIND: False,
    }

    changed = json.loads(policy.read_text(encoding="utf-8"))
    changed["policies"]["untrusted"] = {
        "status": "ACTIVE",
        "policy_sha256": "a" * 64,
    }
    policy.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ArtifactAdmissionStoreError):
        provision_artifact_admission_authority(
            policy_registry_path=policy,
            receipt_authority_registry_path=receipt,
            store_path=store_path,
            observed_at_ms=1_002,
        )

    store = ArtifactAdmissionStore(str(store_path), create=False)
    try:
        assert store.registry_head(POLICY_REGISTRY_KIND).generation == 0
        assert store.decisions() == ()
    finally:
        store.close()


def test_check_fails_closed_on_missing_or_tampered_authority(tmp_path: Path):
    policy, receipt = _registries(tmp_path)
    store_path = tmp_path / "artifact-admission.db"
    with pytest.raises(ArtifactAdmissionStoreError):
        provision_artifact_admission_authority(
            policy_registry_path=policy,
            receipt_authority_registry_path=receipt,
            store_path=store_path,
            observed_at_ms=1_000,
            check_only=True,
        )

    provision_artifact_admission_authority(
        policy_registry_path=policy,
        receipt_authority_registry_path=receipt,
        store_path=store_path,
        observed_at_ms=1_000,
    )
    connection = sqlite3.connect(store_path)
    connection.execute("DROP TRIGGER artifact_admission_events_no_update")
    connection.execute(
        "UPDATE artifact_admission_authority_events "
        "SET event_hash=? WHERE event_seq=1",
        ("f" * 64,),
    )
    connection.commit()
    connection.close()
    with pytest.raises(ArtifactAdmissionStoreError):
        provision_artifact_admission_authority(
            policy_registry_path=policy,
            receipt_authority_registry_path=receipt,
            store_path=store_path,
            observed_at_ms=1_001,
            check_only=True,
        )
