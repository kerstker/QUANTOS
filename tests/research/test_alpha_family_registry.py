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
