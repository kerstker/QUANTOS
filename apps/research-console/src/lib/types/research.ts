/** Research Command Center TypeScript types (Phase 1K.2). No execution types. */

export type RiskDecision =
  | "RISK_APPROVED" | "RISK_APPROVED_WITH_LIMITS" | "NEEDS_MORE_DATA"
  | "REJECTED_MARKET_RISK" | "REJECTED_LIQUIDITY_RISK" | "REJECTED_CONCENTRATION_RISK"
  | "REJECTED_CORRELATION_RISK" | "REJECTED_DRAWDOWN_RISK" | "REJECTED_REGIME_RISK"
  | "REJECTED_STRESS_FAILURE" | "REJECTED_CAPACITY_RISK" | "REJECTED_MODEL_ALPHA_RISK";

export type ValidationDecision =
  | "APPROVED" | "APPROVED_WITH_LIMITS" | "NEEDS_MORE_DATA"
  | "REJECTED_OVERFIT" | "REJECTED_LEAKAGE" | "REJECTED_LOW_CAPACITY"
  | "REJECTED_UNSTABLE" | "REJECTED_HIGH_CORRELATION";

export type AgentState = "CREATED" | "IDLE" | "PLANNING" | "RUNNING" | "WAITING_TOOL"
  | "EVALUATING" | "REFLECTING" | "COMPLETED" | "FAILED" | "PAUSED";

export type PaperLifecycleState = "VALIDATED" | "PAPER_RUNNING" | "PAPER_PASSED"
  | "PAPER_FAILED" | "PAPER_PAUSED" | "PRODUCTION_CANDIDATE";

export type PortfolioLifecycleState = "DRAFT" | "SIMULATING" | "SIMULATION_PASSED"
  | "SIMULATION_FAILED" | "PAPER_PORTFOLIO_READY" | "REJECTED" | "ARCHIVED";

export interface AlphaCandidate {
  candidate_id: string; expression: string; ic: number; rank_ic: number;
  icir: number; sharpe: number; turnover: number; decay_half_life: number;
  capacity_estimate: number; correlation_penalty: number;
  composite_score: number; lifecycle_state: string; promoted_at?: number;
}

export interface ValidationReport {
  run_id: string; alpha_id: string; decision: ValidationDecision;
  gates_passed: number; gates_total: number; reasons: string[];
  leakage_pass: boolean; walk_forward_pass: boolean; multiple_testing_pass: boolean;
  costs_pass: boolean; capacity_pass: boolean; decay_pass: boolean;
  oos_sharpe: number; oos_ic: number; net_sharpe: number;
}

export interface ResearchAgent {
  agent_id: string; name: string; state: AgentState;
  runs_completed: number; alpha_approved: number;
  performance_score: number; budget_spent_cny: number;
}

export interface AgentRuntimeRecord {
  agent_id: string;
  name: string;
  state: AgentState;
  runs_completed: number;
  runs_active: number;
  runs_succeeded: number;
  runs_failed: number;
  last_run_id: string | null;
  last_run_kind: string | null;
  last_run_at: number | null;
  agent_auto_approved: number;
  can_reach_approved: boolean;
  credentials_access: boolean;
  order_submission: boolean;
}

export interface AgentEngineRecord {
  engine_id: string;
  implementation: string;
  readiness: string;
}

export interface AgentsPayload extends ConsolePayloadBase {
  summary: {
    active: number;
    total_runs: number;
    completed: number;
    succeeded: number;
    failed: number;
    cancelled: number;
    agent_auto_approved: number;
    registry_status: string;
  };
  agents: AgentRuntimeRecord[];
  engines: AgentEngineRecord[];
  observed_run_kinds: string[];
  realtime_inputs: {
    status: 'AVAILABLE' | 'STALE' | 'UNAVAILABLE';
    fresh: boolean;
    age_ms: number | null;
    source: string;
    feature_version: string | null;
    feature_count: number;
    instrument_count: number;
    retained_value_count: number;
    drift_status: string | null;
    drift_status_counts: Record<string, number>;
    drift_calibration: string | null;
    drift_calibration_id: string | null;
    drift_warn_ks: number | null;
    drift_block_ks: number | null;
    batch_chain_valid: boolean;
    alerting: {
      status: string;
      current_severity: string | null;
      local_delivery: string | null;
      external_delivery: string | null;
      independent_worker: boolean;
      journal_chain_valid: boolean;
      event_count: number;
    };
    consumption_mode: 'READ_ONLY_DIAGNOSTIC_CONTEXT';
    automatic_action: false;
    promotion_authority: false;
  };
  containment: {
    authorized_level: string;
    research_only: boolean;
    human_approval_required: boolean;
    agent_can_reach_approved: boolean;
    credentials_access: boolean;
    broker_connectivity: boolean;
    order_submission: boolean;
    live_trading: boolean;
  };
}

export interface PaperRun {
  run_id: string; alpha_id: string; status: string;
  metrics: Record<string,number>; equity_curve: {timestamp:number;equity:number}[];
  total_trades: number; final_equity: number;
}

export interface PortfolioWeights { weights_id: string; weights: Record<string,number>; gross_exposure: number; expected_sharpe: number; expected_ic: number; method?: string; optimizer_real?: boolean; }
export interface PortfolioSimulation { run_id: string; status: string; metrics: Record<string,number>; equity_curve: {day:number;equity:number}[]; }
export interface RiskReport { run_id: string; portfolio_id: string; decision: RiskDecision; gates_passed: number; gates_total: number; reasons: string[]; gate_results: Record<string,boolean>; }
export interface EventRecord { event_id: string; event_type: string; timestamp: number; source: string; correlation_id?: string; payload: Record<string,unknown>; }
export interface ServiceHealth {
  service: string;
  status: "healthy"|"degraded"|"down"|"unknown";
  latency_ms: number | null;
  error_rate: number | null;
  probe?: string;
}

export interface ChartPoint {
  x: number;
  y: number;
}

export interface DrawdownPoint extends ChartPoint {
  worst?: number;
}

export interface CalculationLineageItem {
  id: string;
  layer: string;
  formula: string;
  backend: string;
  inputs: string[];
  output: string;
}

export interface ComputationRiskReport {
  run_id: string;
  portfolio_id: string;
  decision: RiskDecision | string;
  gates_passed: number;
  gates_total: number;
  reasons: string[];
  gate_results: Record<string, boolean>;
  reproducibility_hash?: string;
}

export interface ConsolePayloadBase {
  source: "BACKEND_COMPUTED_SNAPSHOT" | string;
  read_only: true;
  api_version: string;
  // every console_api snapshot carries an honesty marker; pages read it to label the ProvenanceRibbon
  // accurately (REAL_* markers => real data, else synthetic). Optional so mock fixtures need not set it.
  data_provenance?: string;
  /** Common backend admission metadata added by the console snapshot middleware. */
  snapshot_status?: 'REAL' | 'SYNTHETIC' | 'NO_SNAPSHOT' | 'STALE' | 'DATA_BLOCKED' | 'UNAVAILABLE' | string;
  snapshot_id?: string | null;
  snapshot_ids?: string[];
  data_quality?: {
    status?: string;
    provenance?: string;
    snapshot_present?: boolean;
    is_synthetic?: boolean;
    is_stale?: boolean;
    is_blocked?: boolean;
    is_available?: boolean;
    [key: string]: unknown;
  };
}

export interface ResearchStats {
  total_hypotheses: number;
  alpha_candidates: number;
  validation_pass_rate: number;
  paper_pass_rate: number;
  portfolio_candidates: number;
  risk_approved: number;
  active_runs: number;
  rejection_reasons: Record<string, number>;
}

export interface FunnelPoint {
  id: string;
  label: string;
  value: number;
}

export interface OverviewPayload extends ConsolePayloadBase {
  stats: ResearchStats;
  funnel: FunnelPoint[];
  agents: ResearchAgent[];
}

export interface EventsPayload extends ConsolePayloadBase {
  events: EventRecord[];
  event_bus_health: EventBusHealth;
}

export interface EventBusHealthAlert {
  code: string;
  observed: number | null;
  severity: string;
  threshold: number | null;
}

export interface EventBusHealth {
  schema_version: number;
  verdict: string;
  checked_at_ms: number;
  group_id: string;
  consumer_mode: string;
  integrity: {
    event_chain_valid: boolean | null;
    consumer_chain_valid: boolean | null;
    coordination_chain_valid: boolean | null;
  };
  active_workers: string[];
  partition_count: number;
  pending_events: number | null;
  oldest_pending_age_ms: number | null;
  dead_letter_count: number | null;
  event_records: number | null;
  consumer_journal_records: number | null;
  coordination_journal_records: number | null;
  allocated_bytes: number | null;
  retention_mode: string;
  alerts: EventBusHealthAlert[];
}

export interface ExperimentArtifact {
  artifact_id: string;
  kind: string;
  status: string;
  uri: string;
  content_hash: string;
}

export interface ResearchExperiment {
  experiment_id: string;
  alpha_id: string;
  expression: string;
  owner_agent: string;
  stage: string;
  decision: ValidationDecision | string;
  created_at: number;
  execution_mode: "L2_DRY_RUN_ONLY";
  config: {
    universe: string;
    horizon_days: number;
    cost_bps: number;
    seed: number;
    data_snapshot_id: string;
    config_hash: string;
  };
  metrics: {
    ic: number;
    rank_ic: number;
    sharpe: number;
    turnover: number;
    capacity_estimate: number;
    composite_score: number;
  };
  reproducibility_hash: string;
  artifacts: ExperimentArtifact[];
  lineage: {
    upstream: string[];
    downstream: string[];
  };
}

export interface ExperimentsPayload extends ConsolePayloadBase {
  registry_mode: string;
  summary: {
    total_experiments: number;
    approved: number;
    rejected: number;
    needs_more_data: number;
    artifact_count: number;
    reproducible: number;
  };
  experiments: ResearchExperiment[];
  stage_counts: Record<string, number>;
  lineage_policy: {
    requires_config_hash: boolean;
    requires_data_snapshot: boolean;
    requires_metric_artifact: boolean;
    mutable_records: boolean;
  };
}

export interface DataCatalogCheck {
  check_id: string;
  name: string;
  status: "PASS" | "WARN" | "FAIL" | string;
  metric: number;
  threshold: number;
}

export interface DataCatalogDataset {
  dataset_id: string;
  name: string;
  domain: string;
  version: string;
  snapshot_id: string;
  row_count: number;
  coverage: number;
  freshness_hours: number;
  quality_score: number;
  point_in_time_safe: boolean;
  owner: string;
  storage_uri: string;
  content_hash: string;
  checks: DataCatalogCheck[];
}

export interface DataCatalogFeature {
  feature_id: string;
  name: string;
  family: string;
  expression: string;
  source_dataset_id: string;
  version: string;
  owner: string;
  lookback_days: number;
  point_in_time_policy: {
    event_timestamp_column: string;
    as_of_join: boolean;
    max_source_lag_days: number;
    leakage_guard: string;
  };
  quality_score: number;
  lineage_hash: string;
  used_by_experiments: string[];
}

export interface DataCatalogLineageEdge {
  from: string;
  to: string;
  kind: "DATASET_TO_FEATURE" | "FEATURE_TO_EXPERIMENT" | string;
}

