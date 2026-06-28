from fastapi.testclient import TestClient

from services.research.console_api.main import app


client = TestClient(app)


def test_health_read_only():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "READ_ONLY"


def test_phase1b_real_series_back_engines(monkeypatch):
    # Phase 1B: when the offline snapshot's real per-factor return series are loaded, the engines run on
    # REAL data and provenance flips to REAL_CRYPTO_SERIES; absent, everything fails closed to synthetic.
    # The snapshot is gitignored (CI-absent), so we force both states via the module cache.
    from services.research.console_api import main as capi
    real = [{"alpha_id": "h_real", "name": "h_real_zscore_3",
             "returns": [0.01, -0.006, 0.008, -0.003, 0.012, -0.009] * 20,   # 120 real-ish periods
             "ic": 0.05, "rank_ic": 0.04, "net_sharpe": 1.7, "turnover": 0.32,
             "deflated_sharpe": 0.6, "capacity_adj_sharpe": 1.3, "pbo": 0.12,
             "breadth": 12, "state": "ACTIVE", "verdict": "PASS"}]
    monkeypatch.setattr(capi, "_REAL_FACTORS_CACHE", real)
    af = client.get("/api/v1/console/alpha-factory").json()
    assert af["data_provenance"] == "REAL_CRYPTO_SERIES"
    assert any(c["expression"] == "h_real_zscore_3" for c in af["candidates"])   # real factor surfaced
    assert any(round(c["ic"], 4) == 0.05 for c in af["candidates"])              # real IC, not synthetic
    risk = client.get("/api/v1/console/risk-dashboard?alpha_id=alpha-1").json()
    assert risk["data_provenance"] == "REAL_CRYPTO_SERIES"
    assert risk["primary_report"]["gates_total"] == 9                           # the real 9-engine gate ran

    monkeypatch.setattr(capi, "_REAL_FACTORS_CACHE", [])                          # fail-closed -> synthetic
    assert client.get("/api/v1/console/alpha-factory").json()["data_provenance"] == "SYNTHETIC_DETERMINISTIC"


def test_computation_lab_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/computation-lab?alpha_id=alpha-1&cost_bps=18&stress=1.2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["metrics"]["gross_sharpe"] != 0
    assert 0 < len(payload["equity_curve"]) <= 120   # real per-factor series may be shorter than synthetic 120
    assert 0 < len(payload["drawdown_curve"]) <= 120
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
    # Events derive from the real run_registry when it has runs (RESEARCH_RUN_REGISTRY), else synthetic
    # (12, SYNTHETIC_DETERMINISTIC). Both honest — assert shape, not a fixed count.
    assert payload["data_provenance"] in {"SYNTHETIC_DETERMINISTIC", "RESEARCH_RUN_REGISTRY"}
    assert len(payload["events"]) >= 1
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
    # Real path reads the experiment_registry (EXPERIMENT_REGISTRY); synthetic fallback is 10. Assert shape.
    assert payload["data_provenance"] in {"SYNTHETIC_DETERMINISTIC", "EXPERIMENT_REGISTRY"}
    assert payload["summary"]["total_experiments"] >= 1
    assert payload["summary"]["artifact_count"] >= payload["summary"]["total_experiments"]
    assert len(payload["experiments"]) == payload["summary"]["total_experiments"]
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
    # DATASETS upgrade to REAL: real path enumerates the on-disk PIT store (42 crypto_daily instruments),
    # synthetic fallback is 5. Assert the shape/relationships, not a fixed count.
    assert payload["data_provenance"] in {"SYNTHETIC_DETERMINISTIC", "REAL_PIT_DATASETS"}
    assert payload["summary"]["datasets"] == len(payload["datasets"]) >= 5
    assert payload["summary"]["features"] == 8
    assert payload["summary"]["quality_checks"] == 3 * len(payload["datasets"])
    assert payload["summary"]["point_in_time_safe_features"] == 8
    assert len(payload["features"]) == 8
    assert all(dataset["content_hash"] for dataset in payload["datasets"])
    assert all(feature["lineage_hash"] for feature in payload["features"])
    assert any(edge["kind"] == "FEATURE_TO_EXPERIMENT" for edge in payload["lineage_edges"])
    assert payload["quality_policy"]["blocks_future_leakage"] is True
    assert payload["quality_policy"]["mutable_raw_inputs"] is False
    # G2: catalog content hashes + PIT-safety now come from the real data layer.
    assert payload["data_backend"] == "PIT_DATA_LAYER_v1"
    assert all(dataset["point_in_time_safe"] for dataset in payload["datasets"])


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
    assert 0 < len(payload["equity_curve"]) <= 120   # real per-factor series may be shorter than synthetic 120
    assert 0 < len(payload["drawdown_curve"]) <= 120
    assert len(payload["segments"]) >= 1   # segment count scales with the (real) series length
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


