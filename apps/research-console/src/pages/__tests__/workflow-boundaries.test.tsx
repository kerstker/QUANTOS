import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Validation from '@/pages/Validation';
import Risk from '@/pages/Risk';
import DryRunPipeline from '@/pages/DryRunPipeline';
import LiveTrading from '@/pages/LiveTrading';
import PromotionQueue from '@/pages/PromotionQueue';
import { ConfirmDialogHost } from '@/ui/dialog';
import { ToastProvider } from '@/ui/toast';
import { clearGovernanceAccessToken } from '@/lib/api/promotion-api';

type MockSnapshot = {
  payload: unknown;
  apiState: 'disabled' | 'loading' | 'connected' | 'error';
  error: string | null;
  usingBackend: boolean;
};

const hookState = vi.hoisted(() => ({ queue: [] as MockSnapshot[] }));

// Pages use the production hook for transport; these tests inject only fixed
// snapshots and never start a runner, ops API, or exchange connection.
vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: () => hookState.queue.shift() ?? {
    payload: null, apiState: 'disabled', error: null, usingBackend: false,
  },
}));

function snapshot(payload: unknown, overrides: Partial<MockSnapshot> = {}): MockSnapshot {
  return {
    payload,
    apiState: 'connected',
    error: null,
    usingBackend: true,
    ...overrides,
  };
}

function renderPage(page: React.ReactNode) {
  return render(<MemoryRouter><ToastProvider><ConfirmDialogHost>{page}</ConfirmDialogHost></ToastProvider></MemoryRouter>);
}

const baseValidationReport = {
  run_id: 'validation-reject-1', alpha_id: 'alpha-overfit', decision: 'REJECTED_OVERFIT',
  gates_passed: 2, gates_total: 6, reasons: ['PBO exceeds limit', 'walk-forward unstable'],
  leakage_pass: true, walk_forward_pass: false, multiple_testing_pass: false,
  costs_pass: true, capacity_pass: true, decay_pass: false,
  oos_sharpe: -0.42, oos_ic: -0.03, net_sharpe: -0.61,
};

const baseRiskReport = {
  run_id: 'risk-reject-1', portfolio_id: 'portfolio-1', decision: 'REJECTED_CONCENTRATION_RISK',
  gates_passed: 4, gates_total: 9,
  reasons: ['concentration limit exceeded'],
  gate_results: { market: true, liquidity: true, concentration: false, correlation: true, drawdown: false },
  reproducibility_hash: 'risk-hash',
};

const baseAlpha = {
  candidate_id: 'alpha-risk', expression: '(rank :close)', turnover: 0.2,
  capacity_estimate: 1000000, lifecycle_state: 'VALIDATED', ic: 0.02,
  rank_ic: 0.03, icir: 0.4, sharpe: 0.7, decay_half_life: 15,
  composite_score: 0.4, correlation_penalty: 0.1,
};

const safetyPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'REAL_SAFETY_PRIMITIVES', safety_mode: 'L2_FAIL_CLOSED',
  kill_switch: { is_active: true, trip_count: 2, fail_closed: true, human_only_reset: true, auto_reset: false },
  pre_trade_gate: { checks_registered: 23, checks: [], fail_closed_on_unknown: true, refuses_unsafe_build: true },
  safety_config: { live_trading_enabled: false, real_broker_enabled: false, allow_order_submission: false,
    allow_ai_direct_trading: false, require_human_approval: true },
  live_authorization_allowed: false,
};

describe('research workflow fail-closed boundaries', () => {
  beforeEach(() => {
    hookState.queue = [];
    clearGovernanceAccessToken();
  });

  afterEach(() => {
    hookState.queue = [];
  });

  it('validation rejection is visible as a blocked decision, not a usable result', () => {
    hookState.queue.push(snapshot({
      source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
      data_provenance: 'REAL_VALIDATION_SNAPSHOT', selected_report: baseValidationReport,
      reports: [baseValidationReport], lineage: [],
    }));
    const { container } = renderPage(<Validation />);
    expect(container.textContent).toContain('REJECTED_OVERFIT');
    expect(container.textContent).toContain('DATA_BLOCKED');
    expect(container.textContent).toContain('Rejection Reasons');
    expect(container.textContent).toContain('PBO exceeds limit');
  });

  it('risk rejection and an active Kill Switch remain visible together and prohibit live authorization', () => {
    hookState.queue.push(
      snapshot({
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
        data_provenance: 'REAL_RISK_SNAPSHOT', selected_alpha_id: 'alpha-risk', alpha: baseAlpha,
        primary_report: baseRiskReport, reports: [baseRiskReport], lineage: [],
      }),
      snapshot(safetyPayload),
    );
    const { container } = renderPage(<Risk />);
    expect(container.textContent).toContain('REJECTED_CONCENTRATION_RISK');
    expect(container.textContent).toContain('DATA_BLOCKED');
    expect(container.textContent).toContain('TRIPPED');
    expect(container.textContent).toContain('Live Auth');
    expect(container.textContent).toContain('DENIED');
    expect(container.textContent).toContain('ORDER_SUBMIT');
    expect(container.textContent).toContain('OFF');
  });

  it('dry-run with no run is shown as NO_SNAPSHOT, not as a successful dry-run', () => {
    hookState.queue.push(snapshot({
      source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
      data_provenance: 'REAL_DRY_RUN_OFFLINE_SNAPSHOT', snapshot_present: false,
      verdict: 'NO_RUN', n_runs: 0, latest: {}, runs: [],
      safety_note: 'No dry-run snapshot',
    }));
    const { container } = renderPage(<DryRunPipeline />);
    expect(container.textContent).toContain('NO_SNAPSHOT');
    expect(container.textContent).toContain('NO_RUN');
    expect(container.textContent).not.toContain('DRY_RUN_OK');
  });

  it('reconciliation hard stop is surfaced and a submit action is never rendered by the read-only page', () => {
    hookState.queue.push(snapshot({
      source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
      data_provenance: 'REAL_LIVE_TRADING_SNAPSHOT', snapshot_present: true,
      verdict: 'LIVE_TRADING_TRACKING_OK', honesty_note: 'testnet only',
      forward_track: { present: true, evidence_gate_weeks: 8, alphas: [] },
      execution_stack: {
        present: true, mode: 'preview', hard_stop: true, tier: 'NEEDS_HUMAN_REVIEW',
        n_submitted: 0, n_pending_review: 0, n_rejected_by_gate: 1, n_dropped_by_filters: 0,
        n_submission_errors: 0, reconciliation: { in_sync: false, proceed: false, max_abs_drift: 12.5 },
        risk_breach: { reasons: ['reconciliation drift'] }, would_submit: [],
      },
    }));
    const { container } = renderPage(<LiveTrading />);
    expect(container.textContent).toContain('HARD STOP');
    expect(container.textContent).toContain('DATA_BLOCKED');
    expect(container.textContent).toContain('drift');
    expect(container.textContent).not.toContain('Submit order');
  });

  it('promotion controls remain disabled until governance identity is configured and authenticated', () => {
    const { getByRole } = renderPage(<PromotionQueue />);
    const signIn = getByRole('button', { name: /OIDC sign in/i });
    expect(signIn).toBeDisabled();
    expect(getByRole('button', { name: /Refresh/i })).toBeDisabled();
  });
});
