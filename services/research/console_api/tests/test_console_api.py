from fastapi.testclient import TestClient

from services.research.console_api.main import app


client = TestClient(app)


def test_health_read_only():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "READ_ONLY"


def test_computation_lab_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/computation-lab?alpha_id=alpha-1&cost_bps=18&stress=1.2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["metrics"]["gross_sharpe"] != 0
    assert len(payload["equity_curve"]) == 120
    assert len(payload["drawdown_curve"]) == 120
    assert payload["risk_report"]["gates_total"] == 9
    assert any(item["id"] == "IC" for item in payload["lineage"])


def test_overview_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["stats"]["alpha_candidates"] == 12
    assert len(payload["funnel"]) == 6
    assert payload["agents"][0]["state"] == "RUNNING"


def test_agents_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/agents")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["summary"]["active"] == 1
    assert payload["summary"]["total_runs"] == 85
    assert len(payload["agents"]) == 3
    assert payload["agents"][0]["state"] == "RUNNING"


def test_events_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/events")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert len(payload["events"]) == 12
    assert payload["events"][0]["event_type"].startswith("research.")
    assert payload["events"][0]["payload"]["read_only"] is True


def test_phase3a_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/phase-3a-readiness")
    assert response.status_code == 200
    payload = response.json()
    readiness = payload["readiness"]
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert readiness["current_level"] == "L2_DRY_RUN_ONLY"
    assert readiness["decision"] == "NO_GO"
    assert readiness["phase_status"] == "BLOCKED"
    assert readiness["implementation_authorized"] is False
    assert readiness["approved_count"] == 10
    assert readiness["total_count"] == 15
    assert len(readiness["blockers"]) == 5


def test_architecture_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/architecture")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["current_level"] == "L2_DRY_RUN_ONLY"
    assert payload["phase3a_decision"] == "NO_GO"
    assert payload["implementation_authorized"] is False
    assert payload["safety_boundary"]["execution_boundary"] == "LIVE_TRADING_DISALLOWED"
    assert len(payload["modules"]) >= 8
    assert len(payload["data_flows"]) >= 6
    assert any(module["id"] == "console-api" for module in payload["modules"])
    assert any(gap["id"] == "G01" for gap in payload["gaps"])


def test_experiments_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/experiments")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["registry_mode"] == "READ_ONLY_RESEARCH_REGISTRY"
    assert payload["summary"]["total_experiments"] == 10
    assert payload["summary"]["artifact_count"] >= 30
    assert len(payload["experiments"]) == 10
    first = payload["experiments"][0]
    assert first["execution_mode"] == "L2_DRY_RUN_ONLY"
    assert first["config"]["config_hash"]
    assert first["reproducibility_hash"]
    assert len(first["artifacts"]) >= 3
    assert payload["lineage_policy"]["mutable_records"] is False


def test_data_catalog_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/data-catalog")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["catalog_mode"] == "READ_ONLY_DATA_CATALOG"
    assert payload["feature_store_mode"] == "OFFLINE_POINT_IN_TIME_RESEARCH"
    assert payload["summary"]["datasets"] == 5
    assert payload["summary"]["features"] == 8
    assert payload["summary"]["quality_checks"] == 15
    assert payload["summary"]["point_in_time_safe_features"] == 8
    assert len(payload["datasets"]) == 5
    assert len(payload["features"]) == 8
    assert all(dataset["content_hash"] for dataset in payload["datasets"])
    assert all(feature["lineage_hash"] for feature in payload["features"])
    assert any(edge["kind"] == "FEATURE_TO_EXPERIMENT" for edge in payload["lineage_edges"])
    assert payload["quality_policy"]["blocks_future_leakage"] is True
    assert payload["quality_policy"]["mutable_raw_inputs"] is False


def test_backtest_explainability_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/backtest-explainability?alpha_id=alpha-1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["explainability_mode"] == "READ_ONLY_BACKTEST_EXPLAINABILITY"
    assert payload["run"]["execution_mode"] == "L2_DRY_RUN_ONLY"
    assert payload["run"]["config_hash"]
    assert payload["run"]["reproducibility_hash"]
    assert len(payload["equity_curve"]) == 120
    assert len(payload["drawdown_curve"]) == 120
    assert len(payload["segments"]) >= 5
    assert len(payload["cost_breakdown"]) == 4
    assert len(payload["drawdown_periods"]) == 5
    assert any(driver["driver"] == "Transaction cost drag" for driver in payload["decision_drivers"])
    assert payload["explainability_policy"]["requires_cost_model"] is True
    assert payload["explainability_policy"]["mutable_results"] is False


