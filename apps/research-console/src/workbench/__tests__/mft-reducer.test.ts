import { describe, expect, it } from 'vitest';

import {
  MFT_CONSOLE_SCHEMA,
  type MftDelta,
  type MftDeltaEvent,
} from '../contracts';
import { applyMftDelta, canonicalMftJson, stableMftHash } from '../mft-reducer';
import { makeMftSnapshot } from './mft-fixtures';

const ZERO_HASH = '0'.repeat(64);

async function event(overrides: Partial<MftDeltaEvent> = {}): Promise<MftDeltaEvent> {
  const payload = overrides.payload ?? { status: 'RUNNING', next_rebalance_at_ms: 3_000 };
  const payloadHash = await stableMftHash(payload);
  const base = {
    sequence: 1,
    event_id: 'scheduler.1',
    section: 'scheduler' as const,
    event_at_ms: 2_500,
    payload,
    payload_hash_input: canonicalMftJson(payload),
    payload_hash: payloadHash,
    prev_hash: ZERO_HASH,
  };
  const fields = { ...base, ...overrides };
  return {
    ...fields,
    event_hash: overrides.event_hash ?? await stableMftHash({
      sequence: fields.sequence,
      event_id: fields.event_id,
      section: fields.section,
      event_at_ms: fields.event_at_ms,
      payload_hash: fields.payload_hash,
      prev_hash: fields.prev_hash,
    }),
  };
}

function delta(change: MftDeltaEvent, overrides: Record<string, unknown> = {}): MftDelta {
  const cursor = `${change.sequence}:${change.event_hash}`;
  const prior = makeMftSnapshot();
  const metadata = {
    source: prior.source,
    read_only: true,
    api_version: 'v1',
    data_provenance: prior.data_provenance,
    snapshot_id: change.event_hash,
    snapshot_ids: [change.event_hash],
    snapshot_status: 'REAL',
    data_quality: {
      status: 'REAL',
      provenance: prior.data_provenance,
      snapshot_ids: [change.event_hash],
    },
    schema_version: MFT_CONSOLE_SCHEMA,
    as_of_ms: 3_000,
    status: 'AVAILABLE',
    freshness: { ...prior.freshness, age_ms: 500 },
    watermark: {
      sequence: change.sequence,
      event_at_ms: change.event_at_ms,
      event_hash: change.event_hash,
    },
    cursor,
    missing_sections: [],
    safety: prior.safety,
  };
  return {
    source: prior.source,
    read_only: true,
    api_version: 'v1',
    data_provenance: prior.data_provenance,
    schema_version: MFT_CONSOLE_SCHEMA,
    status: 'OK',
    from_cursor: prior.cursor as string,
    cursor,
    as_of_ms: 3_000,
    events: [change],
    has_more: false,
    result_metadata: metadata,
    ...overrides,
  } as MftDelta;
}

describe('applyMftDelta', () => {
  it('matches the Python stable_hash canonical vector', async () => {
    expect(await stableMftHash({
      status: '运行',
      ratio: 0.1234567890123,
      count: 3,
      items: [true, null, 'x'],
    })).toBe('eb8408a212508558c7296fcf82f25c744ae0376c091033bafc9e5bcb68a5c68d');
  });

  it('rebuilds the same replacement-state snapshot and advances the cursor', async () => {
    const prior = makeMftSnapshot();
    const change = await event();
    const result = await applyMftDelta(prior, delta(change));
    expect(result.status).toBe('APPLIED');
    if (result.status !== 'APPLIED') return;
    expect(result.snapshot.scheduler).toEqual(change.payload);
    expect(result.snapshot.cursor).toBe(`1:${change.event_hash}`);
    expect(prior.scheduler).not.toEqual(change.payload);
  });

  it('accepts the backend canonical hash input when JSON loses the 1.0 float tag', async () => {
    const change = await event({
      payload: { fill_ratio: 1 },
      payload_hash_input: '{"fill_ratio":1.0}',
      payload_hash: 'f6eff70c513aab222e4f21b088eabb62dac5032ad60359c2a5b82a2b9396c8aa',
    });
    const result = await applyMftDelta(makeMftSnapshot(), delta(change));
    expect(result.status).toBe('APPLIED');
  });

  it.each([
    ['sequence gap', { sequence: 2 }],
    ['payload hash mismatch', { payload_hash: '1'.repeat(64) }],
    ['payload/hash-input mismatch', { payload_hash_input: '{"status":"TAMPERED"}' }],
    ['previous hash mismatch', { prev_hash: '2'.repeat(64) }],
    ['unknown section', { section: 'secrets' as MftDeltaEvent['section'] }],
    ['event time rollback', { event_at_ms: 999 }],
  ])('requires a fresh snapshot on %s without mutating the prior snapshot', async (_label, overrides) => {
    const prior = makeMftSnapshot();
    const before = JSON.stringify(prior);
    const change = await event(overrides);
    const result = await applyMftDelta(prior, delta(change));
    expect(result.status).toBe('RESNAPSHOT_REQUIRED');
    expect(JSON.stringify(prior)).toBe(before);
  });

  it('rejects a terminal cursor mismatch and incomplete result metadata', async () => {
    const change = await event();
    expect((await applyMftDelta(
      makeMftSnapshot(),
      delta(change, { cursor: `1:${'f'.repeat(64)}` }),
    )).status).toBe('RESNAPSHOT_REQUIRED');
    const broken = delta(change);
    delete (broken.result_metadata as Record<string, unknown>).freshness;
    expect((await applyMftDelta(makeMftSnapshot(), broken)).status).toBe('RESNAPSHOT_REQUIRED');
  });

  it('propagates an authority resnapshot response without applying events', async () => {
    const result = await applyMftDelta(makeMftSnapshot(), {
      schema_version: MFT_CONSOLE_SCHEMA,
      status: 'RESNAPSHOT_REQUIRED',
      reason: 'CURSOR_GAP',
      refetch: '/api/v1/console/mft-execution',
      read_only: true,
    });
    expect(result).toEqual({ status: 'RESNAPSHOT_REQUIRED', reason: 'CURSOR_GAP' });
  });
});
