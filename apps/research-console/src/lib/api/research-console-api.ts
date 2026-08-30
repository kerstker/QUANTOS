import type {
  AlphaLifecyclePayload,
  AgentsPayload,
  AShareAlphaPayload,
  AShareInstitutionalSearchPayload,
  CryptoLivePayload,
  FxResearchPayload,
  CarryReadinessPayload,
  DryRunPipelinePayload,
  IcPanelPayload,
  ResearchFindingsPayload,
  Lance1mValidationPayload,
  LiveTradingPayload,
  GonogoPayload,
  ResearchRunsPayload,
  ResearchRunPayload,
  ResearchRunsComparePayload,
  ResearchRunDetail,
  DataCatalogPayload,
  EventsPayload,
  ExperimentsPayload,
  MonitoringDriftPayload,
  OverviewPayload,
  PaperTradingPayload,
  PortfolioDashboardPayload,
  PopulationOptimizerPayload,
  AlphaGraphPayload,
  AlphaZooPayload,
  DslExplorerPayload,
  DslExprRow,
  SafetyPrimitivesPayload,
  MexcMarketPayload,
  MexcStrategyPayload,
  MexcReadinessPayload,
  GoLiveReadinessPayload,
  LifecycleMonitorPayload,
  BacktestResultsPayload,
  RiskDashboardPayload,
  RuntimeContractPayload,
  SystemCompletenessPayload,
  SystemHealthPayload,
  ValidationDashboardPayload,
} from '@/lib/types/research';
import {
  parseMftDelta,
  parseMftSnapshot,
  type MftDeltaResponse,
  type MftSnapshot,
} from '@/workbench/contracts';
import {
  parseHourlyAlphaSnapshot,
  type HourlyAlphaSnapshot,
} from '@/workbench/hourly-alpha-contract';

const SAME_ORIGIN_MARKER = '__QUANTOS_SAME_ORIGIN__';
const configuredConsoleBase = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

export const RESEARCH_CONSOLE_API_BASE = configuredConsoleBase === SAME_ORIGIN_MARKER
  ? (typeof window === 'undefined' ? '' : window.location.origin)
  : configuredConsoleBase;

/**
 * The console has three deliberately explicit runtime modes.  A missing API
 * URL is not the same thing as an intentional mock session: it is an
 * unavailable real console and must be visible to the operator.
 */
export type ConsoleRuntimeMode = 'MOCK' | 'REAL_API' | 'REAL_API_UNAVAILABLE';

export function getConsoleRuntimeMode(): ConsoleRuntimeMode {
  if (import.meta.env.VITE_MOCK_MODE === 'true') return 'MOCK';
  return RESEARCH_CONSOLE_API_BASE ? 'REAL_API' : 'REAL_API_UNAVAILABLE';
}

export function isResearchConsoleApiEnabled() {
  return import.meta.env.VITE_MOCK_MODE === 'false' && RESEARCH_CONSOLE_API_BASE.length > 0;
}

/**
 * Read-only snapshots are polled by several shared surfaces.  A short-lived
 * client cache prevents a page from issuing the same expensive snapshot more
 * than once during a render/poll burst, while keeping the authoritative
 * backend as the source of truth.  Errors are never cached.
 */
const SNAPSHOT_CACHE_TTL_MS = 3_000;
const snapshotCache = new Map<string, { expiresAt: number; value: unknown }>();

export function clearConsoleSnapshotCache() {
  snapshotCache.clear();
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

async function fetchConsoleJson<TPayload>(path: string, signal?: AbortSignal): Promise<TPayload> {
  if (!RESEARCH_CONSOLE_API_BASE) {
    throw new Error('VITE_API_URL is not configured');
  }

  if (signal?.aborted) {
    throw new Error('Console snapshot request aborted');
  }
  const cached = snapshotCache.get(path);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.value as TPayload;
  }

  const response = await fetch(new URL(path, RESEARCH_CONSOLE_API_BASE), { method: 'GET', signal });
  if (!response.ok) {
    throw new Error(`Console API returned ${response.status}`);
  }

  const value = assertConsoleSnapshot<TPayload>(await response.json(), path);
  snapshotCache.set(path, { value, expiresAt: Date.now() + SNAPSHOT_CACHE_TTL_MS });
  return value;
}

export function assertMftExecutionSnapshot(body: unknown): MftSnapshot {
  return parseMftSnapshot(body);
}

export function assertMftExecutionDelta(body: unknown): MftDeltaResponse {
  return parseMftDelta(body);
}

async function fetchGuardedJson<T>(
  path: string,
  guard: (body: unknown) => T,
  signal?: AbortSignal,
): Promise<T> {
  if (!RESEARCH_CONSOLE_API_BASE) throw new Error('VITE_API_URL is not configured');
  if (signal?.aborted) throw new Error('Console snapshot request aborted');
  const response = await fetch(new URL(path, RESEARCH_CONSOLE_API_BASE), { method: 'GET', signal });
  if (!response.ok) throw new Error(`Console API returned ${response.status}`);
  return guard(await response.json());
}

export function fetchMftExecutionSnapshot(signal?: AbortSignal): Promise<MftSnapshot> {
  return fetchGuardedJson('/api/v1/console/mft-execution', assertMftExecutionSnapshot, signal);
}

export function fetchMftExecutionDelta(
  cursor: string,
  signal?: AbortSignal,
  limit = 500,
): Promise<MftDeltaResponse> {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 1_000) {
    return Promise.reject(new Error('MFT delta limit must be between 1 and 1000'));
  }
  const path = `/api/v1/console/mft-execution/delta?cursor=${encodeURIComponent(cursor)}&limit=${limit}`;
  return fetchGuardedJson(path, assertMftExecutionDelta, signal);
}