def test_scenario_stress_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/scenario-stress?alpha_id=alpha-1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["stress_mode"] == "READ_ONLY_SCENARIO_STRESS"
    assert payload["run"]["execution_mode"] == "L2_DRY_RUN_ONLY"
    assert payload["run"]["config_hash"]
    assert payload["run"]["reproducibility_hash"]
    assert payload["summary"]["scenarios"] == 6
    assert len(payload["scenarios"]) == 6
    assert len(payload["exposure_buckets"]) == 4
    assert payload["summary"]["worst_scenario_id"]
    first = payload["scenarios"][0]
    assert first["metrics"]["stressed_max_drawdown"] >= 0
    assert first["shocks"]["liquidity_haircut"] > 0
    assert any(driver["driver"] == "cost_shift" for driver in first["drivers"])
    assert payload["scenario_policy"]["requires_multi_scenario_replay"] is True
    assert payload["scenario_policy"]["authorizes_live_trading"] is False
    assert payload["scenario_policy"]["mutable_results"] is False


def test_model_risk_review_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/model-risk-review")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["model_risk_mode"] == "READ_ONLY_MODEL_RISK_REVIEW"
    assert payload["summary"]["models"] == 7
    assert payload["summary"]["high_risk_models"] >= 5
    assert payload["summary"]["independent_validation_complete"] is False
    assert payload["summary"]["live_use_authorized"] is False
    assert len(payload["models"]) == 7
    assert len(payload["validation_dimensions"]) == 4
    assert len(payload["issue_register"]) == 4
    assert any(issue["severity"] == "HIGH" for issue in payload["issue_register"])
    first = payload["models"][0]
    assert first["evidence"]["config_hash"]
    assert first["prohibited_use"].startswith("Live trading authorization")
    assert "L2 dry-run execution boundary" in first["compensating_controls"]
    assert payload["governance_policy"]["requires_independent_validation"] is True
    assert payload["governance_policy"]["authorizes_live_trading"] is False
    assert payload["governance_policy"]["mutable_reviews"] is False


def test_evidence_ledger_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/evidence-ledger")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["ledger_mode"] == "READ_ONLY_IMMUTABLE_EVIDENCE_LEDGER"
    assert payload["summary"]["entries"] == 7
    assert payload["summary"]["sealed_entries"] == 7
    assert payload["summary"]["hash_chain_valid"] is True
    assert payload["summary"]["live_authorization_allowed"] is False
    assert len(payload["entries"]) == 7
    assert payload["entries"][0]["previous_hash"] == "GENESIS"
    assert payload["entries"][-1]["ledger_hash"] == payload["root_hash"]
    assert all(entry["content_hash"] for entry in payload["entries"])
    assert all(entry["verification"]["no_live_authorization"] is True for entry in payload["entries"])
    assert any(blocker["source_module"] == "Phase 3A Governance" for blocker in payload["blockers"])
    assert payload["ledger_policy"]["append_only"] is True
    assert payload["ledger_policy"]["mutable_entries"] is False
    assert payload["ledger_policy"]["authorizes_live_trading"] is False


def test_reviewer_workflow_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/reviewer-workflow")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["workflow_mode"] == "READ_ONLY_REVIEWER_WORKFLOW"
    assert payload["summary"]["tasks"] == 8
    assert payload["summary"]["blocking_tasks"] >= 5
    assert payload["summary"]["approval_gates"] == 5
    assert payload["summary"]["live_authorization_allowed"] is False
    assert len(payload["reviewers"]) == 5
    assert len(payload["tasks"]) == 8
    assert len(payload["approval_gates"]) == 5
    assert any(task["state"] == "OVERDUE" for task in payload["tasks"])
    assert all(task["task_hash"] for task in payload["tasks"])
    assert any(gate["status"] == "BLOCKED" for gate in payload["approval_gates"])
    assert payload["workflow_policy"]["requires_independent_reviewer"] is True
    assert payload["workflow_policy"]["mutable_tasks"] is False
    assert payload["workflow_policy"]["authorizes_live_trading"] is False


