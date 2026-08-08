import {
  fetchPortfolioSnapshot,
  fetchRiskDashboardSnapshot,
} from '@/lib/api/research-console-api';
import type { PortfolioDashboardPayload, RiskDashboardPayload } from '@/lib/types/research';
import { useConsoleSnapshot, type SnapshotResult } from '@/ui/useConsoleSnapshot';
import {
  parsePortfolioRiskEvidence,
  parseResearchRiskEvidence,
} from './portfolio-risk-contract';
import { useMftReadModel, type MftReadModelState } from './useMftReadModel';

export interface PortfolioRiskAuxiliary<T> {
  status: 'LOADING' | 'AVAILABLE' | 'UNAVAILABLE';
  payload: T | null;
  reason: string | null;
}

export interface PortfolioRiskData {
  portfolio: PortfolioRiskAuxiliary<PortfolioDashboardPayload>;
  risk: PortfolioRiskAuxiliary<RiskDashboardPayload>;
  mft: MftReadModelState;
}

function unavailable<T>(result: SnapshotResult<T>): PortfolioRiskAuxiliary<T> | null {
  if (result.usingBackend && result.payload !== null) return null;
  return {
    status: result.apiState === 'loading' ? 'LOADING' : 'UNAVAILABLE',
    payload: null,
    reason: result.apiState === 'loading'
      ? 'LOADING'
      : result.apiState === 'disabled'
        ? 'REAL_API_NOT_CONFIGURED'
        : result.error ?? 'UNAVAILABLE',
  };
}

function blockedStatus(payload: { snapshot_status?: string }): string | null {
  return payload.snapshot_status === undefined || payload.snapshot_status === 'REAL'
    ? null
    : `SNAPSHOT_STATUS:${payload.snapshot_status}`;
}

function admittedProvenance(provenance: string | undefined): boolean {
  return provenance === 'REAL_CRYPTO_SERIES'
    || provenance === 'REAL'
    || provenance === 'REPOSITORY_EVIDENCE';
}

export function admitPortfolioEvidence(
  result: SnapshotResult<PortfolioDashboardPayload>,
): PortfolioRiskAuxiliary<PortfolioDashboardPayload> {
  const base = unavailable(result);
  if (base) return base;
  try {
    const payload = parsePortfolioRiskEvidence(result.payload);
    const blocked = blockedStatus(payload);
    if (blocked) return { status: 'UNAVAILABLE', payload: null, reason: blocked };
    if (!admittedProvenance(payload.data_provenance)) {
      return {
        status: 'UNAVAILABLE', payload: null,
        reason: `PROVENANCE:${payload.data_provenance ?? 'MISSING'}`,
      };
    }
    return { status: 'AVAILABLE', payload, reason: null };
  } catch (error) {
    return {
      status: 'UNAVAILABLE', payload: null,
      reason: `CONTRACT:${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

export function admitRiskEvidence(
  result: SnapshotResult<RiskDashboardPayload>,
  expectedAlphaId: string,
): PortfolioRiskAuxiliary<RiskDashboardPayload> {
  const base = unavailable(result);
  if (base) return base;
  try {
    const payload = parseResearchRiskEvidence(result.payload);
    const blocked = blockedStatus(payload);
    if (blocked) return { status: 'UNAVAILABLE', payload: null, reason: blocked };
    if (!admittedProvenance(payload.data_provenance)) {
      return {
        status: 'UNAVAILABLE', payload: null,
        reason: `PROVENANCE:${payload.data_provenance ?? 'MISSING'}`,
      };
    }
    if (payload.selected_alpha_id !== expectedAlphaId) {
      return { status: 'UNAVAILABLE', payload: null, reason: 'SELECTED_ALPHA_MISMATCH' };
    }
    return { status: 'AVAILABLE', payload, reason: null };
  } catch (error) {
    return {
      status: 'UNAVAILABLE', payload: null,
      reason: `CONTRACT:${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

export function bindRiskToPortfolio(
  portfolio: PortfolioRiskAuxiliary<PortfolioDashboardPayload>,
  risk: PortfolioRiskAuxiliary<RiskDashboardPayload>,
): PortfolioRiskAuxiliary<RiskDashboardPayload> {
  if (portfolio.status === 'LOADING') {
    return { status: 'LOADING', payload: null, reason: 'LOADING' };
  }
  if (portfolio.status !== 'AVAILABLE' || portfolio.payload === null) {
    return { status: 'UNAVAILABLE', payload: null, reason: 'NO_PORTFOLIO_TARGET' };
  }
  return risk;
}

export function usePortfolioRiskData(): PortfolioRiskData {
  const portfolioResult = useConsoleSnapshot<PortfolioDashboardPayload>(fetchPortfolioSnapshot);
  const portfolio = admitPortfolioEvidence(portfolioResult);
  const selectedAlphaId = portfolio.payload
    ? Object.keys(portfolio.payload.weights.weights).sort()[0]
    : 'alpha-1';
  const riskResult = useConsoleSnapshot<RiskDashboardPayload>(
    signal => fetchRiskDashboardSnapshot(selectedAlphaId, signal),
    [selectedAlphaId],
  );
  const risk = admitRiskEvidence(riskResult, selectedAlphaId);
  return {
    portfolio,
    risk: bindRiskToPortfolio(portfolio, risk),
    mft: useMftReadModel(),
  };
}
