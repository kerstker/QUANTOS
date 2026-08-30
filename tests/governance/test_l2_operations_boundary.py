from __future__ import annotations

import ast
import json
from pathlib import Path

from tools.l2_backup import DEFAULT_SOURCES, SOURCE_CONTRACT_VERSION


ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = (node.module or "",)
        else:
            continue
        for name in names:
            modules.add(name)
    return modules


def _forbidden_network_imports(modules: set[str]) -> set[str]:
    forbidden_roots = {"ccxt", "httpx", "requests", "socket"}
    return {
        module
        for module in modules
        if module.split(".", 1)[0] in forbidden_roots
        or (module == "urllib" or module.startswith("urllib.request"))
        or module.startswith("live.pipeline")
        or module.startswith("live.exchange")
    }


def _call_names(tree: ast.AST) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    return calls


def _calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return _call_names(tree)


def test_l2_backup_sources_exclude_credentials_and_live_submission_state():
    actual = {
        source.relative_path.replace("\\", "/"): (source.role, source.required)
        for source in DEFAULT_SOURCES
    }
    assert actual == {
        "data/cache/alpha_maintenance.db": ("alpha_maintenance", False),
        "data/cache/alpha_production_workflow.db": (
            "alpha_production_workflow",
            True,
        ),
        "data/cache/approvals.db": ("approval_authority", False),
        "data/cache/binance_book_depth_archive.db": (
            "binance_book_depth_archive_authority",
            False,
        ),
        "data/cache/bybit_linear_events.db": (
            "bybit_public_linear_event_authority",
            False,
        ),
        "data/cache/bybit_market_resilience.db": (
            "outcome_free_market_resilience_authority",
            False,
        ),
        "data/cache/cross_venue_liquidation_health.json": (
            "cross_venue_liquidation_health",
            False,
        ),
        "data/cache/artifact_admission.db": (
            "artifact_admission_authority",
            False,
        ),
        "data/cache/data_quality_admission.db": ("data_quality_admission", True),
        "data/cache/deployment_state.db": ("deployment_state", False),
        "data/cache/defillama_tvl_vintages.db": (
            "defillama_tvl_forward_vintages",
            False,
        ),
        "data/cache/defillama_stablecoin_vintages.db": (
            "defillama_stablecoin_forward_vintages",
            False,
        ),
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
        "data/cache/mft_testnet_soak.db": (
            "mft_testnet_soak_evidence_authority",
            False,
        ),
        "data/cache/okx_borrow_vintages.db": (
            "okx_public_borrow_forward_vintages",
            False,
        ),
        "data/cache/paper_candidate_registry.db": (
            "factory_candidate_admission",
            False,
        ),
        "data/cache/paper_track.db": ("paper_track", True),
        "data/cache/realtime_alpha_execution_paper.db": (
            "realtime_engineering_execution_paper",
            True,
        ),
        "data/cache/research_runs.db": ("research_run_registry", True),
        "data/cache/vendor_delivery_history.db": (
            "vendor_delivery_history_authority",
            False,
        ),
        "data/stocks/research.db": ("ashare_research_database", True),
        "data/stocks/snapshots/alpha_snapshot.json": (
            "ashare_alpha_research_snapshot",
            True,
        ),
        "data/stocks/snapshots/klines_snapshot.json": (
            "ashare_klines_snapshot",
            True,
        ),
        "data/stocks/snapshots/paper_track_snapshot.json": (
            "ashare_paper_track_snapshot",
            True,
        ),
        "data/stocks/stocks.db": ("ashare_market_data_authority", True),
        "evidence/cross_venue_liquidation_preregistration_764e5b5.json": (
            "cross_venue_liquidation_preregistration",
            False,
        ),
        "evidence/borrow_pressure_preregistration_5eed0f1.json": (
            "borrow_pressure_preregistration",
            False,
        ),
    }
    assert all(
        path.startswith("data/cache/")
        or path.startswith("data/stocks/")
        or path.startswith("evidence/")
        for path in actual
    )
    assert {
        path for path in actual if path.startswith("evidence/")
    } == {
        "evidence/borrow_pressure_preregistration_5eed0f1.json",
        "evidence/cross_venue_liquidation_preregistration_764e5b5.json",
    }
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
        "version": SOURCE_CONTRACT_VERSION,
        "sources": sorted(
            (
                {
                    "relative_path": source.relative_path.replace("\\", "/"),
                    "required": source.required,
                    "role": source.role,
                    "snapshot_policy": source.snapshot_policy,
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
    assert not _forbidden_network_imports(imports)
    combined = (
        backup_path.read_text(encoding="utf-8")
        + watchdog_path.read_text(encoding="utf-8")
    ).lower()
    assert "submit_order" not in combined
    assert "cancel_order" not in combined
    assert "--submit" not in combined
    assert "--act" not in combined


_FORBIDDEN_ENTRYPOINT_CALLS = {
    "submit_order",
    "place_order",
    "cancel_order",
    "withdraw",
    "transfer",
    "connect",
    "connect_broker",
    "_load_credentials",
    "load_credentials",
    "_load_key",
    "load_key",
    "load_api_key",
    "read_secret",
    "load_secret",
}


def test_l2_schedule_entrypoint_allows_non_secret_security_proof_fields():
    tree = ast.parse("result = {'credentials_accessed': False}")

    assert not _call_names(tree) & _FORBIDDEN_ENTRYPOINT_CALLS


def test_l2_schedule_entrypoint_policy_rejects_import_and_call_variants(tmp_path):
    fixture = tmp_path / "dangerous.py"
    fixture.write_text(
        """
import benign, socket
import urllib.request
from requests.sessions import Session
import ccxt.pro

def run(client):
    client.submit_order()
    client.connect()
    load_credentials()
    load_secret()
""",
        encoding="utf-8",
    )

    assert _forbidden_network_imports(_imports(fixture)) == {
        "socket",
        "urllib.request",
        "requests.sessions",
        "ccxt.pro",
    }
    assert _calls(fixture) & _FORBIDDEN_ENTRYPOINT_CALLS == {
        "submit_order",
        "connect",
        "load_credentials",
        "load_secret",
    }


def test_l2_schedule_entrypoint_has_no_broker_network_or_secret_dependency():
    entrypoint = ROOT / "tools" / "l2_schedule_entrypoint.py"
    imports = _imports(entrypoint)
    assert not _forbidden_network_imports(imports)
    assert not _calls(entrypoint) & _FORBIDDEN_ENTRYPOINT_CALLS
    source = entrypoint.read_text(encoding="utf-8").lower()
    assert "--submit" not in source
    assert "--act" not in source


def test_paper_evidence_producers_have_no_broker_network_or_order_surface():
    producers = (
        ROOT / "tools" / "paper_alpha_evaluation.py",
        ROOT / "tools" / "paper_research_risk_bundle.py",
        ROOT / "tools" / "paper_shadow_auto.py",
        ROOT / "tools" / "paper_shadow_realtime.py",
        ROOT / "tools" / "paper_operational_evidence.py",
    )
    for producer in producers:
        imports = _imports(producer)
        assert not _forbidden_network_imports(imports)
        calls = {name.lower() for name in _calls(producer)}
        assert not calls & {
            "submit_order",
            "place_order",
            "cancel_order",
            "withdraw",
            "transfer",
            "connect_broker",
        }


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
