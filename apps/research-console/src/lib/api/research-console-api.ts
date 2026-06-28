import type {
  AccessControlPayload,
  AgentsPayload,
  AlphaFactoryPayload,
  ArchitecturePayload,
  BacktestExplainabilityPayload,
  ComputationLabPayload,
  CryptoLivePayload,
  ResearchRunsPayload,
  ResearchRunPayload,
  ResearchRunsComparePayload,
  ResearchRunDetail,
  DataCatalogPayload,
  EvidenceLedgerPayload,
  EventsPayload,
  ExperimentsPayload,
  IncidentResponsePayload,
  ModelRiskReviewPayload,
  MonitoringDriftPayload,
  OverviewPayload,
  PaperTradingPayload,
  Phase3AReadinessPayload,
  PortfolioDashboardPayload,
  PopulationOptimizerPayload,
  PromotionControlPayload,
  ReleaseCommitteePayload,
  RiskDashboardPayload,
  ReviewerWorkflowPayload,
  ScenarioStressPayload,
  SystemCompletenessPayload,
  SystemHealthPayload,
  ValidationDashboardPayload,
} from '@/lib/types/research';

export const RESEARCH_CONSOLE_API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

export function isResearchConsoleApiEnabled() {
  return import.meta.env.VITE_MOCK_MODE === 'false' && RESEARCH_CONSOLE_API_BASE.length > 0;
}

/**
 * Runtime contract guard. `response.json() as TPayload` is a compile-time cast
 * only — a 200 with the wrong shape (an HTML error page, a proxy-injected body,
 * a drifted backend) would otherwise be accepted and rendered as `undefined`
 * while the page still claims it is "connected". Every console_api payload
 * carries these read-only markers; if they are absent the body is not a trusted
 * snapshot, so we throw and let the page fall back to mock with a visible error
 * instead of silently showing broken data.
 */
export function assertConsoleSnapshot<TPayload>(body: unknown, path: string): TPayload {
  const ok =
    typeof body === 'object' &&
    body !== null &&
    (body as Record<string, unknown>).source === 'BACKEND_COMPUTED_SNAPSHOT' &&
    (body as Record<string, unknown>).read_only === true;
  if (!ok) {
    throw new Error(`Console API ${path} returned a non-snapshot body (contract markers missing)`);
  }
  return body as TPayload;
}

export async function fetchComputationLabSnapshot({
  alphaId,
  costBps,
  stress,
  signal,
}: {
  alphaId: string;
  costBps: number;
  stress: number;
  signal?: AbortSignal;
}): Promise<ComputationLabPayload> {
  if (!RESEARCH_CONSOLE_API_BASE) {
    throw new Error('VITE_API_URL is not configured');
  }

  const url = new URL('/api/v1/console/computation-lab', RESEARCH_CONSOLE_API_BASE);
  url.searchParams.set('alpha_id', alphaId);
  url.searchParams.set('cost_bps', String(costBps));
  url.searchParams.set('stress', String(stress));

  const response = await fetch(url, { method: 'GET', signal });
  if (!response.ok) {
    throw new Error(`Computation API returned ${response.status}`);
  }

  return assertConsoleSnapshot<ComputationLabPayload>(await response.json(), '/api/v1/console/computation-lab');
}

async function fetchConsoleJson<TPayload>(path: string, signal?: AbortSignal): Promise<TPayload> {
  if (!RESEARCH_CONSOLE_API_BASE) {
    throw new Error('VITE_API_URL is not configured');
  }

  const response = await fetch(new URL(path, RESEARCH_CONSOLE_API_BASE), { method: 'GET', signal });
  if (!response.ok) {
    throw new Error(`Console API returned ${response.status}`);
  }

  return assertConsoleSnapshot<TPayload>(await response.json(), path);
}

export function fetchOverviewSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<OverviewPayload>('/api/v1/console/overview', signal);
}

export function fetchAgentsSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<AgentsPayload>('/api/v1/console/agents', signal);
}

export function fetchEventsSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<EventsPayload>('/api/v1/console/events', signal);
}

export function fetchPhase3AReadinessSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<Phase3AReadinessPayload>('/api/v1/console/phase-3a-readiness', signal);
}

export function fetchArchitectureSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<ArchitecturePayload>('/api/v1/console/architecture', signal);
}