def test_lifecycle_monitor_is_read_only_and_honest_about_live():
    response = client.get("/api/v1/console/lifecycle-monitor")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["monitoring_mode"] == "READ_ONLY_LIFECYCLE_MONITOR"
    # Real path reads the experiment_registry; synthetic fallback is the deterministic candidate set.
    assert payload["data_provenance"] in {"SYNTHETIC_DETERMINISTIC", "EXPERIMENT_REGISTRY"}
    assert payload["summary"]["total"] == len(payload["rows"]) >= 1
    # HONESTY: L2 has no live/paper IC source — every row's live side stays null and the degradation
    # recommendation is NO_LIVE_DATA (never a fabricated DEGRADED). live_tracked must therefore be 0.
    assert payload["summary"]["live_tracked"] == 0
    for row in payload["rows"]:
        assert row["live_ic"] is None
        assert row["recommendation"] == "NO_LIVE_DATA"
        assert row["days_in_state"] is None  # single-batch as_of, no honest per-alpha tenure
    # Lifecycle policy must declare the L2 safety posture: recommend-only, human-only promotion, no live.
    policy = payload["lifecycle_policy"]
    assert policy["recommends_only"] is True
    assert policy["promotion_human_only"] is True
    assert policy["live_trading_built"] is False
    assert policy["live_ic_available"] is False
    # Real degradation thresholds are surfaced read-only.
    assert payload["thresholds"]["min_ic_retention"] > 0


def test_backtest_results_aggregates_real_runs_or_fails_closed():
    response = client.get("/api/v1/console/backtest-results")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["read_only"] is True
    assert payload["results_mode"] == "READ_ONLY_BACKTEST_RESULTS"
    # Real path aggregates done run_registry runs by role; synthetic fallback is all-None blocks (CI).
    assert payload["data_provenance"] in {"SYNTHETIC_DETERMINISTIC", "RESEARCH_RUN_REGISTRY"}
    for role in ("carry", "combo", "oi_research", "survivorship"):
        assert role in payload
        block = payload[role]
        if block is not None:
            assert set(block) >= {"run_id", "kind", "verdict", "metrics", "series"}
            assert block["status"] == "done"
    assert "factory" in payload and set(payload["factory"]) == {"loop", "orthogonal"}
    # When the registry is present (real), carry is the deployable funding-carry edge with its real metric.
    if payload["data_provenance"] == "RESEARCH_RUN_REGISTRY":
        assert payload["carry"] is not None
        assert "net_sharpe_5bps" in payload["carry"]["metrics"]


def test_alpha_decay_monitors_are_engine_computed():
    """ALPHA_DECAY rows must come from the REAL Phase-6 degradation-monitor engine, not a
    hand-coded formula. Proven two ways: (1) the summary advertises the engine, and (2) every
    engine verdict surfaced in remediation maps to the row's status via the documented
    recommendation->status table — so the status is engine-derived, never invented here."""
    payload = client.get("/api/v1/console/monitoring-drift").json()
    assert payload["summary"]["alpha_decay_engine"] == "degradation_monitor"

    rec_to_status = {"OK": "OK", "DEGRADED": "WATCH", "PAUSED": "BREACH", "RETIRED": "BREACH"}
    decay = [m for m in payload["monitors"] if m["category"] == "ALPHA_DECAY"]
    assert decay, "expected ALPHA_DECAY monitors"

    engine_voiced = 0
    for m in decay:
        assert m["status"] in {"OK", "WATCH", "BREACH"}
        assert m["metric"] == "health_drift"  # holistic, not pure ic_drift
        # Internal coherence: drift_score must sit in the band that matches the status relative to the
        # warn/breach thresholds — so the bar length, its colour and the status chip can never disagree.
        warn, breach, score = m["warn_threshold"], m["breach_threshold"], m["drift_score"]
        if m["status"] == "OK":
            assert score < warn, (m["monitor_id"], score, warn)
        elif m["status"] == "WATCH":
            assert warn <= score < breach, (m["monitor_id"], score, warn, breach)
        else:  # BREACH
            assert score >= breach, (m["monitor_id"], score, breach)
        rem = m["remediation"]
        if rem.startswith("[degradation_monitor:"):
            engine_voiced += 1
            rec = rem[len("[degradation_monitor:"):].split("]", 1)[0]
            assert rec in rec_to_status, rec
            assert rec_to_status[rec] == m["status"]
    # The deterministic L2 inputs intentionally trip at least one alert (return/slippage drift),
    # so the engine MUST have voiced a non-trivial verdict on at least one row.
    assert engine_voiced >= 1


