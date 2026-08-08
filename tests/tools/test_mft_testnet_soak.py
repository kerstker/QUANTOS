from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from quantos.contracts.hashing import stable_hash
from services.monitoring.mft_soak import REQUIRED_EVIDENCE_KINDS, parse_soak_policy
from tools import mft_testnet_soak


SAFETY_FLAGS = {
    "LIVE_TRADING_ENABLED": False,
    "REAL_BROKER_ENABLED": False,
    "ALLOW_ORDER_SUBMISSION": False,
    "ALLOW_AI_DIRECT_TRADING": False,
    "REQUIRE_HUMAN_APPROVAL": True,
}


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "MFT_TESTNET_SOAK_POLICY",
        "policy_id": "mft-testnet-soak.cli-test.v1",
        "policy_version": "1.0.0",
        "venue": "BINANCE_FUTURES_TESTNET",
        "mode": "TESTNET_ONLY",
        "cadence_ms": 100,
        "minimum_duration_ms": 100,
        "minimum_completed_cycles": 2,
        "maximum_cycle_gap_ms": 150,
        "thresholds": {
            "max_market_gap_ms": 10,
            "max_scheduler_lag_ms": 10,
            "max_reconciliation_abs_error": 1e-8,
            "max_abs_tca_slippage_bps": 200.0,
            "max_parity_position_drift": 1e-8,
            "max_oms_unknown_count": 0,
            "max_risk_breach_count": 0,
            "max_failed_cycle_count": 0,
        },
        "required_evidence_kinds": list(REQUIRED_EVIDENCE_KINDS),
        "safety_flags": dict(SAFETY_FLAGS),
        "calibration": "PREREGISTERED_UNCALIBRATED_ENGINEERING_LIMITS",
        "completion_effect": "TESTNET_ENGINEERING_ONLY_NO_MICRO_LIVE_AUTHORITY",
    }


