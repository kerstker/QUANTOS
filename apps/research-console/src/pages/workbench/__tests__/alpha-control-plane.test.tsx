import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MemoryRouter } from '@/lib/router';
import AlphaControlPlane from '../AlphaControlPlane';

const model = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));

vi.mock('@/workbench/useAlphaControlPlaneData', () => ({
  useAlphaControlPlaneData: () => model.value,
}));

const HASH_A = 'a'.repeat(64);

function snapshot(count = 2) {
  const candidates = Array.from({ length: count }, (_, ordinal) => ({
    ordinal,
    candidate_id: `candidate-${String(ordinal + 1).padStart(2, '0')}`,
    state: ordinal === 0 ? 'VALIDATED' : 'QUEUED',
    trial_id: ordinal === 0 ? `trial-${ordinal + 1}` : null,
    artifact_commitment: ordinal === 0 ? HASH_A : null,
  }));
  return {
    source: 'HOURLY_ALPHA_SQLITE_AUTHORITIES', read_only: true, api_version: 'v1',
    data_provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES', snapshot_id: HASH_A,
    snapshot_ids: [HASH_A], snapshot_status: 'REAL',
    data_quality: { status: 'REAL', provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES', snapshot_ids: [HASH_A] },
    schema_version: 'quantos.hourly-alpha-console.v1', as_of: 120, status: 'AVAILABLE',
    freshness: { status: 'CURRENT', age_seconds: 20 },
    watermark: { event_at: 100, batch_id: HASH_A },
    batch_funnel: {
      generated: count + 4, representatives: count, validated: 1, blocked: 0,
      forward_decisions: 1, forward_realizations: 0,
    },
    candidate_states: candidates,
    stores: {
      workflow: { status: 'AVAILABLE' },
      trials: { status: 'AVAILABLE', trials: 4 },
      forward: { status: 'AVAILABLE' },
    },
    safety: {
      read_only: true, readiness: 'L2_DRY_RUN_ONLY', allow_order_submission: false,
      allow_ai_direct_trading: false, require_human_approval: true,
    },
  };
}

function availableModel(count = 2) {
  return {
    authority: { phase: 'AVAILABLE', snapshot: snapshot(count), reason: null },
    zoo: {
      status: 'AVAILABLE', reason: null,
      payload: {
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: 'v1',
        data_provenance: 'EXPERIMENT_REGISTRY', dimensions: [],
        rows: [{
          alpha_id: 'candidate-01', name: 'Carry family candidate', generator: 'factor-garden',
          status: 'VALIDATED', cluster_id: 'carry-family', tags: ['carry'], ic: 0.031,
          net_sharpe: 1.1, deflated_sharpe: 0.8, paper_ic: 0.022,
          paper_sharpe: 0.7, n_paper_periods: 12,
        }],
      },
    },
    lifecycle: {
      status: 'AVAILABLE', reason: null,
      payload: {
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true,
        data_provenance: 'REAL_ALPHA_LIFECYCLE', snapshot_present: true,
        artifact: 'ALPHA_LIFECYCLE_COCKPIT', generated_at_ms: 1,
        readiness: 'L2_DRY_RUN_ONLY', stage_legend: {}, honesty_note: '',
        alphas: [{
          alpha_id: 'candidate-01', display_name: 'Carry family candidate',
          derived_stage: 'PAPER', lifecycle_state: 'PAPER', stages: {}, warnings: [],
        }],
      },
    },
    ashare: {
      status: 'AVAILABLE', reason: null,
      payload: {
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
        data_provenance: 'REPOSITORY_EVIDENCE', snapshot_present: true,
        authoritative: false, diagnostic_only: true,
        evidence_status: 'TECHNICAL_VALIDATION_ONLY',
        availability_mode: 'READ_ONLY_TECHNICAL_RESEARCH', generated_at: '2026-08-31T00:00:00Z',
        candidate_usefulness: 'NOT_VALIDATED',
        source_summary: { instruments: 5549, rows: 6267665, first_date: 20210802, last_date: 20260828 },
        trial_accounting: { candidate_specs: 183, factor_definitions: 61 },
        surface_coverage: {
          value: { status: 'BLOCKED_MISSING_HISTORICAL_PIT', reason: 'no PIT valuation history' },
          capital_flow: { status: 'PARTIAL_SIGNED_AMOUNT_PROXY_ONLY', reason: 'daily amount only' },
        },
        surface_results: [{
          surface: 'capital_flow_proxy',
          candidate: { name: 'flow_h5', family: 'signed_amount_flow', window: 5, skip: 0, hold_days: 5 },
          stable_positive_before_confirmation: true,
          confirmation_excess_sharpe: -0.2, confirmation_excess_return: -0.01,
          confirmation_median_excess_sharpe: -0.3, bonferroni_adjusted_p: 1,
          candidate_usefulness: 'NOT_VALIDATED',
        }],
        untouched_forward_observation: {
          status: 'ARMED_AWAITING_NEW_SESSIONS', freeze_cutoff: 20260828,
          minimum_sessions: 60, selection_locked: true,
        },
        limitations: ['survivor bias'],
      },
    },
  };
}

function renderPage() {
  return render(<MemoryRouter initialEntries={['/workbench/alpha']}><AlphaControlPlane /></MemoryRouter>);
}

describe('AlphaControlPlane', () => {
  beforeEach(() => { model.value = availableModel(); });

  it('aggregates the authority funnel, candidate evidence, family, OOS, and lifecycle without implying capital approval', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: 'Alpha 控制平面' })).toBeTruthy();
    expect(screen.getByText('6')).toBeTruthy();
    expect(screen.getByText(/研究验证.*非资金批准/)).toBeTruthy();
    expect(screen.getByText('carry-family')).toBeTruthy();
    expect(screen.getByText(/IC 0\.031/)).toBeTruthy();
    expect(screen.getAllByText('PAPER')).toHaveLength(2);
    expect(screen.getAllByRole('link', { name: /validation evidence/i })).toHaveLength(2);
    expect(screen.getAllByText('REAL_HOURLY_ALPHA_AUTHORITIES').length).toBeGreaterThan(1);
    expect(screen.queryByRole('button', { name: /approve|submit|buy|sell|批准|下单/i })).toBeNull();
  });

  it('fails closed when Trial Ledger or forward authority is unavailable', () => {
    const current = snapshot();
    const blocked = {
      ...current,
      status: 'BLOCKED',
      snapshot_status: 'UNAVAILABLE',
      freshness: { status: 'UNAVAILABLE', age_seconds: null },
      batch_funnel: null,
      candidate_states: [],
      stores: {
        ...current.stores,
        trials: { status: 'UNAVAILABLE', reason: 'FileNotFoundError' },
      },
    };
    model.value = {
      ...availableModel(),
      authority: { phase: 'UNAVAILABLE', snapshot: blocked, reason: 'TRIALS:FileNotFoundError' },
    };
    renderPage();
    expect(screen.getByText('ALPHA_AUTHORITY_UNAVAILABLE')).toBeTruthy();
    expect(screen.getByText(/TRIALS:FileNotFoundError/)).toBeTruthy();
    expect(screen.getByText('DIAGNOSTIC_ONLY')).toBeTruthy();
    expect(screen.queryByText('candidate-01')).toBeNull();
  });

  it('does not render retained candidates when the authority snapshot is stale', () => {
    const stale = {
      ...snapshot(),
      snapshot_status: 'STALE',
      freshness: { status: 'STALE', age_seconds: 9_000 },
    };
    model.value = {
      ...availableModel(),
      authority: { phase: 'STALE', snapshot: stale, reason: 'FRESHNESS_STALE' },
    };
    renderPage();
    expect(screen.getByText('ALPHA_AUTHORITY_STALE')).toBeTruthy();
    expect(screen.getByText('DIAGNOSTIC_ONLY')).toBeTruthy();
    expect(screen.queryByText('candidate-01')).toBeNull();
  });

  it('labels the hard V1 quarantine as expected safe isolation instead of a transport error', () => {
    const blocked = {
      ...snapshot(), status: 'BLOCKED', snapshot_status: 'UNAVAILABLE',
      freshness: { status: 'UNAVAILABLE', age_seconds: null }, watermark: null,
      batch_funnel: null, candidate_states: [],
      stores: {
        workflow: { status: 'UNAVAILABLE', reason: 'INVALIDATED_PERP_LEDGER_V1' },
        trials: { status: 'UNAVAILABLE', reason: 'INVALIDATED_PERP_LEDGER_V1' },
        forward: { status: 'UNAVAILABLE', reason: 'INVALIDATED_PERP_LEDGER_V1' },
      },
      authority_mode: 'QUARANTINED_LEGACY_AUTHORITY',
      invalidation_code: 'INVALIDATED_PERP_LEDGER_V1',
    };
    model.value = {
      ...availableModel(),
      authority: { phase: 'QUARANTINED', snapshot: blocked, reason: 'INVALIDATED_PERP_LEDGER_V1' },
    };
    renderPage();
    expect(screen.getByText('ALPHA_TECHNICAL_SEARCH_AVAILABLE')).toBeTruthy();
    expect(screen.getByText('READ_ONLY_PIT_SEARCH')).toBeTruthy();
    expect(screen.getByText('ALPHA_V1_AUTHORITY_QUARANTINED')).toBeTruthy();
    expect(screen.getByText('EXPECTED_SAFE_ISOLATION')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'A股机构因子搜索' })).toBeTruthy();
    expect(screen.getByText('5549')).toBeTruthy();
    expect(screen.getByText(/ARMED_AWAITING_NEW_SESSIONS/)).toBeTruthy();
    expect(screen.queryByText('ALPHA_AUTHORITY_UNAVAILABLE')).toBeNull();
  });

  it('supports filtering, bounded pagination, and arrow-key row targeting', () => {
    model.value = availableModel(30);
    renderPage();
    expect(screen.getAllByTestId('candidate-row')).toHaveLength(25);

    const first = screen.getAllByTestId('candidate-row')[0];
    const second = screen.getAllByTestId('candidate-row')[1];
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(second);

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getAllByTestId('candidate-row')).toHaveLength(5);
    expect(screen.getByText('candidate-30')).toBeTruthy();

    fireEvent.change(screen.getByRole('searchbox', { name: '筛选候选' }), {
      target: { value: 'candidate-03' },
    });
    expect(screen.getAllByTestId('candidate-row')).toHaveLength(1);
    expect(screen.getByText('candidate-03')).toBeTruthy();
  });
});
