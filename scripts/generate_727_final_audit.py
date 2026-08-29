#!/usr/bin/env python
"""Generate the Phase 13 evidence bundle required by ``docs/QUANTOS_727.md``.

This is a read-only evidence aggregator.  It never fetches market data, opens
credentials, creates fills, changes a database, or grants deployment.  The
individual status documents are deliberately conservative: an implemented
and tested research subsystem can reach ``READY_FOR_RESEARCH_AUTONOMY``, while
missing forward evidence, user-stream support, or external approvals keeps
the corresponding higher-risk stage at ``NOT_READY``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = (
    "evidence/autonomy_readiness.json",
    "evidence/trial_ledger_integrity.json",
    "evidence/factor_garden_status.json",
    "evidence/alpha_lifecycle_status.json",
    "evidence/paper_shadow_status.json",
    "evidence/mexc_integration_status.json",
    "evidence/chaos_recovery_status.json",
    "docs/reports/QUANTOS_AUTONOMY_FINAL_AUDIT.md",
)
ALLOWED_STATUSES = {
    "READY_FOR_RESEARCH_AUTONOMY",
    "READY_FOR_FORWARD_PAPER",
    "READY_FOR_SHADOW",
    "READY_FOR_MICRO_LIVE_REVIEW",
    "NOT_READY",
}
SAFETY_FLAG_NAMES = (
    "LIVE_TRADING_ENABLED",
    "REAL_BROKER_ENABLED",
    "ALLOW_ORDER_SUBMISSION",
    "ALLOW_AI_DIRECT_TRADING",
    "REQUIRE_HUMAN_APPROVAL",
)
_DYNAMIC_REPORT_INPUTS = {
    "evidence/trial_ledger_integrity.json": frozenset(
        {
            "data/cache/experiment_registry.db",
        }
    ),
    "evidence/paper_shadow_status.json": frozenset(
        {
            "data/cache/paper_health_10k.json",
            "evidence/shadow_status.json",
        }
    ),
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS
from scripts.evidence_binding import (
    assert_canonical_working_tree,
    assert_current_binding,
    resolve_canonical_source_anchor,
    validation_binding_from_payload,
)
from scripts.generate_trial_ledger_integrity import (
    _APPEND_ONLY_TABLES,
    build_report as build_trial_report,
)
from services.execution.backtest_authority import (
    AUTHORITATIVE_EVIDENCE_STATUS,
    LEGACY_INVALIDATION_CODE,
    is_authoritative_backtest_record,
)
from services.research.experiment_registry.store import _SEARCH_SCHEMA_VERSION

_CURRENT_TRIAL_LEDGER_SCHEMA_VERSION = _SEARCH_SCHEMA_VERSION

_AUTHORITATIVE_BACKTEST_FIELD = "authoritative_backtest_record"
_LEGACY_PROFITABILITY_ARTIFACTS = (
    "evidence/alpha_lifecycle_cockpit.json",
    "evidence/alpha_paper_cycle.json",
    "evidence/three_alpha_paper_flow.json",
    "data/cache/binance_usdt_continuous_paper.json",
)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    try:
        with path.open(encoding="utf-8-sig") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _commit(root: Path) -> str:
    try:
        return resolve_canonical_source_anchor(root)
    except (OSError, ValueError, subprocess.SubprocessError):
        return "UNKNOWN"


def _working_tree_dirty(root: Path) -> bool | None:
    try:
        assert_canonical_working_tree(root)
    except ValueError:
        return True
    except (OSError, subprocess.SubprocessError):
        return None
    return False


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _bundle(root: Path, relative_path: str) -> dict[str, Any]:
    """Return a compact hash inventory for a file or Python source directory."""
    path = root / relative_path
    if path.is_dir():
        files = sorted(
            p for p in path.rglob("*.py")
            if "__pycache__" not in p.parts
        )
        entries = [
            {"path": _relative(root, item), "sha256": _sha256(item)}
            for item in files
        ]
        return {
            "kind": "python_directory",
            "exists": True,
            "file_count": len(entries),
            "files_sha256": _canonical_hash(entries),
        }
    return {
        "kind": "file",
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path),
    }


def _manifest(root: Path, paths: Iterable[str]) -> tuple[dict[str, Any], str]:
    entries = {path: _bundle(root, path) for path in sorted(set(paths))}
    return entries, _canonical_hash(entries)


def _artifact(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    payload = _load_json(root, relative_path)
    result: dict[str, Any] = {
        "path": relative_path,
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path),
    }
    for key in (
        "artifact",
        "mode",
        "verdict",
        "readiness",
        "status",
        "generated_at_ms",
        "commit",
    ):
        if key in payload:
            result[key] = payload[key]
    return result


def _backtest_authority_admission(
    root: Path, relative_path: str
) -> dict[str, Any]:
    """Admit only an explicit record through the independent authority gate.

    File retention, a content hash, a non-empty list, or a self-described READY
    verdict is never authority.  The source artifact remains in the input
    manifest for audit/reproduction, but its profitability claims are excluded
    from readiness until its explicit record passes the separately controlled
    admission predicate.
    """

    payload = _load_json(root, relative_path)
    record = payload.get(_AUTHORITATIVE_BACKTEST_FIELD)
    present = isinstance(record, dict)
    admitted = present and is_authoritative_backtest_record(record)
    return {
        "source_path": relative_path,
        "required_field": _AUTHORITATIVE_BACKTEST_FIELD,
        "record_present": present,
        "independently_admitted": admitted,
        "status": (
            AUTHORITATIVE_EVIDENCE_STATUS
            if admitted
            else LEGACY_INVALIDATION_CODE
        ),
        "counted_as_completion_evidence": admitted,
    }


def _safety_flags() -> dict[str, bool]:
    values = RESEARCH_SAFE_DEFAULTS.to_dict()
    return {
        "LIVE_TRADING_ENABLED": bool(values["live_trading_enabled"]),
        "REAL_BROKER_ENABLED": bool(values["real_broker_enabled"]),
        "ALLOW_ORDER_SUBMISSION": bool(values["allow_order_submission"]),
        "ALLOW_AI_DIRECT_TRADING": bool(values["allow_ai_direct_trading"]),
        "REQUIRE_HUMAN_APPROVAL": bool(values["require_human_approval"]),
    }


def _safety_ok(flags: dict[str, bool]) -> bool:
    return (
        flags["LIVE_TRADING_ENABLED"] is False
        and flags["REAL_BROKER_ENABLED"] is False
        and flags["ALLOW_ORDER_SUBMISSION"] is False
        and flags["ALLOW_AI_DIRECT_TRADING"] is False
        and flags["REQUIRE_HUMAN_APPROVAL"] is True
    )


def _validation(root: Path) -> dict[str, Any]:
    report = _load_json(root, "evidence/validation_report.json")
    try:
        canonical_source = resolve_canonical_source_anchor(root)
        # Validate the report's explicit binding as-is.  In particular, retain
        # current=false after a failed Phase 13 tail rather than manufacturing
        # a fresh current claim from legacy fields.
        binding = validation_binding_from_payload(report)
        assert_current_binding(
            {"validation": binding},
            head=canonical_source,
            repo_root=root,
        )
        binding_current = True
    except (OSError, ValueError, subprocess.SubprocessError):
        canonical_source = _commit(root)
        binding_current = False
    validate_hash = _sha256(root / "scripts" / "validate.py")
    report_binding = report.get("validation")
    report_current = (
        isinstance(report_binding, dict)
        and report_binding.get("current") is True
    )
    current = (
        report.get("selection_scope") == "ALL"
        and report.get("overall_status") == "PASS"
        and report_current
        and binding_current
        and report.get("validate_script_sha256") == validate_hash
    )
    return {
        "current": current,
        "selection_scope": report.get("selection_scope"),
        "overall_status": report.get("overall_status"),
        "reported_commit": report.get("commit"),
        "head_commit": canonical_source,
        "validate_script_sha256": validate_hash,
        "reported_validate_script_sha256": report.get("validate_script_sha256"),
    }


def _common(
    *,
    root: Path,
    artifact: str,
    status: str,
    input_paths: Iterable[str],
    now_ms: int,
    flags: dict[str, bool],
    validation: dict[str, Any],
    blockers: Iterable[str] = (),
) -> dict[str, Any]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"unsupported Phase 13 status: {status}")
    inputs, input_hash = _manifest(root, input_paths)
    return {
        "schema_version": 1,
        "artifact": artifact,
        "generated_at_ms": int(now_ms),
        "commit": _commit(root),
        "readiness": "L2_DRY_RUN_ONLY",
        "status": status,
        "blockers": list(dict.fromkeys(str(item) for item in blockers)),
        "safety_flags": flags,
        "safety_verdict": "PASS" if _safety_ok(flags) else "BLOCK",
        "validation": validation,
        "working_tree_dirty": _working_tree_dirty(root),
        "inputs": inputs,
        "input_hash": input_hash,
        "config_hash": _sha256(root / "quantos" / "contracts" / "config.py"),
        "generator_sha256": _sha256(root / "scripts" / "generate_727_final_audit.py"),
    }


def _research_status(
    *,
    validation: dict[str, Any],
    flags: dict[str, bool],
    source_bundles: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    blockers: list[str] = []
    if not validation["current"]:
        blockers.append("FULL_VALIDATION_NOT_CURRENT_PASS")
    if not _safety_ok(flags):
        blockers.append("SAFETY_FLAGS_NOT_RESEARCH_SAFE")
    for bundle in source_bundles:
        if not bundle.get("exists") or bundle.get("file_count", 0) == 0:
            blockers.append(f"SOURCE_OR_TEST_BUNDLE_MISSING:{bundle['name']}")
    return (
        "READY_FOR_RESEARCH_AUTONOMY" if not blockers else "NOT_READY",
        blockers,
    )


def _autonomy(root: Path, now_ms: int, flags: dict[str, bool], validation: dict[str, Any]) -> dict[str, Any]:
    paths = [
        "docs/QUANTOS_727.md",
        "docs/engineering/specs/2026-07-27-autonomy-kernel-design.md",
        "services/orchestration",
        "tests/orchestration",
    ]
    manifest, _ = _manifest(root, paths)
    bundles = [
        {"name": path, **manifest[path]}
        for path in ("services/orchestration", "tests/orchestration")
    ]
    status, blockers = _research_status(
        validation=validation, flags=flags, source_bundles=bundles
    )
    report = _common(
        root=root,
        artifact="QUANTOS_727_AUTONOMY_READINESS",
        status=status,
        input_paths=paths,
        now_ms=now_ms,
        flags=flags,
        validation=validation,
        blockers=blockers,
    )
    report.update(
        {
            "implementation": {
                "autonomy_kernel": True,
                "workflow_event_policy_idempotency_scheduler": True,
                "leader_lease_and_recovery": True,
                "production_order_authority": False,
            },
            "acceptance": {
                "research_autonomy": status == "READY_FOR_RESEARCH_AUTONOMY",
                "live_authority": False,
            },
            "honesty_note": (
                "This status covers deterministic research-plane autonomy only. "
                "It does not authorize paper, shadow, micro-live, or live orders."
            ),
        }
    )
    return report


def _trial_ledger(root: Path, now_ms: int) -> dict[str, Any]:
    ledger = root / "data" / "cache" / "experiment_registry.db"
    report = build_trial_report(root=root, ledger=ledger)
    # Keep this requested artifact on the same canonical source and snapshot
    # timestamp as the other Phase 13 products.
    report["commit"] = _commit(root)
    report["generated_at_ms"] = int(now_ms)
    return report


def _factor_garden(root: Path, now_ms: int, flags: dict[str, bool], validation: dict[str, Any]) -> dict[str, Any]:
    paths = [
        "docs/QUANTOS_727.md",
        "docs/engineering/specs/2026-07-27-factor-garden-design.md",
        "services/factor_garden",
        "services/research/factor_engine",
        "tests/factor_garden",
    ]
    manifest, _ = _manifest(root, paths)
    bundles = [
        {"name": path, **manifest[path]}
        for path in ("services/factor_garden", "services/research/factor_engine", "tests/factor_garden")
    ]
    status, blockers = _research_status(
        validation=validation, flags=flags, source_bundles=bundles
    )
    report = _common(
        root=root,
        artifact="QUANTOS_727_FACTOR_GARDEN_STATUS",
        status=status,
        input_paths=paths,
        now_ms=now_ms,
        flags=flags,
        validation=validation,
        blockers=blockers,
    )
    report.update(
        {
            "implementation": {
                "registry": True,
                "materializer": True,
                "dependency_graph": True,
                "offline_online_parity": True,
                "family_cluster": True,
            },
            "acceptance": {
                "factor_garden_research_plane": status == "READY_FOR_RESEARCH_AUTONOMY",
                "live_order_authority": False,
            },
            "honesty_note": (
                "Factor Garden is a tested research-plane capability; it is not a "
                "promotion grant and does not permit real trading."
            ),
        }
    )
    return report


def _alpha_lifecycle(root: Path, now_ms: int, flags: dict[str, bool], validation: dict[str, Any]) -> dict[str, Any]:
    paths = [
        "docs/QUANTOS_727.md",
        "docs/engineering/specs/2026-07-27-alpha-factory-phase5-design.md",
        "evidence/alpha_lifecycle_cockpit.json",
        "evidence/alpha_paper_cycle.json",
        "services/alpha",
        "services/alpha_factory",
        "tests/alpha",
        "tests/alpha_factory",
        "tests/lifecycle",
    ]
    manifest, _ = _manifest(root, paths)
    bundles = [
        {"name": path, **manifest[path]}
        for path in ("services/alpha", "services/alpha_factory", "tests/alpha", "tests/alpha_factory", "tests/lifecycle")
    ]
    status, blockers = _research_status(
        validation=validation, flags=flags, source_bundles=bundles
    )
    authority = _backtest_authority_admission(
        root, "evidence/alpha_lifecycle_cockpit.json"
    )
    cockpit = _load_json(root, "evidence/alpha_lifecycle_cockpit.json")
    alphas = cockpit.get("alphas") if isinstance(cockpit.get("alphas"), list) else []
    if not alphas:
        blockers.append("ALPHA_LIFECYCLE_COCKPIT_EMPTY_OR_MISSING")
        status = "NOT_READY"
    if not authority["independently_admitted"]:
        blockers.append(LEGACY_INVALIDATION_CODE)
        status = "NOT_READY"
    report = _common(
        root=root,
        artifact="QUANTOS_727_ALPHA_LIFECYCLE_STATUS",
        status=status,
        input_paths=paths,
        now_ms=now_ms,
        flags=flags,
        validation=validation,
        blockers=blockers,
    )
    report.update(
        {
            "cockpit": {
                "path": "evidence/alpha_lifecycle_cockpit.json",
                "exists": (root / "evidence" / "alpha_lifecycle_cockpit.json").is_file(),
                "sha256": _sha256(root / "evidence" / "alpha_lifecycle_cockpit.json"),
                "retained_alpha_count": len(alphas),
                "retention_status": LEGACY_INVALIDATION_CODE,
                "counted_as_completion_evidence": False,
            },
            "authoritative_backtest": authority,
            "legacy_profitability_artifacts": {
                path: {
                    "exists": (root / path).is_file(),
                    "sha256": _sha256(root / path),
                    "retention_status": LEGACY_INVALIDATION_CODE,
                    "counted_as_completion_evidence": False,
                }
                for path in _LEGACY_PROFITABILITY_ARTIFACTS
                if path in paths
            },
            "implementation": {
                "research_state_machine": True,
                "deployment_state_machine": True,
                "health_state_machine": True,
                "promotion_and_grant": True,
                "degradation_pause_retirement": True,
            },
            "acceptance": {
                "research_lifecycle": (
                    status == "READY_FOR_RESEARCH_AUTONOMY"
                    and authority["independently_admitted"]
                ),
                "external_approval": False,
                "live_authority": False,
            },
            "honesty_note": (
                "The cockpit and paper-cycle files are retained and hash-audited "
                "diagnostic history. Their existence, non-empty rows, stages, or "
                "hashes do not satisfy lifecycle acceptance without an independently "
                "admitted authoritative_backtest_record."
            ),
        }
    )
    return report


def _paper_shadow(root: Path, now_ms: int, flags: dict[str, bool], validation: dict[str, Any]) -> dict[str, Any]:
    paths = [
        "docs/QUANTOS_727.md",
        "docs/engineering/specs/2026-07-27-shadow-phase7-design.md",
        "data/cache/paper_health_10k.json",
        "evidence/shadow_status.json",
        "evidence/alpha_paper_cycle.json",
        "evidence/three_alpha_paper_flow.json",
        "data/cache/binance_usdt_continuous_paper.json",
        "tests/live",
        "tests/research",
    ]
    paper = _load_json(root, "data/cache/paper_health_10k.json")
    shadow = _load_json(root, "evidence/shadow_status.json")
    paper_authority = _backtest_authority_admission(
        root, "data/cache/paper_health_10k.json"
    )
    paper_verdict = paper.get("verdict") if isinstance(paper.get("verdict"), dict) else {}
    paper_blockers = paper.get("blockers")
    if not isinstance(paper_blockers, list):
        paper_blockers = ["PAPER_EVIDENCE_MALFORMED"]
    else:
        paper_blockers = [str(item) for item in paper_blockers]
    if not paper_authority["independently_admitted"]:
        paper_blockers.append(LEGACY_INVALIDATION_CODE)
    paper_blockers = list(dict.fromkeys(paper_blockers))
    paper_forward = paper.get("forward_evidence")
    if not isinstance(paper_forward, dict):
        paper_forward = {}
    paper_ready = (
        paper_authority["independently_admitted"]
        and paper_verdict.get("paper_ready") is True
        and bool(paper_forward)
        and not paper_blockers
    )
    shadow_readiness = shadow.get("readiness") if isinstance(shadow.get("readiness"), dict) else {}
    shadow_blockers = shadow_readiness.get("blockers")
    if not isinstance(shadow_blockers, list):
        shadow_blockers = ["SHADOW_EVIDENCE_MALFORMED"]
    shadow_safety = shadow.get("safety") if isinstance(shadow.get("safety"), dict) else {}
    shadow_ready = (
        shadow_readiness.get("shadow_ready") is True
        and shadow_readiness.get("chain_valid") is True
        and shadow_readiness.get("outbox_chain_valid") is True
        and not shadow_blockers
        and shadow_safety.get("order_path_touched") is False
    )
    blockers: list[str] = []
    if not paper_ready:
        blockers.extend(str(item) for item in paper_blockers[:20])
        if not blockers:
            blockers.append("PAPER_NOT_READY")
    if not shadow_ready:
        blockers.extend(str(item) for item in shadow_blockers)
        if not shadow_blockers:
            blockers.append("SHADOW_NOT_READY")
    if not _safety_ok(flags):
        blockers.append("SAFETY_FLAGS_NOT_RESEARCH_SAFE")
    status = (
        "READY_FOR_SHADOW"
        if paper_ready and shadow_ready and _safety_ok(flags)
        else "NOT_READY"
    )
    report = _common(
        root=root,
        artifact="QUANTOS_727_PAPER_SHADOW_STATUS",
        status=status,
        input_paths=paths,
        now_ms=now_ms,
        flags=flags,
        validation=validation,
        blockers=blockers,
    )
    report.update(
        {
            "paper": {
                "status": "READY_FOR_FORWARD_PAPER" if paper_ready else "NOT_READY",
                "paper_ready": paper_ready,
                "paper_mark_ready": (
                    paper_verdict.get("paper_mark_ready")
                    if paper_authority["independently_admitted"]
                    else False
                ),
                "paper_preview_ready": (
                    paper_verdict.get("paper_preview_ready")
                    if paper_authority["independently_admitted"]
                    else False
                ),
                "forward_evidence": (
                    paper_forward
                    if paper_authority["independently_admitted"]
                    else {}
                ),
                "retained_forward_evidence_present": bool(paper_forward),
                "authoritative_backtest": paper_authority,
                "blockers": paper_blockers,
            },
            "shadow": {
                "status": "READY_FOR_SHADOW" if shadow_ready else "NOT_READY",
                "shadow_ready": shadow_ready,
                "observation_count": shadow_readiness.get("observation_count"),
                "chain_valid": shadow_readiness.get("chain_valid"),
                "outbox_chain_valid": shadow_readiness.get("outbox_chain_valid"),
                "safety": shadow_safety,
            },
            "honesty_note": (
                "Shadow readiness is not fill or paper proof. Legacy paper-cycle, "
                "three-alpha, continuous-paper, and health artifacts remain hash-audited "
                "diagnostic history and cannot become paper evidence through retention."
            ),
        }
    )
    return report


def _mexc(root: Path, now_ms: int, flags: dict[str, bool], validation: dict[str, Any]) -> dict[str, Any]:
    paths = [
        "docs/QUANTOS_727.md",
        "docs/engineering/specs/2026-07-27-mexc-phase8-design.md",
        "live/exchange/mexc_spot.py",
        "live/pipeline.py",
        "live/execution/order_recovery.py",
        "live/execution/startup_recovery.py",
        "tools/mexc_credentials.py",
        "tools/mexc_readonly_smoke.py",
        "tools/live_testnet_smoke.py",
        "evidence/binance_testnet_smoke.json",
        "tests/live/test_mexc_spot.py",
        "tests/live/test_mexc_pipeline_integration.py",
        "tests/live/test_order_recovery.py",
        "tests/live/test_startup_recovery.py",
        "tests/tools/test_live_testnet_smoke.py",
    ]
    adapter_source = (root / "live" / "exchange" / "mexc_spot.py").read_text(
        encoding="utf-8", errors="replace"
    ) if (root / "live" / "exchange" / "mexc_spot.py").is_file() else ""
    has_user_stream = bool(re.search(r"^\s*def\s+user_stream\s*\(", adapter_source, re.MULTILINE))
    binance_testnet = _load_json(root, "evidence/binance_testnet_smoke.json")
    binance_checks = binance_testnet.get("checks")
    binance_safety = binance_testnet.get("safety")
    binance_testnet_verified = (
        binance_testnet.get("verdict") == "PASS"
        and binance_testnet.get("venue") == "BINANCE_FUTURES_TESTNET"
        and binance_testnet.get("place_order_requested") is True
        and isinstance(binance_checks, list)
        and bool(binance_checks)
        and all(
            isinstance(check, dict) and check.get("status") == "PASS"
            for check in binance_checks
        )
        and isinstance(binance_safety, dict)
        and binance_safety.get("testnet_only") is True
        and binance_safety.get("real_capital") is False
        and binance_safety.get("mainnet_order_submission") is False
        and binance_safety.get("credentials_persisted") is False
        and binance_safety.get("raw_account_response_persisted") is False
    )
    blockers = [
        "MEXC_NO_SPOT_TESTNET_EXTERNAL_PATH",
        "REAL_CREDENTIAL_ACCOUNT_SMOKE_NOT_EXECUTED",
    ]
    if not has_user_stream:
        blockers.append("USER_STREAM_NOT_IMPLEMENTED")
    if not _safety_ok(flags):
        blockers.append("SAFETY_FLAGS_NOT_RESEARCH_SAFE")
    report = _common(
        root=root,
        artifact="QUANTOS_727_MEXC_INTEGRATION_STATUS",
        status="NOT_READY",
        input_paths=paths,
        now_ms=now_ms,
        flags=flags,
        validation=validation,
        blockers=blockers,
    )
    report.update(
        {
            "implementation": {
                "public_market_read": (root / "live/exchange/mexc_spot.py").is_file(),
                "account_read_only": "def account" in adapter_source,
                "commission_and_rules": "commission" in adapter_source,
                "test_order": "def test_order" in adapter_source,
                "unknown_order_persistence": (root / "live/execution/order_recovery.py").is_file(),
                "startup_recovery_choreography": (root / "live/execution/startup_recovery.py").is_file(),
                "user_stream": has_user_stream,
                "micro_live_broker": False,
            },
            "external_verification": {
                "credentials_accessed": False,
                "account_smoke": False,
                "test_order_executed": False,
                "user_stream_verified": False,
                "adjacent_binance_futures_testnet_order_verified": (
                    binance_testnet_verified
                ),
                "adjacent_binance_evidence_ref": (
                    "evidence/binance_testnet_smoke.json"
                    if binance_testnet_verified
                    else None
                ),
            },
            "acceptance": {
                "read_only_and_test_order_code": True,
                "real_order_path": False,
                "micro_live_review": False,
            },
            "honesty_note": (
                "M1/M2, the M3 safety kernel, and the M4 user-stream seam are implemented and tested. "
                "The adapter remains fail-closed; no real credential or order "
                "evidence is claimed, and MEXC spot has no testnet. A successful "
                "Binance Futures testnet order is recorded only as adjacent venue "
                "connectivity evidence and does not satisfy MEXC or production "
                "multi-venue acceptance."
            ),
        }
    )
    return report


def _chaos(root: Path, now_ms: int, flags: dict[str, bool], validation: dict[str, Any]) -> dict[str, Any]:
    paths = [
        "docs/QUANTOS_727.md",
        "tests/live/test_restore_drill.py",
        "tests/live/test_risk_recovery_drill.py",
        "tests/live/test_live_backup.py",
        "tests/live/test_order_recovery.py",
        "tests/live/test_startup_recovery.py",
        "tests/orchestration/test_recovery.py",
        "tests/orchestration/test_leader_lease.py",
    ]
    restore = _load_json(root, "evidence/restore_drill_latest.json")
    risk = _load_json(root, "evidence/risk_recovery_drill_latest.json")
    verified = {
        "backup_restore": restore.get("verdict") == "PASS",
        "kill_switch_recovery": risk.get("verdict") == "PASS",
        "unknown_order": (root / "tests/live/test_order_recovery.py").is_file(),
        "startup_ordering": (root / "tests/live/test_startup_recovery.py").is_file(),
        "leader_fencing": (root / "tests/orchestration/test_leader_lease.py").is_file(),
    }
    required = {
        "exchange_network_partition": False,
        "websocket_disconnect_and_replay": False,
        "partial_fill_and_reconciliation": False,
        "rate_limit_backoff_and_dlq": False,
        "cross_process_kill_switch_authority": False,
    }
    blockers = [
        f"CHAOS_SCENARIO_NOT_EVIDENCED:{name}"
        for name, covered in required.items()
        if not covered
    ]
    if not _safety_ok(flags):
        blockers.append("SAFETY_FLAGS_NOT_RESEARCH_SAFE")
    report = _common(
        root=root,
        artifact="QUANTOS_727_CHAOS_RECOVERY_STATUS",
        status="NOT_READY",
        input_paths=paths,
        now_ms=now_ms,
        flags=flags,
        validation=validation,
        blockers=blockers,
    )
    report.update(
        {
            "internal_drills": {
                "restore": restore.get("verdict"),
                "risk_recovery": risk.get("verdict"),
                "verified_scenarios": verified,
            },
            "required_scenarios": required,
            "acceptance": {
                "no_uncontrolled_orders_in_drills": all(verified.values()),
                "replayable_recovery_path": verified["backup_restore"] and verified["startup_ordering"],
                "full_phase12_chaos_matrix": False,
            },
            "honesty_note": (
                "Existing local restore/risk drills and M3 deterministic tests are "
                "reported, but unverified exchange network/WebSocket and external "
                "process-isolation scenarios remain blocked."
            ),
        }
    )
    return report


def _strip_json_volatile(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: _strip_json_volatile(value)
            for key, value in payload.items()
            if key not in {"generated_at_ms", "commit"}
        }
    if isinstance(payload, list):
        return [_strip_json_volatile(value) for value in payload]
    return payload


def _strip_markdown_time(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("- 生成时间")
    )


def _strip_markdown_volatile(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if not line.startswith("- 生成时间") and not line.startswith("- commit")
    )


def _valid_snapshot_file_bundle(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "kind",
        "exists",
        "size_bytes",
        "sha256",
    } or value.get("kind") != "file":
        return False
    if value.get("exists") is False:
        return value.get("size_bytes") is None and value.get("sha256") is None
    return (
        value.get("exists") is True
        and isinstance(value.get("size_bytes"), int)
        and not isinstance(value.get("size_bytes"), bool)
        and value["size_bytes"] >= 0
        and isinstance(value.get("sha256"), str)
        and _SHA256_PATTERN.fullmatch(value["sha256"]) is not None
    )


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_chain_head(value: Any, *, count: int) -> bool:
    if count == 0:
        return value == ""
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _absent_trial_snapshot_matches(
    actual: dict[str, Any], expected: dict[str, Any]
) -> bool:
    ledger = actual.get("ledger")
    provenance = actual.get("provenance")
    oos = actual.get("oos_access")
    if not all(isinstance(value, dict) for value in (ledger, provenance, oos)):
        return False
    guards = ledger.get("append_only_guards")
    counts = ledger.get("row_counts")
    ledger_path = ledger.get("path")
    expected_ledger = expected.get("ledger")
    inputs = actual.get("inputs")
    required_tables = set(_APPEND_ONLY_TABLES)
    return (
        isinstance(expected_ledger, dict)
        and ledger_path == expected_ledger.get("path")
        and ledger.get("exists") is False
        and ledger.get("sha256") is None
        and ledger.get("sqlite_integrity") is None
        and ledger.get("user_version") is None
        and isinstance(counts, dict)
        and set(counts) == required_tables
        and all(_non_negative_int(value) and value == 0 for value in counts.values())
        and isinstance(guards, dict)
        and set(guards) == required_tables
        and all(value == "UNKNOWN" for value in guards.values())
        and isinstance(inputs, dict)
        and isinstance(ledger_path, str)
        and _valid_snapshot_file_bundle(inputs.get(ledger_path))
        and inputs[ledger_path].get("exists") is False
        and provenance.get("table_present") is False
        and provenance.get("chain_valid") is False
        and provenance.get("reason") == "LEDGER_ABSENT"
        and oos.get("table_present") is False
        and oos.get("chain_valid") is False
        and oos.get("reason") == "LEDGER_ABSENT"
        and actual.get("verdict") == "BLOCKED"
        and actual.get("block_reasons") == ["LEDGER_ABSENT"]
        and actual.get("open_gaps") == expected.get("open_gaps")
    )


def _trial_snapshot_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Accept only a self-consistent, append-only prior trial-ledger snapshot."""

    dynamic_fields = {
        "generated_at_ms",
        "commit",
        "inputs",
        "input_hash",
        "ledger",
        "provenance",
        "oos_access",
        "verdict",
        "block_reasons",
        "open_gaps",
    }
    actual_common = {key: value for key, value in actual.items() if key not in dynamic_fields}
    expected_common = {key: value for key, value in expected.items() if key not in dynamic_fields}
    if actual_common != expected_common or not _safety_ok(actual.get("safety_flags", {})):
        return False
    if actual.get("safety_verdict") != "PASS":
        return False

    ledger = actual.get("ledger")
    expected_ledger = expected.get("ledger")
    if not isinstance(ledger, dict) or not isinstance(expected_ledger, dict):
        return False
    if ledger.get("exists") is False:
        return _absent_trial_snapshot_matches(actual, expected)
    if actual.get("block_reasons") != []:
        return False
    static_ledger_fields = {"path", "exists", "sqlite_integrity", "user_version", "append_only_guards"}
    if ledger.get("path") != expected_ledger.get("path"):
        return False
    if (
        ledger.get("exists") is not True
        or ledger.get("sqlite_integrity") != "ok"
        or ledger.get("user_version") != _CURRENT_TRIAL_LEDGER_SCHEMA_VERSION
    ):
        return False
    guards = ledger.get("append_only_guards")
    required_tables = set(_APPEND_ONLY_TABLES)
    if (
        not isinstance(guards, dict)
        or set(guards) != required_tables
        or any(value != "GUARDED" for value in guards.values())
    ):
        return False
    ledger_hash = ledger.get("sha256")
    if not isinstance(ledger_hash, str) or _SHA256_PATTERN.fullmatch(ledger_hash) is None:
        return False
    inputs = actual.get("inputs")
    ledger_path = ledger.get("path")
    if not isinstance(inputs, dict) or not isinstance(ledger_path, str):
        return False
    ledger_input = inputs.get(ledger_path)
    if not _valid_snapshot_file_bundle(ledger_input) or ledger_input.get("sha256") != ledger_hash:
        return False

    row_counts = ledger.get("row_counts")
    expected_counts = expected_ledger.get("row_counts")
    if not isinstance(row_counts, dict) or not isinstance(expected_counts, dict):
        return False
    if set(row_counts) != required_tables or set(expected_counts) != required_tables:
        return False
    if any(not _non_negative_int(value) for value in row_counts.values()):
        return False
    if any(row_counts[key] > expected_counts[key] for key in row_counts):
        return False

    provenance = actual.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != set(expected.get("provenance", {})):
        return False
    if (
        provenance.get("table_present") is not True
        or provenance.get("chain_valid") is not True
        or provenance.get("reason") is not None
    ):
        return False
    attempts = provenance.get("attempts_recorded")
    covered = provenance.get("configs_with_provenance")
    bare = provenance.get("bare_trials")
    total_trials = row_counts.get("trials")
    if not all(_non_negative_int(value) for value in (attempts, covered, bare, total_trials)):
        return False
    if attempts != row_counts.get("trial_provenance") or covered > total_trials:
        return False
    if bare != total_trials - covered:
        return False
    expected_coverage = round(covered / total_trials, 6) if total_trials else 0.0
    if provenance.get("coverage_ratio") != expected_coverage:
        return False
    actor_counts = provenance.get("actor_type_counts")
    status_counts = provenance.get("status_counts")
    if not isinstance(actor_counts, dict) or not isinstance(status_counts, dict):
        return False
    if any(not _non_negative_int(value) for value in (*actor_counts.values(), *status_counts.values())):
        return False
    if sum(actor_counts.values()) != attempts or sum(status_counts.values()) != attempts:
        return False
    max_peeks = provenance.get("max_oos_peek_count")
    if (attempts == 0 and max_peeks is not None) or (
        attempts > 0 and not _non_negative_int(max_peeks)
    ):
        return False
    if not _valid_chain_head(provenance.get("chain_head"), count=attempts):
        return False

    oos = actual.get("oos_access")
    if not isinstance(oos, dict) or set(oos) != set(expected.get("oos_access", {})):
        return False
    if oos.get("table_present") is not True or oos.get("chain_valid") is not True or oos.get("reason") is not None:
        return False
    accesses = oos.get("accesses_recorded")
    accessed_trials = oos.get("trials_with_access")
    if not _non_negative_int(accesses) or not _non_negative_int(accessed_trials):
        return False
    if accesses != row_counts.get("trial_oos_access") or accessed_trials > accesses:
        return False
    if not _valid_chain_head(oos.get("chain_head"), count=accesses):
        return False

    expected_verdict = "PARTIAL" if bare > 0 else "PASS"
    return (
        actual.get("verdict") == expected_verdict
        and actual.get("open_gaps") == expected.get("open_gaps")
    )