def _start(policy_hash: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "MFT_TESTNET_SOAK_START",
        "run_id": "cli-run",
        "operator_start_id": "operator-start",
        "started_at_ms": 1_000,
        "policy_hash": policy_hash,
        "venue": "BINANCE_FUTURES_TESTNET",
        "mode": "TESTNET_ONLY",
        "authorization_class": "OPERATOR_ASSERTED_TESTNET_ONLY",
        "human_authorization_receipt_hash": "1" * 64,
        "testnet_mandate_hash": "2" * 64,
        "credential_reference_hash": "3" * 64,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _cycle(policy_hash: str, index: int) -> dict[str, object]:
    started = 1_000 + index * 100
    completed = started + 50
    return {
        "schema_version": 1,
        "kind": "MFT_TESTNET_SOAK_CYCLE",
        "run_id": "cli-run",
        "operator_start_id": "operator-start",
        "cycle_id": f"cycle-{index}",
        "cycle_index": index,
        "started_at_ms": started,
        "completed_at_ms": completed,
        "policy_hash": policy_hash,
        "venue": "BINANCE_FUTURES_TESTNET",
        "mode": "TESTNET_ONLY",
        "evidence": {
            kind: {
                "artifact": f"{kind}-{index}.json",
                "artifact_hash": stable_hash({"kind": kind, "index": index}),
                "observed_at_ms": completed,
                "status": "VERIFIED",
            }
            for kind in REQUIRED_EVIDENCE_KINDS
        },
        "metrics": {
            "market_gap_ms": 0,
            "scheduler_lag_ms": 0,
            "intent_count": 1,
            "oms_unknown_count": 0,
            "fill_count": 1,
            "reconciliation_abs_error": 0.0,
            "risk_breach_count": 0,
            "tca_abs_slippage_bps": 1.0,
            "parity_position_drift": 0.0,
            "recovery_drill_passed": True,
            "testnet_submission_count": 1,
            "mainnet_submission_count": 0,
        },
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _materialize_evidence(root: Path, row: dict[str, object]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for kind, item in row["evidence"].items():
        payload = json.dumps({"kind": kind, "cycle_id": row["cycle_id"]}).encode()
        (root / item["artifact"]).write_bytes(payload)
        item["artifact_hash"] = hashlib.sha256(payload).hexdigest()


def _write(path: Path, value: object) -> str:
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def test_cli_records_explicit_start_then_cycles_and_publishes_status(tmp_path, capsys) -> None:
    policy_document = _policy()
    policy = parse_soak_policy(policy_document)
    policy_path = _write(tmp_path / "policy.json", policy_document)
    start_path = _write(tmp_path / "start.json", _start(policy.policy_hash))
    store = str(tmp_path / "soak.db")
    status = str(tmp_path / "status.json")
    evidence_root = tmp_path / "cycle-evidence"
    base = ["--policy", policy_path, "--store", store, "--status-out", status, "--evidence-root", str(evidence_root)]

    assert mft_testnet_soak.main([*base, "--start-receipt", start_path], clock_ms=lambda: 1_000) == 0
    first_row = _cycle(policy.policy_hash, 0)
    _materialize_evidence(evidence_root, first_row)
    first = _write(tmp_path / "cycle-0.json", first_row)
    assert mft_testnet_soak.main([*base, "--cycle-receipt", first], clock_ms=lambda: 1_050) == 0
    second_row = _cycle(policy.policy_hash, 1)
    _materialize_evidence(evidence_root, second_row)
    second = _write(tmp_path / "cycle-1.json", second_row)
    assert mft_testnet_soak.main([*base, "--cycle-receipt", second], clock_ms=lambda: 1_150) == 0

    projection = json.loads(Path(status).read_text(encoding="utf-8"))
    output = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert projection["status"] == "OPERATIONAL_SOAK_PASSED"
    assert output["status"] == "OPERATIONAL_SOAK_PASSED"
    assert projection["micro_live_ready"] is False
    assert projection["live_order_authority"] is False


def test_cli_without_operator_start_is_pending_and_cycle_cannot_create_one(tmp_path, capsys) -> None:
    policy_document = _policy()
    policy = parse_soak_policy(policy_document)
    policy_path = _write(tmp_path / "policy.json", policy_document)
    status = str(tmp_path / "status.json")
    base = [
        "--policy", policy_path,
        "--store", str(tmp_path / "soak.db"),
        "--status-out", status,
        "--evidence-root", str(tmp_path / "cycle-evidence"),
    ]

    assert mft_testnet_soak.main(base, clock_ms=lambda: 900) == 0
    assert json.loads(Path(status).read_text(encoding="utf-8"))["status"] == "OPERATIONAL_SOAK_PENDING"

    cycle = _write(tmp_path / "cycle.json", _cycle(policy.policy_hash, 0))
    assert mft_testnet_soak.main([*base, "--cycle-receipt", cycle], clock_ms=lambda: 1_050) == 3
    output = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert output == {"error_code": "SOAK_START_RECEIPT_MISSING", "status": "ERROR"}


def test_cli_records_failed_cycle_and_returns_blocked(tmp_path, capsys) -> None:
    policy_document = _policy()
    policy = parse_soak_policy(policy_document)
    policy_path = _write(tmp_path / "policy.json", policy_document)
    start_path = _write(tmp_path / "start.json", _start(policy.policy_hash))
    store = str(tmp_path / "soak.db")
    status = str(tmp_path / "status.json")
    evidence_root = tmp_path / "cycle-evidence"
    base = ["--policy", policy_path, "--store", store, "--status-out", status, "--evidence-root", str(evidence_root)]
    assert mft_testnet_soak.main([*base, "--start-receipt", start_path], clock_ms=lambda: 1_000) == 0
    failed = _cycle(policy.policy_hash, 0)
    failed["evidence"]["market_continuity"]["status"] = "HTTP_429"
    _materialize_evidence(evidence_root, failed)
    failed_path = _write(tmp_path / "failed.json", failed)

    assert mft_testnet_soak.main([*base, "--cycle-receipt", failed_path], clock_ms=lambda: 1_050) == 2
    projection = json.loads(Path(status).read_text(encoding="utf-8"))
    assert projection["status"] == "OPERATIONAL_SOAK_BLOCKED"
    assert projection["failed_cycles"] == 1
    raw = capsys.readouterr().out
    assert "HTTP_429" not in raw


def test_cli_boundary_has_no_credentials_network_adapter_or_submission_switch() -> None:
    source_path = Path(mft_testnet_soak.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "BINANCE_TESTNET_API_KEY" not in source
    assert "BINANCE_TESTNET_API_SECRET" not in source
    assert not any(name.startswith(("live", "requests", "httpx", "urllib")) for name in imports)
    option_strings = {
        option
        for action in mft_testnet_soak.build_parser()._actions
        for option in action.option_strings
    }
    assert not any("submit" in option or "mainnet" in option or "credential" in option for option in option_strings)