export interface DataChannelArtifact {
  path: string;
  exists: boolean;
  size_bytes: number | null;
  last_observed_ms: number | null;
  status: string;             // REAL_HISTORY | REAL_SNAPSHOT | REAL_DIAGNOSTIC | PUBLIC_STREAM_REQUIRED | BLOCKED_MISSING
  parse_error: string | null;
}
export interface DataChannel {
  channel_id: string;
  provider: string;
  venue: string;
  dataset_id: string;
  cadence: string;
  temporal_resolution: string;
  fields: string[];
  artifact_path: string;
  availability: string;
  alpha_families: string[];
  freshness_sla_hours: number | null;
  endpoint_class: string;
  notes: string;
  artifact: DataChannelArtifact;
}
export interface BinanceRealtimeCoverage {
  requested?: number;
  complete_core?: number;
  persisted_this_flush?: number;
  complete?: number;
  inserted?: number;
}
export interface BinanceRealtimePayload {
  verdict: string;
  checked_at_ms: number;
  universe_size: number;
  stream: {
    status_present: boolean;
    effective_streaming: boolean;
    reported_streaming: boolean;
    fresh: boolean;
    age_ms: number | null;
    lag_ms: number | null;
    coverage: BinanceRealtimeCoverage;
    connections: Record<string, boolean>;
    reconnects: Record<string, number>;
    event_counts: Record<string, number>;
    rejected_frames: number | null;
    chain_valid: boolean | null;
    routes: Record<string, string>;
    source_path: string | null;
  };
  rest_snapshot: {
    status_present: boolean;
    fresh: boolean;
    age_ms: number | null;
    verdict: string | null;
    coverage: BinanceRealtimeCoverage;
    chain_valid: boolean | null;
    errors: number;
    fields: string[];
    source_path: string | null;
  };
  systemic_risk: {
    status_present: boolean;
    fresh: boolean;
    age_ms: number | null;
    verdict: string | null;
    coverage: BinanceRealtimeCoverage;
    chain_valid: boolean | null;
    row_count: number;
    row_counts: Record<string, number>;
    inserted: Record<string, number>;
    latest: Record<string, {
      adl_risk?: string;
      basis_rate?: number;
      constituent_count?: number;
      constituent_dispersion_bps?: number;
      insurance_asset_count?: number;
    }>;
    errors: number;
    request_rate_limit?: {
      planned_ip_weight: number;
      endpoint_weight_per_symbol: number;
      configured_max_workers: number;
      min_request_interval_ms: number;
      cooldown_active: boolean;
      last_status_code: number | null;
      retry_after_seconds: number;
      max_used_weights: Record<string, number>;
    };
    fields: string[];
    source_path: string | null;
  };
  rpi_depth: {
    status_present: boolean;
    fresh: boolean;
    age_ms: number | null;
    verdict: string | null;
    coverage: BinanceRealtimeCoverage;
    chain_valid: boolean | null;
    row_count: number;
    inserted: number;
    latest: Record<string, {
      bid_level_count?: number;
      ask_level_count?: number;
      spread_bps?: number;
      notional_imbalance?: number;
    }>;
    errors: number;
    request_policy: Record<string, string | number>;
    fields: string[];
    source_path: string | null;
  };
  feature_projection: {
    status_present: boolean;
    fresh: boolean;
    age_ms: number | null;
    status: string | null;
    feature_version: string | null;
    feature_count: number;
    instrument_count: number;
    retained_value_count: number;
    drift_status: string | null;
    drift_status_counts: Record<string, number>;
    drift_series_count: number;
    batch_chain_valid: boolean | null;
    calibration: string | null;
    calibration_id: string | null;
    warn_ks: number | null;
    block_ks: number | null;
    calibration_window_count: number;
    load_bearing: boolean;
    source_path: string | null;
  };
  drift_alerting: {
    status_present: boolean;
    fresh: boolean;
    age_ms: number | null;
    status: string | null;
    current_severity: string | null;
    reason: string | null;
    transition: string | null;
    deduplicated: boolean;
    local_delivery: string | null;
    external_delivery: string | null;
    independent_worker: boolean;
    event_count: number;
    chain_valid: boolean | null;
    source_path: string | null;
  };
  api_coverage: {
    status: string;
    catalog_endpoint_count: number;
    accounted_endpoint_count: number;
    accounted_ratio: number;
    load_bearing_endpoint_count: number;
    gap_count: number;
    gaps: Array<{
      id: string;
      title: string;
      path: string;
      reason: string;
    }>;
    error_type?: string;
  };
  stream_coverage: {
    status: string;
    catalog_stream_count: number;
    accounted_stream_count: number;
    accounted_ratio: number;
    load_bearing_stream_count: number;
    gap_count: number;
    gaps: Array<{
      id: string;
      title: string;
      route: string;
      stream: string;
      reason: string;
    }>;
    error_type?: string;
  };
  safety: {
    public_market_data_only: boolean;
    credentials: boolean;
    private_stream: boolean;
    broker_connectivity: boolean;
    order_submission: boolean;
    live_trading: boolean;
  };
}
export interface DataCatalogPayload extends ConsolePayloadBase {
  catalog_mode: string;
  feature_store_mode: string;
  summary: {
    datasets: number;
    features: number;
    quality_checks: number;
    quality_passed: number;
    point_in_time_safe_features: number;
    lineage_edges: number;
    avg_dataset_quality: number;
    avg_feature_quality: number;
  };
  datasets: DataCatalogDataset[];
  features: DataCatalogFeature[];
  lineage_edges: DataCatalogLineageEdge[];
  data_channels: {
    status: string;           // REAL_COVERAGE_INVENTORY | UNAVAILABLE | INVALID
    generated_at_ms?: number | null;
    channels: DataChannel[];
    summary: Record<string, unknown>;
    honesty?: string[];
  };
  binance_realtime: BinanceRealtimePayload;
  quality_policy: {
    requires_dataset_hash: boolean;
    requires_event_timestamp: boolean;
    requires_asof_join: boolean;
    blocks_future_leakage: boolean;
    mutable_raw_inputs: boolean;
  };
}

export interface MonitoringDriftRecord {
  monitor_id: string;
  category: string;
  subject_id: string;
  metric: string;
  baseline: number;
  current: number;
  drift_score: number;
  warn_threshold: number;
  breach_threshold: number;
  status: 'OK' | 'WATCH' | 'BREACH' | string;
  owner: string;
  evidence_ref: string;
  last_checked: number;
  remediation: string;
}

export interface MonitoringDriftPayload extends ConsolePayloadBase {
  monitoring_mode: string;
  summary: {
    monitors: number;
    ok: number;
    watch: number;
    breach: number;
    categories: number;
    blocking_monitors: number;
    avg_drift_score: number;
    live_authorization_allowed: boolean;
  };
  status_counts: Record<string, number>;
  category_counts: Record<string, number>;
  monitors: MonitoringDriftRecord[];
  top_breaches: MonitoringDriftRecord[];
  watchlist: MonitoringDriftRecord[];
  monitoring_policy: {
    read_only: boolean;
    requires_owner: boolean;
    requires_thresholds: boolean;
    requires_evidence_ref: boolean;
    mutable_alerts: boolean;
    authorizes_live_trading: boolean;
  };
}

export interface RiskDashboardPayload extends ConsolePayloadBase {
  selected_alpha_id: string;
  alpha: AlphaCandidate;
  primary_report: ComputationRiskReport;
  reports: ComputationRiskReport[];
  lineage: CalculationLineageItem[];
}

export interface ValidationDashboardPayload extends ConsolePayloadBase {
  selected_report: ValidationReport;
  reports: ValidationReport[];
  lineage: CalculationLineageItem[];
}

export interface PaperTradingPayload extends ConsolePayloadBase {
  selected_alpha_id: string;
  run: PaperRun;
  lineage: CalculationLineageItem[];
}

export interface PortfolioDashboardPayload extends ConsolePayloadBase {
  simulation: PortfolioSimulation;
  drawdown_curve?: Array<{day: number; drawdown: number; worst: number}>;
  weights: PortfolioWeights;
  risk_contributions?: Array<{
    alpha_id: string;
    weight: number;
    annualized_volatility: number;
    contribution: number;
  }>;
  return_attribution?: Array<{
    alpha_id: string;
    weight: number;
    alpha_total_return: number;
    contribution_return: number;
  }>;
  analytics_method?: string;
  lineage: CalculationLineageItem[];
}

export interface IntegrationGateStatus {
  g: string;
  s: string;
}

export type TopTierRequirementStatus =
  | "PASS"
  | "PARTIAL"
  | "FAILED"
  | "UNVERIFIED"
  | "MANUAL_EVIDENCE_REQUIRED";

export interface TopTierRequirement {
  id: string;
  domain: string;
  tier: "A" | "B";
  target_maturity: string;
  current_maturity: string;
  classification: "L2_SAFE_NOW" | "NEEDS_EXTERNAL" | "POST_L2";
  description: string;
  required_evidence: string[];
  red_line: boolean;
  checker: string;
  next_action: string;
  status: TopTierRequirementStatus;
  meets_target: boolean;
  evidence: string[];
  detail: string;
}

export interface TopTierDomainAssessment {
  domain: string;
  current_maturity: string;
  requirements: number;
  passed: number;
  red_lines_failed: number;
}

export interface SystemCompletenessPayload extends ConsolePayloadBase {
  data_provenance?: "TOP_TIER_EVIDENCE_ASSESSMENT" | string;
  assessment: {
    schema_version: number;
    verdict: string;
  };
  inventory: {
    domains: { domain: string; n_modules: number; loc: number }[];
    total_services_modules: number; total_services_loc: number;
    n_test_files: number; n_test_functions: number;
    n_console_pages: number; n_console_endpoints: number;
  };
  summary: {
    total: number;
    passed: number;
    partial: number;
    failed: number;
    unverified: number;
    manual_evidence_required: number;
    red_lines_failed: number;
  };
  domains: TopTierDomainAssessment[];
  requirements: TopTierRequirement[];
  red_lines: TopTierRequirement[];
  safety_boundary: {
    read_only_console: boolean;
    execution_boundary: string;
    authorized_level: string;
    live_trading_authorized: boolean;
  };
}

export interface SystemHealthPayload extends ConsolePayloadBase {
  services: ServiceHealth[];
  gates: IntegrationGateStatus[];
  safety_checks: string[];
}

/** Read-only frontend/backend runtime handshake (console-runtime.v1). */
export interface RuntimeContractPayload extends ConsolePayloadBase {
  contract_version: string;
  service: string;
  instance_id: string;
  process_id: number;
  build_sha: string;
  build_sha_known: boolean;
  event_schema_catalog: {
    sha256: string;
    event_type_count: number;
    latest_versions: Record<string, string>;
  };
  safety_boundary: {
    read_only_console: boolean;
    authorized_level: "L2_DRY_RUN_ONLY" | string;
    live_trading_authorized: false;
    order_submission_allowed: false;
    broker_connectivity: false;
  };
  generated_at_ms: number;
}

export type Phase3ACriterionStatus = "APPROVED" | "PENDING" | "REJECTED";

export interface Phase3ACriterion {
  id: string;
  criterion: string;
  category: string;
  owner: string;
  status: Phase3ACriterionStatus;
  evidence: string;
}