export function fetchExperimentsSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<ExperimentsPayload>('/api/v1/console/experiments', signal);
}

export function fetchDataCatalogSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<DataCatalogPayload>('/api/v1/console/data-catalog', signal);
}

export function fetchBacktestExplainabilitySnapshot(alphaId: string, signal?: AbortSignal) {
  const path = `/api/v1/console/backtest-explainability?alpha_id=${encodeURIComponent(alphaId)}`;
  return fetchConsoleJson<BacktestExplainabilityPayload>(path, signal);
}

export function fetchScenarioStressSnapshot(alphaId: string, signal?: AbortSignal) {
  const path = `/api/v1/console/scenario-stress?alpha_id=${encodeURIComponent(alphaId)}`;
  return fetchConsoleJson<ScenarioStressPayload>(path, signal);
}

export function fetchModelRiskReviewSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<ModelRiskReviewPayload>('/api/v1/console/model-risk-review', signal);
}

export function fetchEvidenceLedgerSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<EvidenceLedgerPayload>('/api/v1/console/evidence-ledger', signal);
}

export function fetchReviewerWorkflowSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<ReviewerWorkflowPayload>('/api/v1/console/reviewer-workflow', signal);
}

export function fetchAccessControlSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<AccessControlPayload>('/api/v1/console/access-control', signal);
}

export function fetchMonitoringDriftSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<MonitoringDriftPayload>('/api/v1/console/monitoring-drift', signal);
}

export function fetchIncidentResponseSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<IncidentResponsePayload>('/api/v1/console/incident-response', signal);
}

export function fetchPromotionControlSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<PromotionControlPayload>('/api/v1/console/promotion-control', signal);
}

export function fetchReleaseCommitteeSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<ReleaseCommitteePayload>('/api/v1/console/release-committee', signal);
}

export function fetchAlphaFactorySnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<AlphaFactoryPayload>('/api/v1/console/alpha-factory', signal);
}

export function fetchRiskDashboardSnapshot(alphaId: string, signal?: AbortSignal) {
  const path = `/api/v1/console/risk-dashboard?alpha_id=${encodeURIComponent(alphaId)}`;
  return fetchConsoleJson<RiskDashboardPayload>(path, signal);
}

export function fetchValidationSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<ValidationDashboardPayload>('/api/v1/console/validation', signal);
}

export function fetchPaperTradingSnapshot(alphaId: string, signal?: AbortSignal) {
  const path = `/api/v1/console/paper-trading?alpha_id=${encodeURIComponent(alphaId)}`;
  return fetchConsoleJson<PaperTradingPayload>(path, signal);
}

export function fetchPortfolioSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<PortfolioDashboardPayload>('/api/v1/console/portfolio', signal);
}

export function fetchPopulationOptimizerSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<PopulationOptimizerPayload>('/api/v1/console/population-optimizer', signal);
}

export function fetchSystemHealthSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<SystemHealthPayload>('/api/v1/console/system-health', signal);
}

export function fetchSystemCompletenessSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<SystemCompletenessPayload>('/api/v1/console/system-completeness', signal);
}

export function fetchCryptoLiveSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<CryptoLivePayload>('/api/v1/console/crypto-live', signal);
}

export function fetchResearchRuns(signal?: AbortSignal) {
  return fetchConsoleJson<ResearchRunsPayload>('/api/v1/console/research-runs', signal);
}

export function fetchResearchRun(runId: string, signal?: AbortSignal) {
  return fetchConsoleJson<ResearchRunPayload>(
    '/api/v1/console/research-run?run_id=' + encodeURIComponent(runId), signal);
}

/**
 * Fetch several runs at once for side-by-side comparison. Each run is fetched through the same contract-
 * guarded read-only endpoint (so every body is snapshot-validated); a missing run (null) is dropped.
 * Pure aggregation over the read-only API — adds no new mutating surface.
 */
export async function fetchResearchRunsCompare(
  runIds: string[], signal?: AbortSignal): Promise<ResearchRunsComparePayload> {
  const payloads = await Promise.all(runIds.map(id => fetchResearchRun(id, signal)));
  const runs = payloads.map(p => p.run).filter((r): r is ResearchRunDetail => r != null);
  return { source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, data_provenance: 'RESEARCH_RUN_REGISTRY', runs };
}
