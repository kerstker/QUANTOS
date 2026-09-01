import { describe, expect, it } from 'vitest';

import type {
  AlphaLifecyclePayload,
  AShareInstitutionalSearchPayload,
} from '@/lib/types/research';
import type { SnapshotResult } from '@/ui/useConsoleSnapshot';
import { ashareEnrichment, lifecycleEnrichment } from './useAlphaControlPlaneData';

function result(payload: AlphaLifecyclePayload): SnapshotResult<AlphaLifecyclePayload> {
  return {
    payload,
    apiState: 'connected',
    error: null,
    usingBackend: true,
    mockFixturesAllowed: false,
    snapshotState: 'REAL',
    actionsAllowed: true,
  };
}

function payload(overrides: Partial<AlphaLifecyclePayload> = {}): AlphaLifecyclePayload {
  return {
    source: 'BACKEND_COMPUTED_SNAPSHOT',
    read_only: true,
    api_version: 'v1',
    data_provenance: 'REAL_ALPHA_LIFECYCLE',
    snapshot_status: 'REAL',
    snapshot_present: true,
    artifact: 'ALPHA_LIFECYCLE_COCKPIT',
    generated_at_ms: 1,
    readiness: 'L2_DRY_RUN_ONLY',
    alphas: [],
    stage_legend: {},
    honesty_note: 'test',
    ...overrides,
  };
}

describe('alpha lifecycle enrichment authority boundary', () => {
  it('accepts a present real snapshot only when it has no authority denial', () => {
    expect(lifecycleEnrichment(result(payload())).status).toBe('AVAILABLE');
  });

  it('keeps retained-v1 lifecycle identities visible but labels them diagnostic', () => {
    const retained = payload({
      authoritative: false,
      evidence_status: 'DIAGNOSTIC_ONLY',
      invalidation_code: 'INVALIDATED_PERP_LEDGER_V1',
    });
    const enrichment = lifecycleEnrichment(result(retained));

    expect(enrichment).toEqual({
      status: 'AVAILABLE',
      payload: retained,
      reason: 'VISIBLE_DIAGNOSTIC:AUTHORITATIVE_FALSE',
    });
  });
});

function ashareResult(
  dataProvenance: string,
): SnapshotResult<AShareInstitutionalSearchPayload> {
  return {
    payload: {
      source: 'BACKEND_COMPUTED_SNAPSHOT',
      read_only: true,
      api_version: '0.1.0',
      data_provenance: dataProvenance,
      snapshot_present: true,
      authoritative: false,
      diagnostic_only: true,
      evidence_status: 'TECHNICAL_VALIDATION_ONLY',
      availability_mode: 'READ_ONLY_TECHNICAL_RESEARCH',
      candidate_usefulness: 'NOT_VALIDATED',
      source_summary: {},
      surface_coverage: {},
      surface_results: [],
      untouched_forward_observation: {},
      limitations: [],
    },
    apiState: 'connected',
    error: null,
    usingBackend: true,
    mockFixturesAllowed: false,
    snapshotState: 'REAL',
    actionsAllowed: false,
  };
}

describe('A-share technical search enrichment', () => {
  it.each(['REPOSITORY_EVIDENCE', 'REAL_ASHARE_TECHNICAL_RUNTIME'])(
    'accepts the admitted read-only provenance %s',
    provenance => {
      expect(ashareEnrichment(ashareResult(provenance))).toMatchObject({
        status: 'AVAILABLE',
        reason: null,
      });
    },
  );

  it('rejects an unrelated provenance marker', () => {
    expect(ashareEnrichment(ashareResult('REAL_UNRELATED_RUNTIME')).status).toBe(
      'UNAVAILABLE',
    );
  });
});
