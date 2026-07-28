import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DryRunPipelinePayload } from '@/lib/types/research';
import DryRunPipeline from '@/pages/DryRunPipeline';

const state = vi.hoisted(() => ({
  payload: {
    source: 'BACKEND_COMPUTED_SNAPSHOT',
    read_only: true,
    api_version: '0.1.0',
    data_provenance: 'REAL_DRY_RUN_OFFLINE_SNAPSHOT',
    snapshot_present: false,
    verdict: 'NO_RUN',
    n_runs: 0,
    latest: {},
    runs: [],
    paper_shadow: {
      source: 'AUTOMATED_SHADOW_PAPER_ARTIFACTS',
      read_only: true,
      snapshot_present: true,
      execution_class: 'AUTOMATED_NONPROMOTABLE_SHADOW_PAPER',
      status: 'RUNNING',
      generated_at: 1_785_263_000_000,
      observed_at: 1_785_263_100_000,
      provider: 'BINANCE_PUBLIC_MARK_PRICE',
      verdict: {
        shadow_paper_running: true,
        auto_rebalance_ready: true,
        paper_mark_ready: true,
        boundary_valid: true,
        official_paper_ready: false,
        promotion_eligible: false,
        forward_evidence_eligible: false,
        deployment_eligible: false,
        live_allowed: false,
        order_submission_allowed: false,
      },
      alphas: {
        crowd_contra: {
          alpha_id: 'crowd_contra',
          status: 'PAPER_MARKED',
          equity: 998_000,
          net_pnl: -2_000,
          funding_pnl: 176,
          positions: { BTCUSDT: 1 },
          reconciliation_residual: 0,
        },
      },
      previews: {
        crowd_contra: {
          alpha_id: 'crowd_contra',
          n_intents: 8,
          n_paper_fills: 8,
        },
      },
      history: [
        {
          alpha_id: 'crowd_contra',
          observed_at: 1_785_263_100_000,
          net_pnl: -2_000,
        },
      ],
      totals: {
        equity: 2_956_000,
        net_pnl: -44_000,
        funding_pnl: 1_140,
      },
      paper_ledger: { n_rows: 3, n_decisions: 1 },
      persistence: { new_snapshots: 3, chain_valid: true },
      errors: {},
      safety: {
        paper_only: true,
        separate_from_official_paper: true,
        public_market_data_only: true,
        private_account_access: false,
        credentials_accessed: false,
        broker_connectivity: false,
        broker_order_submission: false,
      },
    },
    scheduler_watchdog: {
      snapshot_present: true,
      verdict: 'PASS',
      summary: { PASS: 2 },
      checks: [
        {
          task_name: 'QUANTOS_paper_shadow_auto',
          status: 'PASS',
          code: 'HEALTHY',
          detail: 'healthy',
        },
        {
          task_name: 'QUANTOS_paper_shadow_realtime',
          status: 'PASS',
          code: 'HEALTHY',
          detail: 'healthy',
        },
      ],
    },
    safety_note: 'L2 only.',
  } as DryRunPipelinePayload,
}));

vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: () => ({
    payload: state.payload,
    apiState: 'connected',
    error: null,
    usingBackend: true,
    snapshotState: 'real',
  }),
}));

vi.mock('@/lib/api/research-runner-api', () => ({
  fetchRunnerHealth: vi.fn(),
  getRunnerToken: () => null,
  runnerEnabled: () => false,
  triggerRun: vi.fn(),
}));

describe('automated shadow-paper cockpit', () => {
  it('renders live shadow metrics and scheduled automation separately', () => {
    render(<DryRunPipeline />);

    expect(screen.getByText(/全自动影子纸面驾驶舱/)).toBeInTheDocument();
    expect(screen.getByText('SCHEDULED_ACTIVE')).toBeInTheDocument();
    expect(screen.getByText('$2,956,000')).toBeInTheDocument();
    expect(screen.getByText('$-44,000')).toBeInTheDocument();
    expect(screen.getByText(/独立、不可晋级的自动观察账户/)).toBeInTheDocument();
    expect(screen.queryByText('IDLE')).not.toBeInTheDocument();
  });
});