def test_research_runs_list_and_detail_round_trip(tmp_path, monkeypatch):
    """The research-ops console reads runs from the registry: list summaries + a generic detail.
    Isolated against a seeded temp db (the real cached result JSONs are gitignored / CI-absent)."""
    from services.research.console_api import main as capi
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.contract import ResearchRun
    from fastapi.testclient import TestClient

    db = str(tmp_path / "runs.db")
    monkeypatch.setattr(capi, "_RUNS_DB", db)
    reg = RunRegistry(ResearchRunStore(db))
    reg.upsert(ResearchRun(run_id="rX", kind="combo_book", title="组合", params={"n": 14}, status="done",
                           created_ts=5, finished_ts=6, metrics={"combo_sharpe": 1.5},
                           series={"per_year": [{"label": "22", "value": 2.2}]},
                           verdict="ok", caveats=["c"], provenance="p", code_version="abc123def456"))
    c = TestClient(capi.app)

    body = c.get("/api/v1/console/research-runs").json()
    assert body["read_only"] is True and body["data_provenance"] == "RESEARCH_RUN_REGISTRY"
    summ = next(r for r in body["runs"] if r["run_id"] == "rX")
    assert summ["headline"] == {"label": "combo_sharpe", "value": 1.5} and summ["verdict"] == "ok"
    # history retention: the list surfaces params + git sha at a glance (not only the detail view)
    assert summ["params"] == {"n": 14} and summ["code_version"] == "abc123def456"

    detail = c.get("/api/v1/console/research-run?run_id=rX").json()["run"]
    assert detail["run_id"] == "rX" and detail["metrics"]["combo_sharpe"] == 1.5
    assert detail["series"]["per_year"][0]["value"] == 2.2 and detail["caveats"] == ["c"]

    # unknown id -> visible null (fail-soft), not a 500
    assert c.get("/api/v1/console/research-run?run_id=nope").json()["run"] is None
    # kind filter
    assert all(r["kind"] == "combo_book" for r in c.get("/api/v1/console/research-runs?kind=combo_book").json()["runs"])


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
    # leakage_pass is now produced by the REAL leakage detector, not a modulo hack
    assert payload["validation_backend"] == "REAL_LEAKAGE_DETECTOR_v1"
    assert payload["reports"][2]["leakage_pass"] is False   # idx 2 injects a future-peeking feature
    assert payload["reports"][0]["leakage_pass"] is True


def test_paper_trading_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/paper-trading?alpha_id=alpha-2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["selected_alpha_id"] == "alpha-2"
    assert payload["run"]["metrics"]["sharpe"] != 0
    assert 0 < len(payload["run"]["equity_curve"]) <= 120


def test_portfolio_snapshot_uses_backend_contract():
    response = client.get("/api/v1/console/portfolio")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert payload["simulation"]["status"] == "COMPLETED"
    assert 0 < len(payload["simulation"]["equity_curve"]) <= 120
    assert 0 < len(payload["drawdown_curve"]) <= 120
    # weights now come from the REAL risk-parity optimizer (was hardcoded equal-weight); they sum to ~1
    # (rounding to 4dp can make the gross 1.0001). method/optimizer_real expose whether they're real.
    assert abs(payload["weights"]["gross_exposure"] - 1.0) < 0.01
    assert payload["weights"]["method"] in {"RISK_PARITY", "EQUAL_WEIGHT_FALLBACK"}
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
