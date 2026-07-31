import { render, screen } from '@testing-library/react';
import { MemoryRouter } from '@/lib/router';
import { describe, expect, it, vi } from 'vitest';
import type { DryRunPipelinePayload } from '@/lib/types/research';
import CommandCenter from '@/pages/CommandCenter';

const state = vi.hoisted(() => ({
  payload: {
    source: 'BACKEND_COMPUTED_SNAPSHOT',
    read_only: true,
    api_version: '0.1.0',
    data_provenance: 'REAL_DRY_RUN_OFFLINE_SNAPSHOT',
    snapshot_present: true,
    verdict: 'DRY_RUN_OK',
    n_runs: 601,
    latest: {},
    runs: [],
    paper_shadow: {
      source: 'AUTOMATED_SHADOW_PAPER_ARTIFACTS',
      read_only: true,
      snapshot_present: true,
      execution_class: 'AUTOMATED_NONPROMOTABLE_SHADOW_PAPER',
      status: 'RUNNING',
      observed_at: Date.now(),
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
          n_fills: 17,
          equity: 1_008_000,
          net_pnl: 8_000,
          funding_pnl: 222,
          positions: { BTCUSDT: 1, ETHUSDT: -1 },
          reconciliation_residual: 0,
        },
      },
      previews: {},
      history: [],
      totals: {
        equity: 3_000_000,
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
    paper_health: {
      source: 'PAPER_HEALTH',
      read_only: true,
      snapshot_present: true,
      max_age_ms: 300_000,
      verdict: {
        paper_ready: false,
        paper_mark_ready: true,
        paper_preview_ready: false,
        backtest_reference_present_for_all_three: true,
        live_allowed: false,
        order_submission_allowed: false,
      },
      readiness_class: 'PAPER_MARKS_ONLY_REPLAY',
      next_action: 'accumulate independent forward periods',
      blockers: ['MODEL_ALPHA_EVIDENCE_INSUFFICIENT'],
      research_risk_bundle_complete: true,
      forward_evidence: {
        present: true,
        evidence_gate_periods: 8,
        complete_for_all_three: false,
        alphas: {
          crowd_contra: {
            n_forward_periods: 1,
            periods_to_gate: 7,
            evidence_state: 'ATTESTED',
          },
        },
      },
      safety: {
        paper_only: true,
        public_market_data_only: true,
        broker_connectivity: false,
        credentials_accessed: false,
        broker_order_submission: false,
      },
    },
    scheduler_watchdog: {
      snapshot_present: true,
      verdict: 'PARTIAL',
      summary: { PASS: 21, PARTIAL: 5, UNAVAILABLE: 1, BREACH: 0 },
      checks: [],
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

describe('operations command center', () => {
  it('prioritizes live paper, evidence, operations, and safety without layout controls', () => {
    render(<MemoryRouter><CommandCenter /></MemoryRouter>);

    expect(screen.getByText('PAPER ACTIVE')).toBeInTheDocument();
    expect(screen.getByText('$3M')).toBeInTheDocument();
    expect(screen.getByText('-$44,000')).toBeInTheDocument();
    expect(screen.getByText('1/8')).toBeInTheDocument();
    expect(screen.getByText('NO BREACH')).toBeInTheDocument();
    expect(screen.getByText('BINANCE_PUBLIC_MARK_PRICE')).toBeInTheDocument();
    expect(screen.getByText('BLOCKED')).toBeInTheDocument();
    expect(screen.queryByLabelText(/resize tile|缩放磁贴/)).not.toBeInTheDocument();
  });
});
