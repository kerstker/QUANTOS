import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]


def test_console_http_adapter_is_owned_by_console_domain():
    old_root = ROOT / "services/research/console_api"
    new_root = ROOT / "services/console/api"

    assert not list(old_root.glob("*.py"))
    assert (new_root / "main.py").is_file()
    assert (new_root / "data_port.py").is_file()
    assert (new_root / "risk_port.py").is_file()

_ARTIFACT_SNAPSHOT_ROUTES = {
    "/api/v1/console/carry-readiness",
    "/api/v1/console/dry-run",
    "/api/v1/console/fx-research",
    "/api/v1/console/gonogo",
    "/api/v1/console/ic-panel",
    "/api/v1/console/lance-1m-validation",
    "/api/v1/console/live-trading",
    "/api/v1/console/research-findings",
}

_REGISTRY_SNAPSHOT_ROUTES = {
    "/api/v1/console/experiments",
    "/api/v1/console/lifecycle-monitor",
}

_DATA_CATALOG_ROUTES = {"/api/v1/console/data-catalog"}

_SYSTEM_VIEW_ROUTES = {
    "/health",
    "/api/v1/console/agents",
    "/api/v1/console/events",
    "/api/v1/console/phase-3a-readiness",
    "/api/v1/console/system-health",
}

_ARCHITECTURE_VIEW_ROUTES = {
    "/api/v1/console/architecture",
    "/api/v1/console/system-completeness",
}

_MODEL_EVIDENCE_ROUTES = {
    "/api/v1/console/evidence-ledger",
    "/api/v1/console/model-risk-review",
}

_REVIEW_ACCESS_ROUTES = {
    "/api/v1/console/access-control",
    "/api/v1/console/reviewer-workflow",
}

_MONITORING_ROUTES = {"/api/v1/console/monitoring-drift"}
_INCIDENT_ROUTES = {"/api/v1/console/incident-response"}
_PROMOTION_ROUTES = {"/api/v1/console/promotion-control"}
_RELEASE_COMMITTEE_ROUTES = {"/api/v1/console/release-committee"}

_BACKTEST_VIEW_ROUTES = {
    "/api/v1/console/backtest-explainability",
    "/api/v1/console/scenario-stress",
}

_ALPHA_LIBRARY_ROUTES = {
    "/api/v1/console/alpha-graph",
    "/api/v1/console/alpha-zoo",
    "/api/v1/console/crypto-live",
    "/api/v1/console/dsl-explorer",
    "/api/v1/console/population-optimizer",
}

_RESEARCH_RUN_ROUTES = {
    "/api/v1/console/backtest-results",
    "/api/v1/console/research-run",
    "/api/v1/console/research-runs",
}

_PRIMARY_VIEW_ROUTES = {
    "/api/v1/console/alpha-factory",
    "/api/v1/console/computation-lab",
    "/api/v1/console/overview",
    "/api/v1/console/paper-trading",
    "/api/v1/console/portfolio",
    "/api/v1/console/risk-dashboard",
    "/api/v1/console/safety-primitives",
    "/api/v1/console/validation",
}

_MOVED_ARTIFACT_PAYLOADS = {
    "_carry_readiness_payload",
    "_dry_run_payload",
    "_fx_research_payload",
    "_gonogo_payload",
    "_ic_panel_payload",
    "_lance_1m_validation_payload",
    "_live_trading_payload",
    "_research_findings_payload",
}

_MOVED_REGISTRY_PAYLOADS = {
    "_degradation_thresholds_snapshot",
    "_experiments_payload",
    "_lifecycle_monitor_payload",
    "_lifecycle_rows_real",
    "_lifecycle_rows_synthetic",
    "_lifecycle_summary",
    "_load_experiment_store",
    "_load_paper_track_store",
    "_real_experiment_rows",
}

_MOVED_DATA_CATALOG_PAYLOADS = {
    "_data_catalog_payload",
    "_dataset_checks",
    "_enumerate_real_datasets",
}