export interface Phase3ABlocker {
  id: string;
  blocker: string;
  owner: string;
  dependency: string;
  next_action: string;
}

export interface Phase3AReadiness {
  current_level: "L2_DRY_RUN_ONLY";
  target_level: "L3_BROKER_TEST_ENV_READY";
  decision: "NO_GO" | "GO_WITH_CONDITIONS" | "GO_FOR_PHASE_3A";
  phase_status: "BLOCKED";
  implementation_authorized: false;
  approved_count: number;
  total_count: number;
  criteria: Phase3ACriterion[];
  blockers: Phase3ABlocker[];
}

/* ---- Crypto Live: REAL read-only CoinGecko research snapshot (not synthetic) ---- */
export interface CryptoCoinMeta {
  instrument_id: string;
  coin_id: string;
  bars: number;
  ingested: number;
  rejected: number;
}

export interface CryptoLiveMeta {
  provider: string;
  asset_class: string;
  vs_currency: string;
  days: number;
  granularity: string;
  fwd_horizon: number;
  n_instruments: number;
  n_periods: number;
  coins: CryptoCoinMeta[];
  dropped?: Array<{ instrument_id: string; coin_id: string; reason: string }>;
  n_dropped?: number;
  search_mode: string;
  evo_generations: number;
  evo_population: number;
  evo_elite: number;
  operators: string[];
  windows: number[];
}

export interface CryptoLiveSummary {
  n_evaluated: number;
  trial_count: number;
  auto_approved: number;
  requires_human_review: number;
  rejected: number;
  max_net_sharpe: number | null;
  max_deflated_sharpe: number | null;
  top_alpha_id: string | null;
}

export interface CryptoFactor {
  alpha_id: string;
  name: string;
  family: "price" | "interaction" | "gp_tree" | string;
  expr?: string | null;
  ic: number | null;
  rank_ic: number | null;
  net_sharpe: number | null;
  capacity_adj_sharpe: number | null;
  deflated_sharpe: number | null;
  pbo: number | null;
  turnover: number | null;
  breadth: number | null;
  verdict: string;
  state: string | null;
  reproducibility_hash: string;
}

export interface CryptoOosFold {
  window: number;
  train_range?: [number, number];
  test_range?: [number, number];
  selected?: string[];
  is_sharpe: number;
  oos_sharpe: number;
}

export interface CryptoOos {
  method: string;
  n_aligned_periods: number;
  candidate_pool: string;
  n_candidates: number;
  selection: {
    n_folds: number;
    folds: CryptoOosFold[];
    mean_is_sharpe: number | null;
    mean_oos_sharpe: number | null;
    oos_minus_is: number | null;
    oos_positive_frac: number | null;
    train_size: number;
    test_size: number;
    embargo: number;
  };
  naive_full_sample_best: { alpha_id: string; sharpe: number };
  headline_factor: {
    alpha_id: string;
    expr?: string | null;
    full_sample_sharpe: number;
    folds: CryptoOosFold[];
    mean_oos_sharpe: number | null;
    oos_positive_frac: number | null;
  };
}

export interface CryptoSynergisticSet {
  method: string;
  n_candidates: number;
  max_k: number;
  min_incremental_ir: number;
  selected: Array<{ id: string; incremental_ir: number; expr?: string | null }>;
  selected_ids: string[];
  dropped_redundant: string[];
}

export interface CryptoCostPoint {
  one_way_bps: number;
  net_sharpe: number;
}

export interface CryptoCostStress {
  factor_id: string;
  turnover: number;
  breakeven_bps: number | null;
  points: CryptoCostPoint[];
}

export interface CryptoRegimeBucket {
  label: string;
  n: number;
  sharpe: number | null;
  positive_frac: number | null;
}

export interface CryptoRegime {
  factor_id: string;
  by_vol: CryptoRegimeBucket[];
  by_trend: CryptoRegimeBucket[];
}

export interface FxYearRow { year: number; n: number; net_sharpe: number | null; }
export interface FxCostSweepRow {
  cost_bps: number; is_best: string; is_sharpe: number; oos_mean_sharpe: number | null; n_folds: number;
}
export interface FxFullUniverse {
  n_currencies?: number; n_periods?: number; fwd_horizon?: number; pool?: string[];
  leakage_verified?: string; verdicts?: Record<string, string>;
  in_sample_best?: { name: string; sharpe: number };
  oos?: { mean_oos_sharpe?: number | null; mean_is_sharpe?: number | null; n_folds?: number;
          oos_positive_frac?: number | null };
  headline?: string; per_year?: FxYearRow[];
}
export interface FxGauntlet {
  universe?: string[]; n_currencies?: number; fwd_horizon?: number;
  cost_sweep?: FxCostSweepRow[]; headline?: string; per_year_5bps?: FxYearRow[];
}
export interface FxResearchPayload extends ConsolePayloadBase {
  data_provenance: "REAL_FX_ECB_OFFLINE_SNAPSHOT" | string;
  snapshot_present: boolean;
  verdict: string;                 // NO_SNAPSHOT | REFUTED_NO_TRADABLE_EDGE | SURVIVES_TRADABLE_GAUNTLET | LEAD_UNGATED
  full_universe: FxFullUniverse;
  tradable_gauntlet: FxGauntlet;
  honesty_note: string;
}

export interface CarrySpec {
  net_sharpe?: number; max_drawdown?: number; worst_day?: number;
  per_year?: Record<string, number>; blocks?: number[]; n_days?: number;
}
export interface CarryCapacityRow { aum: number; net_sharpe: number; ann_return: number; ann_pnl_usd: number; }
export interface CarryCapacityBand {
  rows?: CarryCapacityRow[]; capacity_half_sharpe_usd?: number; pnl_peak_aum_usd?: number; pnl_peak_usd?: number;
}
export interface CarryReadinessPayload extends ConsolePayloadBase {
  data_provenance: "REAL_CARRY_OFFLINE_SNAPSHOT" | string;
  snapshot_present: boolean;
  verdict: string;        // VALIDATED_NOT_LIVE_READY | PARTIAL_OVERFIT_RISK | REFUTED | INCOMPLETE | NO_SNAPSHOT
  strategy: {
    sharpe_price_only?: number; sharpe_price_plus_funding?: number; sharpe_net_5bps?: number;
    avg_turnover?: number; avg_funding_bps_day?: number; n_instruments?: number; n_days?: number; signal_expr?: string;
  };
  robustness: { verdict?: string; n_liquid?: number; specs?: Record<string, CarrySpec> };
  capacity: {
    base_sharpe?: number; funding_bps_per_year?: Record<string, number>; pnl_top5_share?: number;
    capacity_curve?: Record<string, CarryCapacityBand>;
  };
  deployment_gaps: string[];
  honesty_note: string;
}

export interface DryRunPnl {
  total_pnl?: number; net_pnl?: number; gross_pnl?: number; costs?: number;
  equity?: number; turnover?: number; residual?: number; positions?: Record<string, number>;
  // multi-period summary (run_multi_period_dry_run)
  total_return?: number; sharpe?: number; max_drawdown?: number; final_equity?: number;
  total_funding?: number; n_periods?: number; total_rejected?: number; avg_net_exposure_pct?: number;
  // vol-target curve summary stats (审计发现 #10): honestly null when vol_target wasn't requested —
  // NOT 0 and NOT omitted, so callers can tell "not computed" apart from "computed as zero".
  sharpe_vt?: number | null; max_drawdown_vt?: number | null;
}

export interface DryRunRunRow {
  run_id: string;
  strategy_id: string;
  timestamp: number;
  n_intents: number;
  n_approved: number;
  n_rejected: number;
  rejected_reasons: string[];
  pnl: DryRunPnl;
  audit_root: string;
  detail: {
    n_fills?: number; n_adapter_rejected?: number; targets?: Record<string, number>;
    mode?: string; n_periods?: number; cadence?: string;
    equity_curve?: { t: number; equity: number; ret: number; approved?: number; rejected?: number; fills?: number }[];
    equity_curve_vt?: { t: number; equity: number; ret: number }[];
  };
}

export interface PaperOrderPreviewOrder {
  instrument: string;
  side: string;
  quantity: number;
  target_signed_quantity?: number;
  decision_mark_price?: number;
  notional?: number;
  paper_position_before?: number;
  paper_position_after_fill?: number;
}

export interface PaperOrderPreviewAlpha {
  alpha_id: string;
  decision_ts?: number;
  decision_source?: string;
  scope?: string[];
  paper_positions_before?: Record<string, number>;
  paper_positions?: Record<string, number>;
  orders?: PaperOrderPreviewOrder[];
  n_intents?: number;
  n_approved?: number;
  n_rejected?: number;
  rejected_reasons?: string[];
  n_paper_fills?: number;
  paper_execution?: string;
  reconciliation_residual?: number;
  replayed_from_store?: boolean;
  research_risk?: {
    decision?: string;
    status?: string;
    gates_passed?: number;
    gates_run?: number;
    engines_run?: Record<string, boolean>;
    reasons?: string[];
    provenance?: string;
    scope?: string;
    reports?: {
      capacity?: {
        passed?: boolean;
        capacity_usage?: number;
        bottleneck_alpha?: string;
      };
      model_alpha?: {
        passed?: boolean;
        alphas_with_decay?: number;
        alphas_with_overfitting_flags?: number;
        alphas_with_leakage_flags?: number;
        min_validation_confidence?: number;
        min_paper_confidence?: number;
      };
    };
  };
}

export interface PaperOrderPreviewPayload {
  source: string;
  read_only: boolean;
  snapshot_present: boolean;
  generated_at?: number;
  verdict: {
    paper_preview_ready?: boolean;
    workflow_admission_valid?: boolean;
    workflow_authorized_ids?: string[];
    live_allowed: boolean;
    order_submission_allowed: boolean;
  };
  alphas: Record<string, PaperOrderPreviewAlpha>;
  errors: Record<string, { error_type?: string; error?: string; paper_execution?: string }>;
  safety: {
    paper_only: boolean;
    broker_connectivity: boolean;
    broker_order_submission: boolean;
    credentials_accessed: boolean;
  };
}

export interface L2PaperOperatorPayload {
  source: string;
  read_only: boolean;
  snapshot_present: boolean;
  generated_at?: number;
  verdict: {
    operator_completed: boolean;
    paper_preview_ready: boolean;
    research_packet_ready_for_all_three: boolean;
    live_allowed: boolean;
    order_submission_allowed: boolean;
  };
  stages: {
    three_alpha_paper_flow: { status: string; flow_completed: boolean };
    paper_order_preview: { status: string; paper_preview_ready: boolean };
  };
  safety: {
    paper_only: boolean;
    private_binance_access: boolean;
    credential_access: boolean;
    broker_order_submission: boolean;
  };
}

export interface PaperRealtimeAlpha {
  alpha_id: string;
  observed_at?: number;
  status?: string;
  n_fills?: number;
  positions?: Record<string, number>;
  marks?: Record<string, number>;
  cash?: number;
  market_value?: number;
  equity?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  funding_pnl?: number;
  funding_events?: number;
  funding_data_complete?: boolean;
  funding_missing_instruments?: string[];
  funding_missing_events?: string[];
  gross_pnl?: number;
  costs?: number;
  net_pnl?: number;
  return_on_aum?: number;
  turnover?: number;
  reconciliation_residual?: number;
  paper_only?: boolean;
  live_allowed?: boolean;
  order_submission_allowed?: boolean;
}

