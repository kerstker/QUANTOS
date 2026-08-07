"""Governance tests for the Phase 13 evidence bundle required by 727."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import scripts.evidence_binding as evidence_binding
from scripts.evidence_binding import assert_current_binding, resolve_canonical_source_anchor
from scripts.generate_727_final_audit import (
    ALLOWED_STATUSES,
    OUTPUTS,
    _validation,
    _working_tree_dirty,
    _write_json,
    _write_text,
    build_phase13,
    phase13_artifact_matches,
)
from scripts.generate_trial_ledger_integrity import build_report as build_trial_report
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.trial_provenance import ActorType, TrialProvenance, TrialStatus


ROOT = Path(__file__).resolve().parents[2]
JSON_OUTPUTS = OUTPUTS[:-1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_binding_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "tests")
    evidence = repo / "evidence"
    evidence.mkdir()
    (evidence / "validation_report.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo, subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def test_phase13_dirty_state_ignores_allowlisted_generated_artifact(tmp_path: Path) -> None:
    repo, _source = _init_binding_repo(tmp_path)
    (repo / "evidence" / "validation_report.json").write_text(
        '{"marker": "generated"}\n', encoding="utf-8"
    )

    assert _working_tree_dirty(repo) is False


def test_phase13_dirty_state_rejects_source_dirty_outside_allowlist(tmp_path: Path) -> None:
    repo, _source = _init_binding_repo(tmp_path)
    (repo / "source.py").write_text("source = 1\n", encoding="utf-8")

    assert _working_tree_dirty(repo) is True


def test_phase13_dirty_state_staged_allowlisted_artifact_is_false(tmp_path: Path) -> None:
    repo, _source = _init_binding_repo(tmp_path)
    report = repo / "evidence" / "validation_report.json"
    report.write_text('{"marker": "staged"}\n', encoding="utf-8")
    _git(repo, "add", "evidence/validation_report.json")

    assert _working_tree_dirty(repo) is False


def test_phase13_dirty_state_staged_source_is_true(tmp_path: Path) -> None:
    repo, _source = _init_binding_repo(tmp_path)
    source = repo / "source.py"
    source.write_text("source = 1\n", encoding="utf-8")
    _git(repo, "add", "source.py")

    assert _working_tree_dirty(repo) is True


def test_phase13_dirty_state_untracked_exact_allowlisted_artifact_is_false(tmp_path: Path) -> None:
    repo, _source = _init_binding_repo(tmp_path)
    path = repo / "evidence" / "autonomy_readiness.json"
    path.write_text('{"generated": true}\n', encoding="utf-8")

    assert _working_tree_dirty(repo) is False


def test_phase13_dirty_state_timeout_is_unknown(tmp_path: Path, monkeypatch) -> None:
    repo, _source = _init_binding_repo(tmp_path)

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == evidence_binding.GIT_COMMAND_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(evidence_binding.subprocess, "check_output", timeout)

    assert _working_tree_dirty(repo) is None


def test_phase13_artifact_only_head_accepts_stable_anchor(
    tmp_path: Path, monkeypatch
) -> None:
    for key in (
        "QUANTOS_VALIDATION_RUN_ID",
        "QUANTOS_VALIDATION_SOURCE_COMMIT",
        "QUANTOS_VALIDATION_STARTED_AT_MS",
    ):
        monkeypatch.delenv(key, raising=False)
    repo, source = _init_binding_repo(tmp_path)
    report = repo / "evidence" / "validation_report.json"
    report.write_text('{"marker": "artifact"}\n', encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "refresh artifact")
    anchor = resolve_canonical_source_anchor(repo)
    assert anchor == source
    assert_current_binding(
        {"validation": {"run_id": "a" * 64, "source_commit": source, "generated_at": 1, "current": True}},
        head=anchor,
        repo_root=repo,
    )


def test_phase13_mixed_source_and_environment_mismatch_fail_closed(tmp_path: Path, monkeypatch) -> None:
    repo, source = _init_binding_repo(tmp_path)
    (repo / "evidence" / "validation_report.json").write_text('{"marker": "mixed"}\n', encoding="utf-8")
    (repo / "notes.txt").write_text("unallowlisted\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "mixed refresh")
    anchor = resolve_canonical_source_anchor(repo)
    assert anchor != source
    payload = {"validation": {"run_id": "a" * 64, "source_commit": source, "generated_at": 1, "current": True}}
    try:
        assert_current_binding(payload, head=anchor, repo_root=repo)
    except ValueError:
        pass
    else:
        raise AssertionError("mixed source must fail closed")
    monkeypatch.setenv("QUANTOS_VALIDATION_SOURCE_COMMIT", "b" * 40)
    try:
        assert_current_binding(payload, head=source, repo_root=repo)
    except ValueError:
        pass
    else:
        raise AssertionError("environment source mismatch must fail closed")


def test_validation_evidence_rejects_malformed_legacy_and_stale_reports(tmp_path: Path) -> None:
    repo, source = _init_binding_repo(tmp_path)
    report = repo / "evidence" / "validation_report.json"
    base = {
        "selection_scope": "ALL",
        "overall_status": "PASS",
        "validate_script_sha256": None,
        "validation": {
            "run_id": "a" * 64,
            "source_commit": source,
            "generated_at": 1,
            "current": True,
        },
    }

    for payload in (
        {},
        {"commit": source, "checks_manifest_sha256": "b" * 64},
        {**base, "validation": {**base["validation"], "current": False}},
    ):
        report.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        assert _validation(repo)["current"] is False


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


def test_phase13_write_boundary_preserves_semantically_current_artifacts(
    tmp_path: Path
) -> None:
    relative_path = "evidence/autonomy_readiness.json"
    json_path = tmp_path / relative_path
    text_path = tmp_path / "audit.md"
    first = {"generated_at_ms": 100, "commit": "a" * 40, "status": "NOT_READY"}
    second = {"generated_at_ms": 200, "commit": "a" * 40, "status": "NOT_READY"}
    _write_json(json_path, first, relative_path=relative_path)
    _write_text(text_path, "- 生成时间（Unix ms）：`100`\n- commit：`a`\nstatus\n")
    json_before = (json_path.read_bytes(), json_path.stat().st_mtime_ns)
    text_before = (text_path.read_bytes(), text_path.stat().st_mtime_ns)

    _write_json(json_path, second, relative_path=relative_path)
    _write_text(text_path, "- 生成时间（Unix ms）：`200`\n- commit：`a`\nstatus\n")

    assert (json_path.read_bytes(), json_path.stat().st_mtime_ns) == json_before
    assert (text_path.read_bytes(), text_path.stat().st_mtime_ns) == text_before

    changed = {**second, "commit": "b" * 40}
    _write_json(json_path, changed, relative_path=relative_path)
    _write_text(text_path, "- 生成时间（Unix ms）：`300`\n- commit：`b`\nstatus\n")
    assert json.loads(json_path.read_text(encoding="utf-8"))["commit"] == changed["commit"]
    assert "- commit：`b`" in text_path.read_text(encoding="utf-8")


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


def test_phase13_paper_snapshot_rejects_unverifiable_dynamic_input_drift() -> None:
    reports, _ = build_phase13(root=ROOT, now_ms=1_800_000_000_000)
    path = "evidence/paper_shadow_status.json"
    expected = reports[path]
    actual = json.loads(json.dumps(expected))
    paper_input = actual["inputs"]["data/cache/paper_health_10k.json"]
    paper_input.update({"exists": True, "size_bytes": 128, "sha256": "f" * 64})
    shadow_input = actual["inputs"]["evidence/shadow_status.json"]
    shadow_input.update({"exists": True, "size_bytes": 64, "sha256": "e" * 64})
    actual["paper"]["forward_evidence"] = {"present": True, "n_forward_periods": 2}
    actual["input_hash"] = _canonical_hash(actual["inputs"])

    assert not phase13_artifact_matches(path, actual, expected)


def test_phase13_paper_snapshot_rejects_forged_readiness() -> None:
    reports, _ = build_phase13(root=ROOT, now_ms=1_800_000_000_000)
    path = "evidence/paper_shadow_status.json"
    expected = reports[path]
    actual = json.loads(json.dumps(expected))
    actual["paper"].update(
        {
            "paper_ready": True,
            "status": "READY_FOR_FORWARD_PAPER",
            "forward_evidence": {},
            "blockers": [],
        }
    )

    assert not phase13_artifact_matches(path, actual, expected)


def test_phase13_absent_trial_snapshot_remains_strictly_verifiable(tmp_path: Path) -> None:
    path = "evidence/trial_ledger_integrity.json"
    ledger = tmp_path / "data" / "cache" / "experiment_registry.db"
    expected = build_trial_report(root=tmp_path, ledger=ledger)
    actual = json.loads(json.dumps(expected))
    actual["generated_at_ms"] -= 1
    actual["commit"] = "a" * 40

    assert phase13_artifact_matches(path, actual, expected)

    actual["ledger"]["row_counts"]["trials"] = False
    assert not phase13_artifact_matches(path, actual, expected)

    actual = json.loads(json.dumps(expected))
    actual["ledger"]["path"] = "other.db"
    assert not phase13_artifact_matches(path, actual, expected)

    actual = json.loads(json.dumps(expected))
    actual["open_gaps"] = []
    assert not phase13_artifact_matches(path, actual, expected)


def test_phase13_trial_snapshot_rejects_unverifiable_ledger_drift(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "data" / "cache" / "experiment_registry.db"
    ledger.parent.mkdir(parents=True)
    store = SqliteExperimentStore(str(ledger), create=True)
    store.add_trials(["trial-a", "trial-b", "trial-c"], first_seen_ts=1)
    store.record_trial_provenance(
        TrialProvenance(
            trial_id="trial-provenance-a",
            research_session_id="session-a",
            config_hash="trial-a",
            actor_id="tests.governance",
            actor_type=ActorType.AUTOMATED_SEARCH,
            status=TrialStatus.RECORDED,
            started_at=1,
            completed_at=2,
        )
    )
    store._conn.close()
    path = "evidence/trial_ledger_integrity.json"
    expected = build_trial_report(root=tmp_path, ledger=ledger)
    actual = json.loads(json.dumps(expected))
    ledger_input = actual["inputs"]["data/cache/experiment_registry.db"]
    ledger_input["sha256"] = "f" * 64
    ledger_input["size_bytes"] = max(1, ledger_input["size_bytes"] - 4096)
    actual["input_hash"] = _canonical_hash(actual["inputs"])
    actual["ledger"]["sha256"] = "f" * 64
    actual["ledger"]["row_counts"]["trials"] -= 1
    actual["provenance"]["bare_trials"] -= 1
    actual["provenance"]["coverage_ratio"] = round(
        actual["provenance"]["configs_with_provenance"]
        / actual["ledger"]["row_counts"]["trials"],
        6,
    )

    assert not phase13_artifact_matches(path, actual, expected)

    actual["provenance"]["bare_trials"] += 1
    assert not phase13_artifact_matches(path, actual, expected)

    actual = json.loads(json.dumps(expected))
    actual["family_multiple_testing"]["wired_into_promotion_gate"] = False
    assert not phase13_artifact_matches(path, actual, expected)

    actual = json.loads(json.dumps(expected))
    actual["ledger"]["append_only_guards"].pop("trials")
    assert not phase13_artifact_matches(path, actual, expected)

    actual = json.loads(json.dumps(expected))
    actual["open_gaps"] = []
    assert not phase13_artifact_matches(path, actual, expected)

    actual = json.loads(json.dumps(expected))
    actual["ledger"]["user_version"] = 3
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