export function assertHourlyAlphaSnapshot(body: unknown): HourlyAlphaSnapshot {
  return parseHourlyAlphaSnapshot(body);
}

export function fetchHourlyAlphaSnapshot(signal?: AbortSignal): Promise<HourlyAlphaSnapshot> {
  return fetchGuardedJson(
    '/api/v1/console/hourly-alpha', assertHourlyAlphaSnapshot, signal,
  );
}

export function fetchOverviewSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<OverviewPayload>('/api/v1/console/overview', signal);
}

export function fetchEventsSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<EventsPayload>('/api/v1/console/events', signal);
}

export function fetchAgentsSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<AgentsPayload>('/api/v1/console/agents', signal);
}

export function fetchExperimentsSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<ExperimentsPayload>('/api/v1/console/experiments', signal);
}

export function fetchDataCatalogSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<DataCatalogPayload>('/api/v1/console/data-catalog', signal);
}

export function fetchMonitoringDriftSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<MonitoringDriftPayload>('/api/v1/console/monitoring-drift', signal);
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

export function fetchAlphaGraphSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<AlphaGraphPayload>('/api/v1/console/alpha-graph', signal);
}

export function fetchAlphaZooSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<AlphaZooPayload>('/api/v1/console/alpha-zoo', signal);
}

export function fetchDslExplorerSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<DslExplorerPayload>('/api/v1/console/dsl-explorer', signal);
}

export async function checkDslExpression(expr: string, signal?: AbortSignal): Promise<DslExprRow> {
  if (!RESEARCH_CONSOLE_API_BASE) {
    throw new Error('VITE_API_URL is not configured');
  }
  const url = new URL('/api/v1/console/dsl-explorer/check', RESEARCH_CONSOLE_API_BASE);
  url.searchParams.set('expr', expr);
  const response = await fetch(url, { method: 'GET', signal });
  if (!response.ok) {
    throw new Error(`Console API returned ${response.status}`);
  }
  const body = assertConsoleSnapshot<{ row: DslExprRow }>(await response.json(), '/api/v1/console/dsl-explorer/check');
  return body.row;
}

export function fetchSafetyPrimitivesSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<SafetyPrimitivesPayload>('/api/v1/console/safety-primitives', signal);
}

export function fetchLifecycleMonitorSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<LifecycleMonitorPayload>('/api/v1/console/lifecycle-monitor', signal);
}

export function fetchBacktestResultsSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<BacktestResultsPayload>('/api/v1/console/backtest-results', signal);
}

export function fetchSystemHealthSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<SystemHealthPayload>('/api/v1/console/system-health', signal);
}

export function fetchRuntimeContract(signal?: AbortSignal) {
  return fetchConsoleJson<RuntimeContractPayload>('/api/v1/console/runtime-contract', signal);
}

export function fetchSystemCompletenessSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<SystemCompletenessPayload>('/api/v1/console/system-completeness', signal);
}

export function fetchCryptoLiveSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<CryptoLivePayload>('/api/v1/console/crypto-live', signal);
}

export function fetchFxResearchSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<FxResearchPayload>('/api/v1/console/fx-research', signal);
}

export function fetchCarryReadinessSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<CarryReadinessPayload>('/api/v1/console/carry-readiness', signal);
}

export function fetchDryRunSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<DryRunPipelinePayload>('/api/v1/console/dry-run', signal);
}

export function fetchIcPanelSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<IcPanelPayload>('/api/v1/console/ic-panel', signal);
}

export function fetchAShareAlphaSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<AShareAlphaPayload>('/api/v1/console/ashare-alpha', signal);
}

export function fetchAShareInstitutionalSearch(signal?: AbortSignal) {
  return fetchConsoleJson<AShareInstitutionalSearchPayload>(
    '/api/v1/console/ashare-alpha-search', signal,
  );
}

export function fetchResearchFindings(signal?: AbortSignal) {
  return fetchConsoleJson<ResearchFindingsPayload>('/api/v1/console/research-findings', signal);
}

export function fetchLance1mValidation(signal?: AbortSignal) {
  return fetchConsoleJson<Lance1mValidationPayload>('/api/v1/console/lance-1m-validation', signal);
}

export function fetchLiveTradingSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<LiveTradingPayload>('/api/v1/console/live-trading', signal);
}

export function fetchGonogo(signal?: AbortSignal) {
  return fetchConsoleJson<GonogoPayload>('/api/v1/console/gonogo', signal);
}

export function fetchMexcMarketSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<MexcMarketPayload>('/api/v1/console/mexc-market', signal);
}

export function fetchMexcStrategySnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<MexcStrategyPayload>('/api/v1/console/mexc-strategy', signal);
}

export function fetchMexcReadinessSnapshot(signal?: AbortSignal) {
  return fetchConsoleJson<MexcReadinessPayload>('/api/v1/console/mexc-readiness', signal);
}

export function fetchGoLiveReadiness(signal?: AbortSignal) {
  return fetchConsoleJson<GoLiveReadinessPayload>('/api/v1/console/go-live-readiness', signal);
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
  return {
    source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
    data_provenance: 'RESEARCH_RUN_REGISTRY', runs,
    authoritative: false, evidence_status: 'DIAGNOSTIC_ONLY',
    invalidation_code: 'INVALIDATED_PERP_LEDGER_V1', snapshot_status: 'DATA_BLOCKED',
  };
}

export function fetchAlphaLifecycle(signal?: AbortSignal) {
  return fetchConsoleJson<AlphaLifecyclePayload>('/api/v1/console/alpha-lifecycle', signal);
}