def test_access_control_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/access-control")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["access_mode"] == "READ_ONLY_RBAC_AUDIT_EXPORT"
    assert payload["summary"]["roles"] == 5
    assert payload["summary"]["export_packages"] == 3
    assert payload["summary"]["access_reviews"] == 4
    assert payload["summary"]["live_authorization_allowed"] is False
    assert len(payload["roles"]) == 5
    assert len(payload["forbidden_capabilities"]) == 5
    assert all(item["status"] == "DENIED" for item in payload["forbidden_capabilities"])
    dangerous_permissions = [
        item for item in payload["permission_matrix"]
        if item["permission"] in {"approve:live", "trade:any", "credential:any"}
    ]
    assert dangerous_permissions
    assert all(item["allowed"] is False for item in dangerous_permissions)
    assert all(package["download_enabled"] is False for package in payload["export_packages"])
    assert all(package["contains_credentials"] is False for package in payload["export_packages"])
    assert payload["access_policy"]["mutable_permissions"] is False
    assert payload["access_policy"]["downloads_enabled"] is False
    assert payload["access_policy"]["authorizes_live_trading"] is False


def test_monitoring_drift_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/monitoring-drift")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["monitoring_mode"] == "READ_ONLY_MONITORING_DRIFT"
    assert payload["summary"]["monitors"] >= 30
    assert payload["summary"]["categories"] >= 5
    assert payload["summary"]["live_authorization_allowed"] is False
    assert payload["status_counts"]["BREACH"] >= 0
    assert len(payload["monitors"]) == payload["summary"]["monitors"]
    assert any(monitor["category"] == "ALPHA_DECAY" for monitor in payload["monitors"])
    assert any(monitor["category"] == "FEATURE_DRIFT" for monitor in payload["monitors"])
    assert any(monitor["category"] == "REVIEWER_SLA" for monitor in payload["monitors"])
    first = payload["monitors"][0]
    assert first["evidence_ref"]
    assert first["warn_threshold"] <= first["breach_threshold"]
    assert payload["monitoring_policy"]["requires_thresholds"] is True
    assert payload["monitoring_policy"]["mutable_alerts"] is False
    assert payload["monitoring_policy"]["authorizes_live_trading"] is False


def test_incident_response_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/incident-response")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["incident_mode"] == "READ_ONLY_INCIDENT_RESPONSE"
    assert payload["summary"]["incidents"] >= 10
    assert payload["summary"]["open_incidents"] == payload["summary"]["incidents"]
    assert payload["summary"]["external_alerts_sent"] == 0
    assert payload["summary"]["live_authorization_allowed"] is False
    assert len(payload["incidents"]) == payload["summary"]["incidents"]
    assert len(payload["routing_rules"]) == 5
    assert len(payload["runbooks"]) == 5
    assert len(payload["timeline"]) >= payload["summary"]["incidents"] * 3
    assert any(incident["source_monitor_id"].startswith("MON-") for incident in payload["incidents"])
    assert any(incident["severity"] in {"CRITICAL", "HIGH"} for incident in payload["incidents"])
    assert all(incident["external_alert_sent"] is False for incident in payload["incidents"])
    assert all(incident["evidence_packet_hash"] for incident in payload["incidents"])
    assert all(rule["notification_mode"] == "READ_ONLY_NO_EXTERNAL_SEND" for rule in payload["routing_rules"])
    assert payload["incident_policy"]["external_notifications_enabled"] is False
    assert payload["incident_policy"]["mutable_incidents"] is False
    assert payload["incident_policy"]["authorizes_live_trading"] is False


def test_promotion_control_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/promotion-control")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["promotion_mode"] == "READ_ONLY_PROMOTION_CONTROL"
    assert payload["current_level"] == "L2_DRY_RUN_ONLY"
    assert payload["phase3a_decision"] == "NO_GO"
    assert payload["summary"]["candidates"] == 6
    assert payload["summary"]["broker_test_env_allowed"] == 0
    assert payload["summary"]["live_release_allowed"] == 0
    assert len(payload["promotions"]) == 6
    assert len(payload["gates"]) == payload["summary"]["candidates"] * 9
    assert any(record["decision"] == "BLOCKED" for record in payload["promotions"])
    assert all(record["broker_test_env_allowed"] is False for record in payload["promotions"])
    assert all(record["live_release_allowed"] is False for record in payload["promotions"])
    assert all(gate["blocks_live_authorization"] is True for gate in payload["gates"])
    assert "MODEL_RISK" in payload["stage_counts"]
    assert "INCIDENT_RESPONSE" in payload["stage_counts"]
    assert payload["release_policy"]["mutable_promotions"] is False
    assert payload["release_policy"]["broker_test_env_enabled"] is False
    assert payload["release_policy"]["live_release_enabled"] is False
    assert payload["release_policy"]["authorizes_live_trading"] is False
    assert payload["safety_boundary"]["execution_boundary"] == "LIVE_TRADING_DISALLOWED"


