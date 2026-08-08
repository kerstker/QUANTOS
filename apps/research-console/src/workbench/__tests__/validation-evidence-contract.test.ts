import { describe, expect, it } from 'vitest';

import {
  parseExperimentsEvidence,
  parseValidationEvidence,
} from '../validation-evidence-contract';
import {
  admitExperimentsEvidence,
  admitValidationEvidence,
} from '../useValidationEvidenceData';

function validation(overrides: Record<string, unknown> = {}) {
  const report = {
    run_id: 'run-1', alpha_id: 'candidate-01', decision: 'APPROVED',
    gates_passed: 5, gates_total: 6, reasons: [],
    leakage_pass: true, walk_forward_pass: true, multiple_testing_pass: true,
    costs_pass: true, capacity_pass: true, decay_pass: false,
    oos_sharpe: 1.1, oos_ic: 0.03, net_sharpe: 0.8,
  };
  return {
    source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
    data_provenance: 'REAL_CRYPTO_SERIES', selected_report: report,
    reports: [report], lineage: [], ...overrides,
  };
}

function experiments(overrides: Record<string, unknown> = {}) {
  const row = {
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
    source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
    data_provenance: 'EXPERIMENT_REGISTRY', registry_mode: 'READ_ONLY_RESEARCH_REGISTRY',
    summary: {
      total_experiments: 1, approved: 1, rejected: 0, needs_more_data: 0,
      artifact_count: 1, reproducible: 1,
    },
    experiments: [row], stage_counts: { VALIDATION: 1 },
    lineage_policy: {
      requires_config_hash: true, requires_data_snapshot: true,
      requires_metric_artifact: true, mutable_records: false,
    },
    ...overrides,
  };
}

describe('validation evidence auxiliary contracts', () => {
  it('accepts complete real validation and immutable experiment evidence', () => {
    expect(parseValidationEvidence(validation()).reports).toHaveLength(1);
    expect(parseExperimentsEvidence(experiments()).experiments).toHaveLength(1);
  });

  it('rejects malformed gate counts, non-finite metrics, and selected-report drift', () => {
    const malformed = validation();
    const report = { ...(malformed.reports as Array<Record<string, unknown>>)[0], gates_passed: 7 };
    expect(() => parseValidationEvidence({
      ...malformed, selected_report: report, reports: [report],
    })).toThrow(/REPORT/);

    const drift = validation();
    expect(() => parseValidationEvidence({
      ...drift, selected_report: { ...(drift.selected_report as object), run_id: 'other' },
    })).toThrow(/SELECTED/);

    const infinite = validation();
    const bad = { ...(infinite.reports as Array<Record<string, unknown>>)[0], oos_sharpe: Infinity };
    expect(() => parseValidationEvidence({ ...infinite, selected_report: bad, reports: [bad] }))
      .toThrow(/REPORT/);
  });

  it('rejects mutable, count-drifted, or non-L2 experiment registries', () => {
    const mutable = experiments();
    expect(() => parseExperimentsEvidence({
      ...mutable,
      lineage_policy: { ...(mutable.lineage_policy as object), mutable_records: true },
    })).toThrow(/POLICY/);

    const countDrift = experiments();
    expect(() => parseExperimentsEvidence({
      ...countDrift,
      summary: { ...(countDrift.summary as object), artifact_count: 9 },
    })).toThrow(/SUMMARY/);

    const nonL2 = experiments();
    const row = { ...(nonL2.experiments as Array<Record<string, unknown>>)[0], execution_mode: 'LIVE' };
    expect(() => parseExperimentsEvidence({ ...nonL2, experiments: [row] })).toThrow(/EXPERIMENT/);
  });

  it('admits only real auxiliary provenance and fails closed on malformed bodies', () => {
    const result = (payload: unknown) => ({
      payload, apiState: 'connected', error: null, usingBackend: true,
      snapshotState: 'REAL', actionsAllowed: true,
    });
    expect(admitValidationEvidence(result(validation()) as never).status).toBe('AVAILABLE');
    expect(admitValidationEvidence(result({
      ...validation(), data_provenance: 'SYNTHETIC_DETERMINISTIC',
    }) as never)).toMatchObject({ status: 'UNAVAILABLE', reason: 'PROVENANCE:SYNTHETIC_DETERMINISTIC' });
    expect(admitValidationEvidence(result({ ...validation(), reports: [] }) as never).reason)
      .toMatch(/^CONTRACT:/);

    expect(admitExperimentsEvidence(result(experiments()) as never).status).toBe('AVAILABLE');
    expect(admitExperimentsEvidence(result({
      ...experiments(), snapshot_status: 'STALE',
    }) as never)).toMatchObject({ status: 'UNAVAILABLE', reason: 'SNAPSHOT_STATUS:STALE' });
  });
});
