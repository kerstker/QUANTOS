"""F1 — Factor Garden registry tests: garden boundary + store round-trip of the
widened contract (this is what catches store serialization drift)."""
import pytest

from quantos.contracts.feature import (
    FactorFamily,
    FeatureDefinition,
    FeatureStatus,
)
from services.factor_garden import (
    FactorDefinitionError,
    FactorGardenRegistry,
    build_factor_definition,
    factor_content_hash,
)

CROWD_EXPR = ("cs_rank", ("neg", ("field", "global_ls")))
CARRY_EXPR = ("cs_rank", ("field", "funding_rate"))


def _crowd(**over):
    kw = dict(
        name="crowd_contra",
        family=FactorFamily.POSITIONING_CROWDING,
        expression=CROWD_EXPR,
        economic_hypothesis="反着做拥挤的散户多空仓位",
        required_datasets=("oi_metrics",),
        frequency="1d",
        universe="crypto_oi_majors",
        minimum_lag=1,
        normalization="cs_rank",
        expected_turnover=0.15,
        known_failure_regimes=("2021-05_early_oi_sparse",),
        creator_actor="researcher:test",
        creator_model="gpt-test",
        prompt_hash="ph_1",
        created_at_ms=1_000,
    )
    kw.update(over)
    return build_factor_definition(**kw)


def _carry(**over):
    kw = dict(
        name="carry_composite",
        family=FactorFamily.FUNDING_CARRY,
        expression=CARRY_EXPR,
        economic_hypothesis="资金费现金流 carry",
        required_datasets=("funding_rates",),
        frequency="1d",
        universe="crypto_funding",
        creator_actor="researcher:test",
        created_at_ms=2_000,
    )
    kw.update(over)
    return build_factor_definition(**kw)


def test_register_get_roundtrip_preserves_all_widened_fields(tmp_path):
    with FactorGardenRegistry(str(tmp_path / "garden.db"), create=True) as reg:
        reg.register(_crowd(), factor_version="1.0.0", recorded_at_ms=10,
                     leakage_passed=True, source_dataset_id="oi_metrics",
                     source_version="2026-07-27")
        item = reg.get("crowd_contra", "1.0.0")
    assert item.definition == _crowd()          # every widened field survived sqlite
    assert item.version == "1.0.0" and item.owner == "researcher:test"
    assert item.status is FeatureStatus.DRAFT


def test_versions_accumulate(tmp_path):
    with FactorGardenRegistry(str(tmp_path / "garden.db"), create=True) as reg:
        reg.register(_crowd(), factor_version="1.0.0", recorded_at_ms=10)
        reg.register(_crowd(expression=("neg", ("cs_rank", ("field", "global_ls")))),
                     factor_version="2.0.0", recorded_at_ms=20)
        assert reg.versions("crowd_contra") == ["1.0.0", "2.0.0"]


def test_by_family_filters(tmp_path):
    with FactorGardenRegistry(str(tmp_path / "garden.db"), create=True) as reg:
        reg.register(_crowd(), recorded_at_ms=10)
        reg.register(_carry(), recorded_at_ms=20)
        crowding = reg.by_family(FactorFamily.POSITIONING_CROWDING)
        assert [d.name for d in crowding] == ["crowd_contra"]
        assert [d.name for d in reg.by_family("FUNDING_CARRY")] == ["carry_composite"]
        with pytest.raises(FactorDefinitionError):
            reg.by_family("NOT_A_FAMILY")


def test_duplicate_id_version_rejected(tmp_path):
    with FactorGardenRegistry(str(tmp_path / "garden.db"), create=True) as reg:
        reg.register(_crowd(), recorded_at_ms=10)
        with pytest.raises(ValueError):
            reg.register(_crowd(), recorded_at_ms=20)


def test_set_status_flows_to_history_chain(tmp_path):
    with FactorGardenRegistry(str(tmp_path / "garden.db"), create=True) as reg:
        reg.register(_crowd(), recorded_at_ms=10)
        reg.set_status("crowd_contra", "1.0.0", FeatureStatus.VALIDATED,
                       recorded_at_ms=30)
        audit = reg.audit("crowd_contra", "1.0.0")
    assert [e["operation"] for e in audit["history"]] == ["REGISTER", "STATUS"]
    assert audit["history"][1]["status"] == "VALIDATED"
    assert audit["history"][1]["recorded_at_ms"] == 30
    assert audit["leakage_passed"] is False


def test_boundary_rejects_hand_built_definition_missing_hypothesis(tmp_path):
    bare = FeatureDefinition(feature_id="raw", name="raw", formula="x")
    with FactorGardenRegistry(str(tmp_path / "garden.db"), create=True) as reg:
        with pytest.raises(FactorDefinitionError):
            reg.register(bare, recorded_at_ms=10)


def test_persistence_across_reopen(tmp_path):
    path = str(tmp_path / "garden.db")
    with FactorGardenRegistry(path, create=True) as reg:
        reg.register(_crowd(), factor_version="1.0.0", recorded_at_ms=10,
                     leakage_passed=True)
    with FactorGardenRegistry(path) as reg:                     # reopen, create=False
        assert reg.get("crowd_contra", "1.0.0").definition == _crowd()
        assert [d.name for d in reg.by_family(FactorFamily.POSITIONING_CROWDING)] == \
            ["crowd_contra"]
        assert reg.audit("crowd_contra", "1.0.0")["leakage_passed"] is True


def test_content_hash_recorded_in_audit_lineage(tmp_path):
    with FactorGardenRegistry(str(tmp_path / "garden.db"), create=True) as reg:
        reg.register(_crowd(), factor_version="1.0.0", recorded_at_ms=10)
        audit = reg.audit("crowd_contra", "1.0.0")
    lineage = audit["history"][0]["metadata"]
    assert lineage["factor_content_hash"] == \
        factor_content_hash(_crowd(), factor_version="1.0.0")
