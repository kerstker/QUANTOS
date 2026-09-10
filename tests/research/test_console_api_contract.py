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
import hashlib
import json

from fastapi.testclient import TestClient

from quantos.contracts.hashing import stable_hash
from services.console.main import app
from services.console.api import artifact_snapshots
from services.console.api import artifact_paper_views
from services.console.api import alpha_lifecycle_views
from services.console.api import ashare_views

client = TestClient(app)


def test_factory_execution_cost_contract_keeps_unknown_real_costs():
    from services.research.ai_factory_execution_feedback import execution_feedback
    from services.research.perpetual_alpha import replay
    from tests.research.test_perpetual_alpha import market
    report = execution_feedback(replay(**market(), decision=lambda at, held: .25,
                                      slip_bps=5, stop=True))['profitability_attribution']
    assert report['status'] == 'RECONCILED_TECHNICAL_ONLY'
    assert report['actual_venue_fees'] is None and report['spread_component'] is None
    assert report['economic_eligible'] is False
    assert report['reconciliation_residual'] == '0'
    assert set(report['position_contribution']) == {'long', 'short'}


def test_system_health_includes_operational_failure_receipts(tmp_path, monkeypatch):
    from services.console.api import runtime_health, system_views

    monkeypatch.setattr(runtime_health, "_STATE_ROOT", tmp_path)
    monkeypatch.setattr(system_views.time, "time", lambda: 1000)
    monkeypatch.setattr(system_views, "_real_monitoring_snapshot", lambda: {"healthy": True})
    monkeypatch.setattr(system_views, "_ashare_search_runtime_service", lambda: {"service": "ashare", "status": "unknown"})
    cache = tmp_path / "data/cache"
    cache.mkdir(parents=True)
    (cache / "l2_backup_status.json").write_text(json.dumps({
        "verdict": "BREACH", "observed_at_ms": 999000,
        "last_reason_code": "RESTORE_RTO_EXCEEDED",
    }))
    (cache / "binance_book_depth_archive_status.json").write_text(json.dumps({
        "status": "FAIL", "generated_at_ms": 999000, "reason_code": "QUALITY_REJECTED",
    }))
    rows = {row["service"]: row for row in system_views._system_health_payload()["services"]}
    for service, reason in (("l2-backup-restore", "RESTORE_RTO_EXCEEDED"), ("binance-book-depth-archive", "QUALITY_REJECTED")):
        assert rows[service]["status"] == "down"
        assert rows[service]["reason_code"] == reason
        assert rows[service]["age_ms"] == 1000
        assert rows[service]["observed_at_ms"] == 999000
    assert rows["binance-rpi-depth"]["reason_code"] == "RECEIPT_MISSING"

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
    "CORRUPT_OR_UNREADABLE",
    "UNAVAILABLE",
}

