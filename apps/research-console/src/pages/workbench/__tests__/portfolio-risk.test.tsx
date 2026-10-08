import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PortfolioRisk from '../PortfolioRisk';
import { makeMftSnapshot } from '@/workbench/__tests__/mft-fixtures';

const model = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));

vi.mock('@/workbench/usePortfolioRiskData', () => ({
  usePortfolioRiskData: () => model.value,
}));

function portfolioPayload() {
  return {
    source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
    data_provenance: 'REAL_CRYPTO_SERIES', snapshot_status: 'REAL',
    simulation: {
      run_id: 'sim-1', status: 'COMPLETED',
      metrics: { sharpe: 1.4, total_return: 8, max_drawdown: 3, volatility: 12 },
      equity_curve: [{ day: 0, equity: 100 }, { day: 1, equity: 108 }],
    },
    weights: {
      weights_id: 'weights-1', weights: { 'alpha-1': 0.25, 'alpha-2': 0.75 },
      gross_exposure: 1, expected_sharpe: 1.4, expected_ic: 0.03,
      method: 'RISK_PARITY', optimizer_real: true,
    },
    risk_contributions: [
      { alpha_id: 'alpha-1', weight: 0.25, annualized_volatility: 0.18, contribution: 0.4 },
      { alpha_id: 'alpha-2', weight: 0.75, annualized_volatility: 0.12, contribution: 0.6 },
    ],
    return_attribution: [
      { alpha_id: 'alpha-1', weight: 0.25, alpha_total_return: 16, contribution_return: 4 },
      { alpha_id: 'alpha-2', weight: 0.75, alpha_total_return: 5.3333, contribution_return: 4 },
    ],
    analytics_method: 'BACKEND_RESEARCH_SIMULATION_ATTRIBUTION', lineage: [],
  };
}

function riskPayload() {
  const report = {
    run_id: 'risk-1', portfolio_id: 'portfolio-alpha-1', decision: 'RISK_REJECTED',
    gates_passed: 4, gates_total: 9, reasons: ['FULL_INPUTS_REQUIRED'],
    gate_results: { market: true, liquidity: true, concentration: false, stress: true },
    reproducibility_hash: 'risk-hash',
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
    primary_report: report, reports: [report], lineage: [],
  };
}

function liveMft() {
  return {
    phase: 'LIVE', reason: null, pages: 1, reloaded: false, transport: 'SSE',
    snapshot: makeMftSnapshot({
      market_gap: { status: 'CONTIGUOUS', missing_intervals: [] },
      positions: {
        status: 'RECONCILED', position_count: 2,
        items: [
          { instrument: 'BTCUSDT', quantity: 0.01 },
          { instrument: 'ETHUSDT', quantity: -0.2 },
        ],
      },
      risk: {
        status: 'ALLOWED_TESTNET', kill_switch_latched: false,
        new_risk_allowed: true, gross_exposure: 5_000, drawdown: 0.002,
      },
      reconciliation: { status: 'IN_SYNC', requires_manual_review: false },
      mandate_expiry: { status: 'VALID_TESTNET_ONLY', expires_at_ms: 1_800_000_000_000 },
    }),
  };
}

function availableModel() {
  return {
    portfolio: { status: 'AVAILABLE', reason: null, payload: portfolioPayload() },
    risk: { status: 'AVAILABLE', reason: null, payload: riskPayload() },
    mft: liveMft(),
  };
}