_MOVED_SYSTEM_PAYLOADS = {
    "_agents_payload",
    "_events_payload",
    "_phase3a_readiness_payload",
    "_system_health_payload",
}

_MOVED_ARCHITECTURE_PAYLOADS = {
    "_architecture_payload",
    "_repo_inventory",
    "_system_completeness_payload",
}

_MOVED_MODEL_EVIDENCE_PAYLOADS = {
    "_evidence_ledger_payload",
    "_model_risk_review_payload",
}

_MOVED_REVIEW_ACCESS_PAYLOADS = {
    "_access_control_payload",
    "_reviewer_workflow_payload",
}

_MOVED_MONITORING_PAYLOADS = {"_monitoring_drift_payload"}
_MOVED_INCIDENT_PAYLOADS = {"_incident_response_payload"}
_MOVED_PROMOTION_PAYLOADS = {"_promotion_control_payload"}
_MOVED_RELEASE_COMMITTEE_PAYLOADS = {"_release_committee_payload"}

_MOVED_BACKTEST_PAYLOADS = {
    "_backtest_explainability_payload",
    "_drawdown_periods",
    "_monthly_returns",
    "_scenario_stress_payload",
    "_segment_returns",
}

_MOVED_ALPHA_LIBRARY_PAYLOADS = {
    "_alpha_graph_payload",
    "_alpha_zoo_payload",
    "_alpha_zoo_rows_real",
    "_alpha_zoo_rows_synthetic",
    "_corr",
    "_crypto_live_payload",
    "_dsl_explorer_payload",
    "_expr_fields",
    "_inverse_vol_weights",
    "_population_optimizer_payload",
    "_short_factor_label",
    "_weighted_max_dd",
    "_zoo_tags",
}

_MOVED_RESEARCH_RUN_PAYLOADS = {
    "_backtest_results_payload",
    "_latest_done_run",
    "_overview_active_runs",
    "_run_summary",
    "_runs_registry",
}

_MOVED_PRIMARY_PAYLOADS = {"_overview_payload"}


class _FakeRiskPort:
    def risk_decision(self, alpha_id, returns, equity_points, capacity):
        return {"decision": "FAKE"}

    def monitoring_snapshot(self, *, audit_db, kill_switch_state):
        return {"provenance": "FAKE"}

    def safety_primitives(self, *, kill_switch_state):
        return {"safety_mode": "FAKE"}


def test_risk_runtime_registry_fails_closed_and_cannot_be_replaced():
    from services.console.api.risk_port import (
        RiskConsoleRuntime,
        RiskConsoleUnavailable,
    )

    runtime = RiskConsoleRuntime()
    with pytest.raises(RiskConsoleUnavailable, match="services.console.main"):
        runtime.require()

    port = _FakeRiskPort()
    runtime.configure(port)
    assert runtime.require() is port
    runtime.configure(port)
    with pytest.raises(RuntimeError, match="already configured"):
        runtime.configure(_FakeRiskPort())


def test_research_console_package_does_not_import_risk():
    offenders = []
    root = ROOT / "services" / "research" / "console_api"
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "services.risk" or alias.name.startswith("services.risk."):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            if module == "services.risk" or (isinstance(module, str) and module.startswith("services.risk.")):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, offenders


def test_artifact_snapshot_router_is_bounded_read_only_and_extracted():
    from services.console.api.artifact_snapshots import router

    module_path = ROOT / "services/console/api/artifact_snapshots.py"
    assert len(module_path.read_text(encoding="utf-8").splitlines()) < 800

    routes = {route.path: route.methods for route in router.routes}
    assert set(routes) == _ARTIFACT_SNAPSHOT_ROUTES
    assert all(methods == {"GET"} for methods in routes.values())

    main_path = ROOT / "services/console/api/main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    remaining = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not (_MOVED_ARTIFACT_PAYLOADS & remaining)


