import json
from pathlib import Path

import pytest

from services.governance.top_tier_assessment import RegistryError, assess_repository, load_requirements


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config" / "top_tier_requirements.json"


def _write_registry(tmp_path: Path, requirements: list[dict]) -> Path:
    path = tmp_path / "requirements.json"
    path.write_text(json.dumps({"schema_version": 1, "requirements": requirements}), encoding="utf-8")
    return path


def _requirement(requirement_id: str, **overrides) -> dict:
    item = {
        "id": requirement_id,
        "domain": "platform_engineering",
        "tier": "A",
        "target_maturity": "M3",
        "classification": "L2_SAFE_NOW",
        "description": "test requirement",
        "required_evidence": ["implementation", "verification"],
        "red_line": False,
        "checker": "manual_evidence",
        "next_action": "provide evidence",
    }
    item.update(overrides)
    return item


def test_registry_rejects_duplicate_ids(tmp_path):
    path = _write_registry(tmp_path, [_requirement("DUP"), _requirement("DUP")])
    with pytest.raises(RegistryError, match="duplicate requirement id"):
        load_requirements(path)


def test_red_line_failure_forces_not_top_tier(tmp_path):
    path = _write_registry(tmp_path, [_requirement(
        "PLATFORM.MISSING", checker="paths_exist",
        checker_args={"paths": ["missing.file"]}, red_line=True)])
    report = assess_repository(tmp_path, path)
    assert report["verdict"] == "NOT_TOP_TIER"
    assert report["summary"]["red_lines_failed"] == 1
    assert report["requirements"][0]["status"] == "FAILED"


def test_committed_registry_covers_all_eleven_domains():
    requirements = load_requirements(REGISTRY)
    assert {item["domain"] for item in requirements} == {
        "data_moat", "alpha_research", "validation_model_risk",
        "portfolio_construction", "risk_management", "execution_microstructure",
        "ai_agents", "governance_security", "platform_engineering",
        "reliability_operations", "frontend_console",
    }
    assert len(requirements) >= 22


@pytest.mark.parametrize(("field", "value"), [
    ("domain", "unknown"), ("tier", "C"), ("target_maturity", "M9"),
    ("classification", "MAGIC"),
    ("required_evidence", ["implementation", "hope"]),
    ("checker", "unknown_checker"),
])
def test_registry_rejects_invalid_enums(tmp_path, field, value):
    path = _write_registry(tmp_path, [_requirement("BAD", **{field: value})])
    with pytest.raises(RegistryError, match=field):
        load_requirements(path)


def test_manual_and_external_requirements_never_auto_pass(tmp_path):
    path = _write_registry(tmp_path, [
        _requirement("GOV.IDENTITY.STRONG_AUTH", checker="manual_evidence", red_line=True),
        _requirement("DATA.VENDOR.TICK", checker="paths_exist", checker_args={"paths": []},
                     classification="NEEDS_EXTERNAL"),
    ])
    report = assess_repository(tmp_path, path)
    statuses = {item["id"]: item["status"] for item in report["requirements"]}
    assert statuses == {
        "GOV.IDENTITY.STRONG_AUTH": "MANUAL_EVIDENCE_REQUIRED",
        "DATA.VENDOR.TICK": "MANUAL_EVIDENCE_REQUIRED",
    }
    assert report["summary"]["passed"] == 0


def test_all_tier_a_targets_can_reach_top_tier_research(tmp_path):
    (tmp_path / "proof.txt").write_text("real evidence", encoding="utf-8")
    path = _write_registry(tmp_path, [_requirement(
        "PLATFORM.PROOF", checker="paths_exist",
        checker_args={"paths": ["proof.txt"], "pass_maturity": "M3"}, red_line=True)])
    report = assess_repository(tmp_path, path)
    assert report["verdict"] == "TOP_TIER_RESEARCH"
    assert report["requirements"][0]["status"] == "PASS"
    assert report["domains"][0]["current_maturity"] == "M3"


def test_assessment_is_deterministic(tmp_path):
    (tmp_path / "proof.txt").write_text("proof", encoding="utf-8")
    path = _write_registry(tmp_path, [_requirement(
        "PLATFORM.PROOF", checker="paths_exist", checker_args={"paths": ["proof.txt"]})])
    assert assess_repository(tmp_path, path) == assess_repository(tmp_path, path)
