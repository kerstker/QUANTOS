import { fireEvent, render, screen, within } from '@testing-library/react';
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
      feedback: {
        status: 'ADAPTIVE_EXPLORATION_READY',
        consumed_realizations: 854,
        active_adaptive_candidates: 2,
        new_candidate_ids: ['rt_liquidation_cascade_1m_fb1'],
        families: [
          { family_id: 'liquidation', action: 'EXPAND' },
          { family_id: 'order_flow', action: 'DEPRIORITIZE' },
        ],
        formal_gate_impact: false,
        promotion_eligible: false,
        live_allowed: false,
        order_submission_allowed: false,
      },
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
      lifecycle: [
        {
          candidate_id: 'rt_basis_convergence_1h_v1',
          family_id: 'funding_basis',
          mechanism: 'basis_convergence',
          horizon_ms: 3_600_000,
          pipeline_stage: 'EXECUTION_PAPER',
          performance_state: 'LOSS',
          research: {
            status: 'EXPLORATORY_ONLY',
            evaluation_status: 'EVALUATED',
            n_periods: 99,
            net_return: -0.0367,
            holdout_net_sharpe: -13.91,
            gate_pass: false,
            gate_reasons: ['REALTIME_HISTORY_BELOW_30D'],
          },
          paper: {
            n_decisions: 10,
            n_pending: 1,
            n_realizations: 9,
            cum_return: -0.04,
            max_drawdown: 0.05,
            performance_history: [
              { as_of_ms: 1_785_592_930_000, period_return: -0.01, cum_return: -0.01, drawdown: 0.01 },
              { as_of_ms: 1_785_592_990_000, period_return: -0.03, cum_return: -0.04, drawdown: 0.04 },
            ],
            pnl_source: 'ENGINEERING_SHADOW_REALIZED_NET_RETURN',
          },
          execution: {
            state: 'EXECUTING',
            n_executions: 1,
            n_orders: 6,
            n_fill_slices: 8,
            n_no_actions: 3,
            n_missed_execution_windows: 2,
            execution_cost: 1.25,
          },
          last_event_ms: 1_785_592_990_000,
          errors: [],
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
        status: 'RUNNING',
        mode: 'AUTONOMOUS_REALTIME_ENGINEERING_SHADOW',
        generated_at_ms: 1_785_592_980_000,
        eligible_candidates: ['rt_basis_convergence_1h_v1'],
        admitted_candidates: ['rt_basis_convergence_1h_v1'],
        handoff: {
          admission_blockers: [],
          graduation_blockers: [
            'THIRTY_DAY_FORMAL_VALIDATION_REQUIRED',
            'NINETY_DAY_HISTORY_MATURITY_REQUIRED',
          ],
        },
        feature_input: { ready: true, status: 'PASS' },
        cycle: { new_admissions: 0, new_decisions: 0, new_realizations: 0 },
        tracks: {},
        errors: {},
        ledger: { append_only: true, chain_valid: true, event_count: 0 },
        verdict: {
          forward_shadow_running: false,
          engineering_shadow_running: true,
          paper_observation_started_without_maturity_wait: true,
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
      execution_paper: {
        snapshot_present: true,
        boundary_valid: true,
        status: 'RUNNING',
        mode: 'AUTONOMOUS_REALTIME_ENGINEERING_EXECUTION_PAPER',
        execution_profile: 'REAL_PUBLIC_BOOK_LOT_NORMALIZED_SIMULATED_SOR_V1',
        generated_at_ms: 1_785_592_990_000,
        candidate_count: 1,
        cycle: { new_admissions: 1, new_executions: 1, new_no_actions: 3, waiting_count: 0 },
        routing: {
          snapshot_id: 'book-1',
          as_of_ms: 1_785_592_989_000,
          venues: ['binance_usdm', 'bybit_linear'],
          public_data_only: true,
          simulation_only: true,
        },
        lot_authority: { instrument_count: 14, spec_hash: 'a'.repeat(64) },
        tracks: {
          rt_basis_convergence_1h_v1: {
            state: 'EXECUTING',
            n_executions: 1,
            n_missed_execution_windows: 2,
            n_no_actions: 3,
            n_orders: 6,
            n_fill_slices: 8,
            execution_cost: 1.25,
          },
        },
        waiting: {},
        errors: {},
        ledger: {
          append_only: true,
          chain_valid: true,
          source_shadow_chain_valid: true,
          event_count: 2,
          separate_from_official_paper: true,
        },
        verdict: {
          engineering_execution_paper_running: true,
          production_execution_seam_exercised: true,
          official_paper_ready: false,
          promotion_eligible: false,
          forward_evidence_eligible: false,
          deployment_eligible: false,
          live_allowed: false,
          order_submission_allowed: false,
        },
        blockers: [],
        safety: {
          paper_only: true,
          public_market_data_only: true,
          credentials_accessed: false,
          private_stream: false,
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
          definition_kind: 'ALPHA_EXPRESSION',
          family_id: 'CROWDING_REVERSAL',
          source_kind: 'STATIC_PREREGISTERED',
          execution_paper: true,
          deployment_eligible: false,
          blockers: ['LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE'],
          maturity: { observed_periods: 1, required_periods: 8 },
        },
      ],
      exception_queue: [
        {
          code: 'FORMAL_BLINDED_SIGNAL_SHADOW_NOT_AVAILABLE',
          candidate_count: 26,
          candidate_ids: ['rt_basis_convergence_1h_v1'],
        },
      ],
      workflow: {
        authority_present: true,
        verified: true,
        population_count: 36,
        event_count: 72,
        execution_paper_eligible_count: 0,
        realtime_handoff_count: 26,
        realtime_research_policy_hash: 'd'.repeat(64),
        release_commit: 'c'.repeat(40),
        source_as_of_ms: 1_785_592_900_000,
      },
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
    paper_realtime: {
      source: 'PAPER_REALTIME_MARK_ARTIFACT',
      read_only: true,
      snapshot_present: true,
      observed_at: 1_785_263_100_000,
      provider: 'BINANCE_PUBLIC_MARK_PRICE',
      verdict: {
        cycle_completed: true,
        paper_mark_ready: true,
        backtest_reference_present_for_all_three: true,
        live_allowed: false,
        order_submission_allowed: false,
      },
      alphas: {
        carry_oi_majors: {
          alpha_id: 'carry_oi_majors',
          status: 'PAPER_MARKED',
          n_fills: 8,
          positions: { BTC: -2 },
          equity: 992_584,
          net_pnl: -7_416,
          return_on_aum: -0.007416,
        },
        crowd_contra: {
          alpha_id: 'crowd_contra',
          status: 'PAPER_MARKED',
          n_fills: 8,
          positions: { BTC: 2 },
          equity: 1_000_879,
          net_pnl: 879,
          return_on_aum: 0.000879,
        },
        pending_factor: {
          alpha_id: 'pending_factor',
          status: 'PAPER_PENDING',
          n_fills: 0,
          positions: {},
          equity: 1_000_000,
          net_pnl: 0,
          return_on_aum: 0,
        },
      },
      backtests: {},
      history: [],
      paper_ledger: {},
      persistence: { new_snapshots: 2, chain_valid: true },
      errors: {},
      safety: {
        paper_only: true,
        public_market_data_only: true,
        private_binance_access: false,
        credentials_accessed: false,
        broker_connectivity: false,
        broker_order_submission: false,
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
        workflow_admission_valid: true,
        workflow_authorized_ids: [],
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

    expect(screen.getByText(/全自动纸面交易驾驶舱/)).toBeInTheDocument();
    expect(screen.getByText('SCHEDULED_ACTIVE')).toBeInTheDocument();
    expect(screen.getByText('自主 Alpha 发现')).toBeInTheDocument();
    expect(screen.getByText('SYSTEM NATIVE')).toBeInTheDocument();
    expect(screen.getByText('SSE STREAM')).toBeInTheDocument();
    expect(screen.queryByText('BREACH')).not.toBeInTheDocument();
    expect(screen.queryByText('10s POLL')).not.toBeInTheDocument();
    expect(screen.getByText('RUNNING_EXPLORATORY')).toBeInTheDocument();
    expect(screen.getByText('EXEC PAPER 1')).toBeInTheDocument();
    expect(screen.getByText(/1 accounts · 1 routed · 3 no-action · 2 audited misses/)).toBeInTheDocument();
    expect(screen.getByText(/THIRTY_DAY_FORMAL_VALIDATION_REQUIRED/)).toBeInTheDocument();
    expect(screen.getByText('LEGACY PAPER')).toBeInTheDocument();
    expect(screen.getByText('diagnostic only · never deployment eligible')).toBeInTheDocument();
    expect(screen.getByText(/36 frozen identities \/ 26 realtime handoff/)).toBeInTheDocument();
    expect(screen.getByText(/26 · FORMAL BLINDED SIGNAL SHADOW NOT AVAILABLE/)).toBeInTheDocument();
    expect(screen.getAllByText('rt_basis_convergence_1h_v1').length).toBeGreaterThan(1);
    expect(screen.getByText('全量 Alpha 生命周期与实时业绩')).toBeInTheDocument();
    expect(screen.getByText('ADAPTIVE_EXPLORATION_READY')).toBeInTheDocument();
    expect(screen.getByText(/854 realized outcomes · 2 adaptive challengers/)).toBeInTheDocument();
    expect(screen.getAllByText('1 LOSS').length).toBeGreaterThan(1);
    expect(screen.getByText('Factor lifecycle report · SSE live')).toBeInTheDocument();
    expect(screen.getByText('Realized paper performance index')).toBeInTheDocument();
    expect(screen.getByText('100 = first decision · net of modeled costs')).toBeInTheDocument();
    expect(screen.getByText('NON-PROMOTABLE')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'LOSS 1' })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', { name: 'LOSS 1' }));
    expect(screen.getByRole('button', { name: 'LOSS 1' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('主纸面账户实时业绩')).toBeInTheDocument();
    expect(screen.getByText('Official factor lifecycle report · SSE live')).toBeInTheDocument();
    expect(screen.getByText('Retained paper equity')).toBeInTheDocument();
    expect(screen.getByText('Collecting the first paper mark.')).toBeInTheDocument();
    expect(screen.getByText('1 PROFIT')).toBeInTheDocument();
    expect(screen.getByText('1 PENDING')).toBeInTheDocument();
    expect(screen.getAllByText('LEGACY_DIAGNOSTIC_PAPER').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/CROWDING_REVERSAL · ALPHA_EXPRESSION/).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Maturity 1/8').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Lifecycle blockers: LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE/).length).toBeGreaterThan(0);
    expect(screen.getByText('影子 Challenger 实时权益与策略账簿')).toBeInTheDocument();
    expect(screen.getAllByText('$-7,416').length).toBeGreaterThan(0);
    expect(screen.getByText('3.49d')).toBeInTheDocument();
    expect(screen.getByText(/Capacity max \$875/)).toBeInTheDocument();
    expect(screen.getByText('$2,956,000')).toBeInTheDocument();
    expect(screen.getByText('$-44,000')).toBeInTheDocument();
    expect(screen.getByText(/主纸面账户与独立 Challenger 分栏/)).toBeInTheDocument();
    expect(screen.getByText('8/9')).toBeInTheDocument();
    expect(screen.getByText('78.3%')).toBeInTheDocument();
    expect(screen.getByText('0.020')).toBeInTheDocument();
    expect(screen.getByText('0.125')).toBeInTheDocument();
    expect(screen.getByText('blocked by model_alpha')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看 pending_factor 主纸面生命周期报告' }));
    const pendingReport = screen.getByTestId('official-factor-lifecycle-report');
    expect(within(pendingReport).getByText('NO_REALIZED_PNL')).toBeInTheDocument();
    expect(within(pendingReport).getAllByText('no realized paper PnL').length).toBeGreaterThan(0);
    expect(screen.getByText('连续运行与故障恢复')).toBeInTheDocument();
    expect(screen.getAllByText('6/6').length).toBeGreaterThan(0);
    expect(screen.getByText('Restart recovery')).toBeInTheDocument();
    expect(screen.getByText('Workflow authority')).toBeInTheDocument();
    expect(screen.getByText('0 candidates authorized')).toBeInTheDocument();
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