@pytest.mark.parametrize(
    ("module_name", "expected_routes", "moved_payloads"),
    [
        (
            "services.console.api.registry_snapshots",
            _REGISTRY_SNAPSHOT_ROUTES,
            _MOVED_REGISTRY_PAYLOADS,
        ),
        (
            "services.console.api.data_catalog",
            _DATA_CATALOG_ROUTES,
            _MOVED_DATA_CATALOG_PAYLOADS,
        ),
        (
            "services.console.api.system_views",
            _SYSTEM_VIEW_ROUTES,
            _MOVED_SYSTEM_PAYLOADS,
        ),
        (
            "services.console.api.architecture_views",
            _ARCHITECTURE_VIEW_ROUTES,
            _MOVED_ARCHITECTURE_PAYLOADS,
        ),
        (
            "services.console.api.model_evidence_views",
            _MODEL_EVIDENCE_ROUTES,
            _MOVED_MODEL_EVIDENCE_PAYLOADS,
        ),
        (
            "services.console.api.review_access_views",
            _REVIEW_ACCESS_ROUTES,
            _MOVED_REVIEW_ACCESS_PAYLOADS,
        ),
        (
            "services.console.api.monitoring_views",
            _MONITORING_ROUTES,
            _MOVED_MONITORING_PAYLOADS,
        ),
        (
            "services.console.api.incident_views",
            _INCIDENT_ROUTES,
            _MOVED_INCIDENT_PAYLOADS,
        ),
        (
            "services.console.api.promotion_views",
            _PROMOTION_ROUTES,
            _MOVED_PROMOTION_PAYLOADS,
        ),
        (
            "services.console.api.release_committee_views",
            _RELEASE_COMMITTEE_ROUTES,
            _MOVED_RELEASE_COMMITTEE_PAYLOADS,
        ),
        (
            "services.console.api.backtest_views",
            _BACKTEST_VIEW_ROUTES,
            _MOVED_BACKTEST_PAYLOADS,
        ),
        (
            "services.console.api.alpha_library_views",
            _ALPHA_LIBRARY_ROUTES,
            _MOVED_ALPHA_LIBRARY_PAYLOADS,
        ),
        (
            "services.console.api.research_run_views",
            _RESEARCH_RUN_ROUTES,
            _MOVED_RESEARCH_RUN_PAYLOADS,
        ),
        (
            "services.console.api.primary_views",
            _PRIMARY_VIEW_ROUTES,
            _MOVED_PRIMARY_PAYLOADS,
        ),
    ],
)
def test_extracted_console_routers_are_bounded_and_get_only(module_name, expected_routes, moved_payloads):
    import importlib

    module = importlib.import_module(module_name)
    module_path = Path(module.__file__)
    assert len(module_path.read_text(encoding="utf-8").splitlines()) < 800

    routes = {route.path: route.methods for route in module.router.routes}
    assert set(routes) == expected_routes
    assert all(methods == {"GET"} for methods in routes.values())

    main_path = ROOT / "services/console/api/main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    remaining = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not (moved_payloads & remaining)


def test_console_composition_root_is_bounded_and_contains_no_route_implementations():
    main_path = ROOT / "services/console/api/main.py"
    source = main_path.read_text(encoding="utf-8")
    assert len(source.splitlines()) < 800

    tree = ast.parse(source, filename=str(main_path))
    route_implementations = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "app"
            ):
                route_implementations.append(node.name)
    assert not route_implementations


def test_official_console_composition_runs_real_risk_and_safety_engines():
    from services.console.main import app

    client = TestClient(app)
    risk = client.get("/api/v1/console/risk-dashboard?alpha_id=alpha-1")
    assert risk.status_code == 200
    assert risk.json()["primary_report"]["gates_total"] == 9

    safety = client.get("/api/v1/console/safety-primitives")
    assert safety.status_code == 200
    assert safety.json()["data_provenance"] == "REAL_SAFETY_PRIMITIVES"
