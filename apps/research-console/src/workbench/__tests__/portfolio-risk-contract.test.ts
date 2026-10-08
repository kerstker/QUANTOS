import { describe, expect, it } from 'vitest';

import {
  parsePortfolioRiskEvidence,
  parseResearchRiskEvidence,
} from '../portfolio-risk-contract';
import {
  admitPortfolioEvidence,
  admitRiskEvidence,
  bindRiskToPortfolio,
} from '../usePortfolioRiskData';

function portfolio(overrides: Record<string, unknown> = {}) {
  return {
    source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
    data_provenance: 'REAL_CRYPTO_SERIES', snapshot_status: 'REAL',
    simulation: {
      run_id: 'sim-1', status: 'COMPLETED', metrics: { sharpe: 1.2, total_return: 3 },
      equity_curve: [{ day: 0, equity: 100 }, { day: 1, equity: 103 }],
    },
    weights: {
      weights_id: 'weights-1', weights: { 'alpha-1': 0.4, 'alpha-2': 0.6 },
      gross_exposure: 1, expected_sharpe: 1.2, expected_ic: 0.03,
      method: 'RISK_PARITY', optimizer_real: true,
    },
    risk_contributions: [
      { alpha_id: 'alpha-1', weight: 0.4, annualized_volatility: 0.2, contribution: 0.5 },
      { alpha_id: 'alpha-2', weight: 0.6, annualized_volatility: 0.1, contribution: 0.5 },
    ],
    return_attribution: [
      { alpha_id: 'alpha-1', weight: 0.4, alpha_total_return: 5, contribution_return: 2 },
      { alpha_id: 'alpha-2', weight: 0.6, alpha_total_return: 1.6667, contribution_return: 1 },
    ],
    analytics_method: 'BACKEND_RESEARCH_SIMULATION_ATTRIBUTION', lineage: [],
    ...overrides,
  };
}

function risk(overrides: Record<string, unknown> = {}) {
  const report = {
    run_id: 'risk-1', portfolio_id: 'portfolio-alpha-1', decision: 'RISK_REJECTED',
    gates_passed: 1, gates_total: 9, reasons: ['FULL_INPUTS_REQUIRED'],
    gate_results: { market: true, concentration: false }, reproducibility_hash: 'hash-1',
  };
  return {
    source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
    data_provenance: 'REAL_CRYPTO_SERIES', snapshot_status: 'REAL',
    selected_alpha_id: 'alpha-1',
    alpha: {
      candidate_id: 'alpha-1', expression: 'carry', ic: 0.03, rank_ic: 0.03,
      icir: 0.8, sharpe: 1.2, turnover: 0.2, decay_half_life: 12,
      capacity_estimate: 12_000_000, correlation_penalty: 0.1,
      composite_score: 0.7, lifecycle_state: 'VALIDATED',
    },
    primary_report: report, reports: [report], lineage: [], ...overrides,
  };
}

function result(payload: unknown) {
  return {
    payload, apiState: 'connected', error: null, usingBackend: true,
    snapshotState: 'REAL', actionsAllowed: true,
  };
}

describe('portfolio and risk read contracts', () => {
  it('accepts complete real portfolio and diagnostic research-risk evidence', () => {
    expect(parsePortfolioRiskEvidence(portfolio()).weights.weights['alpha-1']).toBe(0.4);
    expect(parseResearchRiskEvidence(risk()).selected_alpha_id).toBe('alpha-1');
  });

  it('rejects non-finite values, target gross drift, and attribution identity drift', () => {
    const nonFinite = portfolio();
    (nonFinite.weights as { expected_sharpe: number }).expected_sharpe = Infinity;
    expect(() => parsePortfolioRiskEvidence(nonFinite)).toThrow(/WEIGHTS/);

    expect(() => parsePortfolioRiskEvidence(portfolio({
      weights: { ...(portfolio().weights as object), gross_exposure: 4 },
    }))).toThrow(/GROSS/);

    const mismatch = portfolio();
    (mismatch.risk_contributions as Array<Record<string, unknown>>)[0].alpha_id = 'other';
    expect(() => parsePortfolioRiskEvidence(mismatch)).toThrow(/IDENTITY/);
  });

  it('rejects selected-alpha drift, malformed gates, and a primary report not present in reports', () => {
    expect(() => parseResearchRiskEvidence(risk({ selected_alpha_id: 'alpha-2' }))).toThrow(/ALPHA/);

    const malformed = risk();
    (malformed.primary_report as { gates_passed: number }).gates_passed = 10;
    expect(() => parseResearchRiskEvidence(malformed)).toThrow(/REPORT/);

    const absent = risk();
    (absent as Record<string, unknown>).reports = [{
      ...(absent.primary_report as object), run_id: 'other',
    }];
    expect(() => parseResearchRiskEvidence(absent)).toThrow(/PRIMARY/);
  });

  it('admits only real, current bodies and exact risk selection', () => {
    expect(admitPortfolioEvidence(result(portfolio()) as never).status).toBe('AVAILABLE');
    expect(admitPortfolioEvidence(result({
      ...portfolio(), data_provenance: 'SYNTHETIC_DETERMINISTIC',
    }) as never)).toMatchObject({
      status: 'UNAVAILABLE', reason: 'PROVENANCE:SYNTHETIC_DETERMINISTIC',
    });
    expect(admitPortfolioEvidence(result({
      ...portfolio(), snapshot_status: 'STALE',
    }) as never)).toMatchObject({ status: 'UNAVAILABLE', reason: 'SNAPSHOT_STATUS:STALE' });

    expect(admitRiskEvidence(result(risk()) as never, 'alpha-1').status).toBe('AVAILABLE');
    expect(admitRiskEvidence(result(risk()) as never, 'alpha-2')).toMatchObject({
      status: 'UNAVAILABLE', reason: 'SELECTED_ALPHA_MISMATCH',
    });
  });

  it('does not expose a default-alpha risk report without an admitted portfolio target', () => {
    const admittedRisk = admitRiskEvidence(result(risk()) as never, 'alpha-1');
    expect(bindRiskToPortfolio({
      status: 'UNAVAILABLE', payload: null, reason: 'PROVENANCE:SYNTHETIC_DETERMINISTIC',
    }, admittedRisk)).toMatchObject({
      status: 'UNAVAILABLE', payload: null, reason: 'NO_PORTFOLIO_TARGET',
    });
  });
});
