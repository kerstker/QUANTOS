import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MemoryRouter } from '@/lib/router';
import ValidationEvidence from '../ValidationEvidence';

const model = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));

vi.mock('@/workbench/useValidationEvidenceData', () => ({
  useValidationEvidenceData: () => model.value,
}));

const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);

function authority() {
  return {
    source: 'HOURLY_ALPHA_SQLITE_AUTHORITIES', read_only: true, api_version: 'v1',
    data_provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES', snapshot_id: HASH_A,
    snapshot_ids: [HASH_A], snapshot_status: 'REAL',
    data_quality: { status: 'REAL', provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES', snapshot_ids: [HASH_A] },
    schema_version: 'quantos.hourly-alpha-console.v1', as_of: 120, status: 'AVAILABLE',
    freshness: { status: 'CURRENT', age_seconds: 20 },
    watermark: { event_at: 100, batch_id: HASH_A },
    batch_funnel: {
      generated: 4, representatives: 2, validated: 1, blocked: 1,
      forward_decisions: 3, forward_realizations: 2,
    },
    candidate_states: [
      { ordinal: 0, candidate_id: 'candidate-01', state: 'VALIDATED', trial_id: HASH_B, artifact_commitment: HASH_A },
      { ordinal: 1, candidate_id: 'candidate-02', state: 'BLOCKED', trial_id: HASH_A, artifact_commitment: HASH_B },
    ],
    stores: {
      workflow: { status: 'AVAILABLE' }, trials: { status: 'AVAILABLE', trials: 4 },
      forward: { status: 'AVAILABLE' },
    },
    safety: {
      read_only: true, readiness: 'L2_DRY_RUN_ONLY', allow_order_submission: false,
      allow_ai_direct_trading: false, require_human_approval: true,
    },
  };
}

function evidenceModel() {
  const report = {
    run_id: 'run-1', alpha_id: 'candidate-01', decision: 'APPROVED',
    gates_passed: 5, gates_total: 6, reasons: ['DECAY_REQUIRES_REVIEW'],
    leakage_pass: true, walk_forward_pass: true, multiple_testing_pass: true,
    costs_pass: true, capacity_pass: true, decay_pass: false,
    oos_sharpe: 1.1, oos_ic: 0.03, net_sharpe: 0.8,
  };
  const experiment = {
    experiment_id: 'exp-1', alpha_id: 'candidate-01', expression: 'carry',
    owner_agent: 'ResearchAgentLoop', stage: 'VALIDATION', decision: 'APPROVED',
    created_at: 100, execution_mode: 'L2_DRY_RUN_ONLY',
    config: {
      universe: 'CRYPTO', horizon_days: 1, cost_bps: 10, seed: 0,
      data_snapshot_id: 'dataset-v1', config_hash: 'config-hash',
    },
    metrics: {
      ic: 0.03, rank_ic: 0.03, sharpe: 0.8, turnover: 0.2,
      capacity_estimate: 12_000_000, composite_score: 0.7,
    },
    reproducibility_hash: 'repro-hash',
    artifacts: [{
      artifact_id: 'artifact-1', kind: 'EVALUATION', status: 'SEALED',
      uri: 'experiment://exp-1', content_hash: 'content-hash',
    }],
    lineage: { upstream: ['factor_engine'], downstream: ['validation'] },
  };
  return {
    authority: { phase: 'AVAILABLE', snapshot: authority(), reason: null },
    validation: {
      status: 'AVAILABLE', reason: null,
      payload: {
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
        data_provenance: 'REAL_CRYPTO_SERIES', selected_report: report,
        reports: [report], lineage: [],
      },
    },
    experiments: {
      status: 'AVAILABLE', reason: null,
      payload: {
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
        data_provenance: 'EXPERIMENT_REGISTRY', registry_mode: 'READ_ONLY_RESEARCH_REGISTRY',
        summary: {
          total_experiments: 1, approved: 1, rejected: 0, needs_more_data: 0,
          artifact_count: 1, reproducible: 1,
        },
        experiments: [experiment], stage_counts: { VALIDATION: 1 },
        lineage_policy: {
          requires_config_hash: true, requires_data_snapshot: true,
          requires_metric_artifact: true, mutable_records: false,
        },
      },
    },
  };
}

function renderPage() {
  return render(<MemoryRouter initialEntries={['/workbench/validation']}><ValidationEvidence /></MemoryRouter>);
}

describe('ValidationEvidence', () => {
  beforeEach(() => { model.value = evidenceModel(); });

  it('shows authority binding, labelled verdicts, auxiliary falsification, backtest, cost/capacity and forward gap', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: '验证证据' })).toBeTruthy();
    expect(screen.getByText('PASS · RESEARCH VALIDATION')).toBeTruthy();
    expect(screen.getByText(HASH_B)).toBeTruthy();
    expect(screen.getByText(HASH_A)).toBeTruthy();
    expect(screen.getAllByText('quantos.hourly-alpha-console.v1').length).toBeGreaterThan(0);
    expect(screen.getByText(/CHEAP.*UNAVAILABLE_IN_V1/)).toBeTruthy();
    expect(screen.getByText(/^DECAY.*BLOCK$/)).toBeTruthy();
    expect(screen.getByText(/OOS Sharpe 1\.100/)).toBeTruthy();
    expect(screen.getByText(/10 bps/)).toBeTruthy();
    expect(screen.getByText(/\$12,000,000/)).toBeTruthy();
    expect(screen.getByText('config-hash')).toBeTruthy();
    expect(screen.getByText('repro-hash')).toBeTruthy();
    expect(screen.getByText('content-hash')).toBeTruthy();
    expect(screen.getByText(/3 decisions.*2 realizations.*gap 1/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /approve|submit|buy|sell|批准|下单/i })).toBeNull();
  });

  it('switches candidates without borrowing unmatched auxiliary evidence', () => {
    renderPage();
    fireEvent.change(screen.getByRole('combobox', { name: '选择候选' }), {
      target: { value: 'candidate-02' },
    });
    expect(screen.getByText('BLOCK · RESEARCH VALIDATION')).toBeTruthy();
    expect(screen.getAllByText(/UNAVAILABLE · NO_EXACT_MATCH/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/OOS Sharpe 1\.100/)).toBeNull();
    expect(screen.queryByText('config-hash')).toBeNull();
  });

  it('makes malformed, n-mismatch, self-reported positive, stale, and missing policy outcomes explicit', () => {
    renderPage();
    expect(screen.getByText(/MALFORMED.*BLOCK/)).toBeTruthy();
    expect(screen.getByText(/N_MISMATCH.*BLOCK/)).toBeTruthy();
    expect(screen.getByText(/SELF_REPORTED_POSITIVE.*BLOCK/)).toBeTruthy();
    expect(screen.getByText(/STALE.*UNAVAILABLE/)).toBeTruthy();
    expect(screen.getByText(/MISSING.*UNAVAILABLE/)).toBeTruthy();
  });

  it('fails closed and hides retained evidence when the authority is stale or unavailable', () => {
    model.value = {
      ...evidenceModel(),
      authority: { phase: 'STALE', snapshot: authority(), reason: 'FRESHNESS_STALE' },
    };
    renderPage();
    expect(screen.getByText('VALIDATION_AUTHORITY_STALE')).toBeTruthy();
    expect(screen.getByText('DIAGNOSTIC_ONLY')).toBeTruthy();
    expect(screen.queryByText('PASS · RESEARCH VALIDATION')).toBeNull();
    expect(screen.queryByText('config-hash')).toBeNull();
  });

  it('shows auxiliary sources as unavailable instead of using synthetic or malformed fallback', () => {
    const data = evidenceModel();
    data.validation = { status: 'UNAVAILABLE', reason: 'PROVENANCE:SYNTHETIC_DETERMINISTIC', payload: null } as never;
    data.experiments = { status: 'UNAVAILABLE', reason: 'CONTRACT:EXPERIMENT_INVALID', payload: null } as never;
    model.value = data;
    renderPage();
    expect(screen.getAllByText(/UNAVAILABLE · NO_EXACT_MATCH/).length).toBeGreaterThan(0);
    expect(screen.getByText(/SYNTHETIC_DETERMINISTIC/)).toBeTruthy();
    expect(screen.getByText(/CONTRACT:EXPERIMENT_INVALID/)).toBeTruthy();
    expect(screen.queryByText('config-hash')).toBeNull();
  });
});