# endpoint -> (query string, required top-level keys per the frontend TS Payload type).
# Keys mirror apps/research-console/src/lib/types/research.ts (extracted from the
# exported *Payload interfaces). Base markers are checked separately for all.
CONTRACT: dict[str, tuple[str, tuple[str, ...]]] = {
    "/api/v1/console/ai-factory-lifecycle": ("", ("workspace", "workspaces", "models", "comparisons", "campaigns", "rounds", "sources", "capabilities", "scheduler_enabled", "worker", "errors")),
    "/api/v1/console/ai-factory": ("", ("workspace", "workspaces", "sessions", "selected", "trials", "events", "provider", "errors", "forward", "assessment", "verification", "pipeline", "comparison", "resident", "workflow", "source_capture", "profitability", "forward_profitability")),
    "/api/v1/console/forward-dashboard": ("", ("as_of_ms", "observations", "schedule", "errors")),
    "/api/v1/console/market-dashboard": ("", ("symbol", "symbols", "status", "as_of_ms", "observed_at_ms", "candles", "samples", "depth", "integrity", "reason")),
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
    "/api/v1/console/technical-replay": ("?run_id=__contract__", ("technical_replay",)),
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
    "/api/v1/console/hourly-alpha": ("", (
        "schema_version", "as_of", "status", "freshness", "watermark",
        "batch_funnel", "candidate_states", "stores", "safety")),
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
    # Newly admitted TDX daily-bar search evidence. Diagnostic/read-only only:
    # missing historical PIT fundamentals and survivor-free membership block promotion.
    "/api/v1/console/ashare-alpha-search": ("", (
        "candidate_usefulness", "source_summary", "surface_coverage",
        "surface_results", "untouched_forward_observation")),
    "/api/v1/console/ashare-forward-observation": ("", (
        "status", "sessions_observed", "minimum_sessions", "minimum_sessions_met",
        "selection_locked", "chain_valid", "cycle", "candidates", "observation", "safety")),
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
NON_JSON_STREAM_ROUTES = {
    "/api/v1/console/stream/dry-run",
    "/api/v1/console/stream/mft-execution",
}

# Stable backend-only read contracts reserved for the Wave 3 console.  They are
# intentionally not mapped to a frontend *Payload type during Wave 2.
BACKEND_ONLY_ROUTES = {
    "/api/v1/console/mft-execution",
    "/api/v1/console/mft-execution/delta",
}

# Endpoints serving REAL (non-synthetic) read-only data carry a distinct provenance
# marker. They still must be read-only snapshots; only the honesty label differs.
_REAL_OR_SYNTHETIC = {"SYNTHETIC_DETERMINISTIC", "REAL_CRYPTO_SERIES"}
REAL_DATA_PROVENANCE = {
    "/api/v1/console/ai-factory": "REAL_AI_FACTORY_DIAGNOSTIC",
    "/api/v1/console/ai-factory-lifecycle": "REAL_AI_FACTORY_DIAGNOSTIC",
    "/api/v1/console/market-dashboard": "REAL_PUBLIC_MARKET_DIAGNOSTIC",
    "/api/v1/console/forward-dashboard": "REAL_FORWARD_OBSERVATION_DIAGNOSTIC",
    "/api/v1/console/agents": "RESEARCH_RUN_REGISTRY_AND_GUARDRAIL_CONTRACT",
    "/api/v1/console/system-health": "REAL_RUNTIME_HEALTH",
    "/api/v1/console/crypto-live": "REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT",
    "/api/v1/console/safety-primitives": "REAL_SAFETY_PRIMITIVES",   # reads the real KillSwitch/gate/config
    "/api/v1/console/research-runs": "RESEARCH_RUN_REGISTRY",
    "/api/v1/console/research-run": "RESEARCH_RUN_REGISTRY",
    "/api/v1/console/technical-replay": "VERIFIED_RUN_REGISTRY_TECHNICAL_MIRROR",
    # BacktestResults aggregates done runs by role; synthetic (all-None blocks) when the registry is empty.
    "/api/v1/console/backtest-results": {"SYNTHETIC_DETERMINISTIC", "RESEARCH_RUN_REGISTRY"},
    # FX research: REAL ECB snapshot when present, SYNTHETIC-empty in CI / fresh checkout. Both honest.
    "/api/v1/console/fx-research": {"SYNTHETIC_DETERMINISTIC", "REAL_FX_ECB_OFFLINE_SNAPSHOT"},
    # carry readiness: REAL carry snapshots when present, SYNTHETIC-empty in CI / fresh checkout.
    "/api/v1/console/carry-readiness": {"SYNTHETIC_DETERMINISTIC", "REAL_CARRY_OFFLINE_SNAPSHOT"},
    "/api/v1/console/ashare-alpha-search": {
        "REPOSITORY_EVIDENCE", "REAL_ASHARE_TECHNICAL_RUNTIME", "UNAVAILABLE"
    },
    "/api/v1/console/ashare-forward-observation": {
        "UNAVAILABLE", "REAL_ASHARE_FORWARD_OBSERVATION"
    },
    # dry-run pipeline: REAL DryRunStore runs when present, SYNTHETIC-empty in CI / fresh checkout.
    "/api/v1/console/dry-run": {"SYNTHETIC_DETERMINISTIC", "REAL_DRY_RUN_OFFLINE_SNAPSHOT"},
    "/api/v1/console/hourly-alpha": "REAL_HOURLY_ALPHA_AUTHORITIES",
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
    expected = set(CONTRACT) | NON_JSON_STREAM_ROUTES | BACKEND_ONLY_ROUTES
    assert routed == expected, f"contract/route mismatch: {routed ^ expected}"


def test_architecture_inventory_counts_included_router_endpoints():
    body = client.get("/api/v1/console/architecture").json()
    assert body["inventory"]["n_console_endpoints"] == (
        len(CONTRACT) + len(NON_JSON_STREAM_ROUTES) + len(BACKEND_ONLY_ROUTES)
    )


def test_health_endpoint_is_read_only():
    body = client.get("/health").json()
    assert body["status"] == "healthy" and body["mode"] == "READ_ONLY"


def test_ashare_search_prefers_current_runtime_artifact(monkeypatch, tmp_path):
    canonical = tmp_path / "ashare_tdx_pit.sqlite"
    canonical.write_bytes(b"canonical tdx sqlite test bytes")
    canonical_sha256 = hashlib.sha256(canonical.read_bytes()).hexdigest()
    runtime = tmp_path / "runtime-search.json"
    release = tmp_path / "release-search.json"
    runtime.write_text(json.dumps({
        "artifact_kind": "ASHARE_ALPHA_SEARCH_INSTITUTIONAL_SURFACES_V2",
        "evidence_status": "TECHNICAL_VALIDATION_ONLY",
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "candidate_usefulness": "RUNTIME_RESULT",
        "source": {
            "rows": 2, "instruments": 1, "last_date": 20260828,
            "sha256": canonical_sha256,
        },
        "surface_confirmations": {},
        "requested_surface_coverage": {},
        "untouched_forward_observation": {},
    }), encoding="utf-8")
    release.write_text(json.dumps({
        "evidence_status": "TECHNICAL_VALIDATION_ONLY",
        "candidate_usefulness": "STALE_RELEASE_RESULT",
        "source": {},
    }), encoding="utf-8")
    monkeypatch.setattr(ashare_views, "_RUNTIME_SEARCH_PATH", runtime)
    monkeypatch.setattr(ashare_views, "_INSTITUTIONAL_SEARCH_PATH", release)
    monkeypatch.setattr(ashare_views, "_CANONICAL_TDX_SQLITE", canonical)

    payload = ashare_views._ashare_institutional_search_payload()

    assert payload["candidate_usefulness"] == "RUNTIME_RESULT"
    assert payload["data_provenance"] == "REAL_ASHARE_TECHNICAL_RUNTIME"
    assert payload["artifact_scope"] == "RUNTIME_STATE"

    stale_document = json.loads(runtime.read_text(encoding="utf-8"))
    stale_document["source"]["sha256"] = "a" * 64
    runtime.write_text(json.dumps(stale_document), encoding="utf-8")
    stale = ashare_views._ashare_institutional_search_payload()
    assert stale["snapshot_present"] is False
    assert stale["data_provenance"] == "UNAVAILABLE"
    assert stale["artifact_scope"] == "RUNTIME_SOURCE_BINDING_INVALID"

    runtime.write_text("[]", encoding="utf-8")
    invalid = ashare_views._ashare_institutional_search_payload()
    assert invalid["snapshot_present"] is False
    assert invalid["data_provenance"] == "UNAVAILABLE"
    assert invalid["artifact_scope"] == "RUNTIME_ARTIFACT_INVALID"
    assert invalid["candidate_usefulness"] == "UNAVAILABLE"


def test_ashare_forward_endpoint_exposes_only_valid_observation(monkeypatch, tmp_path):
    canonical = tmp_path / "ashare_tdx_pit.sqlite"
    canonical.write_bytes(b"canonical forward source")
    canonical_sha256 = hashlib.sha256(canonical.read_bytes()).hexdigest()
    artifact = tmp_path / "forward.json"
    artifact.write_text(json.dumps({
        "artifact_kind": "ASHARE_UNTOUCHED_FORWARD_OBSERVATION_V1",
        "evidence_status": "TECHNICAL_VALIDATION_ONLY",
        "status": "ARMED_AWAITING_NEW_SESSIONS",
        "sessions_observed": 0,
        "minimum_sessions": 60,
        "minimum_sessions_met": False,
        "selection_locked": True,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "chain_valid": True,
        "source": {"sha256": canonical_sha256},
        "cycle": {"pending_count": 0},
        "candidates": {"candidate": {}},
        "safety": {
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(ashare_views, "_FORWARD_OBSERVATION_PATH", artifact)
    monkeypatch.setattr(ashare_views, "_CANONICAL_TDX_SQLITE", canonical)
    monkeypatch.setattr(ashare_views, "_forward_authority_is_valid", lambda _document: True)

    payload = ashare_views._ashare_forward_observation_payload()

    assert payload["data_provenance"] == "REAL_ASHARE_FORWARD_OBSERVATION"
    assert payload["status"] == "ARMED_AWAITING_NEW_SESSIONS"
    assert payload["selection_locked"] is True
    assert payload["chain_valid"] is True
    assert payload["observation"]["execution_allowed"] is False

    stale_document = json.loads(artifact.read_text(encoding="utf-8"))
    stale_document["source"]["sha256"] = "f" * 64
    artifact.write_text(json.dumps(stale_document), encoding="utf-8")
    stale = ashare_views._ashare_forward_observation_payload()
    assert stale["snapshot_present"] is False
    assert stale["data_provenance"] == "UNAVAILABLE"
    assert stale["status"] == "INVALID_SOURCE_BINDING"
    assert stale["chain_valid"] is False


def test_ashare_canonical_digest_cache_rehashes_only_on_stat_identity_change(
    monkeypatch,
    tmp_path,
):
    canonical = tmp_path / "ashare_tdx_pit.sqlite"
    canonical.write_bytes(b"first canonical database")
    calls = 0
    full_hash = ashare_views._full_file_sha256

    def counted_hash(path, identity):
        nonlocal calls
        calls += 1
        return full_hash(path, identity)

    monkeypatch.setattr(ashare_views, "_full_file_sha256", counted_hash)
    first = ashare_views._canonical_tdx_sha256(canonical)
    assert first == hashlib.sha256(canonical.read_bytes()).hexdigest()
    assert ashare_views._canonical_tdx_sha256(canonical) == first
    assert calls == 1

    canonical.write_bytes(b"replacement canonical database with another size")
    second = ashare_views._canonical_tdx_sha256(canonical)
    assert second == hashlib.sha256(canonical.read_bytes()).hexdigest()
    assert second != first
    assert calls == 2


def test_runtime_ashare_evidence_fails_closed_without_canonical_source(
    monkeypatch,
    tmp_path,
):
    runtime = tmp_path / "runtime-search.json"
    runtime.write_text(json.dumps({
        "artifact_kind": "ASHARE_ALPHA_SEARCH_INSTITUTIONAL_SURFACES_V2",
        "evidence_status": "TECHNICAL_VALIDATION_ONLY",
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "source": {"sha256": "a" * 64},
    }), encoding="utf-8")
    monkeypatch.setattr(ashare_views, "_RUNTIME_SEARCH_PATH", runtime)
    monkeypatch.setattr(
        ashare_views,
        "_CANONICAL_TDX_SQLITE",
        tmp_path / "missing-canonical.sqlite",
    )

    payload = ashare_views._ashare_institutional_search_payload()

    assert payload["snapshot_present"] is False
    assert payload["data_provenance"] == "UNAVAILABLE"
    assert payload["artifact_scope"] == "RUNTIME_CANONICAL_SOURCE_UNAVAILABLE"


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


def test_runtime_contract_uses_pinned_release_commit_when_build_sha_is_absent(
    monkeypatch,
):
    release_commit = "c1b21f9f0b3d586ab8aa12fa8f1e00a73bc9c118"
    monkeypatch.delenv("QUANTOS_BUILD_SHA", raising=False)
    monkeypatch.setenv("QUANTOS_RELEASE_COMMIT", release_commit)

    body = client.get("/api/v1/console/runtime-contract").json()

    assert body["build_sha"] == release_commit
    assert body["build_sha_known"] is True


def test_runtime_contract_does_not_hide_an_explicit_dirty_build(monkeypatch):
    monkeypatch.setenv("QUANTOS_BUILD_SHA", "dirty")
    monkeypatch.setenv(
        "QUANTOS_RELEASE_COMMIT",
        "c1b21f9f0b3d586ab8aa12fa8f1e00a73bc9c118",
    )

    body = client.get("/api/v1/console/runtime-contract").json()

    assert body["build_sha"] == "dirty"
    assert body["build_sha_known"] is False


def test_dry_run_surfaces_paper_order_preview_without_live_capability():
    body = client.get("/api/v1/console/dry-run").json()
    preview = body["paper_order_preview"]
    assert preview["read_only"] is True
    assert preview["safety"]["paper_only"] is True
    assert preview["safety"]["broker_connectivity"] is False
    assert preview["safety"]["broker_order_submission"] is False
    assert isinstance(preview["verdict"]["workflow_admission_valid"], bool)
    assert isinstance(preview["verdict"]["workflow_authorized_ids"], list)
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
        assert isinstance(autonomy["candidates"], list)
        assert isinstance(autonomy["top_candidates"], list)
        assert isinstance(autonomy["lifecycle"], list)
    else:
        assert autonomy["status"] == "NO_SNAPSHOT"
        assert autonomy["autonomy"]["autonomous_search_running"] is False


def test_realtime_alpha_lifecycle_keeps_profit_and_loss_visible():
    candidates = [
        {
            "candidate_id": "loss_alpha",
            "family_id": "flow",
            "mechanism": "continuation",
            "horizon_ms": 60_000,
            "evaluation_status": "EVALUATED",
            "status": "EXPLORATORY_ONLY",
            "net_return": -0.02,
            "holdout_net_sharpe": -1.0,
            "gate_pass": False,
            "gate_reasons": ["OOS_REQUIRED"],
        },
        {
            "candidate_id": "profit_alpha",
            "family_id": "liquidation",
            "mechanism": "exhaustion",
            "horizon_ms": 120_000,
            "evaluation_status": "EVALUATED",
            "status": "EXPLORATORY_ONLY",
            "net_return": 0.01,
            "holdout_net_sharpe": 0.4,
            "gate_pass": False,
            "gate_reasons": ["OOS_REQUIRED"],
        },
    ]
    forward = {
        "tracks": {
            "loss_alpha": {
                "n_decisions": 10,
                "n_realizations": 9,
                "cum_return": -0.04,
                "max_drawdown": 0.05,
                "last_realization_time_ms": 200,
            },
            "profit_alpha": {
                "n_decisions": 8,
                "n_realizations": 7,
                "cum_return": 0.02,
                "max_drawdown": 0.01,
                "last_realization_time_ms": 190,
            },
        },
        "errors": {},
    }
    execution = {
        "tracks": {
            "loss_alpha": {
                "state": "EXECUTING",
                "n_executions": 3,
                "n_orders": 12,
                "execution_cost": 1.25,
                "execution_pnl_available": True,
                "execution_accounting_missing_count": 0,
                "cumulative_net_pnl": -0.42,
                "last_equity": 999.58,
                "positions_lots": {"BTC": -2},
                "execution_history": [
                    {
                        "as_of_ms": 210,
                        "net_pnl": -0.42,
                        "gross_pnl": -0.2,
                        "cumulative_net_pnl": -0.42,
                        "equity": 999.58,
                        "turnover": 12.0,
                        "execution_cost": 1.25,
                        "reconciliation_residual": 0.0,
                    }
                ],
                "last_decision_time_ms": 210,
            }
        },
        "errors": {},
    }

    rows = artifact_paper_views._realtime_alpha_lifecycle_rows(
        candidates, forward, execution
    )

    by_id = {row["candidate_id"]: row for row in rows}
    assert by_id["loss_alpha"]["performance_state"] == "LOSS"
    assert by_id["loss_alpha"]["execution_performance_state"] == "LOSS"
    assert by_id["loss_alpha"]["effective_performance_state"] == "LOSS"
    assert by_id["loss_alpha"]["performance_basis"] == "ENGINEERING_EXECUTION_PAPER_BATCH_NET_PNL"
    assert by_id["loss_alpha"]["paper"]["cum_return"] == -0.04
    assert by_id["loss_alpha"]["pipeline_stage"] == "EXECUTION_PAPER"
    assert by_id["loss_alpha"]["execution"]["n_orders"] == 12
    assert by_id["loss_alpha"]["execution"]["cumulative_net_pnl"] == -0.42
    assert by_id["loss_alpha"]["execution"]["execution_pnl_available"] is True
    assert by_id["loss_alpha"]["execution"]["positions_lots"] == {"BTC": -2}
    assert len(by_id["loss_alpha"]["execution"]["execution_history"]) == 1
    assert by_id["profit_alpha"]["performance_state"] == "PROFIT"
    assert by_id["profit_alpha"]["execution_performance_state"] == "NO_REALIZED_PNL"
    assert by_id["profit_alpha"]["effective_performance_state"] == "PROFIT"
    assert by_id["profit_alpha"]["pipeline_stage"] == "FORWARD_SHADOW"
    assert by_id["loss_alpha"]["formal_shadow"]["status"] == "NOT_AVAILABLE"
    assert all(row["promotion_eligible"] is False for row in rows)
    assert all(row["live_allowed"] is False for row in rows)


def test_formal_blinded_shadow_is_separate_and_realtime_in_lifecycle(tmp_path):
    path = tmp_path / "formal-shadow.json"
    safety = dict(artifact_paper_views._REALTIME_FORWARD_SHADOW_SAFE_BOUNDARY)
    safety.update(
        {
            "formal_blinded": True,
            "selector_feedback_consumed": False,
            "outcome_feedback_feed": "NONE",
        }
    )
    path.write_text(
        json.dumps(
            {
                "mode": "AUTONOMOUS_REALTIME_FORMAL_BLINDED_SHADOW",
                "status": "RUNNING",
                "generated_at_ms": 123,
                "authority": {
                    "name": "FORMAL_BLINDED_SHADOW",
                    "formal_blinded": True,
                    "selector_feedback_consumed": False,
                    "outcome_feedback_feed": "NONE",
                    "ledger_separate_from_engineering": True,
                },
                "safety": safety,
                "ledger": {
                    "append_only": True,
                    "chain_valid": True,
                    "event_count": 3,
                },
                "verdict": {
                    "forward_shadow_running": True,
                    "engineering_shadow_running": False,
                    "paper_observation_started_without_maturity_wait": True,
                    "promotion_eligible": False,
                    "forward_evidence_credit": False,
                    "deployment_eligible": False,
                    "live_allowed": False,
                    "order_submission_allowed": False,
                    "formal_blinded_shadow_running": True,
                    "formal_shadow_evidence_available": True,
                    "selector_feedback_consumed": False,
                },
                "tracks": {
                    "alpha": {
                        "n_decisions": 4,
                        "n_realizations": 3,
                        "cum_return": -0.01,
                        "formal_blinded": True,
                        "formal_maturity": False,
                        "evidence_class": "FORMAL_BLINDED_SIGNAL_SHADOW",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    formal = artifact_paper_views._realtime_alpha_formal_shadow_payload(str(path))
    assert formal["status"] == "RUNNING"
    assert formal["boundary_valid"] is True
    assert formal["authority"]["name"] == "FORMAL_BLINDED_SHADOW"
    assert formal["authority"]["selector_feedback_consumed"] is False
    assert formal["verdict"]["formal_blinded_shadow_running"] is True
    assert formal["safety"]["outcome_feedback_feed"] == "NONE"

    rows = artifact_paper_views._realtime_alpha_lifecycle_rows(
        [
            {
                "candidate_id": "alpha",
                "family_id": "flow",
                "mechanism": "continuation",
                "horizon_ms": 60_000,
                "evaluation_status": "EVALUATED",
                "status": "EXPLORATORY_ONLY",
            }
        ],
        {"tracks": {}, "errors": {}},
        {"tracks": {}, "errors": {}},
        formal,
    )
    row = rows[0]
    assert row["performance_state"] == "NO_REALIZED_PNL"
    assert row["formal_shadow"]["status"] == "RUNNING"
    assert row["formal_shadow"]["formal_blinded"] is True
    assert row["formal_shadow"]["n_realizations"] == 3
    assert row["formal_shadow"]["selector_feedback_consumed"] is False
    assert row["promotion_eligible"] is False
    assert row["live_allowed"] is False


def test_execution_paper_loss_overrides_shadow_profit_state():
    candidate = {
        "candidate_id": "shadow_profit_exec_loss",
        "family_id": "flow",
        "mechanism": "continuation",
        "horizon_ms": 60_000,
        "evaluation_status": "EVALUATED",
        "status": "EXPLORATORY_ONLY",
        "gate_pass": False,
        "gate_reasons": ["OOS_REQUIRED"],
    }
    rows = artifact_paper_views._realtime_alpha_lifecycle_rows(
        [candidate],
        {
            "tracks": {
                "shadow_profit_exec_loss": {
                    "n_decisions": 2,
                    "n_realizations": 2,
                    "cum_return": 0.04,
                }
            },
            "errors": {},
        },
        {
            "tracks": {
                "shadow_profit_exec_loss": {
                    "n_executions": 1,
                    "execution_pnl_available": True,
                    "cumulative_net_pnl": -0.25,
                }
            },
            "errors": {},
        },
    )

    row = rows[0]
    assert row["performance_state"] == "PROFIT"
    assert row["execution_performance_state"] == "LOSS"
    assert row["effective_performance_state"] == "LOSS"
    assert row["performance_basis"] == "ENGINEERING_EXECUTION_PAPER_BATCH_NET_PNL"


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


def test_realtime_alpha_console_projection_blocks_feedback_authority_widening(
    tmp_path,
):
    path = tmp_path / "tampered-feedback.json"
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
                    "allow_order_submission": False,
                    "allow_ai_direct_trading": False,
                    "promotion_eligible": False,
                    "credentials_accessed": False,
                },
                "feedback": {
                    "scope": "EXPLORATION_BUDGET_ONLY",
                    "formal_gate_impact": False,
                    "promotion_eligible": True,
                    "live_allowed": False,
                    "order_submission_allowed": False,
                },
            }
        ),
        encoding="utf-8",
    )

    autonomy = artifact_paper_views._realtime_alpha_autonomy_payload(str(path))

    assert autonomy["status"] == "SAFE_BLOCKED"
    assert "AUTONOMY_FEEDBACK_AUTHORITY_INVALID" in autonomy["blockers"]
    assert autonomy["feedback"]["active_adaptive_candidates"] == 0


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
    risk_path = tmp_path / "risk-inputs.json"
    risk_path.write_text(
        json.dumps(
            {
                "alphas": {
                    "alpha-0": {
                        "status": "PENDING",
                        "input_evidence_status": "READY",
                        "inputs": {
                            "capacity_cny": {"BTC": 1.0},
                            "alpha_returns": {"alpha-0": [0.01]},
                        },
                        "admission_blockers": [
                            "FORWARD_EVIDENCE_MATURITY_PENDING"
                        ],
                        "provenance": {
                            "decision_basis": "CURRENT_ATTESTED_PENDING_DECISION"
                        },
                        "missing_inputs": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        artifact_paper_views,
        "_PAPER_RESEARCH_RISK_BUNDLE",
        str(risk_path),
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
    alpha_zero = next(
        row for row in payload["candidates"] if row["alpha_id"] == "alpha-0"
    )
    assert alpha_zero["risk_packet"] == {
        "status": "PENDING",
        "decision_basis": "CURRENT_ATTESTED_PENDING_DECISION",
        "input_evidence_status": "READY",
        "input_fields": ["alpha_returns", "capacity_cny"],
        "admission_blockers": ["FORWARD_EVIDENCE_MATURITY_PENDING"],
        "missing": [],
        "reasons": {},
    }
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
                    "realtime_handoff_count": 2,
                    "realtime_research_policy_hash": "d" * 64,
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
        "realtime_handoff_count": 2,
        "realtime_research_policy_hash": "d" * 64,
        "release_commit": "c" * 40,
        "source_as_of_ms": 1234,
    }
    assert payload["candidates"][-1]["stage"] == "EXECUTION_PAPER_ELIGIBLE"
    assert payload["candidates"][-1]["execution_paper_eligible"] is True
    assert payload["candidates"][-1]["gates"] == {
        "EXECUTION_PAPER": {
            "status": "PENDING",
            "reference": None,
            "content_hash": None,
            "observed_at_ms": None,
        }
    }
    assert payload["exception_queue"][0] == {
        "code": "LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE",
        "candidate_count": 3,
        "candidate_ids": candidate_ids[:3],
    }
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


def test_dry_run_surfaces_paper_operational_window_without_widening_authority():
    body = client.get("/api/v1/console/dry-run").json()
    operational = body["paper_operational"]
    assert isinstance(operational["snapshot_present"], bool)
    assert isinstance(operational["status"], str)
    assert isinstance(operational["chain_valid"], bool)
    assert isinstance(operational["window"]["engineering_smoke_ready"], bool)
    assert isinstance(operational["window"]["production_window_ready"], bool)
    assert operational["safety"]["paper_only"] is True
    assert operational["safety"]["fault_injection_isolated"] is True
    assert operational["safety"]["credentials_accessed"] is False
    assert operational["safety"]["broker_connectivity"] is False
    assert operational["safety"]["broker_order_submission"] is False


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

    corrupt = snapshot_metadata(
        {
            "data_provenance": "REAL_ALPHA_FAMILY_LEDGER",
            "snapshot_status": "CORRUPT_OR_UNREADABLE",
            "snapshot_id": "must-not-survive",
        }
    )
    assert corrupt["snapshot_status"] == "CORRUPT_OR_UNREADABLE"
    assert corrupt["snapshot_id"] is None and corrupt["snapshot_ids"] == []
    assert corrupt["data_quality"]["is_corrupt"] is True
    assert corrupt["data_quality"]["is_available"] is False

    real = snapshot_metadata(
        {
            "data_provenance": "REAL_PIT_DATASETS",
            "snapshot_present": True,
            "datasets": [{"snapshot_id": "pit-001"}],
        }
    )
    assert real["snapshot_status"] == "REAL"
    assert real["snapshot_id"] == "pit-001"


def test_alpha_family_corrupt_store_is_not_upgraded_to_real(monkeypatch, tmp_path):
    from services.console.api import alpha_family_views

    corrupt = tmp_path / "alpha_family_registry.db"
    corrupt.write_bytes(b"not-a-sqlite-database")
    monkeypatch.setattr(alpha_family_views, "_DB", str(corrupt))

    payload = alpha_family_views.alpha_families()

    assert payload["snapshot_status"] == "CORRUPT_OR_UNREADABLE"
    assert payload["data_quality"]["status"] == "CORRUPT_OR_UNREADABLE"
    assert payload["data_quality"]["is_corrupt"] is True
    assert payload["data_quality"]["is_available"] is False
    assert payload["snapshot_id"] is None and payload["snapshot_ids"] == []
    assert payload["families"] == []


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


def test_alpha_lifecycle_reads_mutable_l2_state_snapshot(monkeypatch, tmp_path):
    state_root = tmp_path / "state"
    evidence = state_root / "evidence"
    evidence.mkdir(parents=True)
    (evidence / "alpha_lifecycle_cockpit.json").write_text(
        json.dumps({
            "artifact": "ALPHA_LIFECYCLE_COCKPIT",
            "schema_version": 1,
            "generated_at_ms": 200,
            "readiness": "L2_DRY_RUN_ONLY",
            "alphas": [{
                "alpha_id": "rt_test",
                "namespace": "REALTIME",
                "derived_stage": "LIVE_TRACK",
                "stages": {
                    "backtest": {
                        "status": "PRESENT",
                        "sharpe_raw": 4.2,
                        "final_equity": 2_000_000,
                    },
                    "paper": {
                        "status": "PRESENT",
                        "track": {"cum_return": 0.5, "paper_ic": 0.3},
                    },
                    "live": {
                        "status": "PRESENT",
                        "runs": [{"mode": "submit", "n_submitted": 12}],
                    },
                },
            }],
            "stage_legend": {"LIVE_TRACK": "legacy maturity claim"},
            "honesty_note": "test evidence",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANTOS_STATE_ROOT", str(state_root))
    monkeypatch.setattr(
        alpha_lifecycle_views,
        "_STATE_ROOT",
        state_root,
    )
    monkeypatch.setattr(
        alpha_lifecycle_views,
        "_STATE_ARTIFACT",
        str(evidence / "alpha_lifecycle_cockpit.json"),
    )
    monkeypatch.setattr(
        alpha_lifecycle_views,
        "_LIFECYCLE_ARTIFACT",
        str(tmp_path / "immutable" / "alpha_lifecycle_cockpit.json"),
    )

    artifact = alpha_lifecycle_views._load_artifact()
    assert artifact is not None
    assert artifact["generated_at_ms"] == 200
    payload = alpha_lifecycle_views._alpha_lifecycle_payload()
    assert payload["snapshot_status"] == "REAL"
    assert payload["alphas"][0]["namespace"] == "REALTIME"
    assert payload["authoritative"] is False
    assert payload["evidence_status"] == "DIAGNOSTIC_ONLY"
    assert payload["invalidation_code"] == "INVALIDATED_PERP_LEDGER_V1"
    alpha = payload["alphas"][0]
    assert alpha["derived_stage"] == "INVALIDATED_PERP_LEDGER_V1"
    assert alpha["stages"]["backtest"] == {
        "status": "INVALIDATED_PERP_LEDGER_V1",
        "authoritative": False,
        "evidence_status": "DIAGNOSTIC_ONLY",
        "invalidation_code": "INVALIDATED_PERP_LEDGER_V1",
    }
    assert alpha["stages"]["paper"]["status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert alpha["stages"]["live"]["status"] == "INVALIDATED_PERP_LEDGER_V1"
    assert "sharpe_raw" not in alpha["stages"]["backtest"]
    assert "track" not in alpha["stages"]["paper"]
    assert "runs" not in alpha["stages"]["live"]
    assert payload["stage_legend"] == {
        "INVALIDATED_PERP_LEDGER_V1": alpha_lifecycle_views._INVALIDATED_STAGE_NOTE,
    }


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


def test_technical_replay_endpoint_has_separate_non_authoritative_contract():
    from fastapi.testclient import TestClient
    from services.console.api.main import app
    payload=TestClient(app).get('/api/v1/console/technical-replay?run_id=missing-replay').json()
    assert set(payload)=={'source','read_only','api_version','data_provenance','authoritative',
                          'evidence_status','technical_replay','snapshot_id','snapshot_ids',
                          'snapshot_status','data_quality'}
    assert payload['read_only'] is True
    assert payload['authoritative'] is False
    assert payload['evidence_status']=='TECHNICAL_VALIDATION_ONLY'
    assert payload['technical_replay'] is None


def test_predictive_result_contract_carries_errors_not_profit(tmp_path, monkeypatch):
    from dataclasses import replace
    from services.console.api import research_run_views as views
    from services.research.run_registry.contract import ResearchRun
    from services.research_runner.run_kinds import RUN_KINDS
    from services.research_runner.runner import _build_provenance, _result_digest

    # Bind a minimal completed predictive mirror through the real provenance validator.
    fields = dict(title='BTC mechanism', params={'attempt': 1},
                  metrics={'result_hash':'a'*64, 'evaluation':'EXPLORATORY_PREDICTION_ONLY',
                           'account_pnl_computed':'false'},
                  series={'all_rmse_bps':[{'label':'PRICE','value':123.5}],
                          'all_r2_vs_zero':[{'label':'PRICE','value':-0.02}]},
                  verdict='TECHNICAL_VALIDATION_ONLY', caveats=['Seen history'])
    run = ResearchRun(run_id='predictive-contract', kind='btc_mechanism', status='done',
                      created_ts=1, finished_ts=2, log=[], code_version=None,
                      provenance=_build_provenance('btc_mechanism', RUN_KINDS['btc_mechanism'], None,
                                                  result_sha256=_result_digest(**fields)), **fields)
    class Registry:
        def get(self, run_id): return run if run_id == run.run_id else None
    monkeypatch.setattr(views, '_runs_registry', Registry)
    body = client.get('/api/v1/console/technical-replay?run_id=predictive-contract').json()
    assert body['snapshot_status'] == 'REAL'
    assert body['authoritative'] is False
    detail = body['technical_replay']
    assert detail['metrics']['account_pnl_computed'] == 'false'
    assert detail['series']['all_rmse_bps'][0]['value'] == 123.5
    assert detail['series']['all_r2_vs_zero'][0]['value'] == -0.02
    assert 'net_equity' not in detail['series']
    assert views._technical_replay_detail(replace(run, status='error')) is None
    identity = client.get('/api/v1/console/research-run?run_id=predictive-contract').json()['run']
    assert identity['verdict'] == 'EXPLORATORY_PREDICTION_ONLY'
    assert identity['metrics'] == {}
    assert 'invalidation_code' not in identity


def test_factory_and_forward_report_contracts(monkeypatch):
    from services.console.api import research_run_views as views
    from services.research.run_registry.contract import ResearchRun
    from services.research_runner.run_kinds import RUN_KINDS
    from services.research_runner.runner import _build_provenance, _result_digest
    reports = {
        'btc_factory': ('factory_report', {'cases': [], 'selection_updates': [], 'fee_bps':'3.6', 'threshold_bps':19.2}),
        'btc_forward': ('forward_report', {'status':'NO_FREEZE','models':[],'reason':'No frozen models',
                                         'observed_at_ms':1,'attempt':1,'authoritative':False,'account_pnl_computed':False,
                                         'online_factory':{'enabled_at_ms':1,'training_required':180,'training_available':0,
                                             'status':'WAITING_WINDOW','versions':[],'factory_decisions':0,'latest_decision':None}}),
    }
    for kind,(field,report) in reports.items():
        fields = dict(title=kind,params={'attempt':1},metrics={'result_hash':'b'*64,field+'_json':json.dumps(report)},
                      series={},verdict='TECHNICAL_VALIDATION_ONLY',caveats=[])
        run=ResearchRun(run_id=kind,kind=kind,status='done',created_ts=1,finished_ts=2,log=[],code_version=None,
                        provenance=_build_provenance(kind,RUN_KINDS[kind],None,result_sha256=_result_digest(**fields)),**fields)
        class Registry:
            def get(self, run_id): return run if run_id==kind else None
        monkeypatch.setattr(views,'_runs_registry',Registry)
        body=client.get('/api/v1/console/technical-replay',params={'run_id':kind}).json()
        assert body['technical_replay'][field]==report
        assert field+'_json' not in body['technical_replay']['metrics']
        assert body['read_only'] is True and body['authoritative'] is False


def test_verified_forward_error_keeps_operational_context(monkeypatch):
    from services.console.api import research_run_views as views
    from services.research.run_registry.contract import ResearchRun
    from services.research_runner.run_kinds import RUN_KINDS
    from services.research_runner.runner import _build_provenance
    run=ResearchRun(run_id='forward-error',kind='btc_forward',title='Forward',params={'attempt':2,'action':2},
        status='error',created_ts=1,finished_ts=2,metrics={},series={},verdict='',caveats=[],
        provenance=_build_provenance('btc_forward',RUN_KINDS['btc_forward'],None),
        log=['public capture failed'],error='funding unavailable',code_version=None)
    class Registry:
        def get(self,run_id):return run
        def list(self,**kwargs):return [run]
    monkeypatch.setattr(views,'_runs_registry',Registry)
    body=client.get('/api/v1/console/research-run',params={'run_id':run.run_id}).json()['run']
    assert body['params']==run.params and body['error']==run.error and body['log']==run.log
    assert body['metrics']=={} and body['authoritative'] is False
    rows=client.get('/api/v1/console/research-runs').json()
    assert rows['registry_available'] is True and rows['runs'][0]['params']==run.params


def test_missing_run_registry_does_not_claim_empty_available_database(monkeypatch):
    from services.console.api import research_run_views as views
    monkeypatch.setattr(views,'_runs_registry',views._EmptyRunRegistry)
    body=client.get('/api/v1/console/research-runs').json()
    assert body['runs']==[] and body['registry_available'] is False


def test_verified_perpetual_failure_keeps_diagnostics_without_legacy_metrics():
    from services.console.api import research_run_views as views
    from services.research.run_registry.contract import ResearchRun
    from services.research_runner.run_kinds import RUN_KINDS
    from services.research_runner.runner import _build_provenance
    run=ResearchRun(run_id='perp-error',kind='perpetual_alpha',title='Research',params={'attempt':3},
        status='error',created_ts=1,finished_ts=2,metrics={},series={},verdict='',caveats=[],
        provenance=_build_provenance('perpetual_alpha',RUN_KINDS['perpetual_alpha'],None),
        log=['input missing'],error='data unavailable',code_version=None)
    body=views._run_detail(run)
    assert body['error']=='data unavailable' and body['params']=={'attempt':3}
    assert body['metrics']=={} and body['authoritative'] is False


def test_factory_provider_connection_contract_is_explicit(tmp_path):
    from services.console.api.ai_factory import factory_payload
    provider = factory_payload(tmp_path)['provider']
    assert set(provider) == {'status', 'model', 'connected'}
    assert provider['status'] in {'NOT_CONFIGURED', 'CONFIGURED_UNVERIFIED', 'RECENT_CALL_SUCCEEDED'}
    assert isinstance(provider['model'], str)
    assert type(provider['connected']) is bool
    assert provider['connected'] == (provider['status'] == 'RECENT_CALL_SUCCEEDED')


def test_factory_lifecycle_calibration_contract_uses_only_valid_mature_labels(tmp_path,monkeypatch):
    from services.console.api import ai_factory_lifecycle as lifecycle
    from services.research.ai_factory_store import FactoryStore
    from tools.btc_factory_research import seal
    from tools.cache_io import atomic_write_json
    monkeypatch.setattr(lifecycle.time,'time_ns',lambda:10000*1000000)
    base=tmp_path/'data/cache/ai_factory'
    with FactoryStore(base/'factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'calibration'})
        store.update(1,'COMPLETED',{'model_hash':'a'*64,'selected':{'model':'ridge'}})
    label={'slot_ms':1,'prediction':.01,'realized_return':.02,'available_at_ms':9000,
           'model_hash':'a'*64,'authoritative':False,'account_pnl_computed':False}
    forward={'attempt':1,'status':'WAITING_WINDOW','observed_at_ms':10000,'labels':1,'predictions':2,
             'recent_labels':[label],'authoritative':False,'account_pnl_computed':False}
    path=base/'1/forward/status.json';path.parent.mkdir(parents=True)
    atomic_write_json(str(path),seal(forward))
    payload=lifecycle.lifecycle_payload(tmp_path)
    diagnostics=payload['models'][0]['diagnostics']
    assert diagnostics['observations']==[{'time':1,'prediction':.01,'actual':.02,'residual':.01}]
    assert diagnostics['mae']==.01 and diagnostics['directional_count']==1
    assert len(diagnostics['calibration'])==6 and diagnostics['economic_eligible'] is False
    atomic_write_json(str(path),seal({**forward,'recent_labels':[dict(label,available_at_ms=10001)]}))
    rejected=lifecycle.lifecycle_payload(tmp_path)
    assert rejected['models'][0]['state']=='UNKNOWN'
    assert rejected['models'][0]['diagnostics']['observations']==[]


def test_profitability_basis_study_is_a_separate_read_only_projection(tmp_path):
    from services.console.api.profitability_views import profitability_payload
    from tools.profitability_study import write_new
    directory=tmp_path/'data/cache/profitability/1/p3_study'
    directory.mkdir(parents=True)
    write_new(directory/'result.json',{'stage':'P3','economic_eligible':False,
              'status':'COMPLETED_TECHNICAL_ONLY','trials_attempted':4,'cases':[]})
    payload=profitability_payload(tmp_path,1)
    assert payload['p3'] is None
    assert payload['p3_study']['trials_attempted']==4
    assert payload['p3_study']['projection']=='CONSOLE_SUMMARY'
    assert payload['authoritative'] is False


def test_profitability_forward_cost_status_preserves_unknown_costs(tmp_path):
    from services.console.api.profitability_views import profitability_payload
    from tools.profitability_study import write_new
    directory=tmp_path/'data/cache/profitability/1/p6';directory.mkdir(parents=True)
    write_new(directory/'status.json',{'stage':'P6','status':'WAITING_MATURITY','economic_eligible':False,
              'actual_venue_costs':None,'missing_slots':[],'window_start_ms':86400300000})
    result=profitability_payload(tmp_path,1)
    assert result['p6']['actual_venue_costs'] is None
    assert result['p6']['status']=='WAITING_MATURITY'
    assert result['p6']['projection']=='CONSOLE_SUMMARY'
    assert result['inputs']==[] and result['p5_calibration'] is None


def test_passive_queue_console_contract_preserves_censored_orders_and_unknown_profit(tmp_path):
    from services.console.api.profitability_views import profitability_payload
    from tools.profitability_study import write_new
    directory=tmp_path/'data/cache/profitability/1/p5_queue';directory.mkdir(parents=True)
    write_new(directory/'result.json',{'stage':'P5_QUEUE','status':'QUEUE_DIAGNOSTICS_COMPLETED',
        'economic_eligible':False,'ledger_applied':False,'selected_profile':None,
        'trials_attempted':3,'quality':{'counts':{'depth_updates':123}},
        'trials':[{'profile_ms':100,'status':'COMPLETED_DIAGNOSTIC','orders':[{
            'order_id':'a','status':'PARTIALLY_FILLED','censored':True,'remaining_quantity':'0.01'}]}]})
    result=profitability_payload(tmp_path,1)
    assert result['p5_queue']['ledger_applied'] is False
    assert result['p5_queue']['selected_profile'] is None
    assert result['p5_queue']['trials'][0]['orders'][0]['censored'] is True
    assert 'net_pnl' not in result['p5_queue']


def test_passive_account_console_contract_keeps_canonical_strings_and_actual_fee_unknown(tmp_path):
    import json
    from tests.research.test_passive_execution_study import account_source
    from tools.passive_execution_study import study
    from services.console.api.profitability_views import profitability_payload
    directory=tmp_path/'data/cache/profitability/1/p5_queue'
    directory.parent.mkdir(parents=True)
    source=tmp_path/'source.json';source.write_text(json.dumps(account_source()))
    study(source,directory)
    result=profitability_payload(tmp_path,1)['p5_queue']
    assert result['ledger_applied'] is True
    assert result['account_environment']['venue_margin_tiers_verified'] is False
    account=result['trials'][0]['account']
    assert account['actual_venue_fees'] is None
    assert account['reconciliation']['fee']=='0.0099'
    assert account['reconciliation']['ending_equity']=='1000.0901'
    assert account['reconciliation']['residual']=='0'
    assert len(account['equity_history'])<=200 and account['history_count']>0


def test_passive_public_dynamic_constraints_reach_console_without_venue_certification(tmp_path):
    import json
    from tests.research.test_passive_execution_study import account_source
    from tests.execution.test_public_venue_constraint_binding import environment
    from tools.passive_execution_study import study
    from services.console.api.profitability_views import profitability_payload
    source=account_source();source['tape'],source['account']['environment']=environment()
    directory=tmp_path/'data/cache/profitability/1/p5_queue';directory.parent.mkdir(parents=True)
    path=tmp_path/'source.json';path.write_text(json.dumps(source))
    study(path,directory)
    result=profitability_payload(tmp_path,1)['p5_queue']
    bounds=result['account_environment']['venue_order_constraints']
    assert bounds['percent_price_up']=='1.1' and bounds['max_open_orders']==3
    assert bounds['scope']=='ISOLATED_DECLARED_ORDERS_ONLY'
    assert bounds['venue_behavior_verified'] is False
    filters=result['account_environment']['venue_order_filters']
    assert filters['schema']=='BINANCE_USDM_DECLARED_FILTERS_V1'
    assert filters['lot_size']=={'minimum':'0.001','maximum':'100','step':'0.001'}
    assert filters['market_lot_size']==filters['lot_size']
    assert filters['quantity_atom']=='0.001'
    assert filters['market_application']=='LOT_SIZE_AND_MARKET_LOT_SIZE'
    assert filters['venue_behavior_verified'] is False
    assert result['account_environment']['actual_venue_fees'] is None
    assert result['trials'][0]['account']['reconciliation']['residual']=='0'


def test_passive_receipt_calibration_console_contract_has_distinct_clocks_and_no_authority(tmp_path):
    import json
    from tests.research.test_passive_calibration_study import source
    from tools.passive_execution_study import study
    from services.console.api.profitability_views import profitability_payload
    directory=tmp_path/'data/cache/profitability/1/p5_queue';directory.parent.mkdir(parents=True)
    path=tmp_path/'source.json';path.write_text(json.dumps(source()));study(path,directory)
    view=profitability_payload(tmp_path,1)['p5_queue'];calibration=view['calibration']
    assert view['simulations_attempted']==6 and calibration['selected_profile_ms']==25
    assert calibration['partition_clock']=='ACTUAL_CLIENT_RECEIPT_TIME'
    assert calibration['calibration_complete'] is False and calibration['venue_receipts_verified'] is False
    assert calibration['heldout']['quantity_path_error_btc_ms']=='0'
    assert calibration['heldout']['receive_time_absolute_error_ms']==0
    assert calibration['heldout']['venue_time_absolute_error_ms']==0
    assert len(calibration['heldout']['rows'])<=20


def test_native_order_receipts_console_contract_keeps_fee_assets_and_exact_ids(tmp_path):
    import json
    from tests.research.test_binance_order_receipts import native_workflow_source
    from tools.passive_execution_study import study
    from services.console.api.profitability_views import profitability_payload
    directory=tmp_path/'data/cache/profitability/1/p5_queue';directory.parent.mkdir(parents=True)
    path=tmp_path/'source.json';path.write_text(json.dumps(native_workflow_source()));study(path,directory)
    view=profitability_payload(tmp_path,1)['p5_queue'];native=view['calibration']['native_observations']
    assert native['reported_commission_by_asset']=={'USDC':'0.00198'}
    assert native['reported_quote_commission']=='0.00198'
    assert native['trade_rows'][0]['venue_order_id']=='123'
    assert isinstance(native['trade_rows'][0]['trade_id'],str)
    assert native['actual_paid_fees'] is None and native['source_verified'] is False
    assert len(native['trade_rows'])<=50
    assert native['fee_comparisons'][1]['reported_minus_simulated_fee']=='-0.00792'
    assert view['calibration']['venue_receipts_verified'] is False


def test_profitability_causal_information_preserves_separate_quotes_and_no_prediction_claim(tmp_path):
    from services.console.api.profitability_views import profitability_payload
    from tools.profitability_study import write_new
    directory=tmp_path/'data/cache/profitability/1/causal';directory.mkdir(parents=True)
    write_new(directory/'status.json',{'status':'COLLECTING','economic_eligible':False,
        'prediction_evidence':'NONE_FEATURE_COLLECTION_ONLY','study':None,
        'collection':{'slot_ms':3600000,'status':'PARTIAL','request_attempts':14,'recovered_requests':1,
                      'elapsed_ms':1200,'failures':[{'quote':'USDT','kind':'oi','category':'HTTP_ERROR','http_status':429}]},
        'quotes':[{'quote':'USDC','ready_rows':1,'required_rows':504,'missing_slots':[]},
                  {'quote':'USDT','ready_rows':0,'required_rows':504,'missing_slots':[3600000]}]})
    result=profitability_payload(tmp_path,1)
    assert result['causal']['study'] is None and result['causal']['projection']=='CONSOLE_SUMMARY'
    assert [r['ready_rows'] for r in result['causal']['quotes']]==[1,0]
    assert result['causal']['prediction_evidence']=='NONE_FEATURE_COLLECTION_ONLY'
    assert result['causal']['collection']['request_attempts']==14
    assert result['causal']['collection']['failures'][0]['http_status']==429


def test_portfolio_research_contract_keeps_account_curves_and_hides_training_data(tmp_path):
    from services.console.api.ai_factory import factory_payload
    from services.research.ai_factory_store import FactoryStore
    from tools.profitability_study import write_new
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
    dest=tmp_path/'data/cache/ai_factory/1/portfolio';dest.mkdir(parents=True)
    write_new(dest/'result.json',{'schema':'QUANTOS_FACTORY_PORTFOLIO_V1','status':'COMPLETED_TECHNICAL_ONLY',
        'authoritative':False,'economic_eligible':False,'training_x':[[123]],
        'selected':{'id':0,'quarters':[0]},'components':[],'inner_trials_attempted':1,
        'inner_trials':[{'spec':{'id':0},'status':'PASSED','metrics':{'net_pnl':0,'drawdown':0,'equity':[{'time':1,'equity':'1500'}]}}],
        'outer_cases':[{'name':'portfolio','status':'PASSED','metrics':{'net_pnl':0,'equity':[{'time':1,'equity':'1500'}]}}]})
    (dest/'forward').mkdir()
    write_new(dest/'forward/status.json',{'schema':'QUANTOS_PORTFOLIO_FORWARD_V1','status':'WAITING_WINDOW',
        'authoritative':False,'account_pnl_computed':False,'predictions':0,'recent_forecasts':[]})
    cost=tmp_path/'data/cache/profitability/1/portfolio-forward';cost.mkdir(parents=True)
    write_new(cost/'status.json',{'status':'WAITING_MATURITY','authoritative':False,'economic_eligible':False})
    report=factory_payload(tmp_path,1)['portfolio_research']
    assert report['forward']['predictions']==0
    assert report['forward_cost']['status']=='WAITING_MATURITY'
    assert 'training_x' not in report
    assert 'equity' not in report['inner_trials'][0]['metrics']
    assert report['outer_cases'][0]['metrics']['equity'][0]['equity']=='1500'


def test_sleeve_console_keeps_owned_positions_and_does_not_inherit_old_forward_costs(tmp_path):
    from services.research.ai_factory_store import FactoryStore
    from services.console.api.ai_factory import factory_payload
    from tools.btc_factory_research import seal
    from tools.cache_io import atomic_write_json
    base=tmp_path/'data/cache/ai_factory'
    with FactoryStore(base/'factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'});store.update(1,'COMPLETED',{})
    trace={'construction':'INDEPENDENT_DECISIONS_ONE_WAY_NET_ACCOUNT_V1','ending':{'actual_quantity':'0.007'},
        'decisions':[],'execution':[],'decision_count':100,'execution_count':150,'trace_hash':'a'*64}
    (base/'1/portfolio-sleeves-v1').mkdir(parents=True)
    (tmp_path/'data/cache/profitability/1/portfolio-forward').mkdir(parents=True)
    atomic_write_json(str(base/'1/portfolio-sleeves-v1/result.json'),seal({
        'schema':'QUANTOS_FACTORY_SLEEVE_PORTFOLIO_V1','status':'COMPLETED_TECHNICAL_ONLY',
        'construction':trace['construction'],'authoritative':False,'economic_eligible':False,
        'outer_cases':[{'name':'portfolio','status':'PASSED','fee_bps':4,'slippage_bps':5,
            'metrics':{'net_pnl':-1.,'sleeve_execution':trace,'training_x':[[999]]}}]}))
    atomic_write_json(str(tmp_path/'data/cache/profitability/1/portfolio-forward/status.json'),
        seal({'status':'LEGACY_WINDOW','authoritative':False}))
    payload=factory_payload(tmp_path,1);report=payload['portfolio_sleeves']
    assert report['outer_cases'][0]['metrics']['sleeve_execution']==trace
    assert 'training_x' not in report['outer_cases'][0]['metrics']
    assert report['forward_cost'] is None and report['forward'] is None


def test_sleeve_process_is_independent_and_rejects_authority_claims(tmp_path):
    from services.console.api.ai_factory import factory_payload
    from services.research.ai_factory_store import FactoryStore
    from tools.profitability_study import write_new
    from tools.btc_factory_research import seal
    import json
    base=tmp_path/'data/cache/ai_factory'
    with FactoryStore(base/'factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3})
    path=base/'1/process-sleeves-v1/process-comparison.json';path.parent.mkdir(parents=True)
    data={'schema':'QUANTOS_SLEEVE_PROCESS_COMPARISON_V1','status':'COMPLETED_TECHNICAL_ONLY',
        'rows':[],'new_model_calls':0,'refitted_models':0,'authoritative':False,'economic_eligible':False,
        'live_eligible':False,'outer_feedback_allowed':False}
    write_new(path,data)
    payload=factory_payload(tmp_path,1)
    assert payload['sleeve_comparison']['schema']==data['schema'] and payload['comparison'] is None
    path.write_text(json.dumps(seal({**data,'economic_eligible':True})))
    payload=factory_payload(tmp_path,1)
    assert payload['sleeve_comparison'] is None and 'SLEEVE_COMPARISON_UNAVAILABLE' in payload['errors']


def test_portfolio_projects_large_trial_evidence_without_exposing_raw_details(tmp_path):
    import pytest
    from services.console.api.factory_portfolio import portfolio_payload
    from services.console.api.forward_dashboard import _read
    from tools.profitability_study import write_new
    dest=tmp_path/'data/cache/ai_factory/1/portfolio';dest.mkdir(parents=True)
    write_new(dest/'result.json',{'authoritative':False,'economic_eligible':False,'status':'COMPLETED_TECHNICAL_ONLY',
        'inner_trials':[{'status':'PASSED','spec':{'id':0},'metrics':{'net_pnl':0.,'raw_replay':'x'*4_100_000}}],
        'outer_cases':[]})
    with pytest.raises(ValueError):_read(dest/'result.json')
    value=portfolio_payload(dest)
    assert value['inner_trials'][0]['metrics']=={'net_pnl':0.}
    assert len(json.dumps(value))<2000


def test_research_notes_contract_keeps_outer_explanations_out_of_search(tmp_path):
    import pytest
    from services.console.api.ai_factory import factory_payload
    from services.research.ai_factory_store import FactoryStore
    from tools.profitability_study import write_new
    base=tmp_path/'data/cache/ai_factory'
    with FactoryStore(base/'factory.db') as store:store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
    (base/'1').mkdir()
    write_new(base/'1/research-verdict.json',{'schema':'QUANTOS_RESEARCH_VERDICT_V1','scope':'OUTER_EXPLANATION',
        'authoritative':False,'economic_eligible':False,'search_feedback_allowed':False,'facts':[]})
    notes=factory_payload(tmp_path,1)['research_notes']
    assert notes['role_runs']==[]
    assert notes['scope']=='HUMAN_READ_ONLY' and notes['outer_results_available_to_search'] is False
    assert notes['verdict']['scope']=='OUTER_EXPLANATION' and notes['episodes']==[] and notes['history'] is None
    from tools.ai_factory_collaboration import bounded_proposal
    def fail(*args,**kwargs):raise TimeoutError()
    with pytest.raises(ValueError,match='role'):
        bounded_proposal('BTC',[0],[],protocol='SINGLE_V1',output=base/'1/research-roles'/('a'*64),options={},propose_fn=fail)
    notes=factory_payload(tmp_path,1)['research_notes']
    assert notes['role_runs'][0]['calls'][0]['reason']=='TimeoutError'
    assert notes['role_runs'][0]['calls'][0]['candidate_id'] is None


def test_restore_profile_contract_requires_matching_history_and_sanitizes(tmp_path, monkeypatch):
    from services.console.api import runtime_health
    monkeypatch.setattr(runtime_health, "_STATE_ROOT", tmp_path)
    cache = tmp_path / "data/cache"
    cache.mkdir(parents=True)
    (tmp_path / "evidence").mkdir()
    head = {"history_event_hash": "a" * 64, "history_event_seq": 4}
    receipt = {**head, "verdict": "BREACH", "observed_at_ms": 999000,
               "last_reason_code": "RESTORE_RTO_EXCEEDED"}
    (cache / "l2_backup_status.json").write_text(json.dumps(receipt))
    evidence = {**head, "restore_diagnostics": {
        "scope": "RESTORE_PERFORMANCE_DIAGNOSTIC_ONLY", "total_ms": 310000,
        "completed": False, "private_path": "/private/source",
        "stages": [{"stage": "ARTIFACT_RESTORE", "elapsed_ms": 310000, "completed": False,
                    "error": "secret exception"}]}}
    path = tmp_path / "evidence/l2_backup_latest.json"
    path.write_text(json.dumps(evidence))
    def row():
        return next(r for r in runtime_health.operational_receipt_services(now_ms=1000000)
                    if r["service"] == "l2-backup-restore")
    result = row()
    assert result["status"] == "down"
    assert result["restore_diagnostics"] == {
        "scope": "RESTORE_PERFORMANCE_DIAGNOSTIC_ONLY", "total_ms": 310000,
        "completed": False, "stages": [{"stage": "ARTIFACT_RESTORE", "elapsed_ms": 310000, "completed": False}]}
    evidence["history_event_seq"] = 3
    path.write_text(json.dumps(evidence))
    assert row().get("restore_diagnostics") is None
    evidence["history_event_seq"] = 4
    evidence["restore_diagnostics"]["total_ms"] = True
    path.write_text(json.dumps(evidence))
    assert row().get("restore_diagnostics") is None


def test_passive_liquidity_fee_contract_preserves_rates_and_unknown_actual_costs(tmp_path):
    import json
    from tests.research.test_passive_execution_study import account_source
    from tools.passive_execution_study import study
    from services.console.api.profitability_views import profitability_payload
    data=account_source();del data['account']['fee_rate']
    data['account']['liquidity_fee_rates']={'maker':'0.0002','taker':'0.001'}
    directory=tmp_path/'data/cache/profitability/1/p5_queue';directory.parent.mkdir(parents=True)
    source=tmp_path/'source.json';source.write_text(json.dumps(data))
    study(source,directory)
    result=profitability_payload(tmp_path,1)['p5_queue']
    account=result['trials'][0]['account']
    assert account['simulated_fee_rate'] is None
    assert account['fee_model']=={'scope':'EXPLICIT_SIMULATION_ASSUMPTION','actual_venue_fees':None,
        'mode':'BY_FILL_LIQUIDITY_ROLE','maker_fee_rate':'0.0002','taker_fee_rate':'0.001',
        'reservation_fee_rate':'0.001','role_source':'EXECUTION_MECHANISM_NOT_ORDER_TYPE'}
    assert account['actual_venue_fees'] is None
    assert account['reconciliation']['fee']=='0.00198'
    assert account['reconciliation']['ending_equity']=='1000.09802'
    assert account['reconciliation']['residual']=='0'
    assert result['trials_attempted']==3 and result['selected_profile'] is None


def test_frozen_account_terms_contract_keeps_observations_separate_from_actual_fees(tmp_path):
    from tests.execution.test_frozen_account_terms import terms_source
    from tools.passive_execution_study import study
    from services.console.api.profitability_views import profitability_payload
    directory=tmp_path/'data/cache/profitability/1/p5_queue';directory.parent.mkdir(parents=True)
    source=tmp_path/'source.json';source.write_text(json.dumps(terms_source()))
    study(source,directory)
    result=profitability_payload(tmp_path,1)['p5_queue']
    terms=result['account_environment']['account_terms']
    assert terms['selected_leverage']==3
    assert terms['new_risk_notional_cap']=='180'
    assert terms['margin_brackets'][-1]['notional_cap']=='200'
    assert terms['margin_brackets'][0]['maximum_initial_leverage']==10
    assert terms['source_verified'] is False and terms['actual_paid_fees'] is None
    assert len(terms['observations']['before']['commission']['response_sha256'])==64
    assert 'raw_utf8' not in json.dumps(terms)
    assert result['trials'][0]['account']['reconciliation']['fee']=='0.00198'
    assert result['trials'][0]['account']['ending_state']['margin_admission_policy']=='INITIAL_FOR_NEW_EXPOSURE_MAINTENANCE_FOR_REDUCTION_V1'
