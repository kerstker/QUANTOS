import { describe, expect, it } from 'vitest';

import {
  classifyHourlyAlphaSnapshot,
  parseHourlyAlphaSnapshot,
} from '../hourly-alpha-contract';

const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);

function makeSnapshot(overrides: Record<string, unknown> = {}) {
  const base = {
    source: 'HOURLY_ALPHA_SQLITE_AUTHORITIES',
    read_only: true,
    api_version: 'v1',
    data_provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES',
    snapshot_id: HASH_A,
    snapshot_ids: [HASH_A],
    snapshot_status: 'REAL',
    data_quality: {
      status: 'REAL',
      provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES',
      snapshot_ids: [HASH_A],
    },
    schema_version: 'quantos.hourly-alpha-console.v1',
    as_of: 120,
    status: 'AVAILABLE',
    freshness: { status: 'CURRENT', age_seconds: 20 },
    watermark: { event_at: 100, batch_id: HASH_A },
    batch_funnel: {
      generated: 3,
      representatives: 2,
      validated: 1,
      blocked: 0,
      forward_decisions: 1,
      forward_realizations: 0,
    },
    candidate_states: [
      { ordinal: 0, candidate_id: HASH_A, state: 'VALIDATED', trial_id: HASH_B, artifact_commitment: HASH_A },
      { ordinal: 1, candidate_id: HASH_B, state: 'QUEUED', trial_id: null, artifact_commitment: null },
    ],
    stores: {
      workflow: { status: 'AVAILABLE' },
      trials: { status: 'AVAILABLE', trials: 1 },
      forward: { status: 'AVAILABLE' },
    },
    safety: {
      read_only: true,
      readiness: 'L2_DRY_RUN_ONLY',
      allow_order_submission: false,
      allow_ai_direct_trading: false,
      require_human_approval: true,
    },
  };
  return { ...base, ...overrides };
}

describe('hourly alpha authority contract', () => {
  it('accepts a complete Wave 1 authority snapshot and classifies it as available', () => {
    const snapshot = parseHourlyAlphaSnapshot(makeSnapshot());
    expect(snapshot.candidate_states).toHaveLength(2);
    expect(classifyHourlyAlphaSnapshot(snapshot)).toEqual({ phase: 'AVAILABLE', reason: null });
  });

  it('rejects safety drift and cross-field funnel drift', () => {
    expect(() => parseHourlyAlphaSnapshot(makeSnapshot({
      safety: {
        read_only: true,
        readiness: 'L2_DRY_RUN_ONLY',
        allow_order_submission: true,
        allow_ai_direct_trading: false,
        require_human_approval: true,
      },
    }))).toThrow(/SAFETY/);

    expect(() => parseHourlyAlphaSnapshot(makeSnapshot({
      batch_funnel: {
        generated: 3,
        representatives: 9,
        validated: 1,
        blocked: 0,
        forward_decisions: 1,
        forward_realizations: 0,
      },
    }))).toThrow(/FUNNEL/);
  });

  it('accepts an honest blocked response but classifies it unavailable', () => {
    const blocked = parseHourlyAlphaSnapshot(makeSnapshot({
      snapshot_status: 'UNAVAILABLE',
      data_quality: {
        status: 'UNAVAILABLE',
        provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES',
        snapshot_ids: [HASH_A],
      },
      status: 'BLOCKED',
      freshness: { status: 'UNAVAILABLE', age_seconds: null },
      watermark: null,
      batch_funnel: null,
      candidate_states: [],
      stores: {
        workflow: { status: 'AVAILABLE' },
        trials: { status: 'UNAVAILABLE', reason: 'FileNotFoundError' },
        forward: { status: 'AVAILABLE' },
      },
    }));
    expect(classifyHourlyAlphaSnapshot(blocked)).toEqual({
      phase: 'UNAVAILABLE',
      reason: 'TRIALS:FileNotFoundError',
    });
  });

  it('keeps stale and no-snapshot authority states explicit', () => {
    const stale = parseHourlyAlphaSnapshot(makeSnapshot({
      snapshot_status: 'STALE',
      data_quality: {
        status: 'STALE', provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES', snapshot_ids: [HASH_A],
      },
      freshness: { status: 'STALE', age_seconds: 9_000 },
    }));
    expect(classifyHourlyAlphaSnapshot(stale)).toEqual({ phase: 'STALE', reason: 'FRESHNESS_STALE' });

    const empty = parseHourlyAlphaSnapshot(makeSnapshot({
      snapshot_id: null,
      snapshot_ids: [],
      snapshot_status: 'NO_SNAPSHOT',
      data_quality: {
        status: 'NO_SNAPSHOT', provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES', snapshot_ids: [],
      },
      freshness: { status: 'NO_RUN', age_seconds: null },
      watermark: null,
      batch_funnel: {
        generated: 0, representatives: 0, validated: 0, blocked: 0,
        forward_decisions: 0, forward_realizations: 0,
      },
      candidate_states: [],
    }));
    expect(classifyHourlyAlphaSnapshot(empty)).toEqual({ phase: 'NO_SNAPSHOT', reason: 'NO_RUN' });
  });
});
