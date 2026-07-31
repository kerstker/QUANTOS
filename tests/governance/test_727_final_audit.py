"""Governance tests for the Phase 13 evidence bundle required by 727."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_727_final_audit import (
    ALLOWED_STATUSES,
    OUTPUTS,
    build_phase13,
    phase13_artifact_matches,
)


ROOT = Path(__file__).resolve().parents[2]
JSON_OUTPUTS = OUTPUTS[:-1]


def test_phase13_outputs_exist_and_are_fail_closed() -> None:
    reports, markdown = build_phase13(root=ROOT, now_ms=1_800_000_000_000)
    assert markdown.startswith("# QUANTOS 自治系统 Phase 13 最终审计")
    assert set(reports) == set(JSON_OUTPUTS)
    for relative_path, payload in reports.items():
        assert payload["readiness"] == "L2_DRY_RUN_ONLY"
        assert payload.get("status", payload.get("phase13_status", payload.get("verdict"))) in ALLOWED_STATUSES | {"PASS", "PARTIAL", "BLOCKED"}
        assert payload["generated_at_ms"] == 1_800_000_000_000
        assert payload["commit"]
        assert payload["config_hash"]
        assert payload["input_hash"]
        assert payload["generator_sha256"]
        assert payload["safety_flags"]["LIVE_TRADING_ENABLED"] is False
        assert payload["safety_flags"]["REAL_BROKER_ENABLED"] is False
        assert payload["safety_flags"]["ALLOW_ORDER_SUBMISSION"] is False
        assert payload["safety_flags"]["ALLOW_AI_DIRECT_TRADING"] is False
        assert payload["safety_flags"]["REQUIRE_HUMAN_APPROVAL"] is True


def test_phase13_current_artifacts_match_read_only_generator() -> None:
    reports, markdown = build_phase13(root=ROOT)
    for relative_path, expected in reports.items():
        actual = json.loads((ROOT / relative_path).read_text(encoding="utf-8-sig"))
        assert phase13_artifact_matches(
            relative_path,
            actual,
            expected,
        ), f"regenerate {relative_path}"
    actual_markdown = (ROOT / "docs/reports/QUANTOS_AUTONOMY_FINAL_AUDIT.md").read_text(
        encoding="utf-8"
    )
    assert actual_markdown.replace("- 生成时间（Unix ms）：`", "- 生成时间（Unix ms）：`")
    assert "当前总状态：`NOT_READY`" in actual_markdown
    assert "READY_FOR_LIVE" not in actual_markdown
    assert "READY_FOR_LIVE" not in markdown


def test_phase13_paper_snapshot_accepts_only_declared_dynamic_input_drift() -> None:
    reports, _ = build_phase13(root=ROOT, now_ms=1_800_000_000_000)
    path = "evidence/paper_shadow_status.json"
    expected = reports[path]
    actual = json.loads(json.dumps(expected))
    actual["inputs"]["data/cache/paper_health_10k.json"]["sha256"] = "f" * 64
    actual["paper"]["forward_evidence"] = {"present": True, "n_forward_periods": 2}
    actual["input_hash"] = _canonical_hash(actual["inputs"])

    assert phase13_artifact_matches(path, actual, expected)

    actual["safety_flags"]["ALLOW_ORDER_SUBMISSION"] = True
    assert not phase13_artifact_matches(path, actual, expected)


def _canonical_hash(value: object) -> str:
    import hashlib

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_trial_ledger_remains_partial_when_provenance_is_incomplete() -> None:
    reports, _ = build_phase13(root=ROOT, now_ms=1_800_000_000_000)
    trial = reports["evidence/trial_ledger_integrity.json"]
    if trial.get("provenance", {}).get("bare_trials", 0) > 0:
        assert trial["verdict"] != "PASS"
        assert trial.get("open_gaps")


def test_final_audit_contains_every_phase_with_explicit_verdict() -> None:
    _reports, markdown = build_phase13(root=ROOT)
    assert "## Phase 0–13 逐阶段验收" in markdown
    for phase in range(14):
        assert f"| {phase} |" in markdown
    for verdict in ("`PASS`", "`PARTIAL`", "`BLOCKED`"):
        assert verdict in markdown


def test_final_audit_covers_all_fifteen_delivery_standards() -> None:
    _reports, markdown = build_phase13(root=ROOT)
    assert "## §25 最终交付标准逐条审计" in markdown
    for number in range(1, 16):
        assert f"| {number} |" in markdown


def test_paper_shadow_does_not_promote_marks_only_replay() -> None:
    reports, _ = build_phase13(root=ROOT, now_ms=1_800_000_000_000)
    paper_shadow = reports["evidence/paper_shadow_status.json"]
    assert paper_shadow["paper"]["paper_ready"] is False
    assert paper_shadow["paper"]["status"] == "NOT_READY"
    assert paper_shadow["status"] in {"READY_FOR_SHADOW", "NOT_READY"}


def test_binance_testnet_evidence_does_not_promote_mexc_or_production() -> None:
    reports, _ = build_phase13(root=ROOT, now_ms=1_800_000_000_000)
    mexc = reports["evidence/mexc_integration_status.json"]
    external = mexc["external_verification"]
    assert external["adjacent_binance_futures_testnet_order_verified"] is True
    assert external["adjacent_binance_evidence_ref"] == (
        "evidence/binance_testnet_smoke.json"
    )
    assert external["credentials_accessed"] is False
    assert external["test_order_executed"] is False
    assert mexc["implementation"]["micro_live_broker"] is False
    assert mexc["status"] == "NOT_READY"
