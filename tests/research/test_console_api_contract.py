"""Phase 12 — frontend<->backend contract conformance.

The research console frontend casts each response with `response.json() as
TPayload` (compile-time only, NO runtime validation), and silently falls back
to mock data on error. So a backend payload that drops a key the page reads
would not crash the build — the page would just render `undefined` while still
claiming it is "connected". That class of drift is invisible to TypeScript and
to the frontend tests.

This test is the guard: for every console_api endpoint it asserts the response
carries the read-only base markers AND the exact top-level keys the matching
TS `*Payload` type in `apps/research-console/src/lib/types/research.ts`
destructures. Backend drift away from the frontend contract turns the gate RED.

If you change a payload's top-level shape, change BOTH this contract and the TS
type in the same commit — that is the point.
"""
import json

from fastapi.testclient import TestClient

from quantos.contracts.hashing import stable_hash
from services.console.main import app
from services.console.api import artifact_snapshots
from services.console.api import artifact_paper_views

client = TestClient(app)

BASE_MARKERS = (
    "source",
    "read_only",
    "api_version",
    "data_provenance",
    "snapshot_id",
    "snapshot_ids",
    "snapshot_status",
    "data_quality",
)

SNAPSHOT_STATUSES = {
    "REAL",
    "SYNTHETIC",
    "NO_SNAPSHOT",
    "STALE",
    "DATA_BLOCKED",
    "UNAVAILABLE",
}

# endpoint -> (query string, required top-level keys per the frontend TS Payload type).
# Keys mirror apps/research-console/src/lib/types/research.ts (extracted from the
# exported *Payload interfaces). Base markers are checked separately for all.
CONTRACT: dict[str, tuple[str, tuple[str, ...]]] = {
    "/api/v1/console/overview": ("", ("stats", "funnel", "agents")),
    "/api/v1/console/agents": ("", ("summary", "agents")),
    "/api/v1/console/events": ("", ("events", "event_bus_health")),
    "/api/v1/console/phase-3a-readiness": ("", ("readiness",)),
    "/api/v1/console/architecture": ("", (
        "current_level", "target_level", "phase3a_decision", "implementation_authorized",
        "architecture_score", "inventory", "modules", "data_flows", "gaps", "safety_boundary")),
    "/api/v1/console/experiments": ("", (
        "registry_mode", "summary", "experiments", "stage_counts", "lineage_policy")),
    "/api/v1/console/data-catalog": ("", (
        "catalog_mode", "feature_store_mode", "summary", "datasets", "features",
        "lineage_edges", "quality_policy")),
    "/api/v1/console/backtest-explainability": ("?alpha_id=alpha-2", (
        "explainability_mode", "selected_alpha_id", "run", "alpha", "summary",
        "equity_curve", "drawdown_curve", "segments", "monthly_returns", "cost_breakdown",
        "drawdown_periods", "trade_distribution", "regime_attribution", "decision_drivers",
        "assumptions", "explainability_policy")),
    "/api/v1/console/scenario-stress": ("?alpha_id=alpha-2", (
        "stress_mode", "selected_alpha_id", "run", "alpha", "baseline", "summary",
        "scenarios", "exposure_buckets", "risk_factor_contributions", "scenario_policy",
        "assumptions")),
    "/api/v1/console/model-risk-review": ("", (
        "model_risk_mode", "review_standard", "summary", "models", "validation_dimensions",
        "issue_register", "evidence_sources", "governance_policy")),
    "/api/v1/console/evidence-ledger": ("", (
        "ledger_mode", "chain_id", "root_hash", "summary", "entries", "blockers",
        "ledger_policy")),
    "/api/v1/console/reviewer-workflow": ("", (
        "workflow_mode", "workflow_id", "summary", "reviewers", "tasks", "approval_gates",
        "workload", "evidence_links", "workflow_policy")),
    "/api/v1/console/access-control": ("", (
        "access_mode", "summary", "roles", "permission_matrix", "export_packages",
        "access_reviews", "forbidden_capabilities", "access_policy")),
    "/api/v1/console/monitoring-drift": ("", (
        "monitoring_mode", "summary", "status_counts", "category_counts", "monitors",
        "top_breaches", "watchlist", "monitoring_policy")),
    "/api/v1/console/incident-response": ("", (
        "incident_mode", "summary", "severity_counts", "state_counts", "routing_counts",
        "incidents", "routing_rules", "runbooks", "timeline", "incident_policy")),
    "/api/v1/console/promotion-control": ("", (
        "promotion_mode", "current_level", "target_stage", "phase3a_decision", "summary",
        "decision_counts", "stage_counts", "promotions", "gates", "release_policy",
        "safety_boundary")),
    "/api/v1/console/release-committee": ("", (
        "committee_mode", "meeting_id", "current_level", "phase3a_decision",
        "committee_decision", "summary", "seats", "agenda", "votes",
        "required_attestations", "blockers", "committee_policy")),
    "/api/v1/console/alpha-factory": ("", (
        "summary", "candidates", "screening", "correlation_matrix", "lineage")),
    "/api/v1/console/risk-dashboard": ("?alpha_id=alpha-2", (
        "selected_alpha_id", "alpha", "primary_report", "reports", "lineage")),
    "/api/v1/console/validation": ("", ("selected_report", "reports", "lineage")),
    "/api/v1/console/paper-trading": ("?alpha_id=alpha-2", (
        "selected_alpha_id", "run", "lineage")),
    "/api/v1/console/portfolio": ("", (
        "simulation", "drawdown_curve", "weights", "risk_contributions",
        "return_attribution", "analytics_method", "lineage")),
    "/api/v1/console/population-optimizer": ("", ("selected", "excluded", "diversity", "gross")),
    "/api/v1/console/safety-primitives": ("", ("safety_mode", "kill_switch", "pre_trade_gate", "safety_config")),
    "/api/v1/console/alpha-graph": ("", ("nodes", "edges", "clusters", "rho_cut", "n_near_dup")),
    "/api/v1/console/alpha-zoo": ("", ("rows", "dimensions")),
    "/api/v1/console/dsl-explorer": ("", ("exprs", "analysis_backend")),
    "/api/v1/console/dsl-explorer/check": ("?expr=" + "(cs_rank (field close))", ("row",)),
    "/api/v1/console/system-completeness": ("", (
        "assessment", "domains", "requirements", "red_lines", "inventory", "summary", "safety_boundary")),
    "/api/v1/console/system-health": ("", ("services", "gates", "safety_checks")),
    "/api/v1/console/runtime-contract": ("", (
        "contract_version", "service", "instance_id", "process_id", "build_sha",
        "build_sha_known", "event_schema_catalog", "safety_boundary", "generated_at_ms")),
    "/api/v1/console/computation-lab": ("?alpha_id=alpha-2&cost_bps=25&stress=1.5", (
        "selected_alpha_id", "alpha_candidates", "alpha", "validation", "metrics",
        "equity_curve", "drawdown_curve", "risk_report", "lineage")),
    "/api/v1/console/crypto-live": ("", (
        "crypto_mode", "snapshot_present", "generated_at", "dataset_version",
        "meta", "summary", "factors", "oos", "synergistic_set",
        "cost_stress", "regime", "safety")),
    # research-ops console: dynamic registry-backed run list + generic detail (real research results)
    "/api/v1/console/research-runs": ("", ("runs",)),
    "/api/v1/console/research-run": ("?run_id=__contract__", ("run",)),
    # backtest results: aggregate of the done run_registry runs by role (carry/combo/oi/survivorship + factory)
    "/api/v1/console/backtest-results": ("", ("carry", "combo", "oi_research", "survivorship", "factory")),
    # alpha lifecycle monitor: real experiment_registry backtest-side artifacts (live side honestly null)
    "/api/v1/console/lifecycle-monitor": ("", ("summary", "rows", "thresholds", "lifecycle_policy")),
    # FX research: real ECB/Frankfurter full-universe + tradable-gauntlet snapshots (synthetic-empty in CI)
    "/api/v1/console/fx-research": ("", ("snapshot_present", "verdict", "full_universe",
                                         "tradable_gauntlet", "honesty_note")),
    # carry readiness: funder-facing summary of the one validated signal (3 carry snapshots; synthetic-empty in CI)
    "/api/v1/console/carry-readiness": ("", ("snapshot_present", "verdict", "strategy", "robustness",
                                             "capacity", "deployment_gaps", "honesty_note")),
    # dry-run pipeline: end-to-end runs (gate as runtime chokepoint) from DryRunStore; synthetic-empty in CI
    "/api/v1/console/dry-run": ("", ("snapshot_present", "verdict", "n_runs", "latest", "runs", "realtime_alpha_autonomy", "safety_note")),
    "/api/v1/console/ic-panel": ("", ("snapshot_present", "verdict", "rows", "universes", "honesty_note")),
    # research findings: capacity(RE-1) + macro regime(FRED) + combo diversification aggregate; synthetic-empty in CI
    "/api/v1/console/research-findings": ("", ("snapshot_present", "verdict", "capacity", "macro_regime", "combo", "honesty_note")),
    # lance 1m strategy validation: independent refutation of a user-supplied strategy's claimed Sharpe 5.01
    "/api/v1/console/lance-1m-validation": ("", ("snapshot_present", "verdict", "fee_sweep",
                                                 "rolling_window_stress_1bps", "rolling_window_stress_5bps",
                                                 "parameter_sensitivity_5bps", "ohlc_ambiguity_comparison",
                                                 "honesty_note")),
    # live trading route: Stage 0 forward paper-track evidence clock + Stage 1 testnet execution stack
    "/api/v1/console/live-trading": ("", ("snapshot_present", "verdict", "forward_track", "execution_stack", "honesty_note")),
    # GoNoGo evidence pack (Phase 5 item 3): forward evidence + execution PnL/cost attribution + incidents
    "/api/v1/console/gonogo": ("", ("snapshot_present", "verdict", "forward_evidence",
                                    "execution_evidence", "incidents", "honesty_note")),
    # A股微盘 alpha 研究栈:真实 alpha/paper-track/klines 快照(缺文件时诚实 SYNTHETIC)
    "/api/v1/console/ashare-alpha": ("", (
        "source", "read_only", "provenance", "generated_at", "data_freshness", "universe",
        "factors", "combo", "basket", "variants", "dry_run", "paper_track",
        "cost_grid", "turnover_caps", "holdings_latest", "honest_limitations")),
    # MEXC 零手续费 BTC 现货对接(read-only 三端点;artifact 未产出时诚实 SYNTHETIC/NO_SNAPSHOT)
    "/api/v1/console/mexc-market": ("", ("snapshot_present", "verdict", "venue", "symbol",
                                         "latest_price", "coverage", "freshness", "bars",
                                         "honesty_note")),
    "/api/v1/console/mexc-strategy": ("", ("snapshot_present", "verdict", "strategy_id", "metrics",
                                           "equity_curve", "buy_and_hold", "runs", "honesty_note")),
    "/api/v1/console/mexc-readiness": ("", ("snapshot_present", "verdict", "readiness", "n_ready",
                                            "n_blocked", "checks", "kill_switch", "honesty_note")),
    # Alpha 生命周期驾驶舱:tools/alpha_lifecycle_cockpit.py 的离线聚合 artifact(缺则诚实 NO_SNAPSHOT)
    "/api/v1/console/alpha-lifecycle": ("", ("snapshot_present", "artifact", "generated_at_ms",
                                             "alphas", "stage_legend", "honesty_note")),
    "/api/v1/console/alpha-families": ("", ("registry_present", "chain_valid", "families",
                                              "honesty_note")),
    # Go-Live 上线向导:tools/go_live_readiness.py 的离线就绪 artifact(缺则诚实 NO_SNAPSHOT)
    "/api/v1/console/go-live-readiness": ("", ("snapshot_present", "artifact", "generated_at_ms",
                                               "venues", "strategies", "guidance", "honesty_note")),
    # live-vs-replay 衰减(W1):tools/live_degradation.py 的离线 artifact(缺则诚实 NO_SNAPSHOT)
    "/api/v1/console/live-degradation": ("", ("snapshot_present", "artifact", "generated_at_ms",
                                              "readiness", "summary", "reports", "honesty_note")),
    # 投资者/审计证据快照(W9 §13):tools/investor_snapshot.py 的离线 artifact(缺则诚实 NO_SNAPSHOT)
    "/api/v1/console/investor-snapshot": ("", ("snapshot_present", "artifact", "generated_at_ms",
                                               "readiness", "data_cutoff_ms", "report_snapshot_hash",
                                               "hash_scheme", "portfolio_views", "summary", "venues",
                                               "amendments", "unknown_fields", "honesty_note")),
}

