from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3

import pytest

from services.research.alpha_family_registry import AlphaFamilyRegistry, AlphaFamilyRegistryError


def _candidate():
    return {
        "candidate_id": "family_test",
        "family_id": "test",
        "expression": "(cs_rank (field close))",
        "required_fields": ["close"],
        "status": "SUPPORTED",
    }


def test_registry_is_append_only_and_idempotent(tmp_path):
    path = tmp_path / "families.db"
    with AlphaFamilyRegistry(str(path), create=True) as reg:
        assert reg.register_candidate(_candidate(), recorded_at=1)
        assert not reg.register_candidate(_candidate(), recorded_at=1)
        assert reg.record_evaluation("family_test", {"verdict": "FAIL"},
                                    dataset_version="d1", recorded_at=2)
        assert reg.record_paper_run("family_test", run_id="dry-1", status="PAPER_ONLY_PENDING_FORWARD",
                                    metrics={"n_periods": 3}, dataset_version="d1", recorded_at=3)
        assert reg.event_count() == 3
        assert reg.verify_chain()
        row = reg.candidates()[0]
        assert row["evaluations"][0]["evaluation"]["verdict"] == "FAIL"
        assert row["paper_runs"][0]["run_id"] == "dry-1"
        definitions = reg.candidate_definitions()
        assert len(definitions) == 1
        assert definitions[0]["candidate_id"] == "family_test"
        assert definitions[0]["event_seq"] == 1
        assert definitions[0]["recorded_at"] == 1
        assert len(definitions[0]["record_hash"]) == 64
        assert "evaluations" not in definitions[0]
        assert "paper_runs" not in definitions[0]
        authority, frozen_definitions, paper_runs = reg.discovery_snapshot()
        assert len(authority) == 64
        assert frozen_definitions == definitions
        assert paper_runs["family_test"][0]["run_id"] == "dry-1"


def test_conflicting_candidate_content_is_rejected(tmp_path):
    with AlphaFamilyRegistry(str(tmp_path / "families.db"), create=True) as reg:
        reg.register_candidate(_candidate(), recorded_at=1)
        changed = dict(_candidate(), status="DATA_UNAVAILABLE")
        with pytest.raises(AlphaFamilyRegistryError):
            reg.register_candidate(changed, recorded_at=1)


def test_presentation_metadata_can_be_corrected_without_rewriting_identity(tmp_path):
    with AlphaFamilyRegistry(str(tmp_path / "families.db"), create=True) as reg:
        reg.register_candidate(_candidate(), recorded_at=1)
        corrected = dict(_candidate(), name_zh="测试候选", notes="中文说明", config_hash="new-display-hash")
        assert not reg.register_candidate(corrected, recorded_at=2)
        assert reg.event_count() == 1
        assert reg.verify_chain()


def test_missing_schema_fails_closed(tmp_path):
    with pytest.raises(AlphaFamilyRegistryError):
        AlphaFamilyRegistry(str(tmp_path / "missing.db"), create=False)


def test_read_only_mode_rejects_creation_memory_and_mutation(tmp_path):
    missing = tmp_path / "missing-read-only.db"
    with pytest.raises(AlphaFamilyRegistryError, match="does not exist"):
        AlphaFamilyRegistry(str(missing), read_only=True)
    assert not missing.exists()
    with pytest.raises(AlphaFamilyRegistryError, match="mutually exclusive"):
        AlphaFamilyRegistry(str(missing), create=True, read_only=True)
    with pytest.raises(AlphaFamilyRegistryError, match="file authority"):
        AlphaFamilyRegistry(":memory:", read_only=True)

    path = tmp_path / "read-only.db"
    with AlphaFamilyRegistry(str(path), create=True) as writer:
        writer.register_candidate(_candidate(), recorded_at=1)
    with AlphaFamilyRegistry(str(path), read_only=True) as reader:
        assert reader.verify_chain()
        with pytest.raises(AlphaFamilyRegistryError, match="append conflict"):
            reader.record_evaluation(
                "family_test", {"verdict": "PASS"},
                dataset_version="d1", recorded_at=2,
            )


def test_read_only_open_does_not_recreate_authority_after_toctou_delete(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "toctou.db"
    with AlphaFamilyRegistry(str(path), create=True):
        pass
    original = sqlite3.connect

    def delete_before_connect(target, *args, **kwargs):
        path.unlink()
        return original(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", delete_before_connect)
    with pytest.raises(AlphaFamilyRegistryError, match="unavailable"):
        AlphaFamilyRegistry(str(path), read_only=True)
    assert not path.exists()


def test_failed_read_only_schema_validation_closes_connection(tmp_path, monkeypatch):
    path = tmp_path / "invalid-read-only.db"
    path.write_bytes(b"not sqlite")
    before = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_size)
    observed = []
    original = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        observed.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    with pytest.raises(AlphaFamilyRegistryError):
        AlphaFamilyRegistry(str(path), read_only=True)
    assert observed
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        observed[0].execute("SELECT 1")
    assert (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_size) == before


def test_concurrent_writers_serialize_chain_head_and_insert(tmp_path):
    path = tmp_path / "families.db"
    with AlphaFamilyRegistry(str(path), create=True):
        pass
    writers = 8
    barrier = Barrier(writers)

    def append(index: int) -> bool:
        candidate = dict(_candidate())
        candidate["candidate_id"] = f"family_test_{index}"
        barrier.wait()
        with AlphaFamilyRegistry(str(path), create=False) as registry:
            return registry.register_candidate(candidate, recorded_at=index + 1)

    with ThreadPoolExecutor(max_workers=writers) as pool:
        assert all(pool.map(append, range(writers)))

    with AlphaFamilyRegistry(str(path), create=False) as registry:
        assert registry.event_count() == writers
        assert registry.verify_chain()


def test_discovery_snapshot_freezes_authority_definitions_and_feedback(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "families.db"
    reader = AlphaFamilyRegistry(str(path), create=True)
    reader.register_candidate(_candidate(), recorded_at=1)
    writer = AlphaFamilyRegistry(str(path), create=False)
    original_authority_hash = reader.authority_hash

    def interleave_append():
        authority = original_authority_hash()
        second = dict(_candidate(), candidate_id="family_test_2")
        assert writer.register_candidate(second, recorded_at=2)
        return authority

    monkeypatch.setattr(reader, "authority_hash", interleave_append)
    try:
        authority, definitions, paper_runs = reader.discovery_snapshot()
        assert [row["candidate_id"] for row in definitions] == ["family_test"]
        assert paper_runs == {"family_test": []}
        assert authority != writer.authority_hash()
    finally:
        writer.close()
        reader.close()


def test_discovery_snapshot_rolls_back_on_unexpected_decode_failure(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "families.db"
    with AlphaFamilyRegistry(str(path), create=True) as registry:
        registry.register_candidate(_candidate(), recorded_at=1)

        def fail_decode():
            raise ValueError("malformed candidate payload")

        monkeypatch.setattr(registry, "candidate_definitions", fail_decode)
        with pytest.raises(ValueError, match="malformed candidate payload"):
            registry.discovery_snapshot()

        assert not registry._conn.in_transaction