export interface PaperRealtimeBacktest {
  run_id?: string;
  n_periods?: number;
  total_return?: number;
  sharpe?: number;
  sharpe_vt?: number;
  max_drawdown?: number;
  max_drawdown_vt?: number;
  reconciliation_residual?: number;
  research_risk_decision?: string;
  provenance?: string;
}

export interface PaperRealtimeHistoryPoint {
  alpha_id: string;
  observed_at: number;
  equity?: number;
  net_pnl?: number;
}

export interface PaperRealtimePayload {
  source: string;
  read_only: boolean;
  snapshot_present: boolean;
  generated_at?: number;
  observed_at?: number;
  provider?: string;
  verdict: {
    cycle_completed: boolean;
    paper_mark_ready: boolean;
    backtest_reference_present_for_all_three: boolean;
    live_allowed: boolean;
    order_submission_allowed: boolean;
    new_paper_orders_allowed?: boolean;
  };
  operator_mode?: string | null;
  readiness_class?: string;
  alphas: Record<string, PaperRealtimeAlpha>;
  backtests: Record<string, PaperRealtimeBacktest>;
  history?: PaperRealtimeHistoryPoint[];
  funding_refresh?: {
    attempted?: boolean;
    provider?: string;
    observed_at?: number | null;
    error?: string | null;
  };
  paper_ledger?: {
    n_rows?: number;
    n_decisions?: number;
    latest_decision_ts?: number | null;
  };
  persistence: { new_snapshots: number; chain_valid: boolean };
  errors: Record<string, string>;
  safety: {
    paper_only: boolean;
    public_market_data_only: boolean;
    private_binance_access: boolean;
    credentials_accessed: boolean;
    broker_connectivity: boolean;
    broker_order_submission: boolean;
  };
}

export interface PaperShadowPayload {
  source: string;
  read_only: boolean;
  snapshot_present: boolean;
  execution_class: 'AUTOMATED_NONPROMOTABLE_SHADOW_PAPER' | string;
  status: string;
  generated_at?: number | null;
  observed_at?: number | null;
  provider?: string | null;
  verdict: {
    shadow_paper_running: boolean;
    auto_rebalance_ready: boolean;
    paper_mark_ready: boolean;
    boundary_valid: boolean;
    candidate_coverage_complete?: boolean;
    candidate_count?: number;
    official_paper_ready: false;
    promotion_eligible: false;
    forward_evidence_eligible: false;
    deployment_eligible: false;
    live_allowed: false;
    order_submission_allowed: false;
  };
  alphas: Record<string, PaperRealtimeAlpha>;
  previews: Record<string, PaperOrderPreviewAlpha>;
  candidate_universe?: {
    authority: string;
    expected: string[];
    expected_count: number;
    running_count: number;
    coverage_complete: boolean;
  };
  history: PaperRealtimeHistoryPoint[];
  totals: {
    equity?: number | null;
    net_pnl?: number | null;
    funding_pnl?: number | null;
  };
  paper_ledger: {
    n_rows?: number;
    n_decisions?: number;
    latest_decision_ts?: number | null;
  };
  persistence: {
    new_snapshots: number;
    chain_valid: boolean;
  };
  errors: Record<string, string>;
  safety: {
    paper_only: true;
    separate_from_official_paper: true;
    public_market_data_only: true;
    private_account_access: false;
    credentials_accessed: false;
    broker_connectivity: false;
    broker_order_submission: false;
  };
}

export interface PaperHealthPayload {
  source: string;
  read_only: boolean;
  snapshot_present: boolean;
  generated_at?: number;
  observed_at?: number;
  age_ms?: number;
  max_age_ms?: number;
  verdict: {
    paper_ready: boolean;
    paper_mark_ready: boolean;
    paper_preview_ready: boolean;
    backtest_reference_present_for_all_three: boolean;
    funding_data_complete_for_all_three?: boolean;
    live_allowed: boolean;
    order_submission_allowed: boolean;
  };
  readiness_class?:
    | 'PAPER_READY'
    | 'PAPER_FORWARD_EVIDENCE_MATURING'
    | 'PAPER_MARKS_ONLY_REPLAY'
    | 'PAPER_MARKS_READY_PENDING_RISK'
    | 'PAPER_MARKS_READY_PENDING_HEALTH'
    | 'PAPER_CYCLE_BLOCKED'
    | 'PAPER_BLOCKED'
    | string;
  next_action?: string | null;
  blockers: string[];
  research_risk_bundle_complete?: boolean;
  research_risk_bundles?: Record<string, {
    status?: string;
    provenance?: string;
    path?: string;
    bundle_sha256?: string;
    missing?: string[];
    reasons?: string[];
  }>;
  forward_evidence?: {
    present?: boolean;
    ran_at?: number;
    evidence_gate_periods?: number;
    complete_for_all_three?: boolean;
    maturity_state?:
      | 'FORWARD_EVIDENCE_MATURING'
      | 'FORWARD_EVIDENCE_COMPLETE'
      | 'FORWARD_EVIDENCE_BLOCKED'
      | string;
    evidence_maturing?: boolean;
    next_realization_eligible_at?: number | null;
    wait_ms?: number | null;
    max_periods_to_gate?: number;
    quality_evidence_current?: boolean;
    data_quality_admission?: {
      verdict?: string;
      quality_verdict?: string;
      lineage_verdict?: string;
    };
    alphas?: Record<string, {
      n_forward_periods?: number | null;
      periods_to_gate?: number | null;
      pending_decisions?: number | null;
      attested_pending_decisions?: number | null;
      evidence_state?: string;
      paper_sharpe?: number | null;
      last_decision_ts?: number | null;
      next_realization_eligible_at?: number | null;
    }>;
    alerts?: string[];
  };
  safety: {
    paper_only: boolean;
    public_market_data_only: boolean;
    broker_connectivity: boolean;
    credentials_accessed: boolean;
    broker_order_submission: boolean;
  };
}

export interface PaperProfilePayload {
  profile_id: string;
  aum: number;
  read_only: boolean;
  snapshot_present: boolean;
  realtime: {
    cycle_completed: boolean;
    paper_mark_ready: boolean;
    backtest_reference_present_for_all_three: boolean;
    readiness_class?: string;
    observed_at?: number;
    funding_refresh?: PaperRealtimePayload['funding_refresh'];
    chain_valid: boolean;
    alphas: Record<string, PaperRealtimeAlpha>;
    operator_mode?: string | null;
    new_paper_orders_allowed?: boolean;
    total_equity?: number | null;
    total_net_pnl?: number | null;
    paper_ledger?: {
      n_rows?: number;
      n_decisions?: number;
      latest_decision_ts?: number | null;
    };
    history?: PaperRealtimeHistoryPoint[];
  };
  health: {
    paper_ready: boolean;
    paper_mark_ready: boolean;
    paper_preview_ready: boolean;
    funding_data_complete_for_all_three?: boolean;
    readiness_class?: string;
    next_action?: string | null;
    research_risk_bundle_complete?: boolean;
    research_risk_bundles?: PaperHealthPayload['research_risk_bundles'];
    age_ms?: number;
    blockers: string[];
  };
  forward_track?: LiveTradingForwardTrack;
  safety: {
    paper_only: boolean;
    broker_connectivity: boolean;
    credentials_accessed: boolean;
    broker_order_submission: boolean;
    live_allowed: boolean;
    order_submission_allowed: boolean;
  };
}

export interface AlphaPipelineCandidate extends LiveTradingForwardAlpha {
  stage: 'FORWARD_PAPER' | 'EXECUTION_PAPER' | 'LEGACY_DIAGNOSTIC_PAPER' | string;
  paper_class: 'FORWARD_CHALLENGER' | 'CANDIDATE_EXECUTION_PAPER' | 'LEGACY_DIAGNOSTIC_PAPER';
  execution_paper: boolean;
  execution_paper_eligible?: boolean;
  workflow_identity_hash?: string | null;
  workflow_bundle_hash?: string | null;
  deployment_eligible: false;
  blockers: string[];
  independent_evaluation?: {
    status?: string | null;
    verified?: boolean;
    verdict?: string | null;
    run_id?: string | null;
  };
  risk_packet?: {
    status?: string | null;
    decision_basis?: string | null;
    input_evidence_status?: string | null;
    input_fields?: string[];
    admission_blockers?: string[];
    missing?: string[];
    reasons?: Record<string, string>;
  };
  maturity?: {
    observed_periods?: number | null;
    required_periods?: number | null;
  };
}

export interface AlphaPipelinePayload {
  source: string;
  read_only: true;
  factory: {
    snapshot_present: boolean;
    generated_at?: number | null;
    rounds?: number | null;
    dry_limit?: number | null;
    data_dimensions: number;
    survivors: number;
    verdict?: string | null;
    containment?: string | null;
  };
  counts: {
    forward_paper: number;
    execution_paper: number;
    candidate_execution_paper: number;
    legacy_diagnostic_paper: number;
    forward_only: number;
    factory_survivors: number;
  };
  candidates: AlphaPipelineCandidate[];
  workflow?: {
    authority_present: boolean;
    verified: boolean;
    population_count?: number | null;
    event_count?: number | null;
    execution_paper_eligible_count: number;
    release_commit?: string | null;
    source_as_of_ms?: number | null;
  };
  handoff: {
    factory_to_forward_registry_connected: boolean;
    forward_to_execution_dynamic: boolean;
    status: 'CONNECTED' | 'GAPPED';
    gap_codes: string[];
    boundary_codes: string[];
  };
  safety: {
    paper_only: true;
    deployment_eligible: false;
    live_allowed: false;
    order_submission_allowed: false;
  };
}

