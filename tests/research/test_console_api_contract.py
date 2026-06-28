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
from fastapi.testclient import TestClient

from services.research.console_api.main import app

client = TestClient(app)

BASE_MARKERS = ("source", "read_only", "api_version")

# endpoint -> (query string, required top-level keys per the frontend TS Payload type).
# Keys mirror apps/research-console/src/lib/types/research.ts (extracted from the
# exported *Payload interfaces). Base markers are checked separately for all.
CONTRACT: dict[str, tuple[str, tuple[str, ...]]] = {
    "/api/v1/console/overview": ("", ("stats", "funnel", "agents")),
    "/api/v1/console/agents": ("", ("summary", "agents")),
    "/api/v1/console/events": ("", ("events",)),
    "/api/v1/console/phase-3a-readiness": ("", ("readiness",)),
    "/api/v1/console/architecture": ("", (
        "current_level", "target_level", "phase3a_decision", "implementation_authorized",
        "architecture_score", "modules", "data_flows", "gaps", "safety_boundary")),
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
    "/api/v1/console/alpha-zoo": ("", ("rows",)),
    "/api/v1/console/dsl-explorer": ("", ("exprs", "analysis_backend")),
    "/api/v1/console/system-completeness": ("", (
        "audit_mode", "current_level", "institutional_target", "readiness_score", "summary",
        "modules", "checks", "gaps", "roadmap", "safety_boundary")),
    "/api/v1/console/system-health": ("", ("services", "gates", "safety_checks")),
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
    # alpha lifecycle monitor: real experiment_registry backtest-side artifacts (live side honestly null)
    "/api/v1/console/lifecycle-monitor": ("", ("summary", "rows", "thresholds", "lifecycle_policy")),
}

# Endpoints serving REAL (non-synthetic) read-only data carry a distinct provenance
# marker. They still must be read-only snapshots; only the honesty label differs.
_REAL_OR_SYNTHETIC = {"SYNTHETIC_DETERMINISTIC", "REAL_CRYPTO_SERIES"}
REAL_DATA_PROVENANCE = {
    "/api/v1/console/crypto-live": "REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT",
    "/api/v1/console/safety-primitives": "REAL_SAFETY_PRIMITIVES",   # reads the real KillSwitch/gate/config
    "/api/v1/console/research-runs": "RESEARCH_RUN_REGISTRY",
    "/api/v1/console/research-run": "RESEARCH_RUN_REGISTRY",
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
    "/api/v1/console/alpha-zoo": _REAL_OR_SYNTHETIC,
    # DataCatalog enumerates the real on-disk PIT store (crypto_daily.db) for its DATASETS when present;
    # the feature/lineage block stays synthetic regardless (honest partial). Synthetic when no db (CI).
    "/api/v1/console/data-catalog": {"SYNTHETIC_DETERMINISTIC", "REAL_PIT_DATASETS"},
    # Events derive from the real run_registry when it has runs, else synthetic (CI / fresh checkout).
    "/api/v1/console/events": {"SYNTHETIC_DETERMINISTIC", "RESEARCH_RUN_REGISTRY"},
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
}


def test_every_frontend_payload_type_has_an_endpoint():
    # if a *Payload type ever maps to a route not under contract, this fails loudly
    routed = {r.path for r in app.routes if getattr(r, "methods", None) and "GET" in r.methods}
    routed = {p for p in routed if p.startswith("/api/v1/console/")}
    assert routed == set(CONTRACT), f"contract/route mismatch: {routed ^ set(CONTRACT)}"


def test_health_endpoint_is_read_only():
    body = client.get("/health").json()
    assert body["status"] == "healthy" and body["mode"] == "READ_ONLY"


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
        for key in keys:
            if key not in body:
                failures.append(f"{path}: missing contract key '{key}'")
    assert not failures, "frontend contract drift:\n" + "\n".join(failures)


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
