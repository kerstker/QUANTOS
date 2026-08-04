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
    realtime_alpha_autonomy: {
      source: 'AUTONOMOUS_REALTIME_ALPHA_DISCOVERY_ARTIFACT',
      read_only: true,
      snapshot_present: true,
      boundary_valid: true,
      status: 'RUNNING_EXPLORATORY',
      mode: 'L2_AUTONOMOUS_REALTIME_ALPHA_RESEARCH',
      generated_at_ms: 1_785_592_975_514,
      dataset_version: 'dataset-v1',
      search_id: 'search-v1',
      autonomy: {
        worker: 'tools.realtime_alpha_discovery',
        scheduler_task: 'QUANTOS_realtime_alpha_discovery',
        autonomous_search_running: true,
        requires_developer_ai: false,
        next_cycle_expected_ms: 1_785_596_575_514,
      },
      data_admission: {
        status: 'EXPLORATION_READY',
        common_span_ms: 301_561_655,
        feature_age_ms: 228_264,
        common_instruments: Array.from({ length: 14 }, (_, index) => `S${index}`),
        exploration_allowed: true,
        formal_validation_allowed: false,
        shadow_admission_allowed: false,
        reasons: ['diagnostic exploration allowed; formal validation requires 30 days'],
      },
      feature_quality: { status: 'PASS', drift_status: 'BLOCK' },
      executable_depth_capacity: {
        status: 'COLLECTING_OR_BLOCKED',
        passed: false,
        evidence_ready: false,
        target_aum_passed: false,
        shadow_sizing_ready: false,
        history_span_ms: 2_172_786,
        feature_age_ms: 0,
        instrument_count: 14,
        conservative_depth_usd: 2_187.24,
        max_supported_aum_usd: 874.90,
        target_aum_usd: 1_000_000,
        shadow_aum_usd: 437.45,
        participation_rate: 0.1,
        legs_per_side: 2,
        reasons: [
          'EXECUTABLE_DEPTH_HISTORY_BELOW_30D',
          'EXECUTABLE_DEPTH_CAPACITY_BELOW_TARGET_AUM',
        ],
      },
      funnel: {
        pre_registered: 24,
        evaluated: 24,
        diagnostic_leads: 0,
        formal_passes: 0,
        shadow_candidates: 0,
      },
      families: [
        { family_id: 'order_flow', hypothesis_count: 6 },
        { family_id: 'order_book', hypothesis_count: 6 },
        { family_id: 'liquidation', hypothesis_count: 6 },
        { family_id: 'funding_basis', hypothesis_count: 6 },
      ],
      top_candidates: [
        {
          candidate_id: 'rt_basis_convergence_1h_v1',
          family_id: 'funding_basis',
          horizon_ms: 3_600_000,
          status: 'EXPLORATORY_ONLY',
          holdout_net_sharpe: -13.91,
          net_return: -0.0367,
          promotion_eligible: false,
          live_allowed: false,
        },
      ],
      global_trial_count: 2577,
      registry: { chain_valid: true, event_count: 48 },
      remaining_gates: [
        '30D_COMMON_HISTORY_FOR_FORMAL_VALIDATION',
        '90D_COMMON_HISTORY_FOR_FORWARD_SHADOW',
        'EXECUTABLE_DEPTH_CAPACITY_CALIBRATION',
      ],
      blockers: [],
      forward_shadow: {
        snapshot_present: true,
        boundary_valid: true,
        status: 'WAITING_FOR_VALIDATED_CANDIDATE',
        generated_at_ms: 1_785_592_980_000,
        eligible_candidates: [],
        feature_input: { ready: true, status: 'PASS' },
        cycle: { new_admissions: 0, new_decisions: 0, new_realizations: 0 },
        tracks: {},
        errors: {},
        ledger: { append_only: true, chain_valid: true, event_count: 0 },
        verdict: {
          forward_shadow_running: false,
          promotion_eligible: false,
          forward_evidence_credit: false,
          deployment_eligible: false,
          live_allowed: false,
          order_submission_allowed: false,
        },
        blockers: [],
        safety: {
          paper_only: true,
          public_market_data_only: true,
          separate_from_official_paper: true,
          credentials_accessed: false,
          broker_connectivity: false,
          broker_order_submission: false,
          allow_ai_direct_trading: false,
        },
      },
      safety: {
        paper_only: true,
        live_trading_enabled: false,
        real_broker_enabled: false,
        allow_order_submission: false,
        allow_ai_direct_trading: false,
        promotion_eligible: false,
        credentials_accessed: false,
      },
    },
    alpha_pipeline: {
      source: 'BACKEND_COMPUTED_ALPHA_PIPELINE',
      read_only: true,
      factory: {
        snapshot_present: true,
        generated_at: 1_785_592_900_000,
        rounds: 2,
        dry_limit: 24,
        data_dimensions: 12,
        survivors: 1,
        verdict: 'research survivor retained',
        containment: 'research only',
      },
      counts: {
        forward_paper: 1,
        execution_paper: 1,
        candidate_execution_paper: 0,
        legacy_diagnostic_paper: 1,
        forward_only: 0,
        factory_survivors: 1,
      },
      candidates: [
        {
          alpha_id: 'crowd_contra',
          n_forward_periods: 1,
          weeks_to_go_no_go: 7,
          paper_sharpe: null,
          paper_ic: null,
          cum_return: -0.002,
          backtest_reference_sharpe: 1.2,
          reference_weeks: 230,
          pending_decisions: 1,
          last_decision_ts: 1_785_263_100_000,
          stage: 'LEGACY_DIAGNOSTIC_PAPER',
          paper_class: 'LEGACY_DIAGNOSTIC_PAPER',
          execution_paper: true,
          deployment_eligible: false,
          blockers: ['LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE'],
        },
      ],
      handoff: {
        factory_to_forward_registry_connected: false,
        forward_to_execution_dynamic: false,
        status: 'GAPPED',
        gap_codes: ['UNIQUE_ALPHA_WORKFLOW_NOT_VERIFIED'],
        boundary_codes: ['LEGACY_DIAGNOSTIC_PAPER_QUARANTINED'],
      },
      safety: {
        paper_only: true,
        deployment_eligible: false,
        live_allowed: false,
        order_submission_allowed: false,
      },
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
    paper_operational: {
      snapshot_present: true,
      generated_at_ms: 1_785_263_120_000,
      status: 'MATURING',
      chain_valid: true,
      policy: {
        version: 'paper_operational_evidence.v1',
        cadence_ms: 300_000,
        production_minimum_span_ms: 86_400_000,
        production_minimum_successes: 274,
        minimum_success_ratio: 0.99,
      },
      window: {
        engineering_smoke_ready: false,
        production_window_ready: false,
        cycle_count: 1,
        accepted_cycle_count: 1,
        success_ratio: 1,
        history_span_ms: 0,
        max_gap_ms: null,
        latest_age_ms: 20_000,
        drills: {
          EXACT_RETRY_IDEMPOTENT: true,
          CONFLICTING_DUPLICATE_REJECTED: true,
          HASH_CHAIN_TAMPER_DETECTED: true,
          STALE_CYCLE_REJECTED: true,
          UNSAFE_BROKER_BOUNDARY_REJECTED: true,
          INCOMPLETE_CANDIDATE_SET_REJECTED: true,
        },
        planned_restart_recoveries: [],
        blockers: ['PRODUCTION_WINDOW_SPAN_MATURING'],
      },
      safety: {
        paper_only: true,
        fault_injection_isolated: true,
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
      readiness_class: 'PAPER_FORWARD_EVIDENCE_MATURING',
      next_action: 'continue_scheduled_forward_evidence_and_review_remaining_risk_gates',
      blockers: ['crowd_contra:PAPER_RESEARCH_RISK_NOT_FULL'],
      research_risk_bundle_complete: true,
      research_risk_bundles: {
        crowd_contra: { status: 'READY' },
      },
      forward_evidence: {
        present: true,
        evidence_gate_periods: 8,
        complete_for_all_three: false,
        evidence_maturing: true,
        maturity_state: 'FORWARD_EVIDENCE_MATURING',
        next_realization_eligible_at: 1_785_758_400_000,
        max_periods_to_gate: 7,
        alphas: {
          crowd_contra: {
            n_forward_periods: 1,
            periods_to_gate: 7,
            attested_pending_decisions: 1,
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
      verdict: 'BREACH',
      summary: { PASS: 2, BREACH: 1 },
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
    expect(screen.getByText('自主 Alpha 发现')).toBeInTheDocument();
    expect(screen.getByText('SYSTEM NATIVE')).toBeInTheDocument();
    expect(screen.getByText('SSE STREAM')).toBeInTheDocument();
    expect(screen.queryByText('BREACH')).not.toBeInTheDocument();
    expect(screen.queryByText('10s POLL')).not.toBeInTheDocument();
    expect(screen.getByText('RUNNING_EXPLORATORY')).toBeInTheDocument();
    expect(screen.getByText('WAITING_FOR_VALIDATED_CANDIDATE')).toBeInTheDocument();
    expect(screen.getByText('LEGACY PAPER')).toBeInTheDocument();
    expect(screen.getByText('diagnostic only · never deployment eligible')).toBeInTheDocument();
    expect(screen.getByText('rt_basis_convergence_1h_v1')).toBeInTheDocument();
    expect(screen.getByText('3.49d')).toBeInTheDocument();
    expect(screen.getByText(/Capacity max \$875/)).toBeInTheDocument();
    expect(screen.getByText('$2,956,000')).toBeInTheDocument();
    expect(screen.getByText('$-44,000')).toBeInTheDocument();
    expect(screen.getByText(/独立、不可晋级的自动观察账户/)).toBeInTheDocument();
    expect(screen.getByText('8/9')).toBeInTheDocument();
    expect(screen.getByText('78.3%')).toBeInTheDocument();
    expect(screen.getByText('0.020')).toBeInTheDocument();
    expect(screen.getByText('0.125')).toBeInTheDocument();
    expect(screen.getByText('blocked by model_alpha')).toBeInTheDocument();
    expect(screen.getByText('连续运行与故障恢复')).toBeInTheDocument();
    expect(screen.getAllByText('6/6').length).toBeGreaterThan(0);
    expect(screen.getByText('Restart recovery')).toBeInTheDocument();
    expect(screen.getByText('MATURING')).toBeInTheDocument();
    expect(screen.getAllByText(/2026-08-03 12:00 UTC/).length).toBeGreaterThan(0);
    expect(screen.getByText(/前向证据按计划成熟中/)).toBeInTheDocument();
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
      expect(screen.getByText('SSE RECONNECTING')).toBeInTheDocument();
      expect(screen.getByText('SCHEDULED_ACTIVE')).toBeInTheDocument();
      expect(screen.queryByText('CONNECTING')).not.toBeInTheDocument();
    } finally {
      state.apiState = 'connected';
      state.usingBackend = true;
    }
  });
});
