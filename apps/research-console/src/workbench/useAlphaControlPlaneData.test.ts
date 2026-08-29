import { describe, expect, it } from 'vitest';

import type { AlphaLifecyclePayload } from '@/lib/types/research';
import type { SnapshotResult } from '@/ui/useConsoleSnapshot';
import { lifecycleEnrichment } from './useAlphaControlPlaneData';

function result(payload: AlphaLifecyclePayload): SnapshotResult<AlphaLifecyclePayload> {
  return {
    payload,
    apiState: 'connected',
    error: null,
    usingBackend: true,
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

  it('rejects retained-v1 lifecycle evidence even when labelled real', () => {
    const enrichment = lifecycleEnrichment(result(payload({
      authoritative: false,
      evidence_status: 'DIAGNOSTIC_ONLY',
      invalidation_code: 'INVALIDATED_PERP_LEDGER_V1',
    })));

    expect(enrichment).toEqual({
      status: 'UNAVAILABLE',
      payload: null,
      reason: 'AUTHORITY_DENIED:AUTHORITATIVE_FALSE',
    });
  });
});