# Streaming transports are console endpoints, but deliberately do not map to a
# single frontend *Payload JSON contract. Their event bodies are validated by
# test_console_live_stream.py before the EventSource hook accepts them.
NON_JSON_STREAM_ROUTES = {"/api/v1/console/stream/dry-run"}

# Endpoints serving REAL (non-synthetic) read-only data carry a distinct provenance
# marker. They still must be read-only snapshots; only the honesty label differs.
_REAL_OR_SYNTHETIC = {"SYNTHETIC_DETERMINISTIC", "REAL_CRYPTO_SERIES"}
REAL_DATA_PROVENANCE = {
    "/api/v1/console/agents": "RESEARCH_RUN_REGISTRY_AND_GUARDRAIL_CONTRACT",
    "/api/v1/console/crypto-live": "REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT",
    "/api/v1/console/safety-primitives": "REAL_SAFETY_PRIMITIVES",   # reads the real KillSwitch/gate/config
    "/api/v1/console/research-runs": "RESEARCH_RUN_REGISTRY",
    "/api/v1/console/research-run": "RESEARCH_RUN_REGISTRY",
    # BacktestResults aggregates done runs by role; synthetic (all-None blocks) when the registry is empty.
    "/api/v1/console/backtest-results": {"SYNTHETIC_DETERMINISTIC", "RESEARCH_RUN_REGISTRY"},
    # FX research: REAL ECB snapshot when present, SYNTHETIC-empty in CI / fresh checkout. Both honest.
    "/api/v1/console/fx-research": {"SYNTHETIC_DETERMINISTIC", "REAL_FX_ECB_OFFLINE_SNAPSHOT"},
    # carry readiness: REAL carry snapshots when present, SYNTHETIC-empty in CI / fresh checkout.
    "/api/v1/console/carry-readiness": {"SYNTHETIC_DETERMINISTIC", "REAL_CARRY_OFFLINE_SNAPSHOT"},
    # dry-run pipeline: REAL DryRunStore runs when present, SYNTHETIC-empty in CI / fresh checkout.
    "/api/v1/console/dry-run": {"SYNTHETIC_DETERMINISTIC", "REAL_DRY_RUN_OFFLINE_SNAPSHOT"},
    "/api/v1/console/ic-panel": {"SYNTHETIC_DETERMINISTIC", "REAL_IC_PANEL_OFFLINE_SNAPSHOT"},
    # MEXC 对接:market/strategy 依赖离线 artifact(缺则诚实 SYNTHETIC),readiness 恒为真实计算清单
    "/api/v1/console/mexc-market": {"SYNTHETIC_DETERMINISTIC", "REAL_MEXC_MARKET_SNAPSHOT"},
    "/api/v1/console/mexc-strategy": {"SYNTHETIC_DETERMINISTIC", "REAL_DRY_RUN_OFFLINE_SNAPSHOT"},
    "/api/v1/console/mexc-readiness": "REAL_MEXC_READINESS_CHECK",
    # go-live-readiness: REAL go_live_readiness.json artifact when the driver has run,
    # SYNTHETIC-empty (NO_SNAPSHOT) in CI / fresh checkout. Both honest.
    "/api/v1/console/go-live-readiness": {"SYNTHETIC_DETERMINISTIC", "REAL_GO_LIVE_READINESS"},
    # live-degradation: REAL live_degradation.json artifact when the driver has run,
    # SYNTHETIC-empty (NO_SNAPSHOT) in CI / fresh checkout. Both honest.
    "/api/v1/console/live-degradation": {"SYNTHETIC_DETERMINISTIC", "REAL_LIVE_DEGRADATION"},
    # investor-snapshot: REAL investor_snapshot.json artifact when the driver has run,
    # SYNTHETIC-empty (NO_SNAPSHOT) in CI / fresh checkout. Both honest.
    "/api/v1/console/investor-snapshot": {"SYNTHETIC_DETERMINISTIC", "REAL_INVESTOR_SNAPSHOT"},
    # alpha-lifecycle: REAL alpha_lifecycle_cockpit.json artifact when the driver has run,
    # SYNTHETIC-empty (NO_SNAPSHOT) in CI / fresh checkout. Both honest.
    "/api/v1/console/alpha-lifecycle": {"SYNTHETIC_DETERMINISTIC", "REAL_ALPHA_LIFECYCLE"},
    "/api/v1/console/alpha-families": "REAL_ALPHA_FAMILY_LEDGER",
    "/api/v1/console/research-findings": {"SYNTHETIC_DETERMINISTIC", "REAL_RESEARCH_FINDINGS"},
    "/api/v1/console/lance-1m-validation": {"SYNTHETIC_DETERMINISTIC", "REAL_LANCE_1M_VALIDATION"},
    # architecture: curated module/maturity roadmap + REAL repo introspection (`inventory`). REAL marker
    # when the live FS walk populated the inventory (always, outside FS errors), else honest synthetic.
    "/api/v1/console/architecture": {"SYNTHETIC_DETERMINISTIC", "REAL_REPO_INVENTORY"},
    # system-completeness: curated readiness map + REAL repo introspection (`inventory`); same marker rule.
    "/api/v1/console/system-completeness": "TOP_TIER_EVIDENCE_ASSESSMENT",
    # Phase 1B: these run the REAL risk/PnL/validation engines on REAL crypto return series WHEN the
    # offline snapshot (factor_series) is present, else fall back to synthetic (CI / fresh checkout).
    # Both are honest — accept either marker.
    "/api/v1/console/alpha-factory": _REAL_OR_SYNTHETIC,
    "/api/v1/console/risk-dashboard": _REAL_OR_SYNTHETIC,
    "/api/v1/console/validation": _REAL_OR_SYNTHETIC,
    "/api/v1/console/paper-trading": _REAL_OR_SYNTHETIC,
    "/api/v1/console/portfolio": _REAL_OR_SYNTHETIC,
    "/api/v1/console/computation-lab": _REAL_OR_SYNTHETIC,
    "/api/v1/console/population-optimizer": _REAL_OR_SYNTHETIC,
    "/api/v1/console/overview": _REAL_OR_SYNTHETIC,
    "/api/v1/console/alpha-graph": _REAL_OR_SYNTHETIC,
    # Alpha Zoo now reads the full multi-dimensional evaluation from the experiment_registry (real) or
    # fails closed to synthetic — accept either marker.
    "/api/v1/console/alpha-zoo": {"SYNTHETIC_DETERMINISTIC", "EXPERIMENT_REGISTRY"},
    # DataCatalog enumerates the real on-disk PIT store (crypto_daily.db) for its DATASETS when present;
    # the feature/lineage block stays synthetic regardless (honest partial). Synthetic when no db (CI).
    "/api/v1/console/data-catalog": {"SYNTHETIC_DETERMINISTIC", "REAL_PIT_DATASETS"},
    # live trading: REAL forward-track/live-track JSON snapshots when present, SYNTHETIC-empty in CI.
    "/api/v1/console/live-trading": {"SYNTHETIC_DETERMINISTIC", "REAL_LIVE_TRADING_SNAPSHOT"},
    # GoNoGo: REAL gonogo_report.json snapshot when the driver has run, SYNTHETIC-empty in CI / fresh checkout.
    "/api/v1/console/gonogo": {"SYNTHETIC_DETERMINISTIC", "REAL_GONOGO_SNAPSHOT"},
    # A股微盘 alpha:真实快照存在时 REAL_ASHARE_ALPHA_SNAPSHOT,缺失时诚实 SYNTHETIC(CI)
    "/api/v1/console/ashare-alpha": {"SYNTHETIC_DETERMINISTIC", "REAL_ASHARE_ALPHA_SNAPSHOT"},
    # Events derive from the real run_registry when it has runs, else synthetic (CI / fresh checkout).
    "/api/v1/console/events": {
        "SYNTHETIC_DETERMINISTIC",
        "RESEARCH_RUN_REGISTRY",
        "DOMAIN_EVENT_PROJECTION",
    },
    # Experiments + EvidenceLedger read the real experiment_registry (immutable artifacts) when present.
    "/api/v1/console/experiments": {"SYNTHETIC_DETERMINISTIC", "EXPERIMENT_REGISTRY"},
    "/api/v1/console/evidence-ledger": {"SYNTHETIC_DETERMINISTIC", "EXPERIMENT_REGISTRY"},
    # LifecycleMonitor reads the real experiment_registry alpha states + backtest IC/sharpe/verdict; the
    # live side is honestly null (no live/paper feed in L2), so it's real-or-synthetic on the backtest side.
    "/api/v1/console/lifecycle-monitor": {"SYNTHETIC_DETERMINISTIC", "EXPERIMENT_REGISTRY"},
    # MonitoringDrift is honestly PARTIAL: ALPHA_DECAY's baseline + degradation verdict run on the REAL
    # factor backtests when the snapshot is present, but the live-drift side stays synthetic (L2 has no
    # live/paper feed). The partial marker says exactly that — never claim fully real.
    "/api/v1/console/monitoring-drift": {"SYNTHETIC_DETERMINISTIC", "PARTIAL_ALPHA_DECAY_REAL"},
    "/api/v1/console/runtime-contract": "REAL_RUNTIME_CONTRACT",
}