def phase13_artifact_matches(
    relative_path: str,
    actual: Any,
    expected: Any,
) -> bool:
    """Compare a Phase 13 artifact without racing declared live projections.

    Static reports remain byte-semantic after volatile timestamps are removed.
    The paper/shadow and trial-ledger reports may lag only their explicitly
    declared live inputs. Embedded manifests must remain self-consistent, every
    static input and common control field must match current code, and safety /
    append-only invariants are re-evaluated from the retained snapshot.
    """

    dynamic_inputs = _DYNAMIC_REPORT_INPUTS.get(relative_path)
    if dynamic_inputs is None:
        return _strip_json_volatile(actual) == _strip_json_volatile(expected)
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    semantically_current = _strip_json_volatile(actual) == _strip_json_volatile(expected)
    if relative_path == "evidence/trial_ledger_integrity.json":
        return semantically_current and _trial_snapshot_matches(actual, expected)
    if semantically_current:
        return True
    actual_inputs = actual.get("inputs")
    expected_inputs = expected.get("inputs")
    if not isinstance(actual_inputs, dict) or not isinstance(expected_inputs, dict):
        return False
    if set(actual_inputs) != set(expected_inputs) or not dynamic_inputs <= set(actual_inputs):
        return False
    if actual.get("input_hash") != _canonical_hash(actual_inputs):
        return False
    if any(not _valid_snapshot_file_bundle(actual_inputs.get(path)) for path in dynamic_inputs):
        return False
    for path in set(actual_inputs) - dynamic_inputs:
        if actual_inputs[path] != expected_inputs[path]:
            return False

    # Paper/shadow source files are mutable caches without an independently
    # signed historical authority. A changed manifest therefore cannot prove
    # that a retained readiness claim was true at that older point in time.
    return False


