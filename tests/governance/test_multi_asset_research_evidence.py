import json
from pathlib import Path

import pytest

from quantos.contracts.hashing import stable_hash
from quantos.contracts.multi_asset import RETAINED_FX_FACTOR_SPECS
from scripts.generate_multi_asset_research_evidence import (
    build_multi_asset_research_evidence,
    main,
)
from services.governance.multi_asset_research_evidence import (
    MULTI_ASSET_IMPLEMENTATION_PATHS,
    MULTI_ASSET_VERIFICATION_PATHS,
    MultiAssetResearchEvidenceError,
    verify_multi_asset_research_evidence,
)
from services.governance.top_tier_assessment import assess_repository, load_requirements


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence/multi_asset_research_baseline.json"
REGISTRY = ROOT / "config/top_tier_requirements.json"


def test_overlapping_volatility_reads_each_received_ratio_at_most_once(monkeypatch):
    import random
    import statistics
    from services.governance import multi_asset_research_evidence as verifier

    randomizer = random.Random(20261006)
    prices = tuple(tuple(None if (row + column) % 17 == 0 else
        100.0 * (1.0 + randomizer.uniform(-0.01, 0.01)) for column in range(3))
        for row in range(180))
    panel = verifier._IndependentPanel(('one', 'two', 'three'), tuple(range(180)), prices, 540)
    expected = [[None for _ in range(3)] for _ in range(180)]
    for column in range(3):
        for row in range(180):
            if prices[row][column] is None:
                continue
            received = [values[column] for values in prices[:row + 1] if values[column] is not None]
            if len(received) >= 61:
                changes = [current / previous - 1.0
                    for previous, current in zip(received[-61:], received[-60:])]
                expected[row][column] = -statistics.pstdev(changes)
    reads = []
    original_ratio = statistics._exact_ratio

    def recorded(value):
        reads.append(value)
        return original_ratio(value)

    monkeypatch.setattr(statistics, '_exact_ratio', recorded)
    observed = verifier._signal(panel, 'negative_volatility', 60)
    assert [[None if value is None else value.hex() for value in row] for row in observed] == [
        [None if value is None else value.hex() for value in row] for row in expected]
    received_returns = sum(max(0, sum(row[column] is not None for row in prices) - 1)
        for column in range(3))
    assert len(reads) <= received_returns


def _resign(document: dict) -> dict:
    document.pop("evidence_sha256", None)
    document["evidence_sha256"] = stable_hash(document)
    return document


@pytest.fixture(scope="module")
def verified_evidence() -> dict:
    return verify_multi_asset_research_evidence(EVIDENCE, repo_root=ROOT)


def test_committed_multi_asset_evidence_replays_real_non_crypto_pit_data(verified_evidence):
    document = verified_evidence

    assert document["schema_version"] == 1
    assert document["evidence_id"] == "quantos-multi-asset-research-fx-v1"
    assert document["scope"]["retained_non_crypto_asset_class"] == "FX"
    assert document["dataset"]["row_count"] == 126_113
    assert document["dataset"]["instrument_count"] == 18
    assert document["replay"]["network_required"] is False
    assert document["replay"]["source_database_required"] is False
    assert document["safety"]["readiness"] == "L2_DRY_RUN_ONLY"
    assert document["safety"]["can_promote_or_trade"] is False

    expected_ids = [spec.factor_id for spec in RETAINED_FX_FACTOR_SPECS]
    assert [row["name"] for row in document["factor_results"]] == expected_ids
    for row in document["factor_results"]:
        assert row["holding_periods"] == [1, 5, 20]
        assert row["n_instruments"] == 18
        assert row["by_period"]["1"]["n_periods"] > 5_000
        assert row["by_period"]["5"]["n_periods"] > 1_000
        assert row["by_period"]["20"]["n_periods"] > 250


def test_generator_is_deterministic_and_matches_committed_artifact():
    assert build_multi_asset_research_evidence(ROOT) == json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert main(["--repo-root", str(ROOT), "--check"]) == 0


def test_verifier_rejects_semantically_tampered_result_even_with_new_self_digest(tmp_path):
    document = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    document["factor_results"][0]["by_period"]["5"]["mean_ic"] = 0.9999
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(_resign(document), sort_keys=True), encoding="utf-8")

    with pytest.raises(MultiAssetResearchEvidenceError, match="replay result"):
        verify_multi_asset_research_evidence(path, repo_root=ROOT)


def test_verifier_rejects_stale_implementation_hash_even_with_new_self_digest(tmp_path):
    document = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    first = sorted(document["implementation_sha256"])[0]
    document["implementation_sha256"][first] = "0" * 64
    path = tmp_path / "stale-code.json"
    path.write_text(json.dumps(_resign(document), sort_keys=True), encoding="utf-8")

    with pytest.raises(MultiAssetResearchEvidenceError, match="implementation digest"):
        verify_multi_asset_research_evidence(path, repo_root=ROOT)


def test_verifier_rejects_weakened_governed_path_contract():
    with pytest.raises(MultiAssetResearchEvidenceError, match="implementation path contract"):
        verify_multi_asset_research_evidence(
            EVIDENCE,
            repo_root=ROOT,
            implementation_paths=MULTI_ASSET_IMPLEMENTATION_PATHS[:-1],
        )


def test_top_tier_requirement_uses_semantic_checker_and_data_evidence(tmp_path):
    requirement = next(item for item in load_requirements(REGISTRY) if item["id"] == "ALPHA.MULTI_ASSET.RESEARCH")

    assert requirement["checker"] == "multi_asset_research_evidence"
    assert "data" in requirement["required_evidence"]
    assert requirement["checker_args"]["evidence_path"] == ("evidence/multi_asset_research_baseline.json")
    assert tuple(requirement["checker_args"]["implementation_paths"]) == MULTI_ASSET_IMPLEMENTATION_PATHS
    assert tuple(requirement["checker_args"]["verification_paths"]) == MULTI_ASSET_VERIFICATION_PATHS

    registry = tmp_path / "requirement.json"
    registry.write_text(
        json.dumps({"schema_version": 1, "requirements": [requirement]}),
        encoding="utf-8",
    )
    result = assess_repository(ROOT, registry)["requirements"][0]
    assert result["status"] == "PASS"
    assert result["current_maturity"] == "M3"