describe('PortfolioRisk', () => {
  beforeEach(() => { model.value = availableModel(); });

  it('separates research sleeves from MFT instrument positions and shows risk evidence read-only', () => {
    render(<PortfolioRisk />);
    expect(screen.getByRole('heading', { name: '组合与风险' })).toBeTruthy();

    const target = screen.getByTestId('research-target-positions');
    expect(within(target).getByText('alpha-1')).toBeTruthy();
    expect(within(target).getByText('25.00%')).toBeTruthy();
    expect(within(target).queryByText('BTCUSDT')).toBeNull();

    const actual = screen.getByTestId('mft-actual-positions');
    expect(within(actual).getByText('BTCUSDT')).toBeTruthy();
    expect(within(actual).getByText('0.010000')).toBeTruthy();
    expect(within(actual).queryByText('25.00%')).toBeNull();

    expect(screen.getAllByText(/TARGET_ALPHA_VS_ACTUAL_INSTRUMENT/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('60.00%').length).toBeGreaterThan(0);
    expect(screen.getAllByText('4.00 pp').length).toBeGreaterThan(0);
    expect(screen.getByText(/12\.0 periods/)).toBeTruthy();
    expect(screen.getByText('RISK_REJECTED')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /reset|edit|submit|approve|重置|编辑|提交|批准/i })).toBeNull();
  });

  it('keeps emergency state permanently visible', () => {
    render(<PortfolioRisk />);
    const emergency = screen.getByTestId('emergency-state');
    expect(within(emergency).getAllByText('CLEAR').length).toBeGreaterThan(0);
    expect(within(emergency).getByText('VALID_TESTNET_ONLY')).toBeTruthy();
    expect(within(emergency).getByText('IN_SYNC')).toBeTruthy();
    expect(within(emergency).getByText('CONTIGUOUS')).toBeTruthy();
  });

  it('never reuses a research target when MFT actual is unavailable', () => {
    const data = availableModel();
    data.mft = {
      phase: 'UNAVAILABLE', snapshot: null, reason: 'STORE_MISSING',
      pages: 0, reloaded: false, transport: 'NONE',
    } as never;
    model.value = data;
    render(<PortfolioRisk />);

    expect(within(screen.getByTestId('research-target-positions')).getByText('25.00%')).toBeTruthy();
    const actual = screen.getByTestId('mft-actual-positions');
    expect(within(actual).getByText(/UNAVAILABLE.*STORE_MISSING/)).toBeTruthy();
    expect(within(actual).queryByText('25.00%')).toBeNull();
    const emergency = screen.getByTestId('emergency-state');
    expect(within(emergency).getAllByText('UNAVAILABLE')).toHaveLength(4);
  });

  it('hides retained actual positions when the MFT authority is stale', () => {
    const data = availableModel();
    data.mft = { ...liveMft(), phase: 'STALE', reason: 'FRESHNESS_EXCEEDED' } as never;
    model.value = data;
    render(<PortfolioRisk />);
    expect(within(screen.getByTestId('mft-actual-positions')).getByText(/UNAVAILABLE.*FRESHNESS_EXCEEDED/)).toBeTruthy();
    expect(screen.queryByText('0.010000')).toBeNull();
  });

  it('raises visible emergency blocks for a latch, divergence, mandate failure, and market gap', () => {
    const data = availableModel();
    data.mft = {
      ...liveMft(),
      snapshot: makeMftSnapshot({
        market_gap: { status: 'GAP_DETECTED', missing_intervals: ['2026-08-08T01:00Z'] },
        positions: { status: 'RECONCILED', items: [] },
        risk: { status: 'BLOCKED', kill_switch_latched: true, new_risk_allowed: false },
        reconciliation: { status: 'DIVERGED', requires_manual_review: true },
        mandate_expiry: { status: 'EXPIRED', expires_at_ms: 1_700_000_000_000 },
      }),
    } as never;
    model.value = data;
    render(<PortfolioRisk />);
    const emergency = screen.getByTestId('emergency-state');
    expect(within(emergency).getByText('LATCHED')).toBeTruthy();
    expect(within(emergency).getByText('DIVERGED')).toBeTruthy();
    expect(within(emergency).getByText('EXPIRED')).toBeTruthy();
    expect(within(emergency).getByText('GAP_DETECTED')).toBeTruthy();
    expect(within(emergency).getAllByText('BLOCK').length).toBeGreaterThanOrEqual(4);
  });

  it('fails mandate state closed when a nominally valid mandate is already expired', () => {
    const data = availableModel();
    data.mft = {
      ...liveMft(),
      snapshot: makeMftSnapshot({
        as_of_ms: 2_000,
        market_gap: { status: 'CONTIGUOUS', missing_intervals: [] },
        positions: { status: 'RECONCILED', items: [] },
        risk: { status: 'ALLOWED_TESTNET', kill_switch_latched: false },
        reconciliation: { status: 'IN_SYNC', requires_manual_review: false },
        mandate_expiry: { status: 'VALID_TESTNET_ONLY', expires_at_ms: 1_999 },
      }),
    } as never;
    model.value = data;
    render(<PortfolioRisk />);
    const mandate = within(screen.getByTestId('emergency-state')).getByText('Mandate expiry').parentElement!;
    expect(within(mandate).getByText('BLOCK')).toBeTruthy();
  });

  it('labels malformed actual position detail instead of treating it as an absent v1 field', () => {
    const data = availableModel();
    data.mft = {
      ...liveMft(),
      snapshot: makeMftSnapshot({
        market_gap: { status: 'CONTIGUOUS', missing_intervals: [] },
        positions: { status: 'RECONCILED', items: [{ instrument: 'BTCUSDT', quantity: 'bad' }] },
        risk: { status: 'ALLOWED_TESTNET', kill_switch_latched: false },
        reconciliation: { status: 'IN_SYNC', requires_manual_review: false },
        mandate_expiry: { status: 'VALID_TESTNET_ONLY', expires_at_ms: 1_800_000_000_000 },
      }),
    } as never;
    model.value = data;
    render(<PortfolioRisk />);
    expect(within(screen.getByTestId('mft-actual-positions')).getByText(/MALFORMED_POSITION_DETAIL/)).toBeTruthy();
    expect(screen.queryByText(/UNAVAILABLE_IN_V1_DETAIL/)).toBeNull();
  });

  it('shows unavailable research data instead of synthetic portfolio fallback', () => {
    const data = availableModel();
    data.portfolio = {
      status: 'UNAVAILABLE', reason: 'PROVENANCE:SYNTHETIC_DETERMINISTIC', payload: null,
    } as never;
    data.risk = { status: 'UNAVAILABLE', reason: 'NO_PORTFOLIO_TARGET', payload: null } as never;
    model.value = data;
    render(<PortfolioRisk />);
    expect(screen.getAllByText(/PROVENANCE:SYNTHETIC_DETERMINISTIC/).length).toBeGreaterThan(0);
    expect(screen.queryByText('25.00%')).toBeNull();
  });
});
