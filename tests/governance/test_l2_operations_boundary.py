from __future__ import annotations

import ast
import json
from pathlib import Path

from tools.l2_backup import DEFAULT_SOURCES


ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }


def test_l2_backup_sources_exclude_credentials_and_live_submission_state():
    actual = {
        source.relative_path.replace("\\", "/"): (source.role, source.required)
        for source in DEFAULT_SOURCES
    }
    assert actual == {
        "data/cache/alpha_maintenance.db": ("alpha_maintenance", False),
        "data/cache/approvals.db": ("approval_authority", False),
        "data/cache/artifact_admission.db": (
            "artifact_admission_authority",
            False,
        ),
        "data/cache/data_quality_admission.db": ("data_quality_admission", True),
        "data/cache/deployment_state.db": ("deployment_state", False),
        "data/cache/domain_event_projection.db": ("domain_event_projection", True),
        "data/cache/domain_events.db": ("domain_event_authority", True),
        "data/cache/dry_run.db": ("dry_run_ledger", True),
        "data/cache/experiment_registry.db": ("experiment_registry", True),
        "data/cache/governance_audit.db": ("governance_audit", True),
        "data/cache/identity_authorization.db": (
            "identity_authorization_authority",
            False,
        ),
        "data/cache/l2_restore_history.db": (
            "l2_restore_history_authority",
            True,
        ),
        "data/cache/live_kill_switch.db": ("kill_switch_authority", False),
        "data/cache/live_refresh_history.db": ("data_refresh_history", True),
        "data/cache/paper_track.db": ("paper_track", True),
        "data/cache/research_runs.db": ("research_run_registry", True),
        "data/cache/vendor_delivery_history.db": (
            "vendor_delivery_history_authority",
            False,
        ),
    }
    assert all(path.startswith("data/cache/") for path in actual)
    assert all(not path.startswith("key/") for path in actual)
    assert all(
        "secret" not in path.lower() and "credential" not in path.lower()
        for path in actual
    )


def test_top_tier_backup_and_restore_require_the_exact_l2_source_contract():
    registry = json.loads(
        (ROOT / "config" / "top_tier_requirements.json").read_text(
            encoding="utf-8"
        )
    )
    requirements = {item["id"]: item for item in registry["requirements"]}
    backup = requirements["OPS.BACKUP.ENCRYPTED"]
    restore = requirements["OPS.RESTORE.DRILL"]
    expected_contract = {
        "version": 2,
        "sources": sorted(
            (
                {
                    "relative_path": source.relative_path.replace("\\", "/"),
                    "required": source.required,
                    "role": source.role,
                }
                for source in DEFAULT_SOURCES
            ),
            key=lambda item: item["relative_path"],
        ),
    }

    backup_args = backup["checker_args"]
    assert backup["checker"] == "l2_backup_evidence"
    assert backup_args["path"] == "evidence/l2_backup_latest.json"
    assert backup_args["expected_source_contract"] == expected_contract
    assert "minimum_required_sources" not in backup_args

    restore_args = restore["checker_args"]
    assert restore["checker"] == "l2_restore_history_evidence"
    assert restore_args["history_path"] == "data/cache/l2_restore_history.db"
    assert restore_args["checkpoint_path"] == (
        "evidence/l2_restore_history_checkpoint.json"
    )
    assert restore_args["trust_registry_path"] == (
        "config/l2_restore_checkpoint_trust_registry.json"
    )
    assert restore_args["expected_source_contract"] == expected_contract
    assert "minimum_required_sources" not in restore_args
    assert "pass_maturity" not in restore_args
    assert restore["target_maturity"] == "M4"
    assert "operation" in restore["required_evidence"]
    assert "restore_drill_latest.json" not in json.dumps(restore, sort_keys=True)
    trust_registry = json.loads(
        (ROOT / "config" / "l2_restore_checkpoint_trust_registry.json").read_text(
            encoding="utf-8"
        )
    )
    assert trust_registry == {"schema_version": 1, "keys": []}
    restore_tool = (ROOT / "tools" / "restore_drill.py").read_text(encoding="utf-8")
    assert "generic_restore_drill_latest.json" in restore_tool


def test_l2_backup_and_scheduler_watchdog_have_no_broker_or_network_dependency():
    backup_path = ROOT / "tools" / "l2_backup.py"
    watchdog_path = ROOT / "tools" / "l2_scheduler_watchdog.py"
    imports = _imports(backup_path) | _imports(watchdog_path)
    forbidden_import_roots = {
        "ccxt",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "live.pipeline",
        "live.exchange",
    }
    assert not imports & forbidden_import_roots
    combined = (
        backup_path.read_text(encoding="utf-8")
        + watchdog_path.read_text(encoding="utf-8")
    ).lower()
    assert "submit_order" not in combined
    assert "cancel_order" not in combined
    assert "--submit" not in combined
    assert "--act" not in combined


def test_scheduler_watchdog_does_not_import_any_monitored_worker():
    imports = _imports(ROOT / "tools" / "l2_scheduler_watchdog.py")
    monitored = {
        f"tools.{module}"
        for module in {
            "alpha_maintenance_sweep",
            "crypto_factory_loop",
            "domain_event_ops",
            "forward_track_advance",
            "gonogo_report",
            "ic_panel_research",
            "l2_backup",
            "domain_event_seal",
            "artifact_admission_seal",
            "external_heartbeat",
        }
    }
    assert not imports & monitored