def test_every_frontend_payload_type_has_an_endpoint():
    # if a *Payload type ever maps to a route not under contract, this fails loudly
    routed = {path for path, operations in app.openapi()["paths"].items() if "get" in operations}
    routed = {p for p in routed if p.startswith("/api/v1/console/")}
    expected = set(CONTRACT) | NON_JSON_STREAM_ROUTES
    assert routed == expected, f"contract/route mismatch: {routed ^ expected}"


def test_architecture_inventory_counts_included_router_endpoints():
    body = client.get("/api/v1/console/architecture").json()
    assert body["inventory"]["n_console_endpoints"] == (
        len(CONTRACT) + len(NON_JSON_STREAM_ROUTES)
    )


def test_health_endpoint_is_read_only():
    body = client.get("/health").json()
    assert body["status"] == "healthy" and body["mode"] == "READ_ONLY"


def test_runtime_contract_binds_version_schema_instance_and_l2_boundary():
    body = client.get("/api/v1/console/runtime-contract").json()
    assert body["contract_version"] == "console-runtime.v1"
    assert body["service"] == "research-console-api"
    assert body["instance_id"]
    assert isinstance(body["process_id"], int)
    assert isinstance(body["event_schema_catalog"]["sha256"], str)
    assert body["event_schema_catalog"]["event_type_count"] >= 1
    assert body["safety_boundary"] == {
        "read_only_console": True,
        "authorized_level": "L2_DRY_RUN_ONLY",
        "live_trading_authorized": False,
        "order_submission_allowed": False,
        "broker_connectivity": False,
    }