export interface DataRefreshSourcePayload {
  source: string;
  outcome: string | null;
  duration_ms: number | null;
  refreshed: number | null;
  skipped: number | null;
  new_days: number | null;
  ingested_rows: number | null;
  failed_count: number;
  failed_tickers: string[];
  watermark_after: Record<string, unknown> | null;
}
export interface DataRefreshPayload {
  snapshot_present: boolean;
  run_id?: string | null;
  ran_at?: number | null;
  outcome?: string | null;
  duration_ms?: number | null;
  failed_total?: number | null;
  failed_ticker_total?: number | null;
  sources?: DataRefreshSourcePayload[];
  staleness_after?: Record<string, {
    worst_days?: number | null;
    n_stale?: number | null;
    n_total?: number | null;
  }>;
  raw_lineage?: { status: string | null; datasets: Record<string, unknown> };
  history?: { chain_valid: boolean | null; run_count: number | null };
  slo?: { verdict: string | null; violations: unknown[] };
  alerts?: string[];
}
export interface SchedulerWatchdogCheckPayload {
  task_name: string;
  status: string;
  code: string;
  detail: string;
}
export interface SchedulerWatchdogPayload {
  snapshot_present: boolean;
  observed_at_ms?: number | null;
  verdict: string;
  summary: Record<string, number>;
  checks: SchedulerWatchdogCheckPayload[];
  external_heartbeat_configured?: boolean;
  event_seal_configured?: boolean;
}
export interface RealtimeAlphaCandidatePayload {
  candidate_id: string;
  family_id?: string | null;
  mechanism?: string | null;
  horizon_ms?: number | null;
  status?: string | null;
  n_periods?: number | null;
  mean_breadth?: number | null;
  mean_ic?: number | null;
  ic_t_stat?: number | null;
  net_sharpe?: number | null;
  holdout_net_sharpe?: number | null;
  net_return?: number | null;
  max_drawdown?: number | null;
  walk_forward_positive_frac?: number | null;
  spread_regime_positive_frac?: number | null;
  diagnostic_lead?: boolean;
  gate_pass?: boolean;
  gate_reasons?: string[];
  deflated_sharpe?: number | null;
  pbo?: number | null;
  promotion_eligible: false;
  live_allowed: false;
}
export interface RealtimeAlphaAutonomyPayload {
  source: string;
  read_only: true;
  snapshot_present: boolean;
  boundary_valid: boolean;
  status: string;
  mode: string;
  generated_at_ms?: number | null;
  dataset_version?: string | null;
  search_id?: string | null;
  autonomy: {
    worker?: string | null;
    scheduler_task?: string | null;
    autonomous_search_running: boolean;
    requires_developer_ai: false;
    next_cycle_expected_ms?: number | null;
  };
  data_admission: {
    status?: string;
    quality_status?: string;
    common_start_ms?: number | null;
    common_end_ms?: number | null;
    common_span_ms?: number;
    feature_age_ms?: number | null;
    common_instruments?: string[];
    missing_features?: string[];
    thin_features?: string[];
    reasons?: string[];
    exploration_allowed?: boolean;
    formal_validation_allowed?: boolean;
    shadow_admission_allowed?: boolean;
  };
  feature_quality: {
    status?: string | null;
    generated_at_ms?: number | null;
    drift_status?: string | null;
  };
  executable_depth_capacity: {
    status?: string;
    passed?: boolean;
    evidence_ready?: boolean;
    target_aum_passed?: boolean;
    shadow_sizing_ready?: boolean;
    history_span_ms?: number;
    feature_age_ms?: number | null;
    instrument_count?: number;
    conservative_depth_usd?: number | null;
    max_supported_aum_usd?: number | null;
    target_aum_usd?: number | null;
    shadow_aum_usd?: number | null;
    participation_rate?: number;
    legs_per_side?: number;
    reasons?: string[];
  };
  funnel: {
    pre_registered: number;
    evaluated: number;
    diagnostic_leads: number;
    formal_passes: number;
    shadow_candidates: number;
  };
  families: Array<{
    family_id: string;
    priority?: string;
    hypothesis_count?: number;
  }>;
  top_candidates: RealtimeAlphaCandidatePayload[];
  global_trial_count?: number | null;
  registry: { chain_valid: boolean; event_count?: number | null };
  remaining_gates: string[];
  blockers: string[];
  forward_shadow: {
    snapshot_present: boolean;
    boundary_valid: boolean;
    status: string;
    generated_at_ms?: number | null;
    eligible_candidates: string[];
    feature_input: { ready?: boolean; status?: string; generated_at_ms?: number | null };
    cycle: {
      new_admissions?: number;
      new_decisions?: number;
      new_realizations?: number;
    };
    tracks: Record<string, {
      n_decisions?: number;
      n_pending?: number;
      n_realizations?: number;
      cum_return?: number | null;
      max_drawdown?: number | null;
    }>;
    errors: Record<string, string>;
    ledger: { append_only: boolean; chain_valid: boolean; event_count?: number };
    verdict: {
      forward_shadow_running: boolean;
      promotion_eligible: false;
      forward_evidence_credit: false;
      deployment_eligible: false;
      live_allowed: false;
      order_submission_allowed: false;
    };
    blockers: string[];
    safety: {
      paper_only: true;
      public_market_data_only: true;
      separate_from_official_paper: true;
      credentials_accessed: false;
      broker_connectivity: false;
      broker_order_submission: false;
      allow_ai_direct_trading: false;
    };
  };
  safety: {
    paper_only: true;
    live_trading_enabled: false;
    real_broker_enabled: false;
    allow_order_submission: false;
    allow_ai_direct_trading: false;
    promotion_eligible: false;
    credentials_accessed: false;
  };
}
export interface DryRunPipelinePayload extends ConsolePayloadBase {
  data_provenance: "REAL_DRY_RUN_OFFLINE_SNAPSHOT" | string;
  snapshot_present: boolean;
  verdict: string;            // DRY_RUN_OK | NO_RUN
  n_runs: number;
  latest: DryRunRunRow | Record<string, never>;
  runs: DryRunRunRow[];
  route_tca_history?: {
    schema_version: number;
    simulation_only: true;
    status: 'NO_ROUTE_TCA' | 'SINGLE_SNAPSHOT' | 'LONGITUDINAL_READY' | 'INVALID_RETAINED_EVIDENCE';
    run_count: number;
    record_count: number;
    venue_count: number;
    first_timestamp: number | null;
    last_timestamp: number | null;
    weighted_shortfall_bps: string | null;
    points: {
      run_id: string; strategy_id: string; timestamp: number; routing_snapshot_id: string;
      record_index: number; period: number | null; instrument_id: string; side: string;
      route_status: string; requested_quantity: number; filled_quantity: number;
      fill_fraction: string; reference_notional: string;
      implementation_shortfall_bps: string; venues: string[];
    }[];
    venues: {
      venue_id: string; record_count: number; filled_quantity: number;
      reference_notional: string; weighted_shortfall_bps: string | null;
      first_timestamp: number; last_timestamp: number;
    }[];
  };
  paper_multivenue_sor?: {
    schema_version: number;
    generated_at_ms: number;
    status: 'PASS' | 'BLOCKED';
    mode: 'L2_PAPER_EXECUTION_CANARY';
    simulation_only: true;
    strategy_edge_claim?: false;
    snapshot_id?: string;
    run_id?: string;
    instruments?: string[];
    venues?: string[];
    route_plan_count?: number;
    route_tca_count?: number;
    book_bundle_count?: number;
    fee_policy_version?: string;
    fee_calibration?: string;
    error_type?: string;
    error?: string;
  } | null;
  paper_order_preview?: PaperOrderPreviewPayload;
  paper_operator?: L2PaperOperatorPayload;
  paper_realtime?: PaperRealtimePayload;
  alpha_pipeline?: AlphaPipelinePayload;
  realtime_alpha_autonomy?: RealtimeAlphaAutonomyPayload;
  paper_shadow?: PaperShadowPayload;
  paper_operational?: {
    snapshot_present: boolean;
    generated_at_ms?: number | null;
    status: string;
    chain_valid: boolean;
    policy?: {
      version?: string;
      cadence_ms?: number;
      production_minimum_span_ms?: number;
      production_minimum_successes?: number;
      minimum_success_ratio?: number;
    };
    window: {
      engineering_smoke_ready: boolean;
      production_window_ready: boolean;
      cycle_count?: number;
      accepted_cycle_count: number;
      success_ratio: number;
      history_span_ms: number;
      max_gap_ms?: number | null;
      latest_age_ms?: number | null;
      drills: Record<string, boolean>;
      planned_restart_recoveries: Array<{
        from_release: string;
        to_release: string;
        recovery_ms: number;
        within_policy: boolean;
      }>;
      blockers: string[];
    };
    safety: {
      paper_only: true;
      fault_injection_isolated: true;
      credentials_accessed: false;
      broker_connectivity: false;
      broker_order_submission: false;
    };
  };
  paper_health?: PaperHealthPayload;
  forward_track?: LiveTradingForwardTrack;
  data_refresh?: DataRefreshPayload | null;
  scheduler_watchdog?: SchedulerWatchdogPayload | null;
  paper_profiles?: Record<string, PaperProfilePayload>;
  safety_note: string;
}

// 单个持有期口径的聚合统计(= 快照 row 的六统计 + per_year 形状)。
export interface IcPanelAgg {
  n_periods: number;
  mean_ic: number | null;
  ic_std: number | null;
  icir: number | null;
  hit_rate: number | null;
  t_stat: number | null;
  per_year: Record<string, number | null>;
}

export interface IcPanelRow extends IcPanelAgg {
  name: string;
  universe: string;
  thesis: string;
  n_instruments: number;
  // schema_version>=2(2026-07-06 per-factor 持有期口径):顶层六统计恒 == by_period["1"](h=1 锚,
  // 跨因子可比);h>1 非重叠抽样(t-stat 诚实)。旧快照/mock 无这两个字段 -> 可选,页面兜底显示 "—"。
  holding_periods?: number[];
  by_period?: Record<string, IcPanelAgg>;
}

export interface IcPanelPayload extends ConsolePayloadBase {
  data_provenance: "REAL_IC_PANEL_OFFLINE_SNAPSHOT" | string;
  snapshot_present: boolean;
  verdict: string;               // IC_PANEL_OK | NO_SNAPSHOT
  rows: IcPanelRow[];
  universes: Record<string, { n_instruments: number; date_start: string; date_end: string }>;
  honesty_note: string;
}

export interface ResearchFindingsCapacityBook {
  aum_low: number | null;
  aum_high: number | null;
  binding?: string;
  gross_sharpe?: number;
  calibrated_range?: { low: number | null; high: number | null };
}
export interface ResearchFindingsMetric {
  ann_sharpe: number;
  max_drawdown: number;
  total_return: number;
}
export interface ResearchFindingsPayload extends ConsolePayloadBase {
  data_provenance: "REAL_RESEARCH_FINDINGS" | string;
  snapshot_present: boolean;
  verdict: string;               // FINDINGS_OK | NO_SNAPSHOT
  capacity: { books?: Record<string, ResearchFindingsCapacityBook>; provenance?: string };
  macro_regime: {
    books?: Record<string, { dims?: Record<string, { buckets?: Record<string, { n: number; ann_sharpe: number }> }> }>;
    note?: string;
  };
  combo: {
    corr_carry_crowd?: number | null;
    standalone?: Record<string, ResearchFindingsMetric>;
    combo?: Record<string, ResearchFindingsMetric>;
    note?: string;
  };
  honesty_note: string;
}

export interface Lance1mFeeResult {
  num_trades: number;
  win_rate: number;
  win_loss_ratio: number;
  profit_factor: number;
  total_return: number;
  total_net_pnl: number;
  total_cost: number;
  sharpe: number;
  max_drawdown: number;
  avg_bars_held: number;
  n_bars: number;
}
export interface Lance1mFeeSweepWindow {
  label: string;
  n_bars: number;
  by_fee: Record<string, Lance1mFeeResult>;
}
export interface Lance1mRollingStress {
  fee_bps?: number;
  window_size?: number;
  n_windows?: number;
  pct_positive?: number;
  mean_sharpe?: number;
  median_sharpe?: number;
  mean_alpha?: number;
  median_alpha?: number;
}
export interface Lance1mValidationPayload extends ConsolePayloadBase {
  data_provenance: "REAL_LANCE_1M_VALIDATION" | string;
  snapshot_present: boolean;
  verdict: string;                // REFUTED | NO_SNAPSHOT
  doc: string;
  fee_sweep: Record<string, Lance1mFeeSweepWindow>;
  rolling_window_stress_1bps: Lance1mRollingStress;
  rolling_window_stress_5bps: Lance1mRollingStress;
  parameter_sensitivity_5bps: Record<string, Record<string, number>>;
  ohlc_ambiguity_comparison: {
    conservative_ohlc_ambiguity?: Lance1mFeeResult;
    doc_literal_no_override?: Lance1mFeeResult;
  };
  honesty_note: string;
}

