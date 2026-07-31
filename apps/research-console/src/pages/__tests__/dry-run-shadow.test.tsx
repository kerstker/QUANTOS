import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DryRunPipelinePayload } from '@/lib/types/research';
import DryRunPipeline from '@/pages/DryRunPipeline';

const state = vi.hoisted(() => ({
  apiState: 'connected' as 'connected' | 'loading',
  usingBackend: true,
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
    route_tca_history: {
      schema_version: 1,
      simulation_only: true,
      status: 'LONGITUDINAL_READY',
      run_count: 664,
      record_count: 2656,
      venue_count: 2,
      first_timestamp: 1_785_288_893_251,
      last_timestamp: 1_785_494_047_051,
      weighted_shortfall_bps: '6.073508244770515195597415338',
      points: [],
      venues: [
        {
          venue_id: 'BINANCE_SIM',
          record_count: 1328,
          filled_quantity: 12_400,
          reference_notional: '5200000',
          weighted_shortfall_bps: '5.81',
          first_timestamp: 1_785_288_893_251,
          last_timestamp: 1_785_494_047_051,
        },
        {
          venue_id: 'BYBIT_SIM',
          record_count: 1328,
          filled_quantity: 11_900,
          reference_notional: '4800000',
          weighted_shortfall_bps: '6.36',
          first_timestamp: 1_785_288_893_251,
          last_timestamp: 1_785_494_047_051,
        },
      ],
    },
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
    paper_order_preview: {
      source: 'PAPER_ORDER_PREVIEW',
      read_only: true,
      snapshot_present: true,
      verdict: {
        paper_preview_ready: false,
        live_allowed: false,
        order_submission_allowed: false,
      },
      alphas: {
        crowd_contra: {
          alpha_id: 'crowd_contra',
          paper_execution: 'PAPER_BLOCKED',
          research_risk: {
            decision: 'RISK_REJECTED',
            status: 'REJECTED',
            gates_passed: 8,
            gates_run: 9,
            engines_run: {
              capacity: true,
              model_alpha: false,
            },
            reasons: [
              'Model/Alpha: validation_conf=0.020, paper_conf=0.125',
            ],
            reports: {
              capacity: {
                passed: true,
                capacity_usage: 0.782512,
                bottleneck_alpha: 'ATOM',
              },
              model_alpha: {
                passed: false,
                alphas_with_decay: 0,
                alphas_with_overfitting_flags: 0,
                alphas_with_leakage_flags: 0,
                min_validation_confidence: 0.02033,
                min_paper_confidence: 0.125,
              },
            },
          },
        },
      },
      errors: {},
      safety: {
        paper_only: true,
        broker_connectivity: false,
        broker_order_submission: false,
        credentials_accessed: false,
      },
    },
    safety_note: 'L2 only.',
  } as DryRunPipelinePayload,
}));

vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: () => ({
    payload: state.payload,
    apiState: state.apiState,
    error: null,
    usingBackend: state.usingBackend,
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
    expect(screen.getByText('8/9')).toBeInTheDocument();
    expect(screen.getByText('78.3%')).toBeInTheDocument();
    expect(screen.getByText('0.020')).toBeInTheDocument();
    expect(screen.getByText('0.125')).toBeInTheDocument();
    expect(screen.getByText('blocked by model_alpha')).toBeInTheDocument();
    expect(screen.getByText('纸面执行质量')).toBeInTheDocument();
    expect(screen.getByText('6.07 bps')).toBeInTheDocument();
    expect(screen.getByText('BINANCE_SIM')).toBeInTheDocument();
    expect(screen.getByText('BYBIT_SIM')).toBeInTheDocument();
    expect(screen.getByText('2,656')).toBeInTheDocument();
    expect(screen.queryByText('IDLE')).not.toBeInTheDocument();
  });

  it('uses a neutral initial loading state instead of flashing false alarms', () => {
    state.apiState = 'loading';
    state.usingBackend = false;
    try {
      render(<DryRunPipeline />);
      expect(screen.getByText('正在建立只读纸面快照')).toBeInTheDocument();
      expect(screen.queryByText('chain invalid')).not.toBeInTheDocument();
      expect(screen.queryByText('NO REAL PROVIDER')).not.toBeInTheDocument();
      expect(screen.queryByText('CHECK')).not.toBeInTheDocument();
    } finally {
      state.apiState = 'connected';
      state.usingBackend = true;
    }
  });

  it('labels retained data as refreshing without downgrading healthy automation', () => {
    state.apiState = 'loading';
    state.usingBackend = true;
    try {
      render(<DryRunPipeline />);
      expect(screen.getByText('REFRESHING')).toBeInTheDocument();
      expect(screen.getByText('SCHEDULED_ACTIVE')).toBeInTheDocument();
      expect(screen.queryByText('CONNECTING')).not.toBeInTheDocument();
    } finally {
      state.apiState = 'connected';
      state.usingBackend = true;
    }
  });
});