def _phase_verdict(payload: dict[str, Any]) -> str:
    native = payload.get("verdict")
    if native in {"PASS", "PARTIAL", "BLOCKED"}:
        return native
    status = payload.get("status")
    if status == "READY_FOR_RESEARCH_AUTONOMY":
        return "PASS"
    if status in {"READY_FOR_FORWARD_PAPER", "READY_FOR_SHADOW", "READY_FOR_MICRO_LIVE_REVIEW"}:
        return "PARTIAL"
    return "BLOCKED"


def render_final_audit(reports: dict[str, dict[str, Any]], *, now_ms: int) -> str:
    autonomy_verdict = _phase_verdict(reports["evidence/autonomy_readiness.json"])
    trial_verdict = _phase_verdict(reports["evidence/trial_ledger_integrity.json"])
    garden_verdict = _phase_verdict(reports["evidence/factor_garden_status.json"])
    lifecycle_verdict = _phase_verdict(reports["evidence/alpha_lifecycle_status.json"])
    paper_verdict = _phase_verdict(reports["evidence/paper_shadow_status.json"])
    mexc_verdict = _phase_verdict(reports["evidence/mexc_integration_status.json"])
    chaos_verdict = _phase_verdict(reports["evidence/chaos_recovery_status.json"])
    research_verdicts = (autonomy_verdict, trial_verdict)
    phase5_verdict = (
        "BLOCKED"
        if "BLOCKED" in research_verdicts
        else "PARTIAL"
        if "PARTIAL" in research_verdicts
        else "PASS"
    )
    rows = [
        ("Autonomy Kernel", reports["evidence/autonomy_readiness.json"]["status"]),
        ("Trial Ledger", "NOT_READY" if reports["evidence/trial_ledger_integrity.json"].get("verdict") != "PASS" else "READY_FOR_RESEARCH_AUTONOMY"),
        ("Factor Garden", reports["evidence/factor_garden_status.json"]["status"]),
        ("Alpha Lifecycle", reports["evidence/alpha_lifecycle_status.json"]["status"]),
        ("Paper / Shadow", reports["evidence/paper_shadow_status.json"]["status"]),
        ("MEXC Integration", reports["evidence/mexc_integration_status.json"]["status"]),
        ("Chaos / Recovery", reports["evidence/chaos_recovery_status.json"]["status"]),
    ]
    phase_rows = [
        (0, "基线与安全冻结", "PASS", "evidence/autonomy_baseline.json；evidence/validation_report.json"),
        (1, "数据血缘与统一门禁", "PASS", "evidence/data_quality_admission.json；evidence/raw_data_lineage_backfill.json"),
        (2, "Autonomy Kernel", autonomy_verdict, "evidence/autonomy_readiness.json"),
        (3, "全局 Trial Ledger", trial_verdict, "evidence/trial_ledger_integrity.json"),
        (4, "Factor Garden", garden_verdict, "evidence/factor_garden_status.json"),
        (5, "Alpha Factory 与 Challenge Council", phase5_verdict, "evidence/autonomy_readiness.json；Trial Ledger"),
        (6, "三状态生命周期", lifecycle_verdict, "evidence/alpha_lifecycle_status.json"),
        (7, "Paper 与 Shadow", paper_verdict, "evidence/paper_shadow_status.json"),
        (8, "MEXC 行情/只读/Test Order", mexc_verdict, "evidence/mexc_integration_status.json"),
        (9, "Micro-Live Broker Adapter", "BLOCKED", "默认禁用；缺外部 Deployment Grant、真实 broker 与人工审查"),
        (10, "Alpha Health、衰减与退役", lifecycle_verdict, "evidence/alpha_lifecycle_status.json"),
        (11, "前端重构", autonomy_verdict, "validation_report frontend typecheck/eslint/tests/build"),
        (12, "Chaos、恢复与运维", chaos_verdict, "evidence/chaos_recovery_status.json"),
        (13, "最终审计", "PASS", "本报告与 7 个机器状态制品；总体状态仍 NOT_READY"),
    ]
    delivery_rows = [
        (1, "重启后从事件账本和交易所状态恢复", "PARTIAL", "本地事件/REST 恢复已测；真实交易所恢复待外部 smoke"),
        (2, "重试不产生重复订单", "PASS", "幂等 client id、unknown-order ledger、禁止盲重试"),
        (3, "数据中断自动停止新增风险", "PASS", "data admission/lineage 与 fail-closed risk gates"),
        (4, "Paper/Shadow/Micro-Live 共用信号核心", "PASS", "统一 intent/dry-run/live pipeline 接缝"),
        (5, "所有研究尝试进入 Trial Ledger", trial_verdict, "以 trial_ledger_integrity.json 的当前覆盖率和阻断原因为准"),
        (6, "AI 不触碰 Secret/资本审批/真实订单", "PASS", "non-human guardrails、无凭证边界、人工 mandate"),
        (7, "订单/成交/余额/仓位/PnL 可对账", "PARTIAL", "本地与 REST 对账已测；真实 MEXC 账户未验证"),
        (8, "部署期间 Alpha 版本不可修改", "PASS", "version pin 与 deployment gate"),
        (9, "Alpha 衰减自动降权或暂停", "PASS", "health/maintenance/degradation sweep"),
        (10, "Retired 版本不能原地复活", "PASS", "三状态生命周期 guard"),
        (11, "增资/恢复实盘/硬风险修改须真人批准", "PASS", "多方审批、强身份接缝、默认 flags 关闭"),
        (12, "前端展示研究/部署/健康/证据", "PASS", "只读 console + provenance ribbons + frontend gate"),
        (13, "运行结果分层标识", "PASS", "Historical/Paper/Shadow/Micro-Live/Live 契约与状态"),
        (14, "外部未完成条件显示 BLOCKED", "PASS", "Phase 3/7/8/9/12 与 top-tier blockers"),
        (15, "长期自治且资本权力在人类", "PASS" if all(verdict == "PASS" for verdict in (autonomy_verdict, trial_verdict, garden_verdict, lifecycle_verdict)) else "BLOCKED", "研究自治证据必须全部通过；资本权限仍由真人门控"),
    ]
    lines = [
        "# QUANTOS 自治系统 Phase 13 最终审计",
        "",
        "> 本报告由 `scripts/generate_727_final_audit.py` 只读生成。它不授予任何订单、资金或部署权限。",
        "",
        f"- 生成时间（Unix ms）：`{now_ms}`",
        f"- commit：`{reports['evidence/autonomy_readiness.json']['commit']}`",
        "- 当前 readiness：`L2_DRY_RUN_ONLY`",
        "- 当前总状态：`NOT_READY`",
        "- 不生成任何开放真实实盘的状态或权限",
        "",
        "## Phase 13 证据状态",
        "",
        "| 能力 | 当前状态 |",
        "|---|---|",
        *[f"| {name} | `{status}` |" for name, status in rows],
        "",
        "## Phase 0–13 逐阶段验收",
        "",
        "| Phase | 范围 | 裁决 | 权威证据 / 原因 |",
        "|---:|---|---|---|",
        *[
            f"| {phase} | {name} | `{status}` | {evidence} |"
            for phase, name, status, evidence in phase_rows
        ],
        "",
        "> `PASS` 只表示该阶段在当前 L2 范围内的仓库验收通过；不授予 Micro-Live/Live 权限。",
        "",
        "## §25 最终交付标准逐条审计",
        "",
        "| # | 标准 | 裁决 | 证据 / 边界 |",
        "|---:|---|---|---|",
        *[
            f"| {number} | {standard} | `{status}` | {evidence} |"
            for number, standard, status, evidence in delivery_rows
        ],
        "",
        "## 已完成且可由仓库证明的部分",
        "",
        "- Autonomy Kernel、Factor Garden、Alpha 生命周期研究面已有源码、测试和当前验证入口。",
        "- Trial Ledger 继续使用既有权威账本；append-only、哈希链和 provenance 覆盖率按只读制品报告。",
        "- Shadow 观测链可验证且未触碰凭证或订单路径；其状态不等于成交或 Paper 通过。",
        "- MEXC M1/M2、未知订单/启动恢复安全内核与 M4 user stream 接缝已落地，但保持结构性拒绝。",
        "",
        "## 当前阻断",
        "",
        "- Trial Ledger 历史 trial 的全量 provenance 仍不完整；家族 FDR 已作为全局 BHY 之外的附加晋级门，官方 OOS 路径已接入追加式访问哈希链。",
        "- Paper marks-only replay 尚未满足完整研究风险包和 8 个有效前向周期。",
        "- MEXC 现货没有 testnet；user stream 代码已完成，但真实 WebSocket、凭证账户回查和外部测试订单证据尚未完成。",
        "- Phase 12 的交易所网络分区、WebSocket 重连、部分成交/限流和跨进程权威场景没有可验证外部证据。",
        "- 外部强身份、多人审批、进程隔离/出进程签名、真实 broker 与人工 Deployment Grant 仍是外部条件。",
        "",
        "## 安全结论",
        "",
        "当前仅允许 L2 研究/干跑/只读 Shadow。`LIVE_TRADING_ENABLED`、`REAL_BROKER_ENABLED`、",
        "`ALLOW_ORDER_SUBMISSION`、`ALLOW_AI_DIRECT_TRADING` 保持 `false`，",
        "`REQUIRE_HUMAN_APPROVAL` 保持 `true`。本审计不会因为测试通过而放开真实订单。",
        "",
        "## 下一步",
        "",
        "继续让新增 driver 复用已接线的 Trial provenance 入口，并补齐 Paper 前向证据和 Phase 12 外部故障证据；",
        "在这些证据与人工审批完成前，",
        "任何 Micro-Live 或 Live 晋级都必须保持 `NOT_READY`。",
        "",
    ]
    return "\n".join(lines)