def test_release_committee_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/release-committee")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["committee_mode"] == "READ_ONLY_RELEASE_COMMITTEE"
    assert payload["current_level"] == "L2_DRY_RUN_ONLY"
    assert payload["phase3a_decision"] == "NO_GO"
    assert payload["committee_decision"] == "NO_GO"
    assert payload["summary"]["agenda_items"] == 6
    assert payload["summary"]["no_go_items"] >= 1
    assert payload["summary"]["quorum_met"] is False
    assert payload["summary"]["signed_votes"] == 0
    assert payload["summary"]["broker_test_env_allowed"] == 0
    assert payload["summary"]["live_release_allowed"] == 0
    assert len(payload["seats"]) == 6
    assert len(payload["agenda"]) == payload["summary"]["agenda_items"]
    assert len(payload["votes"]) == payload["summary"]["agenda_items"] * len(payload["seats"])
    assert any(item["recommendation"] == "NO_GO" for item in payload["agenda"])
    assert all(item["broker_test_env_allowed"] is False for item in payload["agenda"])
    assert all(item["live_release_allowed"] is False for item in payload["agenda"])
    assert all(vote["signed"] is False for vote in payload["votes"])
    assert payload["committee_policy"]["mutable_votes"] is False
    assert payload["committee_policy"]["external_signing_enabled"] is False
    assert payload["committee_policy"]["broker_test_env_enabled"] is False
    assert payload["committee_policy"]["live_release_enabled"] is False
    assert payload["committee_policy"]["authorizes_live_trading"] is False


def test_alpha_factory_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/alpha-factory")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["summary"]["candidates"] == 12
    assert payload["summary"]["avg_ic"] != 0
    assert len(payload["candidates"]) == 12
    assert all("candidate_id" in candidate for candidate in payload["candidates"])
    assert payload["screening"]["buckets"]["APPROVED"] >= 0
    assert len(payload["screening"]["top_candidates"]) == 5
    assert payload["correlation_matrix"]["method"] == "BACKEND_RESEARCH_PROXY_EXPRESSION_SCORE_PENALTY"
    assert len(payload["correlation_matrix"]["ids"]) == 8
    assert len(payload["correlation_matrix"]["matrix"]) == 8


def test_risk_dashboard_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/risk-dashboard?alpha_id=alpha-2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["selected_alpha_id"] == "alpha-2"
    assert payload["primary_report"]["gates_total"] == 9
    assert len(payload["reports"]) == 2


def test_validation_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/validation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["selected_report"]["alpha_id"] == "alpha-1"
    assert len(payload["reports"]) == 6
    assert payload["selected_report"]["gates_total"] == 6


def test_paper_trading_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/paper-trading?alpha_id=alpha-2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["selected_alpha_id"] == "alpha-2"
    assert payload["run"]["metrics"]["sharpe"] != 0
    assert len(payload["run"]["equity_curve"]) == 120


def test_portfolio_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/portfolio")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["simulation"]["status"] == "COMPLETED"
    assert len(payload["simulation"]["equity_curve"]) == 120
    assert len(payload["drawdown_curve"]) == 120
    assert payload["weights"]["gross_exposure"] == 1.0
    assert len(payload["risk_contributions"]) == len(payload["weights"]["weights"])
    assert len(payload["return_attribution"]) == len(payload["weights"]["weights"])
    assert payload["analytics_method"] == "BACKEND_RESEARCH_SIMULATION_ATTRIBUTION"


def test_system_health_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/system-health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert any(service["service"] == "research-console-api" for service in payload["services"])
    assert len(payload["safety_checks"]) == 5


def test_system_completeness_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/system-completeness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["audit_mode"] == "READ_ONLY_SYSTEM_COMPLETENESS_AUDIT"
    assert payload["current_level"] == "L2_5_RESEARCH_CONTROL_PLANE"
    assert payload["institutional_target"] == "TOP_TIER_AI_QUANT_TRADING_SYSTEM"
    assert payload["summary"]["frontend_routes"] == 25
    assert payload["summary"]["api_endpoints"] == 25
    assert payload["summary"]["backend_backed_modules"] == 25
    assert payload["summary"]["live_trading_authorized"] is False
    assert len(payload["modules"]) == 25
    assert any(module["id"] == "system-completeness" for module in payload["modules"])
    assert any(gap["severity"] == "CRITICAL" for gap in payload["gaps"])
    assert any(check["status"] == "FAIL" for check in payload["checks"])
    assert payload["safety_boundary"]["execution_boundary"] == "LIVE_TRADING_DISALLOWED"