export interface LiveTradingForwardAlpha {
  alpha_id: string;
  n_forward_periods: number | null;
  weeks_to_go_no_go: number | null;
  paper_sharpe: number | null;
  paper_ic: number | null;
  cum_return: number | null;
  backtest_reference_sharpe: number | null;
  reference_weeks: number | null;
  pending_decisions: number | null;
  attested_pending_decisions?: number | null;
  evidence_state?: string;
  last_decision_ts: number | null;
}
export interface LiveTradingForwardTrack {
  present: boolean;
  ran_at?: number;
  evidence_state?: "ATTESTED" | "DATA_BLOCKED" | "STALE" | "UNAVAILABLE" | string;
  attested_realizations?: number;
  attested_pending?: number;
  legacy_unattested_decisions?: number;
  evidence_gate_weeks?: number;
  evidence_gate_eligible?: boolean;
  data_admission?: Record<string, unknown>;
  staleness_days?: Record<string, {
    freshest_days: number | null;
    worst_days: number | null;
    n_stale: number | null;
    n_total: number;
  }>;
  quality_evidence_current?: boolean;
  data_quality_admission?: {
    verdict?: string;
    quality_verdict?: string;
    lineage_verdict?: string;
  };
  alphas?: LiveTradingForwardAlpha[];
  alerts?: string[];
  actions_allowed: false;
}
export interface LiveTradingReconciliation {
  drift?: Record<string, number>;
  in_sync?: boolean;
  max_abs_drift?: number;
  proceed?: boolean;
}
export interface LiveTradingWouldSubmitOrder {
  instrument_id: string;
  side: string;
  quantity: number;
}
export interface LiveTradingTcaByInstrument {
  n_fills: number;
  mean_slippage_bps: number | null;
  worst_slippage_bps: number | null;
}
export interface LiveTradingTca {
  n_fills: number;
  n_with_slippage: number;
  mean_slippage_bps: number | null;
  median_slippage_bps: number | null;
  worst_slippage_bps: number | null;
  notional_weighted_slippage_bps: number | null;
  total_notional: number;
  assumed_cost_bps: number;
  weighted_minus_assumed_bps: number | null;
  by_instrument: Record<string, LiveTradingTcaByInstrument>;
}
export interface LiveTradingParitySummary {
  paper_source: string;          // "forward_ledger" | "recomputed_signal"
  n_paper_legs: number;
  n_with_live_order: number;
  max_abs_gap_notional: number | null;
  unexpected_live_positions: string[];
}
export interface LiveTradingEmergencySummary {
  target_gross: number;
  n_submitted: number;
  n_rejected: number;
  n_errors: number;
  n_ledger_write_errors: number;
}
export interface LiveTradingExecutionStack {
  present: boolean;
  ran_at?: number;
  mode?: string;                 // "preview" | "submit"
  hard_stop?: boolean;
  tier?: string;                 // "AUTO" | "NEEDS_HUMAN_REVIEW"
  n_submitted?: number;
  n_pending_review?: number;
  n_rejected_by_gate?: number;
  n_dropped_by_filters?: number;
  n_submission_errors?: number;
  n_ledger_write_errors?: number;
  reconciliation?: LiveTradingReconciliation;
  risk_breach?: { reasons?: string[]; drawdown_pct?: number; gross_exposure?: number; net_exposure?: number } | null;
  aum?: number;
  peak_equity?: number;
  would_submit?: LiveTradingWouldSubmitOrder[];
  alerts?: string[];
  // 2026-07-05 机构级补全(驱动器落进快照,console 只读展示)
  venue?: string | null;         // "binance" | "bybit"
  tca?: LiveTradingTca | null;
  order_stats?: Record<string, number> | null;
  parity?: LiveTradingParitySummary | null;
  emergency?: LiveTradingEmergencySummary | null;
}
export interface LiveTradingPayload extends ConsolePayloadBase {
  data_provenance: "REAL_LIVE_TRADING_SNAPSHOT" | string;
  snapshot_present: boolean;
  verdict: string;                // LIVE_TRADING_TRACKING_OK | NO_SNAPSHOT
  forward_track: LiveTradingForwardTrack;
  execution_stack: LiveTradingExecutionStack;
  honesty_note: string;
}

// ---- GoNoGo evidence pack (Phase 5 item 3): forward evidence + execution PnL/cost attribution + incidents ----
export interface GonogoPnl {
  present: boolean;
  n_points?: number;
  first_equity?: number | null;
  last_equity?: number | null;
  total_equity_change?: number | null;
  latest_unrealized_pnl?: number | null;
  latest_gross_notional?: number | null;
  latest_net_notional?: number | null;
  execution_cost_estimate?: number | null;
  note?: string;
}
export interface GonogoVenue {
  venue: string;
  parity?: {
    n_cycles?: number; n_submit_cycles?: number; latest_max_abs_gap?: number | null;
    unexpected_position_events?: number; paper_source_recomputed_cycles?: number;
  };
  tca?: {
    n_with_slippage?: number; notional_weighted_slippage_bps?: number | null;
    assumed_cost_bps?: number; weighted_minus_assumed_bps?: number | null; worst_slippage_bps?: number | null;
  } | null;
  equity?: { n_points?: number; first?: number; last?: number; max_drawdown_observed?: number };
  pnl?: GonogoPnl;
}
export interface GonogoForwardAlpha {
  weeks_accumulated?: number; weeks_to_gate?: number; paper_sharpe?: number | null;
  paper_ic?: number | null; cum_return?: number | null; backtest_reference_sharpe?: number | null;
  pending_decisions?: number | null;
}
export interface GonogoIncidents {
  n_alert_batches?: number; risk_breaches?: number; reconciliation_hard_stops?: number;
  emergency_flattens?: number; sentinel_failures?: number;
}
export interface GonogoPayload extends ConsolePayloadBase {
  data_provenance: "REAL_GONOGO_SNAPSHOT" | string;
  snapshot_present: boolean;
  verdict: string;                 // GONOGO_EVIDENCE_READY | NO_SNAPSHOT
  generated_at?: number | null;
  readiness?: string;
  forward_evidence: {
    present: boolean; ran_at?: number | null; gate_weeks?: number;
    alphas?: Record<string, GonogoForwardAlpha>;
  };
  execution_evidence: Record<string, GonogoVenue>;
  incidents: GonogoIncidents;
  honesty_note: string;
}

export interface CryptoLivePayload extends ConsolePayloadBase {
  data_provenance: "REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT" | string;
  crypto_mode: string;
  snapshot_present: boolean;
  generated_at: number | null;
  generated_at_iso: string;
  dataset_version: string;
  meta: CryptoLiveMeta;
  summary: CryptoLiveSummary;
  factors: CryptoFactor[];
  oos: CryptoOos | null;
  synergistic_set: CryptoSynergisticSet | null;
  cost_stress: CryptoCostStress | null;
  regime: CryptoRegime | null;
  safety: {
    network_in_services: boolean;
    fetch_path: string;
    auto_approved: number;
    agent_can_reach_approved: boolean;
    live_trading: string;
  };
}

// ---- research-ops console: dynamic registry-backed runs (mirror backend run_registry contract) ----
export interface ResearchRunHeadline { label: string | null; value: number | string | null; }
export interface ResearchRunSummary {
  run_id: string; kind: string; title: string; status: string; created_ts: number;
  headline: ResearchRunHeadline; verdict: string;
  params: Record<string, unknown>; code_version: string | null;
}
export interface ResearchRunsPayload {
  source: string; read_only: boolean; api_version: string; data_provenance: string;
  runs: ResearchRunSummary[];
}
export interface ResearchRunDetail {
  run_id: string; kind: string; title: string; params: Record<string, unknown>; status: string;
  created_ts: number; finished_ts: number | null;
  metrics: Record<string, number | string | null>;
  series: Record<string, Array<{ label: string; value: number }>>;
  verdict: string; caveats: string[]; provenance: string; error: string | null;
  log: string[]; code_version: string | null;
}
export interface ResearchRunPayload {
  source: string; read_only: boolean; api_version: string; data_provenance: string;
  run: ResearchRunDetail | null;
}
export interface ResearchRunsComparePayload {
  source: string; read_only: boolean; data_provenance: string;
  runs: ResearchRunDetail[];
}
export interface PopOptSelected { alpha_id: string; weight: number; marginal_ir: number; marginal_dd: number; }
export interface PopOptExcluded { alpha_id: string; reason: string; }
export interface PopulationOptimizerPayload {
  source: string; read_only: boolean; api_version: string; data_provenance: string;
  selected: PopOptSelected[]; excluded: PopOptExcluded[];
  diversity: number; gross: number; method: string; n_candidates: number;
}
export interface SafetyCheck { name: string; stage: string; }
export interface BacktestRunBlock {
  run_id: string; kind: string; verdict: string; status: string;
  metrics: Record<string, number | string | null>;
  series: Record<string, { label: string; value: number }[]>;
}
export interface BacktestResultsPayload {
  source: string; read_only: boolean; api_version: string; data_provenance: string;
  results_mode: string;
  carry: BacktestRunBlock | null;
  combo: BacktestRunBlock | null;
  oi_research: BacktestRunBlock | null;
  survivorship: BacktestRunBlock | null;
  factory: { loop: BacktestRunBlock | null; orthogonal: BacktestRunBlock | null };
}
export interface SafetyPrimitivesPayload {
  source: string; read_only: boolean; api_version: string; data_provenance: string;
  safety_mode: string;
  kill_switch: { is_active: boolean; trip_count: number; fail_closed: boolean; human_only_reset: boolean; auto_reset: boolean };
  pre_trade_gate: { checks_registered: number; checks: SafetyCheck[]; fail_closed_on_unknown: boolean; refuses_unsafe_build: boolean };
  safety_config: { live_trading_enabled: boolean; real_broker_enabled: boolean; allow_order_submission: boolean; allow_ai_direct_trading: boolean; require_human_approval: boolean };
  live_authorization_allowed: boolean;
}
export interface LifecycleRow {
  alpha_id: string;
  state: string;
  generator: string;
  days_in_state: number | null;     // null: as_of is a single batch ts, not a real per-alpha tenure
  backtest_ic: number | null;
  sharpe: number | null;
  pvalue: number | null;
  net_sharpe: number | null;
  deflated_sharpe: number | null;
  turnover: number | null;
  verdict: string;
  live_ic: number | null;           // honestly null in L2 — no live/paper IC source exists
  recommendation: string;           // NO_LIVE_DATA in L2 (degradation needs a live feed)
  alerts: string[];
  // 727 C4: the three orthogonal lifecycle machines alongside the legacy mixed
  // `state` (design doc 2026-07-27 §3). STORED = dual-written columns; PROJECTED
  // = derived from the legacy state (pre-C2 row); SYNTHETIC = mock/fallback row;
  // UNKNOWN = legacy state unreadable. Nulls only with triple_source=UNKNOWN.
  research_state: string | null;
  deployment_state: string | null;
  health_state: string | null;
  health_reason: string | null;
  triple_source: 'STORED' | 'PROJECTED' | 'SYNTHETIC' | 'UNKNOWN';
}
export interface LifecycleMonitorPayload {
  source: string; read_only: boolean; api_version: string; data_provenance: string;
  monitoring_mode: string;
  summary: {
    total: number; state_counts: Record<string, number>; live_tracked: number; paper_tracked?: number;
    awaiting_review: number; needs_action: number; positive_sharpe: number; avg_backtest_ic: number;
  };
  rows: LifecycleRow[];
  thresholds: {
    min_ic_retention: number; max_return_gap: number; retire_return_gap: number;
    max_slippage_mult: number; max_turnover_mult: number; max_drawdown: number;
    max_capacity_breach_frac: number;
  };
  lifecycle_policy: {
    recommends_only: boolean; promotion_human_only: boolean;
    live_trading_built: boolean; live_ic_available: boolean;
  };
}
export interface DslExprRow {
  expr: string; valid: boolean; complexity: number;
  leakage_ok: boolean | null; deps: string[]; note: string; errors: string[];
}
export interface DslExplorerPayload {
  source: string; read_only: boolean; api_version: string; data_provenance: string;
  exprs: DslExprRow[]; analysis_backend: string;
}
export interface ZooDimension {
  key: string; zh: string; en: string; group: string;
  fmt: 'signed3' | 'signed2' | 'pct' | 'num1' | string;
  good: 'high' | 'low' | string;
}
export interface AlphaZooRow {
  alpha_id: string; name: string; generator: string; status: string;
  cluster_id: string; tags: string[]; verdict?: string;
  // full multi-dimensional evaluation (real persisted value or null when unassessed)
  ic?: number | null; icir?: number | null; ic_tstat?: number | null; hit_rate?: number | null;
  net_sharpe: number | null; deflated_sharpe: number | null; pbo?: number | null;
  pbo_evidence_hash?: string | null; pbo_alignment?: string | null;
  pbo_candidate_count?: number | null; catalog_trial_set_hash?: string | null;
  catalog_evidence_hash?: string | null; catalog_snapshot_hash?: string | null;
  catalog_trial_count?: number | null; catalog_effective_trial_count?: number | null;
  catalog_mean_abs_correlation?: number | null; catalog_var_sr?: number | null;
  turnover?: number | null; marginal_ir?: number | null; crowding_score?: number | null;
  capacity_adj_sharpe?: number | null; mean_breadth?: number | null;
  // 时间/活体征: real forward paper-track summary (out-of-sample paper P&L / IC), or null when no track
  paper_ic?: number | null; paper_sharpe?: number | null; perf_gap?: number | null; n_paper_periods?: number | null;
  [key: string]: number | string | string[] | null | undefined;
}
export interface AlphaZooPayload {
  source: string; read_only: boolean; api_version: string; data_provenance: string;
  rows: AlphaZooRow[];
  dimensions: ZooDimension[];
}
export interface AlphaGraphNode { id: string; label: string; x: number; y: number; cluster: string; }
export interface AlphaGraphEdge { a: string; b: string; corr: number; }
export interface AlphaGraphCluster { name: string; count: number; }
export interface AlphaGraphPayload {
  source: string; read_only: boolean; api_version: string; data_provenance: string;
  nodes: AlphaGraphNode[]; edges: AlphaGraphEdge[]; clusters: AlphaGraphCluster[];
  rho_cut: number; n_near_dup: number;
}