def test_dry_run_surfaces_paper_order_preview_without_live_capability():
    body = client.get("/api/v1/console/dry-run").json()
    preview = body["paper_order_preview"]
    assert preview["read_only"] is True
    assert preview["safety"]["paper_only"] is True
    assert preview["safety"]["broker_connectivity"] is False
    assert preview["safety"]["broker_order_submission"] is False
    assert preview["verdict"]["live_allowed"] is False
    assert preview["verdict"]["order_submission_allowed"] is False


def test_dry_run_surfaces_l2_paper_operator_receipt_without_live_capability():
    body = client.get("/api/v1/console/dry-run").json()
    operator = body["paper_operator"]
    assert operator["read_only"] is True
    # The receipt is an optional runtime artifact; a clean checkout may not
    # have run the operator yet.  Missing evidence must remain visibly false,
    # never turn into a synthetic success.
    assert isinstance(operator["verdict"]["operator_completed"], bool)
    assert isinstance(operator["verdict"]["paper_preview_ready"], bool)
    assert isinstance(operator["verdict"]["research_packet_ready_for_all_three"], bool)
    if not operator["snapshot_present"]:
        assert operator["verdict"]["operator_completed"] is False
        assert operator["verdict"]["paper_preview_ready"] is False
    assert operator["verdict"]["live_allowed"] is False
    assert operator["verdict"]["order_submission_allowed"] is False
    assert operator["safety"]["paper_only"] is True
    assert operator["safety"]["broker_order_submission"] is False


def test_dry_run_surfaces_realtime_paper_marks_and_backtests_without_live_capability():
    body = client.get("/api/v1/console/dry-run").json()
    forward = body["forward_track"]
    assert isinstance(forward["present"], bool)
    assert isinstance(forward["alphas"], list)
    assert isinstance(forward["evidence_gate_weeks"], int)
    if forward["alphas"]:
        assert isinstance(forward["alphas"][0]["alpha_id"], str)
        assert "evidence_state" in forward["alphas"][0]
    realtime = body["paper_realtime"]
    assert realtime["read_only"] is True
    assert isinstance(realtime["verdict"]["cycle_completed"], bool)
    assert isinstance(realtime["verdict"]["paper_mark_ready"], bool)
    assert isinstance(realtime.get("readiness_class"), str)
    assert isinstance(realtime["verdict"]["backtest_reference_present_for_all_three"], bool)
    assert isinstance(realtime["paper_ledger"], dict)
    assert isinstance(realtime["history"], list)
    assert isinstance(realtime.get("funding_refresh", {}), dict)
    if realtime["history"]:
        point = realtime["history"][-1]
        assert isinstance(point["alpha_id"], str)
        assert isinstance(point["observed_at"], int)
    if not realtime["snapshot_present"]:
        assert realtime["verdict"]["cycle_completed"] is False
        assert realtime["verdict"]["paper_mark_ready"] is False
    assert realtime["verdict"]["live_allowed"] is False
    assert realtime["verdict"]["order_submission_allowed"] is False
    assert realtime["safety"]["paper_only"] is True
    assert realtime["safety"]["public_market_data_only"] is True
    assert realtime["safety"]["broker_order_submission"] is False


def test_dry_run_surfaces_system_native_realtime_alpha_discovery_fail_closed():
    body = client.get("/api/v1/console/dry-run").json()
    autonomy = body["realtime_alpha_autonomy"]
    assert autonomy["read_only"] is True
    assert isinstance(autonomy["snapshot_present"], bool)
    assert autonomy["autonomy"]["requires_developer_ai"] is False
    assert autonomy["forward_shadow"]["verdict"]["promotion_eligible"] is False
    assert autonomy["forward_shadow"]["verdict"]["live_allowed"] is False
    assert autonomy["forward_shadow"]["verdict"]["order_submission_allowed"] is False
    assert autonomy["safety"]["paper_only"] is True
    assert autonomy["safety"]["live_trading_enabled"] is False
    assert autonomy["safety"]["real_broker_enabled"] is False
    assert autonomy["safety"]["allow_order_submission"] is False
    assert autonomy["safety"]["allow_ai_direct_trading"] is False
    assert autonomy["safety"]["promotion_eligible"] is False
    assert autonomy["safety"]["credentials_accessed"] is False
    if autonomy["snapshot_present"]:
        assert isinstance(autonomy["funnel"]["pre_registered"], int)
        assert isinstance(autonomy["top_candidates"], list)
    else:
        assert autonomy["status"] == "NO_SNAPSHOT"
        assert autonomy["autonomy"]["autonomous_search_running"] is False


def test_realtime_alpha_console_projection_blocks_tampered_authority(tmp_path):
    path = tmp_path / "tampered-autonomy.json"
    path.write_text(
        json.dumps(
            {
                "mode": "L2_AUTONOMOUS_REALTIME_ALPHA_RESEARCH",
                "generated_at_ms": 123,
                "autonomy": {"requires_developer_ai": False},
                "registry": {"chain_valid": True},
                "safety": {
                    "paper_only": True,
                    "live_trading_enabled": False,
                    "real_broker_enabled": False,
                    "allow_order_submission": True,
                    "allow_ai_direct_trading": False,
                    "promotion_eligible": False,
                    "credentials_accessed": False,
                },
                "top_candidates": [{"candidate_id": "must-not-surface"}],
            }
        ),
        encoding="utf-8",
    )

    autonomy = artifact_paper_views._realtime_alpha_autonomy_payload(str(path))

    assert autonomy["snapshot_present"] is True
    assert autonomy["boundary_valid"] is False
    assert autonomy["status"] == "SAFE_BLOCKED"
    assert autonomy["top_candidates"] == []
    assert "AUTONOMY_SAFETY_ALLOW_ORDER_SUBMISSION_INVALID" in autonomy["blockers"]
    assert autonomy["safety"]["allow_order_submission"] is False


