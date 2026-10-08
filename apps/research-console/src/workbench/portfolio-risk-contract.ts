import type {
  AlphaCandidate,
  ComputationRiskReport,
  PortfolioDashboardPayload,
  RiskDashboardPayload,
} from '@/lib/types/research';

export class PortfolioRiskContractError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = 'PortfolioRiskContractError';
    this.code = code;
  }
}

function fail(code: string): never {
  throw new PortfolioRiskContractError(code);
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= 2_048;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function safeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.length <= 10_000 && value.every(text);
}

function markers(value: Record<string, unknown>, kind: string): void {
  if (value.source !== 'BACKEND_COMPUTED_SNAPSHOT' || value.read_only !== true
    || !text(value.api_version) || !text(value.data_provenance)) {
    fail(`${kind}_MARKERS_INVALID`);
  }
}

function parseWeights(value: unknown): PortfolioDashboardPayload['weights'] {
  if (!record(value) || !text(value.weights_id) || !record(value.weights)
    || !finite(value.gross_exposure) || value.gross_exposure < 0
    || !finite(value.expected_sharpe) || !finite(value.expected_ic)
    || (value.method !== undefined && !text(value.method))
    || (value.optimizer_real !== undefined && typeof value.optimizer_real !== 'boolean')) {
    fail('PORTFOLIO_WEIGHTS_INVALID');
  }
  const entries = Object.entries(value.weights);
  if (entries.length < 1 || entries.length > 10_000
    || entries.some(([id, weight]) => !text(id) || !finite(weight))) {
    fail('PORTFOLIO_WEIGHTS_INVALID');
  }
  const gross = entries.reduce((sum, [, weight]) => sum + Math.abs(Number(weight)), 0);
  if (Math.abs(gross - value.gross_exposure) > 0.001) fail('PORTFOLIO_GROSS_MISMATCH');
  return value as unknown as PortfolioDashboardPayload['weights'];
}

function parseSimulation(value: unknown): PortfolioDashboardPayload['simulation'] {
  if (!record(value) || !text(value.run_id) || !text(value.status) || !record(value.metrics)
    || Object.values(value.metrics).some(metric => !finite(metric))
    || !Array.isArray(value.equity_curve) || value.equity_curve.length < 1
    || value.equity_curve.length > 1_000_000) fail('PORTFOLIO_SIMULATION_INVALID');
  let previousDay = -1;
  for (const point of value.equity_curve) {
    if (!record(point) || !safeInteger(point.day) || point.day <= previousDay
      || !finite(point.equity) || point.equity <= 0) fail('PORTFOLIO_SIMULATION_INVALID');
    previousDay = point.day;
  }
  return value as unknown as PortfolioDashboardPayload['simulation'];
}

function validateTargetRows(
  value: unknown,
  weights: PortfolioDashboardPayload['weights'],
  kind: 'RISK' | 'ATTRIBUTION',
): void {
  if (!Array.isArray(value) || value.length !== Object.keys(weights.weights).length) {
    fail(`PORTFOLIO_${kind}_ROWS_INVALID`);
  }
  const seen = new Set<string>();
  for (const row of value) {
    if (!record(row) || !text(row.alpha_id) || seen.has(row.alpha_id)
      || !Object.prototype.hasOwnProperty.call(weights.weights, row.alpha_id)
      || !finite(row.weight) || Math.abs(row.weight - weights.weights[row.alpha_id]) > 1e-9) {
      fail(`PORTFOLIO_${kind}_IDENTITY_MISMATCH`);
    }
    seen.add(row.alpha_id);
    if (kind === 'RISK') {
      if (!finite(row.annualized_volatility) || row.annualized_volatility < 0
        || !finite(row.contribution) || row.contribution < 0) fail('PORTFOLIO_RISK_ROWS_INVALID');
    } else if (!finite(row.alpha_total_return) || !finite(row.contribution_return)) {
      fail('PORTFOLIO_ATTRIBUTION_ROWS_INVALID');
    }
  }
}

export function parsePortfolioRiskEvidence(value: unknown): PortfolioDashboardPayload {
  if (!record(value)) fail('PORTFOLIO_EVIDENCE_INVALID');
  markers(value, 'PORTFOLIO_EVIDENCE');
  const simulation = parseSimulation(value.simulation);
  const weights = parseWeights(value.weights);
  validateTargetRows(value.risk_contributions, weights, 'RISK');
  validateTargetRows(value.return_attribution, weights, 'ATTRIBUTION');
  if (value.analytics_method !== 'BACKEND_RESEARCH_SIMULATION_ATTRIBUTION'
    || !Array.isArray(value.lineage)) fail('PORTFOLIO_ANALYTICS_INVALID');
  if (value.drawdown_curve !== undefined) {
    if (!Array.isArray(value.drawdown_curve)
      || value.drawdown_curve.some(point => !record(point) || !safeInteger(point.day)
        || !finite(point.drawdown) || !finite(point.worst))) fail('PORTFOLIO_DRAWDOWN_INVALID');
  }
  return { ...value, simulation, weights } as unknown as PortfolioDashboardPayload;
}

function parseAlpha(value: unknown): AlphaCandidate {
  if (!record(value) || !text(value.candidate_id) || !text(value.expression)
    || !text(value.lifecycle_state)) fail('RISK_ALPHA_INVALID');
  const numeric = [
    'ic', 'rank_ic', 'icir', 'sharpe', 'turnover', 'decay_half_life',
    'capacity_estimate', 'correlation_penalty', 'composite_score',
  ];
  if (numeric.some(key => !finite(value[key])) || Number(value.capacity_estimate) < 0) {
    fail('RISK_ALPHA_INVALID');
  }
  return value as unknown as AlphaCandidate;
}

function parseRiskReport(value: unknown): ComputationRiskReport {
  if (!record(value) || !text(value.run_id) || !text(value.portfolio_id) || !text(value.decision)
    || !safeInteger(value.gates_passed) || !safeInteger(value.gates_total)
    || value.gates_total < 1 || value.gates_passed > value.gates_total
    || !stringArray(value.reasons) || !record(value.gate_results)
    || Object.values(value.gate_results).some(result => typeof result !== 'boolean')
    || (value.reproducibility_hash !== undefined && !text(value.reproducibility_hash))) {
    fail('RISK_REPORT_INVALID');
  }
  return value as unknown as ComputationRiskReport;
}

export function parseResearchRiskEvidence(value: unknown): RiskDashboardPayload {
  if (!record(value)) fail('RISK_EVIDENCE_INVALID');
  markers(value, 'RISK_EVIDENCE');
  if (!text(value.selected_alpha_id)) fail('RISK_SELECTED_ALPHA_INVALID');
  const alpha = parseAlpha(value.alpha);
  if (alpha.candidate_id !== value.selected_alpha_id) fail('RISK_SELECTED_ALPHA_MISMATCH');
  const primary = parseRiskReport(value.primary_report);
  if (!Array.isArray(value.reports) || value.reports.length < 1 || value.reports.length > 100_000) {
    fail('RISK_REPORTS_INVALID');
  }
  const reports = value.reports.map(parseRiskReport);
  const match = reports.find(report => report.run_id === primary.run_id);
  if (!match || JSON.stringify(match) !== JSON.stringify(primary)) fail('RISK_PRIMARY_REPORT_MISMATCH');
  if (!Array.isArray(value.lineage)) fail('RISK_LINEAGE_INVALID');
  return { ...value, alpha, primary_report: primary, reports } as unknown as RiskDashboardPayload;
}