// ---- MEXC 零手续费 BTC 现货对接(read-only 三端点;additive contract,
// mirrors services/console/api/mexc_views.py — 改形状两侧同步) ----
export interface MexcMarketPayload extends ConsolePayloadBase {
  data_provenance: "REAL_MEXC_MARKET_SNAPSHOT" | string;
  snapshot_present: boolean;
  verdict: string;              // MEXC_MARKET_OK | NO_SNAPSHOT
  venue: string;                // "MEXC"
  symbol: string;               // "BTCUSDT"
  latest_price: number | null;
  change_24h_pct: number | null;
  volatility_30d_ann: number | null;
  coverage: { date_start: string | null; date_end: string | null; n_rows: number };
  freshness: {
    fetched_at: number | null; age_hours: number | null;
    fresh_within_hours: number; is_fresh: boolean;
  };
  db: { present: boolean; n_rows: number; date_start: string | null; date_end: string | null };
  /** Optional per-bar OHLC series (not yet emitted by the backend; pages render honestly without it). */
  bars?: { t?: number; open: number; high: number; low: number; close: number; volume?: number }[];
  honesty_note: string;
}

export interface MexcStrategyMetrics {
  total_return: number | null;
  sharpe: number | null;
  sharpe_vt: number | null;
  max_drawdown: number | null;
  max_drawdown_vt: number | null;
  turnover: number | null;
  n_periods: number | null;
  n_approved: number | null;
  n_rejected: number | null;
  reconciliation_residual: number | null;
}
export interface MexcBuyAndHold {
  present: boolean;
  total_return?: number | null;
  sharpe?: number | null;
  max_drawdown?: number | null;
  curve?: { t: number; equity: number }[];
}
export interface MexcStrategyPayload extends ConsolePayloadBase {
  data_provenance: "REAL_DRY_RUN_OFFLINE_SNAPSHOT" | string;
  snapshot_present: boolean;
  verdict: string;              // BTC_SPOT_TREND_OK | NO_RUN
  venue: string;
  strategy_id: string;          // "btc_spot_trend"
  n_runs: number;
  metrics: MexcStrategyMetrics;
  equity_curve: { t: number; equity: number; ret?: number }[];
  equity_curve_vt: { t: number; equity: number; ret?: number }[];
  buy_and_hold: MexcBuyAndHold;
  latest: DryRunRunRow | Record<string, never>;
  runs: DryRunRunRow[];
  honesty_note: string;
}

export interface MexcReadinessCheck {
  id: string;
  label: string;
  status: "READY" | "BLOCKED" | string;
  reason: string;
}
export interface MexcReadinessPayload extends ConsolePayloadBase {
  data_provenance: "REAL_MEXC_READINESS_CHECK" | string;
  snapshot_present: boolean;
  verdict: string;              // READY | BLOCKED
  venue: string;
  readiness: string;            // "L2_DRY_RUN_ONLY"
  n_ready: number;
  n_blocked: number;
  checks: MexcReadinessCheck[];
  kill_switch: { tripped: boolean; reason: string | null };
  honesty_note: string;
}

/* ---------- Go-Live 上线向导(go-live readiness) ----------------------------
   Contract for GET /api/v1/console/go-live-readiness — read-only serve of the
   offline artifact evidence/go_live_readiness.json (tools/go_live_readiness.py).
   Present: data_provenance REAL_GO_LIVE_READINESS / snapshot_status REAL.
   Absent (driver never ran, e.g. CI): SYNTHETIC_DETERMINISTIC / NO_SNAPSHOT with
   venues=[] strategies={} guidance=null — pages must render both honestly.
   Existence/summary checks only; never reads mandate contents, never grants any
   live capability. Pinned both sides by tests/research/test_console_api_contract.py. */
export interface GoLiveKeyPermissions {
  status: 'SAFE' | 'NOT_SAFE' | 'UNVERIFIED' | string;
  checked_at_ms: number | null;
}
export interface GoLiveSmoke {
  verdict: 'PASS' | 'FAIL' | 'ABSENT' | string;
  age_ms: number | null;
}
export interface GoLiveMandate {
  path: string;
  present: boolean;
  loads: boolean;
  expired: boolean;
  expires_at: number | null;
  venue_domain_ok: boolean;
  exchange_domain_ok: boolean;
  expected_venue: string;
  expected_exchange: string;
  allowed_instruments: string[];
  max_gross_notional: number | null;
  max_single_order_notional: number | null;
  max_net_notional: number | null;
}
export interface GoLiveVenue {
  venue: string;
  mainnet: boolean;
  suffix: string;
  key_permissions: GoLiveKeyPermissions;
  smoke: GoLiveSmoke;
  mandate: GoLiveMandate;
  attestations: Record<string, { present: boolean; kind: string | null }>;
  kill_switch_tripped: boolean;
  last_run: {
    status_path: string;
    present: boolean;
    at_ms: number | null;
    summary: Record<string, unknown>;
  };
}
export interface GoLiveStrategyLeg {
  instrument: string;
  weight: number;
  price: number | null;
}
export interface GoLiveStrategy {
  decided_ts: number;
  n_legs: number;
  gross: number;
  legs: GoLiveStrategyLeg[];
}
export interface GoLiveGuidance {
  venue: string;
  strategy: string;
  required_instruments: string[];
  missing_from_mandate: string[];
  stale_in_mandate: string[];
  min_deploy_for_all_legs: number;
  min_deploy_binding_leg: string;
  deploy_target_usdt: number;
  suggested_caps: {
    max_gross_notional: number;
    max_single_order_notional: number;
    max_net_notional: number;
    per_instrument: number;
  };
  warnings: string[];
}
export interface GoLiveReadinessPayload extends ConsolePayloadBase {
  data_provenance: "REAL_GO_LIVE_READINESS" | "SYNTHETIC_DETERMINISTIC" | string;
  snapshot_present: boolean;
  artifact: string | null;      // "GO_LIVE_READINESS" when present
  generated_at_ms: number | null;
  readiness: string;            // "L2_DRY_RUN_ONLY"
  venues: GoLiveVenue[];
  strategies: Record<string, GoLiveStrategy>;
  guidance: GoLiveGuidance | null;
  honesty_note: string;
}

/* ---------- A股Alpha 工作台(ashare-alpha) ------------------------------------
   Contract for GET /api/v1/console/ashare-alpha — read-only snapshot of the
   A-share micro-cap multi-factor research stack (services/console/api/ashare_views.py).
   Mock fixtures omit api_version, so this payload does not extend ConsolePayloadBase
   (the runtime contract guard only requires source/read_only markers). */
export interface AShareAlphaPayload {
  source: "BACKEND_COMPUTED_SNAPSHOT" | string;
  read_only: true;
  api_version?: string;
  provenance: string;           // REAL_ASHARE_ALPHA_SNAPSHOT | SYNTHETIC_DETERMINISTIC
  data_provenance?: string;
  snapshot_present?: boolean;
  generated_at: string;
  data_freshness: {
    stocks: number;
    bars: number;
    last_date: number;
    sources: { wind: number; ifind: number };
  };
  universe: { file_count: number; used: number };
  factors: {
    name: string;
    train_ic: number;
    train_t: number;
    test_ic: number;
    test_t: number;
    kept: boolean;
  }[];
  combo: {
    champion: string;
    weights: Record<string, number>;
    h1_test_ic: number;
    h1_test_t: number;
    h5_test_ic: number;
    h5_test_t: number;
    hit_rate: number;
  };
  basket: {
    n: number;
    aum_cny: number;
    cost_bps: number;
    test: { total_return: number; sharpe: number; max_dd: number; avg_turnover: number };
    bench_test: { total_return: number; sharpe: number; max_dd: number };
    equity_test: number[];
    bench_equity_test: number[];
  };
  variants: {
    name: string;
    train_sharpe: number;
    test_return: number;
    test_sharpe: number;
    test_mdd: number;
    turnover: number;
    is_champion: boolean;
  }[];
  dry_run: {
    run_id: string;
    total_return: number;
    sharpe: number;
    max_drawdown: number;
    n_approved: number;
    n_rejected: number;
    residual: number;
  };
  paper_track: {
    alpha_id: string;
    n_weeks: number;
    cum_return: number;
    sharpe: number;
    weekly_ic_mean: number;
    weekly_ic_t: number;
    kind: string;
  };
  cost_grid: { cost_bps: number; total_return: number; sharpe: number }[];
  turnover_caps: { cap: number; train_sharpe: number; test_return: number; test_sharpe: number }[];
  holdings_latest: { code: string; weight: number }[];
  honest_limitations: string[];
}