def test_dry_run_separates_factory_forward_and_execution_paper_populations(
    tmp_path, monkeypatch
):
    factory_path = tmp_path / "factory.json"
    factory_path.write_text(
        json.dumps(
            {
                "fields": ["close", "funding", "global_ls"],
                "rounds": 2,
                "dry_limit": 2,
                "discovered_chain": [],
                "verdict": "no orthogonal survivor",
                "containment": "research; auto_approved=0; drives no lifecycle",
                "generated_at": 1_785_000_000,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        artifact_paper_views, "_FACTORY_LOOP_STATUS", str(factory_path)
    )
    monkeypatch.setattr(
        artifact_paper_views,
        "_CANDIDATE_ADMISSION_STATUS",
        str(tmp_path / "missing-admission.json"),
    )
    monkeypatch.setattr(
        artifact_paper_views,
        "_ALPHA_PRODUCTION_WORKFLOW",
        str(tmp_path / "missing-workflow.json"),
    )
    forward = {
        "alphas": [
            {"alpha_id": f"alpha-{index}", "n_forward_periods": 1}
            for index in range(10)
        ]
    }
    realtime = {
        "alphas": {f"alpha-{index}": {"status": "PAPER_MARKED"} for index in range(3)}
    }

    payload = artifact_paper_views._alpha_pipeline_payload(forward, realtime)

    assert payload["counts"] == {
        "forward_paper": 10,
        "execution_paper": 3,
        "candidate_execution_paper": 3,
        "legacy_diagnostic_paper": 0,
        "forward_only": 7,
        "factory_survivors": 0,
    }
    assert payload["factory"]["data_dimensions"] == 3
    assert payload["factory"]["generated_at"] == 1_785_000_000_000
    assert payload["handoff"]["status"] == "GAPPED"
    assert payload["handoff"]["gap_codes"] == [
        "FACTORY_TO_FORWARD_REGISTRY_NOT_VERIFIED",
        "FORWARD_UNIVERSE_NOT_CONSUMED_BY_EXECUTION_PAPER",
        "UNIQUE_ALPHA_WORKFLOW_NOT_VERIFIED",
    ]
    assert payload["handoff"]["boundary_codes"] == []
    assert sum(row["stage"] == "EXECUTION_PAPER" for row in payload["candidates"]) == 3
    assert sum(row["stage"] == "FORWARD_PAPER" for row in payload["candidates"]) == 7
    assert payload["safety"]["live_allowed"] is False
    assert payload["safety"]["order_submission_allowed"] is False
    assert payload["safety"]["deployment_eligible"] is False


def test_dry_run_prefers_verified_unique_alpha_workflow(tmp_path, monkeypatch):
    candidate_ids = ["carry_oi_majors", "crowd_contra", "top_contra", "new_alpha"]
    candidates = []
    for alpha_id in candidate_ids:
        identity = {"candidate_id": alpha_id, "expression": "canonical"}
        legacy = alpha_id != "new_alpha"
        candidates.append(
            {
                "candidate_id": alpha_id,
                "identity": identity,
                "identity_hash": stable_hash(identity),
                "workflow": {
                    "stage": "PARALLEL_VALIDATION" if legacy else "EXECUTION_PAPER_ELIGIBLE",
                    "bundle_hash": stable_hash({"alpha_id": alpha_id}),
                    "eligible_for_execution_paper": not legacy,
                    "legacy_diagnostic": legacy,
                    "observed_forward_periods": 8,
                    "blockers": ["LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE"] if legacy else [],
                    "gates": [
                        {
                            "gate": "EXECUTION_PAPER",
                            "status": "LEGACY_DIAGNOSTIC" if legacy else "PENDING",
                        }
                    ],
                },
            }
        )
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authority": "ALPHA_PRODUCTION_WORKFLOW",
                "status": "VERIFIED",
                "population_count": 4,
                "event_count": 11,
                "active_candidate_ids": candidate_ids,
                "execution_paper_eligible_ids": ["new_alpha"],
                "candidates": candidates,
                "reconciliation": {
                    "release_commit": "c" * 40,
                    "source_as_of_ms": 1234,
                },
                "safety": {
                    "live_allowed": False,
                    "order_submission_allowed": False,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        artifact_paper_views, "_ALPHA_PRODUCTION_WORKFLOW", str(workflow_path)
    )
    factory_path = tmp_path / "factory.json"
    factory_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "handoff_contract": "VERIFIED_FACTORY_PASS_TO_FORWARD_PAPER",
            }
        ),
        encoding="utf-8",
    )
    admission_path = tmp_path / "admission.json"
    admission_path.write_text(
        json.dumps({"status": "PASS", "registry": {"status": "VERIFIED"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifact_paper_views, "_FACTORY_LOOP_STATUS", str(factory_path))
    monkeypatch.setattr(
        artifact_paper_views, "_CANDIDATE_ADMISSION_STATUS", str(admission_path)
    )
    payload = artifact_paper_views._alpha_pipeline_payload(
        {"alphas": [{"alpha_id": "stale_alpha", "n_forward_periods": 1}]},
        {"alphas": {"stale_alpha": {"status": "PAPER_MARKED"}}},
    )

    assert [row["alpha_id"] for row in payload["candidates"]] == candidate_ids
    assert payload["workflow"] == {
        "authority_present": True,
        "verified": True,
        "population_count": 4,
        "event_count": 11,
        "execution_paper_eligible_count": 1,
        "release_commit": "c" * 40,
        "source_as_of_ms": 1234,
    }
    assert payload["candidates"][-1]["stage"] == "EXECUTION_PAPER_ELIGIBLE"
    assert payload["candidates"][-1]["execution_paper_eligible"] is True
    assert payload["handoff"]["forward_to_execution_dynamic"] is True
    assert payload["handoff"]["status"] == "CONNECTED"
    assert payload["handoff"]["gap_codes"] == []
    assert payload["handoff"]["boundary_codes"] == [
        "LEGACY_DIAGNOSTIC_PAPER_QUARANTINED"
    ]


def test_dry_run_quarantines_legacy_paper_books_from_candidate_pipeline(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        artifact_paper_views,
        "_FACTORY_LOOP_STATUS",
        str(tmp_path / "missing-factory.json"),
    )
    monkeypatch.setattr(
        artifact_paper_views,
        "_CANDIDATE_ADMISSION_STATUS",
        str(tmp_path / "missing-admission.json"),
    )
    monkeypatch.setattr(
        artifact_paper_views,
        "_ALPHA_PRODUCTION_WORKFLOW",
        str(tmp_path / "missing-workflow.json"),
    )
    forward = {
        "alphas": [
            {"alpha_id": "carry_oi_majors", "n_forward_periods": 1},
            {"alpha_id": "crowd_contra", "n_forward_periods": 1},
            {"alpha_id": "top_contra", "n_forward_periods": 0},
            {"alpha_id": "new_challenger", "n_forward_periods": 8},
        ]
    }
    realtime = {
        "alphas": {
            alpha_id: {"status": "PAPER_MARKED"}
            for alpha_id in (
                "carry_oi_majors",
                "crowd_contra",
                "top_contra",
                "new_challenger",
            )
        }
    }

    payload = artifact_paper_views._alpha_pipeline_payload(forward, realtime)
    rows = {row["alpha_id"]: row for row in payload["candidates"]}

    assert payload["counts"]["execution_paper"] == 4
    assert payload["counts"]["candidate_execution_paper"] == 1
    assert payload["counts"]["legacy_diagnostic_paper"] == 3
    assert payload["handoff"]["forward_to_execution_dynamic"] is False
    assert payload["handoff"]["boundary_codes"] == [
        "LEGACY_DIAGNOSTIC_PAPER_QUARANTINED"
    ]
    for alpha_id in ("carry_oi_majors", "crowd_contra", "top_contra"):
        assert rows[alpha_id]["stage"] == "LEGACY_DIAGNOSTIC_PAPER"
        assert rows[alpha_id]["paper_class"] == "LEGACY_DIAGNOSTIC_PAPER"
        assert rows[alpha_id]["deployment_eligible"] is False
        assert "LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE" in rows[alpha_id]["blockers"]
    assert rows["new_challenger"]["stage"] == "EXECUTION_PAPER"
    assert rows["new_challenger"]["paper_class"] == "CANDIDATE_EXECUTION_PAPER"
    assert rows["new_challenger"]["blockers"] == []


def test_dry_run_surfaces_automated_shadow_paper_without_live_capability():
    body = client.get("/api/v1/console/dry-run").json()
    shadow = body["paper_shadow"]
    assert shadow["read_only"] is True
    assert shadow["execution_class"] == "AUTOMATED_NONPROMOTABLE_SHADOW_PAPER"
    assert isinstance(shadow["snapshot_present"], bool)
    assert isinstance(shadow["verdict"]["shadow_paper_running"], bool)
    assert isinstance(shadow["verdict"]["boundary_valid"], bool)
    assert isinstance(shadow["alphas"], dict)
    assert isinstance(shadow["previews"], dict)
    assert isinstance(shadow["history"], list)
    assert isinstance(shadow["totals"], dict)
    assert shadow["verdict"]["official_paper_ready"] is False
    assert shadow["verdict"]["promotion_eligible"] is False
    assert shadow["verdict"]["forward_evidence_eligible"] is False
    assert shadow["verdict"]["deployment_eligible"] is False
    assert shadow["verdict"]["live_allowed"] is False
    assert shadow["verdict"]["order_submission_allowed"] is False
    assert shadow["safety"]["paper_only"] is True
    assert shadow["safety"]["separate_from_official_paper"] is True
    assert shadow["safety"]["credentials_accessed"] is False
    assert shadow["safety"]["broker_connectivity"] is False
    assert shadow["safety"]["broker_order_submission"] is False


def test_shadow_paper_console_payload_fails_closed_on_tampered_safety(
    tmp_path, monkeypatch
):
    auto_path = tmp_path / "auto.json"
    realtime_path = tmp_path / "realtime.json"
    auto_path.write_text(
        json.dumps(
            {
                "verdict": {"shadow_paper_running": True},
                "safety": {
                    "paper_only": True,
                    "broker_connectivity": True,
                    "broker_order_submission": False,
                    "credentials_accessed": False,
                },
                "alphas": {},
            }
        ),
        encoding="utf-8",
    )
    realtime_path.write_text(
        json.dumps(
            {
                "verdict": {
                    "shadow_paper_running": True,
                    "paper_mark_ready": True,
                },
                "safety": {
                    "paper_only": True,
                    "broker_connectivity": False,
                    "broker_order_submission": False,
                    "credentials_accessed": False,
                },
                "persistence": {"chain_valid": True},
                "alphas": {"a": {}, "b": {}, "c": {}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifact_snapshots, "_PAPER_SHADOW_AUTO", str(auto_path))
    monkeypatch.setattr(
        artifact_snapshots, "_PAPER_SHADOW_REALTIME", str(realtime_path)
    )

    shadow = artifact_snapshots._paper_shadow_payload()

    assert shadow["status"] == "SAFE_BLOCKED"
    assert shadow["verdict"]["boundary_valid"] is False
    assert shadow["verdict"]["shadow_paper_running"] is False
    assert shadow["verdict"]["live_allowed"] is False
    assert shadow["verdict"]["order_submission_allowed"] is False


def test_shadow_paper_console_requires_authoritative_candidate_coverage(tmp_path):
    auto_path = tmp_path / "auto.json"
    realtime_path = tmp_path / "realtime.json"
    expected = ["legacy", "challenger"]
    common_safety = {
        "paper_only": True,
        "broker_connectivity": False,
        "broker_order_submission": False,
        "credentials_accessed": False,
    }
    auto_path.write_text(
        json.dumps(
            {
                "verdict": {
                    "shadow_paper_running": True,
                    "candidate_coverage_complete": True,
                },
                "candidate_universe": {"expected": expected},
                "safety": common_safety,
                "alphas": {alpha_id: {} for alpha_id in expected},
            }
        ),
        encoding="utf-8",
    )
    realtime_path.write_text(
        json.dumps(
            {
                "verdict": {
                    "shadow_paper_running": True,
                    "paper_mark_ready": True,
                    "candidate_coverage_complete": True,
                },
                "candidate_universe": {"expected": expected},
                "safety": common_safety,
                "persistence": {"chain_valid": True},
                "alphas": {
                    alpha_id: {"status": "PAPER_MARKED"}
                    for alpha_id in expected
                },
            }
        ),
        encoding="utf-8",
    )

    shadow = artifact_paper_views._paper_shadow_payload(
        auto_path=str(auto_path), realtime_path=str(realtime_path)
    )

    assert shadow["status"] == "RUNNING"
    assert shadow["verdict"]["shadow_paper_running"] is True
    assert shadow["verdict"]["candidate_coverage_complete"] is True
    assert shadow["verdict"]["candidate_count"] == 2
    assert shadow["candidate_universe"]["expected"] == expected


def test_dry_run_surfaces_semantic_paper_health_without_live_capability():
    body = client.get("/api/v1/console/dry-run").json()
    health = body["paper_health"]
    assert health["read_only"] is True
    assert isinstance(health["verdict"]["paper_ready"], bool)
    assert isinstance(health["blockers"], list)
    assert isinstance(health.get("readiness_class"), str)
    assert isinstance(health.get("research_risk_bundles"), dict)
    assert isinstance(health["forward_evidence"], dict)
    assert isinstance(health["forward_evidence"]["complete_for_all_three"], bool)
    assert health["verdict"]["live_allowed"] is False
    assert health["verdict"]["order_submission_allowed"] is False
    assert health["safety"]["paper_only"] is True
    assert health["safety"]["broker_order_submission"] is False


def test_dry_run_surfaces_isolated_small_paper_profile_read_only():
    body = client.get("/api/v1/console/dry-run").json()
    profile = body["paper_profiles"]["small_10k"]
    assert profile["read_only"] is True
    assert profile["aum"] == 10_000.0
    assert isinstance(profile["snapshot_present"], bool)
    assert isinstance(profile["realtime"]["cycle_completed"], bool)
    assert isinstance(profile["realtime"]["chain_valid"], bool)
    assert isinstance(profile["realtime"]["paper_ledger"], dict)
    assert isinstance(profile["realtime"]["history"], list)
    assert profile["realtime"]["paper_ledger"].get("n_decisions") is None or isinstance(
        profile["realtime"]["paper_ledger"].get("n_decisions"), int
    )
    assert profile["realtime"]["total_equity"] is None or isinstance(
        profile["realtime"]["total_equity"], (int, float)
    )
    assert profile["realtime"]["total_net_pnl"] is None or isinstance(
        profile["realtime"]["total_net_pnl"], (int, float)
    )
    assert isinstance(profile["health"]["paper_ready"], bool)
    assert isinstance(profile["health"]["blockers"], list)
    assert isinstance(profile["forward_track"], dict)
    assert isinstance(profile["forward_track"]["alphas"], list)
    if profile["health"]["paper_ready"]:
        assert profile["realtime"]["total_equity"] is not None
        assert profile["realtime"]["total_net_pnl"] is not None
    assert profile["safety"]["paper_only"] is True
    assert profile["safety"]["live_allowed"] is False
    assert profile["safety"]["order_submission_allowed"] is False


def test_every_endpoint_satisfies_the_frontend_contract():
    failures: list[str] = []
    for path, (query, keys) in CONTRACT.items():
        resp = client.get(path + query)
        if resp.status_code != 200:
            failures.append(f"{path}: HTTP {resp.status_code}")
            continue
        body = resp.json()
        for marker in BASE_MARKERS:
            if marker not in body:
                failures.append(f"{path}: missing base marker '{marker}'")
        if body.get("read_only") is not True:
            failures.append(f"{path}: read_only is not True")
        # honesty marker (audit P1): synthetic figures must be self-labelled, never
        # presented as real research/PnL on the wire. Real-data endpoints carry a
        # distinct provenance marker (still read-only) — never the synthetic label.
        expected_prov = REAL_DATA_PROVENANCE.get(path, "SYNTHETIC_DETERMINISTIC")
        actual_prov = body.get("data_provenance")
        prov_ok = actual_prov in expected_prov if isinstance(expected_prov, set) else actual_prov == expected_prov
        if not prov_ok:
            failures.append(f"{path}: data_provenance {actual_prov!r} not in {expected_prov!r}")
        if body.get("snapshot_status") not in SNAPSHOT_STATUSES:
            failures.append(f"{path}: invalid snapshot_status {body.get('snapshot_status')!r}")
        quality = body.get("data_quality")
        if not isinstance(quality, dict):
            failures.append(f"{path}: data_quality is not an object")
        else:
            if quality.get("status") != body.get("snapshot_status"):
                failures.append(f"{path}: data_quality.status does not match snapshot_status")
            if quality.get("provenance") != actual_prov:
                failures.append(f"{path}: data_quality.provenance does not match data_provenance")
            if quality.get("snapshot_ids") != body.get("snapshot_ids"):
                failures.append(f"{path}: data_quality.snapshot_ids does not match snapshot_ids")
        for key in keys:
            if key not in body:
                failures.append(f"{path}: missing contract key '{key}'")
    assert not failures, "frontend contract drift:\n" + "\n".join(failures)


def test_live_trading_forward_track_fails_closed_to_attested_baseline(tmp_path, monkeypatch):
    baseline = tmp_path / "forward_evidence_baseline.json"
    baseline.write_text(json.dumps({
        "observed_at_ms": 1000,
        "max_age_ms": 172_800_000,
        "counts": {
            "attested_realizations": 0,
            "attested_pending": 0,
            "legacy_unattested_decisions": 2,
        },
        "evidence_gate": {"minimum_weeks": 8, "eligible": False},
        "data_admission": {"status": "BLOCKED"},
    }), encoding="utf-8")
    monkeypatch.setattr(artifact_snapshots, "_FORWARD_EVIDENCE_BASELINE", str(baseline), raising=False)

    payload = artifact_snapshots._live_trading_payload(now_ms=1000)

    assert payload["forward_track"]["evidence_state"] == "DATA_BLOCKED"
    assert payload["forward_track"]["attested_realizations"] == 0
    assert payload["forward_track"]["legacy_unattested_decisions"] == 2
    assert payload["forward_track"]["actions_allowed"] is False


def test_live_trading_forward_track_marks_missing_invalid_and_stale_evidence_fail_closed(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(artifact_snapshots, "_FORWARD_EVIDENCE_BASELINE", str(missing), raising=False)
    unavailable = artifact_snapshots._live_trading_payload(now_ms=1000)["forward_track"]
    assert unavailable == {
        "present": False,
        "evidence_state": "UNAVAILABLE",
        "actions_allowed": False,
    }

    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(artifact_snapshots, "_FORWARD_EVIDENCE_BASELINE", str(invalid), raising=False)
    assert artifact_snapshots._live_trading_payload(now_ms=1000)["forward_track"] == unavailable

    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps({
        "observed_at_ms": 1000,
        "max_age_ms": 172_800_000,
        "counts": {
            "attested_realizations": 7,
            "attested_pending": 1,
            "legacy_unattested_decisions": 2,
        },
        "evidence_gate": {"minimum_weeks": 8, "eligible": False},
        "data_admission": {"status": "CURRENT"},
    }), encoding="utf-8")
    monkeypatch.setattr(artifact_snapshots, "_FORWARD_EVIDENCE_BASELINE", str(stale), raising=False)
    stale_payload = artifact_snapshots._live_trading_payload(now_ms=172_801_001)["forward_track"]
    assert stale_payload["present"] is True
    assert stale_payload["evidence_state"] == "STALE"
    assert stale_payload["actions_allowed"] is False


def test_query_bound_endpoints_echo_the_requested_alpha():
    # the page binds its selector to the echoed selected_alpha_id; if a VALID id
    # were not echoed faithfully the page would render the wrong alpha's data
    for path in ("/api/v1/console/risk-dashboard", "/api/v1/console/paper-trading",
                 "/api/v1/console/computation-lab"):
        body = client.get(path + "?alpha_id=alpha-3&cost_bps=20&stress=1.4").json()
        assert body["selected_alpha_id"] == "alpha-3", f"{path} did not echo alpha-3"


def test_unknown_alpha_id_falls_back_to_default_visibly():
    # INTENTIONAL fail-soft: an unknown alpha_id (e.g. a stale/typo'd URL) resolves
    # to the first candidate (_alpha_index returns 0 on miss) and ECHOES that id, so
    # the UI shows which alpha it actually rendered rather than 500-ing or labelling
    # default data as the requested id. Pinned so the fallback stays deliberate.
    for path in ("/api/v1/console/risk-dashboard", "/api/v1/console/paper-trading"):
        body = client.get(path + "?alpha_id=does-not-exist").json()
        assert body["selected_alpha_id"] == "alpha-1", f"{path} did not fall back visibly"


def test_ic_panel_by_period_schema_v2_consistency():
    """IC 面板 schema v2(per-factor 持有期):快照存在且 schema_version>=2 时,每行必须带
    holding_periods + by_period,且 by_period['1'] 的六统计 + per_year 与顶层字段完全一致
    (顶层 h=1 是向后兼容锚)。绑定 schema_version 而非'有没有 by_period',消除"本地旧快照
    静默跳过"窗口(评审 required_change);CI 无快照时诚实 skip。"""
    import json as _json
    import os as _os

    import pytest as _pytest

    from services.console.api.main import _IC_PANEL_SNAPSHOT
    if not _os.path.exists(_IC_PANEL_SNAPSHOT):
        _pytest.skip("no ic_panel snapshot (CI/fresh checkout)")
    snap = _json.load(open(_IC_PANEL_SNAPSHOT, encoding="utf-8"))
    if int(snap.get("schema_version", 1)) < 2:
        _pytest.skip("pre-v2 snapshot — rerun tools/ic_panel_research.py to migrate")

    body = client.get("/api/v1/console/ic-panel").json()
    assert body["snapshot_present"] is True
    assert body["rows"], "v2 snapshot must have rows"
    for row in body["rows"]:
        assert row.get("holding_periods"), f"{row.get('name')}: missing holding_periods"
        bp = row.get("by_period")
        assert bp and "1" in bp, f"{row.get('name')}: missing by_period['1']"
        for f in ("mean_ic", "ic_std", "icir", "hit_rate", "t_stat", "n_periods", "per_year"):
            assert row[f] == bp["1"][f], f"{row.get('name')}: top-level {f} != by_period['1']"
        assert set(bp) == {str(h) for h in row["holding_periods"]}


def test_risk_dashboard_stress_block_carries_provenance_and_scenarios():
    # Rank #7: the stress-gate result surfaced to the console must carry its honesty
    # provenance (REAL_HISTORICAL_REPLAY when timestamps reached the engine, else the
    # labeled SYNTHETIC_MULTIPLIER_UNCALIBRATED fallback) plus per-scenario losses —
    # a stress number without provenance must never reach the wire (Rank #7 contract).
    body = client.get("/api/v1/console/risk-dashboard?alpha_id=alpha-1").json()
    stress = body["primary_report"]["stress"]
    for key in ("passed", "provenance", "worst_scenario", "worst_stress_loss",
                "scenarios_survived", "total_scenarios", "scenarios_skipped", "scenario_losses"):
        assert key in stress, f"stress block missing '{key}'"
    assert stress["provenance"] in ("REAL_HISTORICAL_REPLAY", "SYNTHETIC_MULTIPLIER_UNCALIBRATED")
    assert isinstance(stress["scenario_losses"], dict) and stress["scenario_losses"]


def test_snapshot_metadata_never_upgrades_synthetic_or_missing_data():
    from services.console.api.common import snapshot_metadata

    synthetic = snapshot_metadata({"data_provenance": "SYNTHETIC_DETERMINISTIC"})
    assert synthetic["snapshot_status"] == "SYNTHETIC"
    assert synthetic["snapshot_id"] is None and synthetic["snapshot_ids"] == []
    assert synthetic["data_quality"]["is_synthetic"] is True

    missing = snapshot_metadata(
        {
            "data_provenance": "SYNTHETIC_DETERMINISTIC",
            "snapshot_present": False,
            "verdict": "NO_SNAPSHOT",
        }
    )
    assert missing["snapshot_status"] == "NO_SNAPSHOT"
    assert missing["data_quality"]["is_available"] is False

    blocked = snapshot_metadata({"data_provenance": "REAL_DATA", "verdict": "DATA_BLOCKED"})
    assert blocked["snapshot_status"] == "DATA_BLOCKED"
    assert blocked["data_quality"]["is_blocked"] is True

    nested_blocked = snapshot_metadata(
        {"data_provenance": "REAL_DATA", "data_quality": {"status": "BLOCK"}}
    )
    assert nested_blocked["snapshot_status"] == "DATA_BLOCKED"

    real = snapshot_metadata(
        {
            "data_provenance": "REAL_PIT_DATASETS",
            "snapshot_present": True,
            "datasets": [{"snapshot_id": "pit-001"}],
        }
    )
    assert real["snapshot_status"] == "REAL"
    assert real["snapshot_id"] == "pit-001"


def test_data_catalog_port_failure_is_explicitly_unavailable(monkeypatch):
    from services.console.api import data_catalog

    def unavailable():
        raise RuntimeError("port unavailable")

    monkeypatch.setattr(data_catalog.data_catalog_runtime, "require", unavailable)
    payload = data_catalog._data_catalog_payload()
    assert payload["data_provenance"] == "UNAVAILABLE"
    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["snapshot_present"] is False
    assert payload["datasets"] == [] and payload["features"] == []
    assert payload["data_quality"]["status"] == "UNAVAILABLE"


def test_ashare_alpha_endpoint_numeric_sanity():
    """A股 alpha 端点:形状契约之外的数值合理性(|IC|<=1、持仓权重和≈1、净值曲线非空)。

    快照缺失(CI)时 fail-closed 到 SYNTHETIC 也是合法响应——此时只校验诚实标记。"""
    body = client.get("/api/v1/console/ashare-alpha").json()
    assert body["source"] == "BACKEND_COMPUTED_SNAPSHOT"
    assert body["read_only"] is True
    assert body["provenance"] in ("REAL_ASHARE_ALPHA_SNAPSHOT", "SYNTHETIC_DETERMINISTIC")
    if body["provenance"] != "REAL_ASHARE_ALPHA_SNAPSHOT":
        assert body["snapshot_present"] is False
        assert body["honest_limitations"]
        return
    for f in body["factors"]:
        assert abs(f["train_ic"]) <= 1.0 and abs(f["test_ic"]) <= 1.0
        assert isinstance(f["kept"], bool)
    combo = body["combo"]
    assert abs(combo["h1_test_ic"]) <= 1.0 and abs(combo["h5_test_ic"]) <= 1.0
    assert 0.0 <= combo["hit_rate"] <= 1.0
    holdings = body["holdings_latest"]
    assert holdings, "real snapshot must carry last-week holdings"
    assert abs(sum(h["weight"] for h in holdings) - 1.0) < 1e-6
    assert all(w["weight"] > 0 for w in holdings)
    basket = body["basket"]
    assert basket["equity_test"] and basket["bench_equity_test"]
    assert all(isinstance(v, (int, float)) for v in basket["equity_test"])
    assert basket["n"] > 0 and basket["aum_cny"] > 0
    dry = body["dry_run"]
    assert dry["run_id"] and dry["n_approved"] >= 0 and dry["n_rejected"] >= 0
    paper = body["paper_track"]
    assert paper["kind"] in ("BACKFILL", "FORWARD") and paper["n_weeks"] > 0
    assert abs(paper["weekly_ic_mean"]) <= 1.0
    assert body["variants"] and any(v["is_champion"] for v in body["variants"])
    assert body["cost_grid"] and body["turnover_caps"]
    assert body["data_freshness"]["stocks"] > 0 and body["data_freshness"]["last_date"] > 0
    assert body["honest_limitations"]