def build_phase13(*, root: Path = ROOT, now_ms: int | None = None) -> tuple[dict[str, dict[str, Any]], str]:
    root = root.resolve()
    if now_ms is None:
        now_ms = int(time.time_ns() // 1_000_000)
    flags = _safety_flags()
    validation = _validation(root)
    reports = {
        "evidence/autonomy_readiness.json": _autonomy(root, now_ms, flags, validation),
        "evidence/trial_ledger_integrity.json": _trial_ledger(root, now_ms),
        "evidence/factor_garden_status.json": _factor_garden(root, now_ms, flags, validation),
        "evidence/alpha_lifecycle_status.json": _alpha_lifecycle(root, now_ms, flags, validation),
        "evidence/paper_shadow_status.json": _paper_shadow(root, now_ms, flags, validation),
        "evidence/mexc_integration_status.json": _mexc(root, now_ms, flags, validation),
        "evidence/chaos_recovery_status.json": _chaos(root, now_ms, flags, validation),
    }
    markdown = render_final_audit(reports, now_ms=now_ms)
    return reports, markdown


def _write_json(
    path: Path, payload: dict[str, Any], *, relative_path: str | None = None
) -> None:
    if relative_path is not None and path.is_file():
        try:
            actual = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            actual = None
        if (
            isinstance(actual, dict)
            and actual.get("commit") == payload.get("commit")
            and phase13_artifact_matches(relative_path, actual, payload)
        ):
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _write_text(path: Path, text: str) -> None:
    if path.is_file():
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError:
            actual = None
        if actual is not None and _strip_markdown_time(actual) == _strip_markdown_time(text):
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate QUANTOS 727 Phase 13 evidence")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--check", action="store_true", help="check tracked artifacts without writing")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    reports, markdown = build_phase13(root=root)
    markdown_path = root / "docs/reports/QUANTOS_AUTONOMY_FINAL_AUDIT.md"
    if args.check:
        for relative_path, expected in reports.items():
            path = root / relative_path
            if not path.is_file():
                print(f"missing Phase 13 artifact: {relative_path}", file=sys.stderr)
                return 1
            try:
                actual = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                print(f"unreadable Phase 13 artifact {relative_path}: {exc}", file=sys.stderr)
                return 1
            if not phase13_artifact_matches(relative_path, actual, expected):
                print(f"drifted Phase 13 artifact: {relative_path}", file=sys.stderr)
                return 1
        if not markdown_path.is_file() or _strip_markdown_volatile(markdown_path.read_text(encoding="utf-8")) != _strip_markdown_volatile(markdown):
            print(f"drifted Phase 13 report: {markdown_path}", file=sys.stderr)
            return 1
        print("QUANTOS 727 Phase 13 evidence: current")
        return 0

    for relative_path, payload in reports.items():
        _write_json(root / relative_path, payload, relative_path=relative_path)
    _write_text(markdown_path, markdown)
    statuses = ", ".join(
        f"{Path(path).stem}={payload.get('status', payload.get('phase13_status', payload.get('verdict')))}"
        for path, payload in reports.items()
    )
    print(f"QUANTOS 727 Phase 13 evidence generated: {statuses}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