/* ---------- Alpha 生命周期驾驶舱(alpha-lifecycle cockpit) -------------------
   Contract for GET /api/v1/console/alpha-lifecycle — read-only verbatim serve of
   the offline artifact evidence/alpha_lifecycle_cockpit.json
   (tools/alpha_lifecycle_cockpit.py: read-only aggregation of existing local
   evidence stores — dry_run.db / paper_track.db / IC panel / falsification /
   scenario-tail / capacity / combo / live-track status / maintenance authority;
   no network, no new research math).
   Present: data_provenance REAL_ALPHA_LIFECYCLE / snapshot_status REAL.
   Absent (driver never ran, e.g. CI): SYNTHETIC_DETERMINISTIC / NO_SNAPSHOT with
   alphas=[] stage_legend={} — pages must render both honestly.
   derived_stage is a mechanical evidence-chain derivation (see stage_legend),
   never a promotion endorsement; lifecycle_state stays null (no lifecycle-SM
   state persisted on disk). Pinned both sides by
   tests/research/test_console_api_contract.py. */

/** derived_stage ladder, ascending evidence maturity:
    CANDIDATE < VALIDATED < DRY_RUN < PAPER_TRACKED < LIVE_TRACK. */
export type AlphaDerivedStage =
  "CANDIDATE" | "VALIDATED" | "DRY_RUN" | "PAPER_TRACKED" | "LIVE_TRACK" | string;

/** Every stage cell is fail-soft: {"status":"ABSENT"} when the evidence store
    has nothing for this alpha; the typed fields below appear only on PRESENT,
    so each is optional even though the real artifact populates them. */
export interface AlphaLifecycleStageBase {
  status: "PRESENT" | "ABSENT" | string;
  source?: string;
  sources?: string[];
}

export interface AlphaLifecycleDiscoveryHorizon {
  mean_ic?: number | null;
  icir?: number | null;
  t_stat?: number | null;
  hit_rate?: number | null;
  ic_std?: number | null;
  n_periods?: number | null;
  per_year?: Record<string, number | null>;
}
export interface AlphaLifecycleDiscoveryStage extends AlphaLifecycleStageBase {
  factor?: string;
  universe?: string;
  mean_ic?: number | null;
  icir?: number | null;
  t_stat?: number | null;
  hit_rate?: number | null;
  n_periods?: number | null;
  n_instruments?: number | null;
  per_year?: Record<string, number | null>;
  by_holding_period?: Record<string, AlphaLifecycleDiscoveryHorizon>;
}

export interface AlphaLifecycleFalsificationStage extends AlphaLifecycleStageBase {
  checks?: Record<string, boolean> | null;
  n_checks_pass?: number | null;
  n_checks_total?: number | null;
  verdict_pass?: boolean | null;
  research_verdict?: string | null;
  research_evidence?: Record<string, unknown> | null;
  research_warnings?: string[] | null;
  verify_summary?: Record<string, unknown> | null;
}

export interface AlphaLifecycleCrisis {
  n_periods: number;
  total_return: number;
  max_drawdown: number;
  worst_period: number;
}
export interface AlphaLifecycleVarCvar {
  var: number;
  cvar: number;
  alpha?: number;
}
export interface AlphaLifecycleDrawdownWindow {
  peak_ts?: number;
  trough_ts?: number;
  drawdown?: number;
  peak_to_trough_periods?: number;
  peak_date?: string;
  trough_date?: string;
}
export interface AlphaLifecycleStressStage extends AlphaLifecycleStageBase {
  book?: string;
  provenance?: string;            // "REAL_HISTORICAL_REPLAY"
  n_periods?: number | null;
  crises?: Record<string, AlphaLifecycleCrisis>;
  worst_drawdown_window?: AlphaLifecycleDrawdownWindow | null;
  var_cvar_95?: AlphaLifecycleVarCvar | null;
  var_cvar_99?: AlphaLifecycleVarCvar | null;
  hill_tail_index?: number | null;
  lower_tail_dep_vs_btc?: number | null;
}

export interface AlphaLifecycleCrowdingGate {
  n_pass: number;
  n_warn: number;
  n_block: number;
  worst_score: number | null;
}
export interface AlphaLifecycleBacktestStage extends AlphaLifecycleStageBase {
  strategy_id?: string;
  run_id?: string;
  timestamp?: number;
  canonical?: boolean;
  n_runs_for_strategy?: number;
  mode?: string;
  cadence?: string;
  n_periods?: number | null;
  window_start_ts?: number | null;
  window_end_ts?: number | null;
  total_return?: number | null;
  sharpe_raw?: number | null;
  sharpe_vol_target?: number | null;
  max_drawdown_raw?: number | null;
  max_drawdown_vol_target?: number | null;
  final_equity?: number | null;
  total_funding?: number | null;
  avg_net_exposure_pct?: number | null;
  reconciliation_residual?: number | null;
  n_intents?: number | null;
  n_approved?: number | null;
  n_rejected?: number | null;
  research_risk_decision?: string | null;
  cost_model?: string | null;
  cost_bps?: number | null;
  cost_provenance?: string | null;
  crowding_gate?: AlphaLifecycleCrowdingGate | null;
}

export interface AlphaLifecycleCapacityStage extends AlphaLifecycleStageBase {
  book?: string;
  provenance?: string;            // "UNCALIBRATED_PRINCIPLED" — diagnostic band, never a gate
  participation_cap_aum?: number | null;
  binding?: string | null;
  calibrated_range?: { low: number | null; high: number | null } | null;
  impact_aum_low?: number | null;
  impact_aum_high?: number | null;
  gross_sharpe?: number | null;
  n_periods?: number | null;
  data_quality?: string | null;
}

export interface AlphaLifecycleReturnStreamCombo {
  source?: string;
  kind?: string;                  // "RETURN_STREAM_COMBINE_GROSS_RELATIVE" — gross, RELATIVE only
  standalone_sharpe?: number | null;
  standalone_max_drawdown?: number | null;
  standalone_total_return?: number | null;
  inverse_vol_weight?: number | null;
  combo_equal_weight_sharpe?: number | null;
  combo_inverse_vol_sharpe?: number | null;
  combo_inverse_vol_max_drawdown?: number | null;
  corr_carry_crowd?: number | null;
  common_weeks?: number | null;
}
export interface AlphaLifecycleOptimizerCombo {
  source?: string;
  kind?: string;                  // "OPTIMIZER_COMBO"
  weight?: number | null;
  alone_econ_sharpe?: number | null;
  alone_max_drawdown?: number | null;
  combo_econ_sharpe?: number | null;
  combo_max_drawdown?: number | null;
  lift_vs_best_alone?: number | null;
  verdict?: string | null;
}
export interface AlphaLifecyclePortfolioStage extends AlphaLifecycleStageBase {
  return_stream_combo?: AlphaLifecycleReturnStreamCombo | null;
  optimizer_combo?: AlphaLifecycleOptimizerCombo | null;
}

export interface AlphaLifecyclePaperTrack {
  start_ts?: number | null;
  last_ts?: number | null;
  n_periods?: number | null;
  cum_return?: number | null;
  paper_sharpe?: number | null;
  paper_ic?: number | null;
  backtest_sharpe?: number | null;
  perf_gap?: number | null;
}
export interface AlphaLifecyclePaperForward {
  n_forward_periods?: number | null;
  paper_sharpe?: number | null;
  evidence_state?: string | null;
  paper_verdict?: string | null;
  blockers?: string[];
  pending_decisions?: number | null;
  attested_pending_decisions?: number | null;
  last_decision_ts?: number | null;
}
export interface AlphaLifecyclePaperStage extends AlphaLifecycleStageBase {
  paper_id?: string;
  n_periods?: number | null;
  track?: AlphaLifecyclePaperTrack | null;
  last_period?: {
    as_of_ts?: number | null;
    realized_return?: number | null;
    realized_ic?: number | null;
    source?: string | null;
  } | null;
  forward?: AlphaLifecyclePaperForward | null;
  required_forward_periods?: number | null;
}

export interface AlphaLifecycleLiveRun {
  venue: string;
  mode: string;                   // preview | submit (verbatim transcript)
  tier: string;
  armed: boolean | null;
  hard_stop: boolean;
  n_submitted: number;
  n_pending_review?: number;
  n_rejected_by_gate: number;
  n_dropped_by_filters?: number;
  n_submission_errors?: number;
  order_stats?: Record<string, number>;
  ran_at: number | null;
  status_file?: string;
}
export interface AlphaLifecycleLiveStage extends AlphaLifecycleStageBase {
  runs?: AlphaLifecycleLiveRun[];
}

export interface AlphaLifecycleDecayStage extends AlphaLifecycleStageBase {
  sweep_generated_at?: number | null;
  actor_kind?: string | null;
  n_actions?: number | null;
  alpha_action?: string | null;   // null = 权重侧无降级记录
  degraded?: boolean;
}
export interface AlphaLifecycleRetirementStage extends AlphaLifecycleStageBase {
  retired?: boolean;
  n_retirement_events?: number | null;
  events?: Array<Record<string, unknown>>;
}

/** The ten lifecycle stages, in canonical order (the page renders this order):
    发掘 discovery → 证伪 falsification → 压力 stress → 回测 backtest →
    容量 capacity → 组合 portfolio → 纸面 paper → 实盘 live → 衰弱 decay →
    退役 retirement. Keys are optional only for fail-soft reading; the real
    artifact always carries all ten. */
export interface AlphaLifecycleStages {
  discovery?: AlphaLifecycleDiscoveryStage;
  falsification?: AlphaLifecycleFalsificationStage;
  stress?: AlphaLifecycleStressStage;
  backtest?: AlphaLifecycleBacktestStage;
  capacity?: AlphaLifecycleCapacityStage;
  portfolio?: AlphaLifecyclePortfolioStage;
  paper?: AlphaLifecyclePaperStage;
  live?: AlphaLifecycleLiveStage;
  decay?: AlphaLifecycleDecayStage;
  retirement?: AlphaLifecycleRetirementStage;
}

export interface AlphaLifecycleEntry {
  alpha_id: string;
  display_name: string;
  derived_stage: AlphaDerivedStage;
  lifecycle_state: string | null; // always null today (lifecycle SM not persisted)
  stages: AlphaLifecycleStages;
  warnings: string[];
}

export interface AlphaLifecyclePayload extends ConsolePayloadBase {
  data_provenance: "REAL_ALPHA_LIFECYCLE" | "SYNTHETIC_DETERMINISTIC" | string;
  snapshot_present: boolean;
  artifact: string | null;      // "ALPHA_LIFECYCLE_COCKPIT" when present
  generated_at_ms: number | null;
  readiness: string;            // "L2_DRY_RUN_ONLY"
  alphas: AlphaLifecycleEntry[];
  stage_legend: Record<string, string>;
  honesty_note: string;
}
