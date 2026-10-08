import type {
  ExperimentsPayload,
  ResearchExperiment,
  ValidationDashboardPayload,
  ValidationReport,
} from '@/lib/types/research';

export class ValidationEvidenceContractError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = 'ValidationEvidenceContractError';
    this.code = code;
  }
}

function fail(code: string): never {
  throw new ValidationEvidenceContractError(code);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= 2_048;
}

function safeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.length <= 10_000 && value.every(text);
}

function validateMarkers(value: Record<string, unknown>, kind: string): void {
  if (value.source !== 'BACKEND_COMPUTED_SNAPSHOT' || value.read_only !== true
    || !text(value.api_version) || !text(value.data_provenance)) {
    fail(`${kind}_MARKERS_INVALID`);
  }
}

function parseValidationReport(value: unknown): ValidationReport {
  if (!isRecord(value) || !text(value.run_id) || !text(value.alpha_id) || !text(value.decision)
    || !safeInteger(value.gates_passed) || !safeInteger(value.gates_total)
    || value.gates_total < 1 || value.gates_passed > value.gates_total
    || !stringArray(value.reasons)) fail('VALIDATION_REPORT_INVALID');
  const gateKeys = [
    'leakage_pass', 'walk_forward_pass', 'multiple_testing_pass',
    'costs_pass', 'capacity_pass', 'decay_pass',
  ] as const;
  if (gateKeys.some(key => typeof value[key] !== 'boolean')
    || value.gates_total !== gateKeys.length
    || value.gates_passed !== gateKeys.filter(key => value[key] === true).length
    || !finite(value.oos_sharpe) || !finite(value.oos_ic) || !finite(value.net_sharpe)) {
    fail('VALIDATION_REPORT_INVALID');
  }
  return value as unknown as ValidationReport;
}

export function parseValidationEvidence(value: unknown): ValidationDashboardPayload {
  if (!isRecord(value)) fail('VALIDATION_EVIDENCE_INVALID');
  validateMarkers(value, 'VALIDATION_EVIDENCE');
  if (!Array.isArray(value.reports) || value.reports.length > 100_000 || value.reports.length < 1) {
    fail('VALIDATION_REPORTS_INVALID');
  }
  const reports = value.reports.map(parseValidationReport);
  const selected = parseValidationReport(value.selected_report);
  const matching = reports.find(report => report.run_id === selected.run_id);
  if (!matching || JSON.stringify(matching) !== JSON.stringify(selected)) {
    fail('VALIDATION_SELECTED_REPORT_MISMATCH');
  }
  if (!Array.isArray(value.lineage)) fail('VALIDATION_LINEAGE_INVALID');
  return { ...value, selected_report: selected, reports } as unknown as ValidationDashboardPayload;
}

function parseArtifact(value: unknown) {
  if (!isRecord(value) || !text(value.artifact_id) || !text(value.kind) || !text(value.status)
    || !text(value.uri) || !String(value.uri).startsWith('experiment://')
    || !text(value.content_hash)) fail('EXPERIMENT_ARTIFACT_INVALID');
  return value;
}

function parseExperiment(value: unknown): ResearchExperiment {
  if (!isRecord(value) || !text(value.experiment_id) || !text(value.alpha_id)
    || !text(value.expression) || !text(value.owner_agent) || !text(value.stage)
    || !text(value.decision) || !safeInteger(value.created_at)
    || value.execution_mode !== 'L2_DRY_RUN_ONLY') fail('EXPERIMENT_INVALID');
  if (!isRecord(value.config) || !text(value.config.universe)
    || !safeInteger(value.config.horizon_days) || value.config.horizon_days < 1
    || !finite(value.config.cost_bps) || value.config.cost_bps < 0
    || !safeInteger(value.config.seed) || !text(value.config.data_snapshot_id)
    || !text(value.config.config_hash)) fail('EXPERIMENT_CONFIG_INVALID');
  if (!isRecord(value.metrics)) fail('EXPERIMENT_METRICS_INVALID');
  const metrics = value.metrics;
  if (['ic', 'rank_ic', 'sharpe', 'turnover', 'capacity_estimate', 'composite_score']
      .some(key => !finite(metrics[key]))
    || Number(metrics.capacity_estimate) < 0) fail('EXPERIMENT_METRICS_INVALID');
  if (!text(value.reproducibility_hash)
    || !Array.isArray(value.artifacts) || value.artifacts.length < 1 || value.artifacts.length > 1_000) {
    fail('EXPERIMENT_ARTIFACTS_INVALID');
  }
  value.artifacts.forEach(parseArtifact);
  if (!isRecord(value.lineage)
    || !stringArray(value.lineage.upstream) || !stringArray(value.lineage.downstream)) {
    fail('EXPERIMENT_LINEAGE_INVALID');
  }
  return value as unknown as ResearchExperiment;
}

export function parseExperimentsEvidence(value: unknown): ExperimentsPayload {
  if (!isRecord(value)) fail('EXPERIMENTS_EVIDENCE_INVALID');
  validateMarkers(value, 'EXPERIMENTS_EVIDENCE');
  if (!text(value.registry_mode) || !Array.isArray(value.experiments)
    || value.experiments.length > 100_000) fail('EXPERIMENTS_INVALID');
  const experiments = value.experiments.map(parseExperiment);
  if (!isRecord(value.summary)) fail('EXPERIMENT_SUMMARY_INVALID');
  const summary = value.summary;
  const summaryKeys = [
    'total_experiments', 'approved', 'rejected', 'needs_more_data',
    'artifact_count', 'reproducible',
  ] as const;
  if (summaryKeys.some(key => !safeInteger(summary[key]))) fail('EXPERIMENT_SUMMARY_INVALID');
  const artifactCount = experiments.reduce((sum, row) => sum + row.artifacts.length, 0);
  const expected = {
    total_experiments: experiments.length,
    approved: experiments.filter(row => row.decision === 'APPROVED').length,
    rejected: experiments.filter(row => String(row.decision).startsWith('REJECTED')).length,
    needs_more_data: experiments.filter(row => row.decision === 'NEEDS_MORE_DATA').length,
    artifact_count: artifactCount,
    reproducible: experiments.filter(row => row.reproducibility_hash.length > 0).length,
  };
  if (Object.entries(expected).some(([key, count]) => summary[key] !== count)) {
    fail('EXPERIMENT_SUMMARY_MISMATCH');
  }
  if (!isRecord(value.stage_counts)) fail('EXPERIMENT_STAGE_COUNTS_INVALID');
  const stageCounts = value.stage_counts;
  if (Object.values(stageCounts).some(count => !safeInteger(count))
    || Object.values(stageCounts).reduce<number>((sum, count) => sum + Number(count), 0) !== experiments.length) {
    fail('EXPERIMENT_STAGE_COUNTS_INVALID');
  }
  if (!isRecord(value.lineage_policy)
    || value.lineage_policy.requires_config_hash !== true
    || value.lineage_policy.requires_data_snapshot !== true
    || value.lineage_policy.requires_metric_artifact !== true
    || value.lineage_policy.mutable_records !== false) fail('EXPERIMENT_POLICY_INVALID');
  return { ...value, experiments } as unknown as ExperimentsPayload;
}
