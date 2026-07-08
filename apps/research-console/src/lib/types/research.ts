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

export interface PaperRun {
  run_id: string; alpha_id: string; status: string;
  metrics: Record<string,number>; equity_curve: {timestamp:number;equity:number}[];
  total_trades: number; final_equity: number;
}

export interface PortfolioWeights { weights_id: string; weights: Record<string,number>; gross_exposure: number; expected_sharpe: number; expected_ic: number; method?: string; optimizer_real?: boolean; }
export interface PortfolioSimulation { run_id: string; status: string; metrics: Record<string,number>; equity_curve: {day:number;equity:number}[]; }
export interface RiskReport { run_id: string; portfolio_id: string; decision: RiskDecision; gates_passed: number; gates_total: number; reasons: string[]; gate_results: Record<string,boolean>; }
export interface EventRecord { event_id: string; event_type: string; timestamp: number; source: string; correlation_id?: string; payload: Record<string,unknown>; }
export interface ServiceHealth { service: string; status: "healthy"|"degraded"|"down"; latency_ms: number; error_rate: number; }

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

export interface ComputationAlphaSummary {
  candidate_id: string;
  expression: string;
  turnover: number;
  capacity_estimate: number;
  lifecycle_state: string;
  ic?: number;
  rank_ic?: number;
  icir?: number;
  sharpe?: number;
  composite_score?: number;
}

export interface ComputationValidation {
  run_id: string;
  alpha_id: string;
  decision: ValidationDecision | "APPROVED" | "NEEDS_MORE_DATA";
  gates_passed: number;
  gates_total: number;
  reasons: string[];
  oos_sharpe?: number;
  oos_ic?: number;
  net_sharpe?: number;
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

export interface ComputationMetrics {
  gross_sharpe: number;
  net_sharpe: number;
  max_drawdown: number;
  total_return: number;
  cost_bps: number;
  stress_multiplier: number;
  paper_metrics?: Record<string, number>;
}

export interface ComputationLabPayload {
  source: "BACKEND_COMPUTED_SNAPSHOT" | string;
  read_only: true;
  api_version: string;
  selected_alpha_id: string;
  alpha_candidates: ComputationAlphaSummary[];
  alpha: ComputationAlphaSummary;
  validation: ComputationValidation;
  metrics: ComputationMetrics;
  equity_curve: ChartPoint[];
  drawdown_curve: DrawdownPoint[];
  risk_report: ComputationRiskReport;
  lineage: CalculationLineageItem[];
}

export interface ConsolePayloadBase {
  source: "BACKEND_COMPUTED_SNAPSHOT" | string;
  read_only: true;
  api_version: string;
  // every console_api snapshot carries an honesty marker; pages read it to label the ProvenanceRibbon
  // accurately (REAL_* markers => real data, else synthetic). Optional so mock fixtures need not set it.
  data_provenance?: string;
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

export interface AgentsPayload extends ConsolePayloadBase {
  summary: {
    active: number;
    total_runs: number;
    approved: number;
    budget_spent_cny: number;
  };
  agents: ResearchAgent[];
}

export interface EventsPayload extends ConsolePayloadBase {
  events: EventRecord[];
}

export interface ArchitectureModule {
  id: string;
  name: string;
  layer: string;
  status: string;
  maturity: number;
  owner: string;
  capability: string;
  next_gap: string;
}

export interface ArchitectureFlow {
  from: string;
  to: string;
  label: string;
  mode: string;
}

export interface ArchitectureGap {
  id: string;
  area: string;
  severity: string;
  status: string;
  next_action: string;
}

export interface ArchitecturePayload extends ConsolePayloadBase {
  data_provenance?: "REAL_REPO_INVENTORY" | string;
  current_level: "L2_DRY_RUN_ONLY";
  target_level: "L3_BROKER_TEST_ENV_READY";
  phase3a_decision: "NO_GO" | string;
  implementation_authorized: false;
  architecture_score: number;
  inventory?: {
    domains: { domain: string; n_modules: number; loc: number }[];
    total_services_modules: number; total_services_loc: number;
    n_test_files: number; n_test_functions: number;
    n_console_pages: number; n_console_endpoints: number;
  };
  modules: ArchitectureModule[];
  data_flows: ArchitectureFlow[];
  gaps: ArchitectureGap[];
  safety_boundary: {
    read_only_console: boolean;
    execution_boundary: string;
    forbidden_capabilities: string[];
  };
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
  quality_policy: {
    requires_dataset_hash: boolean;
    requires_event_timestamp: boolean;
    requires_asof_join: boolean;
    blocks_future_leakage: boolean;
    mutable_raw_inputs: boolean;
  };
}

export interface BacktestCostComponent {
  component: string;
  value: number;
  method: string;
}

export interface BacktestSegment {
  segment_id: string;
  start_day: number;
  end_day: number;
  return: number;
  sharpe: number;
  win_rate: number;
  volatility: number;
}

export interface BacktestDrawdownPeriod {
  rank: number;
  start_day: number;
  trough_day: number;
  recovery_day: number;
  depth: number;
  duration_days: number;
}

export interface BacktestDecisionDriver {
  driver: string;
  impact: "POSITIVE" | "NEGATIVE" | "NEUTRAL" | string;
  score: number;
  explanation: string;
}

export interface BacktestExplainabilityPayload extends ConsolePayloadBase {
  explainability_mode: string;
  selected_alpha_id: string;
  run: {
    run_id: string;
    alpha_id: string;
    execution_mode: "L2_DRY_RUN_ONLY";
    engine: string;
    data_snapshot_id: string;
    config_hash: string;
    reproducibility_hash: string;
  };
  alpha: AlphaCandidate;
  summary: {
    gross_return: number;
    net_return: number;
    gross_sharpe: number;
    max_drawdown: number;
    win_rate: number;
    turnover: number;
    cost_drag: number;
    segments_passed: number;
    segments_total: number;
  };
  equity_curve: ChartPoint[];
  drawdown_curve: ChartPoint[];
  segments: BacktestSegment[];
  monthly_returns: Array<{month: string; return: number; positive_days: number; negative_days: number}>;
  cost_breakdown: BacktestCostComponent[];
  drawdown_periods: BacktestDrawdownPeriod[];
  trade_distribution: Array<{bucket: string; count: number; avg_return: number}>;
  regime_attribution: Array<{regime: string; days: number; contribution: number; hit_rate: number}>;
  decision_drivers: BacktestDecisionDriver[];
  assumptions: string[];
  explainability_policy: {
    requires_equity_curve: boolean;
    requires_cost_model: boolean;
    requires_drawdown_periods: boolean;
    requires_regime_attribution: boolean;
    mutable_results: boolean;
  };
}

export interface ScenarioStressDriver {
  driver: string;
  impact: number;
}

export interface ScenarioStressCase {
  scenario_id: string;
  name: string;
  name_zh: string;
  severity: string;
  decision: "PASS" | "WATCH" | "FAIL" | string;
  shock_window: {
    start_day: number;
    end_day: number;
  };
  shocks: {
    return_multiplier: number;
    daily_cost_shift: number;
    liquidity_haircut: number;
    correlation_shift: number;
  };
  metrics: {
    stressed_return: number;
    loss_delta: number;
    stressed_sharpe: number;
    stressed_max_drawdown: number;
    liquidity_buffer_used: number;
    capacity_after_haircut: number;
  };
  curve: ChartPoint[];
  drivers: ScenarioStressDriver[];
}

export interface ScenarioStressPayload extends ConsolePayloadBase {
  stress_mode: string;
  selected_alpha_id: string;
  run: {
    run_id: string;
    execution_mode: "L2_DRY_RUN_ONLY";
    engine: string;
    scenario_set_id: string;
    data_snapshot_id: string;
    config_hash: string;
    reproducibility_hash: string;
  };
  alpha: AlphaCandidate;
  baseline: {
    total_return: number;
    sharpe: number;
    max_drawdown: number;
    turnover: number;
    capacity_estimate: number;
  };
  summary: {
    scenarios: number;
    passes: number;
    watches: number;
    fails: number;
    worst_scenario_id: string;
    worst_loss_delta: number;
    average_stressed_drawdown: number;
    average_liquidity_buffer_used: number;
  };
  scenarios: ScenarioStressCase[];
  exposure_buckets: Array<{factor: string; exposure: number; shock: number; estimated_pnl: number}>;
  risk_factor_contributions: Array<{factor: string; exposure: number; shock: number; estimated_pnl: number}>;
  scenario_policy: {
    requires_multi_scenario_replay: boolean;
    requires_liquidity_haircut: boolean;
    requires_cost_widening: boolean;
    requires_correlation_shift: boolean;
    mutable_results: boolean;
    authorizes_live_trading: boolean;
  };
  assumptions: string[];
}

export interface ModelRiskModel {
  model_id: string;
  name: string;
  model_type: string;
  linked_component: string;
  owner: string;
  risk_tier: string;
  review_status: string;
  intended_use: string;
  prohibited_use: string;
  validation_scores: {
    conceptual_soundness: number;
    implementation_verification: number;
    outcomes_analysis: number;
    ongoing_monitoring: number;
  };
  residual_risk: number;
  evidence: {
    experiment_id: string | null;
    config_hash: string;
    data_snapshot_id: string;
    stress_run_id: string | null;
    backtest_run_id: string | null;
  };
  limitations: string[];
  findings: string[];
  compensating_controls: string[];
}

export interface ModelRiskIssue {
  issue_id: string;
  severity: string;
  status: string;
  owner: string;
  description: string;
  linked_models: string[];
}

export interface ModelRiskReviewPayload extends ConsolePayloadBase {
  model_risk_mode: string;
  review_standard: string;
  summary: {
    models: number;
    high_risk_models: number;
    open_issues: number;
    open_high_issues: number;
    avg_residual_risk: number;
    independent_validation_complete: boolean;
    live_use_authorized: boolean;
  };
  models: ModelRiskModel[];
  validation_dimensions: Array<{
    dimension: string;
    dimension_zh: string;
    score: number;
    status: string;
    evidence: string;
  }>;
  issue_register: ModelRiskIssue[];
  evidence_sources: Array<{source_id: string; name: string; records: number; read_only: boolean}>;
  governance_policy: {
    requires_independent_validation: boolean;
    requires_intended_use_statement: boolean;
    requires_limitations_disclosure: boolean;
    requires_issue_tracking: boolean;
    requires_ongoing_monitoring: boolean;
    mutable_reviews: boolean;
    authorizes_live_trading: boolean;
  };
}

export interface EvidenceLedgerEntry {
  evidence_id: string;
  source_module: string;
  source_endpoint: string;
  artifact_type: string;
  record_count: number;
  status: string;
  owner: string;
  content_hash: string;
  blocks_live_authorization: boolean;
  sequence: number;
  sealed_at: number;
  previous_hash: string;
  ledger_hash: string;
  immutability_status: string;
  read_only: boolean;
  execution_mode: "L2_DRY_RUN_ONLY";
  verification: {
    hash_chain_valid: boolean;
    content_hash_present: boolean;
    source_read_only: boolean;
    no_live_authorization: boolean;
  };
}

export interface EvidenceLedgerPayload extends ConsolePayloadBase {
  ledger_mode: string;
  chain_id: string;
  root_hash: string;
  summary: {
    entries: number;
    sealed_entries: number;
    blocking_entries: number;
    hash_chain_valid: boolean;
    read_only_entries: number;
    live_authorization_allowed: boolean;
  };
  entries: EvidenceLedgerEntry[];
  blockers: Array<{
    evidence_id: string;
    source_module: string;
    reason: string;
    ledger_hash: string;
  }>;
  ledger_policy: {
    append_only: boolean;
    requires_previous_hash: boolean;
    requires_content_hash: boolean;
    requires_source_endpoint: boolean;
    requires_read_only_source: boolean;
    mutable_entries: boolean;
    authorizes_live_trading: boolean;
  };
}

export interface ReviewerWorkflowTask {
  task_id: string;
  title: string;
  evidence_ref: string;
  source_area: string;
  assigned_reviewer_id: string;
  assigned_reviewer: string;
  role: string;
  severity: string;
  gate_type: string;
  state: string;
  age_hours: number;
  sla_hours: number;
  due_in_hours: number;
  blocks_live_authorization: boolean;
  requires_independent_signoff: boolean;
  task_hash: string;
  created_at: number;
}

export interface ReviewerWorkflowPayload extends ConsolePayloadBase {
  workflow_mode: string;
  workflow_id: string;
  summary: {
    tasks: number;
    blocking_tasks: number;
    overdue_tasks: number;
    reviewers: number;
    approval_gates: number;
    blocked_gates: number;
    independent_validation_complete: boolean;
    phase3a_decision: string;
    live_authorization_allowed: boolean;
  };
  reviewers: Array<{reviewer_id: string; name: string; role: string; required: boolean; capacity: number; open_tasks: number}>;
  tasks: ReviewerWorkflowTask[];
  approval_gates: Array<{gate_id: string; name: string; status: string; required_tasks: string[]; owner_role: string}>;
  workload: Array<{
    reviewer_id: string;
    name: string;
    role: string;
    required: boolean;
    capacity: number;
    open_tasks: number;
    utilization: number;
    required_open_blocking: number;
  }>;
  evidence_links: Array<{source: string; ref: string; records: number; blocking: number}>;
  workflow_policy: {
    read_only: boolean;
    requires_independent_reviewer: boolean;
    requires_blocking_task_closure: boolean;
    requires_phase3a_go: boolean;
    requires_signed_attestation: boolean;
    mutable_tasks: boolean;
    authorizes_live_trading: boolean;
  };
}

export interface AccessControlRole {
  role_id: string;
  name: string;
  name_zh: string;
  scope: string;
  users: number;
  permissions: string[];
  denied_capabilities: string[];
}

export interface AccessControlPayload extends ConsolePayloadBase {
  access_mode: string;
  summary: {
    roles: number;
    users: number;
    permission_rows: number;
    allowed_read_permissions: number;
    denied_dangerous_permissions: number;
    export_packages: number;
    access_reviews: number;
    live_authorization_allowed: boolean;
  };
  roles: AccessControlRole[];
  permission_matrix: Array<{role_id: string; permission: string; allowed: boolean; reason: string}>;
  export_packages: Array<{
    package_id: string;
    name: string;
    name_zh: string;
    status: string;
    records: number;
    source_root_hash: string;
    requested_by_role: string;
    export_mode: string;
    content_hash: string;
    contains_credentials: boolean;
    contains_live_orders: boolean;
    download_enabled: boolean;
  }>;
  access_reviews: Array<{review_id: string; role_id: string; status: string; owner: string; age_hours: number; finding: string}>;
  forbidden_capabilities: Array<{capability: string; status: string; reason: string}>;
  access_policy: {
    read_only_console: boolean;
    least_privilege: boolean;
    segregation_of_duties_required: boolean;
    signed_export_required: boolean;
    mutable_permissions: boolean;
    downloads_enabled: boolean;
    authorizes_live_trading: boolean;
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

export interface IncidentResponseIncident {
  incident_id: string;
  source_monitor_id: string;
  category: string;
  subject_id: string;
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | string;
  state: string;
  trigger_status: string;
  trigger_metric: string;
  drift_score: number;
  owner: string;
  incident_commander: string;
  queue: string;
  opened_at: number;
  due_at: number;
  age_minutes: number;
  sla_minutes: number;
  due_in_minutes: number;
  sla_breached: boolean;
  escalation_level: string;
  route_id: string;
  runbook_id: string;
  evidence_ref: string;
  evidence_packet_hash: string;
  containment_action: string;
  next_action: string;
  external_alert_sent: boolean;
  blocks_live_authorization: boolean;
}

export interface IncidentResponsePayload extends ConsolePayloadBase {
  incident_mode: string;
  summary: {
    incidents: number;
    open_incidents: number;
    critical: number;
    high: number;
    medium: number;
    sla_breaches: number;
    escalated: number;
    avg_age_minutes: number;
    external_alerts_sent: number;
    live_authorization_allowed: boolean;
  };
  severity_counts: Record<string, number>;
  state_counts: Record<string, number>;
  routing_counts: Record<string, number>;
  incidents: IncidentResponseIncident[];
  routing_rules: Array<{
    route_id: string;
    category: string;
    queue: string;
    primary_owner: string;
    escalation_owner: string;
    sla_minutes: number;
    escalation_after_minutes: number;
    runbook_id: string;
    notification_mode: string;
  }>;
  runbooks: Array<{
    runbook_id: string;
    category: string;
    title: string;
    objective: string;
    steps: string[];
    requires_postmortem: boolean;
    mutable_runbook: boolean;
  }>;
  timeline: Array<{
    event_id: string;
    incident_id: string;
    timestamp: number;
    event_type: string;
    actor: string;
    detail: string;
    evidence_ref: string;
  }>;
  incident_policy: {
    read_only: boolean;
    requires_evidence_ref: boolean;
    requires_sla: boolean;
    requires_owner: boolean;
    requires_postmortem_for_critical: boolean;
    external_notifications_enabled: boolean;
    mutable_incidents: boolean;
    authorizes_live_trading: boolean;
  };
}

export interface PromotionGate {
  gate_id: string;
  alpha_id: string;
  stage: string;
  status: 'PASS' | 'WATCH' | 'FAIL' | string;
  owner: string;
  evidence_ref: string;
  score: number;
  reason: string;
  blocks_paper_replay: boolean;
  blocks_broker_test_env: boolean;
  blocks_live_authorization: boolean;
}

export interface PromotionRecord {
  promotion_id: string;
  alpha_id: string;
  expression: string;
  current_stage: string;
  requested_stage: string;
  decision: string;
  decision_reason: string;
  readiness_score: number;
  gates_passed: number;
  gates_watch: number;
  gates_failed: number;
  gates_total: number;
  blocking_gates: string[];
  watch_gates: string[];
  evidence_refs: string[];
  candidate_hash: string;
  paper_replay_allowed: boolean;
  broker_test_env_allowed: boolean;
  live_release_allowed: boolean;
  next_action: string;
}

export interface PromotionControlPayload extends ConsolePayloadBase {
  promotion_mode: string;
  current_level: string;
  target_stage: string;
  phase3a_decision: string;
  summary: {
    candidates: number;
    approved_for_dry_run: number;
    blocked: number;
    watch_gates: number;
    failed_gates: number;
    paper_replay_allowed: number;
    broker_test_env_allowed: number;
    live_release_allowed: number;
    avg_readiness_score: number;
  };
  decision_counts: Record<string, number>;
  stage_counts: Record<string, {pass: number; watch: number; fail: number}>;
  promotions: PromotionRecord[];
  gates: PromotionGate[];
  release_policy: {
    read_only: boolean;
    requires_validation_approval: boolean;
    requires_paper_replay: boolean;
    requires_risk_approval: boolean;
    requires_model_risk_review: boolean;
    requires_no_blocking_incidents: boolean;
    requires_evidence_root_hash: boolean;
    requires_access_control: boolean;
    mutable_promotions: boolean;
    broker_test_env_enabled: boolean;
    live_release_enabled: boolean;
    authorizes_live_trading: boolean;
  };
  safety_boundary: {
    execution_boundary: string;
    phase3a_decision: string;
    forbidden_capabilities: string[];
  };
}

export interface ReleaseCommitteePayload extends ConsolePayloadBase {
  committee_mode: string;
  meeting_id: string;
  current_level: string;
  phase3a_decision: string;
  committee_decision: string;
  summary: {
    agenda_items: number;
    no_go_items: number;
    dry_run_only_items: number;
    required_seats: number;
    quorum_required: number;
    quorum_present: number;
    quorum_met: boolean;
    votes: number;
    reject_votes: number;
    abstain_votes: number;
    signed_votes: number;
    blocking_attestations: number;
    open_blocking_tasks: number;
    broker_test_env_allowed: number;
    live_release_allowed: number;
  };
  seats: Array<{
    seat_id: string;
    role: string;
    required: boolean;
    member: string;
    domain: string;
    vote_weight: number;
  }>;
  agenda: Array<{
    agenda_id: string;
    promotion_id: string;
    alpha_id: string;
    requested_stage: string;
    recommendation: string;
    release_decision: string;
    readiness_score: number;
    gate_failures: number;
    watch_gates: number;
    blocking_attestations: number;
    open_blocking_tasks: number;
    evidence_package_hash: string;
    reason: string;
    next_action: string;
    broker_test_env_allowed: boolean;
    live_release_allowed: boolean;
  }>;
  votes: Array<{
    vote_id: string;
    agenda_id: string;
    seat_id: string;
    role: string;
    vote: string;
    rationale: string;
    signed: boolean;
    signature_hash: string | null;
    vote_weight: number;
  }>;
  required_attestations: Array<{
    attestation_id: string;
    name: string;
    owner_role: string;
    status: string;
    evidence_ref: string;
    blocks_release: boolean;
  }>;
  blockers: Array<{
    blocker_id: string;
    source: string;
    ref: string;
    owner: string;
    reason: string;
    blocks_release: boolean;
  }>;
  committee_policy: {
    read_only: boolean;
    requires_quorum: boolean;
    requires_required_seat_signatures: boolean;
    requires_signed_attestations: boolean;
    requires_promotion_go: boolean;
    requires_phase3a_go: boolean;
    mutable_votes: boolean;
    external_signing_enabled: boolean;
    broker_test_env_enabled: boolean;
    live_release_enabled: boolean;
    authorizes_live_trading: boolean;
  };
}

export interface AlphaFactoryPayload extends ConsolePayloadBase {
  summary: {
    candidates: number;
    avg_ic: number;
    avg_sharpe: number;
    active: number;
  };
  candidates: AlphaCandidate[];
  screening?: {
    buckets: Record<string, number>;
    top_candidates: Array<{
      candidate_id: string;
      decision: ValidationDecision | string;
      reason: string;
      composite_score: number;
      expression: string;
    }>;
  };
  correlation_matrix?: {
    method: string;
    ids: string[];
    matrix: number[][];
  };
  lineage: CalculationLineageItem[];
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

export interface SystemCompletenessModule {
  id: string;
  name: string;
  name_zh: string;
  route: string;
  endpoint: string;
  layer: string;
  status: string;
  backend_contract: string;
  frontend_status: string;
  completion_score: number;
  gap: string;
}

export interface SystemCompletenessGap {
  id: string;
  area: string;
  severity: string;
  status: string;
  impact: string;
  next_action: string;
}

export interface SystemCompletenessCheck {
  id: string;
  name: string;
  status: string;
  evidence: string;
}

export interface SystemCompletenessPayload extends ConsolePayloadBase {
  data_provenance?: "REAL_REPO_INVENTORY" | string;
  audit_mode: string;
  current_level: string;
  institutional_target: string;
  readiness_score: number;
  inventory?: {
    domains: { domain: string; n_modules: number; loc: number }[];
    total_services_modules: number; total_services_loc: number;
    n_test_files: number; n_test_functions: number;
    n_console_pages: number; n_console_endpoints: number;
  };
  summary: {
    frontend_routes: number;
    api_endpoints: number;
    backend_backed_modules: number;
    backend_modules?: number;
    test_functions?: number;
    fully_operational_modules: number;
    read_only_modules: number;
    open_high_gaps: number;
    live_trading_authorized: boolean;
  };
  modules: SystemCompletenessModule[];
  checks: SystemCompletenessCheck[];
  gaps: SystemCompletenessGap[];
  roadmap: Array<{
    phase: string;
    objective: string;
    exit_criteria: string;
    status: string;
  }>;
  safety_boundary: {
    read_only_console: boolean;
    execution_boundary: string;
    authorized_level: string;
    blocked_until: string[];
  };
}

export interface SystemHealthPayload extends ConsolePayloadBase {
  services: ServiceHealth[];
  gates: IntegrationGateStatus[];
  safety_checks: string[];
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

export interface Phase3AReadinessPayload extends ConsolePayloadBase {
  readiness: Phase3AReadiness;
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

export interface DryRunPipelinePayload extends ConsolePayloadBase {
  data_provenance: "REAL_DRY_RUN_OFFLINE_SNAPSHOT" | string;
  snapshot_present: boolean;
  verdict: string;            // DRY_RUN_OK | NO_RUN
  n_runs: number;
  latest: DryRunRunRow | Record<string, never>;
  runs: DryRunRunRow[];
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
  last_decision_ts: number | null;
}
export interface LiveTradingForwardTrack {
  present: boolean;
  ran_at?: number;
  staleness_days?: Record<string, { freshest_days: number | null; worst_days: number | null; n_stale: number | null; n_total: number }>;
  evidence_gate_weeks?: number;
  alphas?: LiveTradingForwardAlpha[];
  alerts?: string[];
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
