import { describe, expect, it, vi } from 'vitest';

import {
  MFT_CONSOLE_SCHEMA,
  type MftDelta,
  type MftDeltaEvent,
  type MftSnapshot,
} from '../contracts';
import { canonicalMftJson, stableMftHash } from '../mft-reducer';
import {
  mergeMftEventTape,
  parseMftStreamNotice,
  synchronizeMftReadModel,
  type MftSyncTransport,
} from '../mft-sync';
import { makeMftSnapshot } from './mft-fixtures';

function metadata(snapshot: MftSnapshot): Record<string, unknown> {
  const copy = JSON.parse(JSON.stringify(snapshot)) as Record<string, unknown>;
  for (const section of [
    'market_gap', 'scheduler', 'intent_funnel', 'oms', 'fills', 'positions',
    'risk', 'reconciliation', 'tca', 'mandate_expiry',
  ]) delete copy[section];
  return copy;
}

function emptyDelta(snapshot: MftSnapshot): MftDelta {
  return {
    source: 'MFT_READ_MODEL_SQLITE_AUTHORITY',
    read_only: true,
    api_version: 'v1',
    data_provenance: 'REAL_MFT_READ_MODEL',
    schema_version: MFT_CONSOLE_SCHEMA,
    status: 'OK',
    from_cursor: snapshot.cursor as string,
    cursor: snapshot.cursor as string,
    as_of_ms: snapshot.as_of_ms,
    events: [],
    has_more: false,
    result_metadata: metadata(snapshot),
  };
}

async function nextSnapshot(prior: MftSnapshot): Promise<{ delta: MftDelta; snapshot: MftSnapshot }> {
  const baseCursor = prior.cursor as string;
  const [sequenceText, previousHash] = baseCursor.split(':');
  const payload = { status: 'COMPLETED', next_rebalance_at_ms: 4_000 };
  const payloadHashInput = canonicalMftJson(payload);
  const payloadHash = await stableMftHash(payload);
  const fields = {
    sequence: Number(sequenceText) + 1,
    event_id: 'scheduler.next',
    section: 'scheduler' as const,
    event_at_ms: 3_500,
    payload,
    payload_hash_input: payloadHashInput,
    payload_hash: payloadHash,
    prev_hash: previousHash,
  };
  const change: MftDeltaEvent = {
    ...fields,
    event_hash: await stableMftHash({
      sequence: fields.sequence,
      event_id: fields.event_id,
      section: fields.section,
      event_at_ms: fields.event_at_ms,
      payload_hash: fields.payload_hash,
      prev_hash: fields.prev_hash,
    }),
  };
  const cursor = `${change.sequence}:${change.event_hash}`;
  const snapshot = makeMftSnapshot({
    scheduler: payload,
    snapshot_id: change.event_hash,
    snapshot_ids: [change.event_hash],
    data_quality: {
      status: 'REAL',
      provenance: 'REAL_MFT_READ_MODEL',
      snapshot_ids: [change.event_hash],
    },
    as_of_ms: 4_000,
    watermark: {
      sequence: change.sequence,
      event_at_ms: change.event_at_ms,
      event_hash: change.event_hash,
    },
    cursor,
    freshness: { ...prior.freshness, age_ms: 500 },
  });
  return {
    snapshot,
    delta: {
      source: 'MFT_READ_MODEL_SQLITE_AUTHORITY',
      read_only: true,
      api_version: 'v1',
      data_provenance: 'REAL_MFT_READ_MODEL',
      schema_version: MFT_CONSOLE_SCHEMA,
      status: 'OK',
      from_cursor: baseCursor,
      cursor,
      as_of_ms: snapshot.as_of_ms,
      events: [change],
      has_more: false,
      result_metadata: metadata(snapshot),
    },
  };
}

describe('synchronizeMftReadModel', () => {
  it('loads a full snapshot and catches up through the authoritative delta', async () => {
    const initial = makeMftSnapshot();
    const advanced = await nextSnapshot(initial);
    const transport: MftSyncTransport = {
      fetchSnapshot: vi.fn(async () => initial),
      fetchDelta: vi.fn(async () => advanced.delta),
    };
    const result = await synchronizeMftReadModel(transport);
    expect(result.phase).toBe('LIVE');
    expect(result.snapshot?.cursor).toBe(advanced.snapshot.cursor);
    expect(result.pages).toBe(1);
    expect(result.reloaded).toBe(false);
    expect(result.events).toEqual(advanced.delta.events);
  });

  it('deduplicates an identical event id without duplicating economic effect', async () => {
    const initial = makeMftSnapshot();
    const advanced = await nextSnapshot(initial);
    const first = advanced.delta.events[0];
    const duplicate = { ...first, sequence: first.sequence + 1, event_hash: 'f'.repeat(64) };
    expect(mergeMftEventTape([first], [duplicate])).toEqual([first]);
  });

  it('fails closed when a repeated event id changes its economic payload', async () => {
    const initial = makeMftSnapshot();
    const advanced = await nextSnapshot(initial);
    const first = advanced.delta.events[0];
    const conflict = { ...first, sequence: first.sequence + 1, payload_hash: 'f'.repeat(64) };
    expect(() => mergeMftEventTape([first], [conflict])).toThrow(/DUPLICATE_EVENT_ID_CONFLICT/);
  });

  it('refetches one full snapshot after a cursor gap and never loops', async () => {
    const initial = makeMftSnapshot();
    const reloaded = (await nextSnapshot(initial)).snapshot;
    const fetchSnapshot = vi.fn(async () => reloaded);
    const fetchDelta = vi.fn()
      .mockResolvedValueOnce({
        schema_version: MFT_CONSOLE_SCHEMA,
        status: 'RESNAPSHOT_REQUIRED',
        reason: 'CURSOR_GAP',
        refetch: '/api/v1/console/mft-execution',
        read_only: true,
      })
      .mockResolvedValueOnce(emptyDelta(reloaded));
    const result = await synchronizeMftReadModel(
      { fetchSnapshot, fetchDelta },
      { current: initial },
    );
    expect(result.phase).toBe('LIVE');
    expect(result.reloaded).toBe(true);
    expect(result.snapshot?.cursor).toBe(reloaded.cursor);
    expect(fetchSnapshot).toHaveBeenCalledTimes(1);
  });

  it('stops fail-closed when the reloaded snapshot also cannot catch up', async () => {
    const initial = makeMftSnapshot();
    const response = {
      schema_version: MFT_CONSOLE_SCHEMA,
      status: 'RESNAPSHOT_REQUIRED' as const,
      reason: 'CURSOR_GAP',
      refetch: '/api/v1/console/mft-execution' as const,
      read_only: true as const,
    };
    const result = await synchronizeMftReadModel(
      {
        fetchSnapshot: vi.fn(async () => initial),
        fetchDelta: vi.fn(async () => response),
      },
      { current: initial },
    );
    expect(result.phase).toBe('RESNAPSHOT_REQUIRED');
    expect(result.reason).toBe('CURSOR_GAP');
  });

  it.each([
    [new Error('Console API returned 429'), 'HTTP_429'],
    [new Error('request timeout'), 'TIMEOUT'],
    [new Error('empty response'), 'TRANSPORT_ERROR'],
  ])('classifies transport failures without returning mock data', async (error, reason) => {
    const result = await synchronizeMftReadModel({
      fetchSnapshot: vi.fn(async () => { throw error; }),
      fetchDelta: vi.fn(),
    });
    expect(result).toMatchObject({ phase: 'UNAVAILABLE', snapshot: null, reason });
  });

  it('retains the last verified snapshot for diagnosis when delta transport fails', async () => {
    const initial = makeMftSnapshot();
    const result = await synchronizeMftReadModel(
      {
        fetchSnapshot: vi.fn(),
        fetchDelta: vi.fn(async () => { throw new Error('Console API returned 429'); }),
      },
      { current: initial },
    );
    expect(result).toMatchObject({
      phase: 'UNAVAILABLE', reason: 'HTTP_429', snapshot: { cursor: initial.cursor },
    });
  });
});

describe('parseMftStreamNotice', () => {
  it('accepts a read-only current notice and rejects unknown schemas', () => {
    const notice = {
      source: 'MFT_READ_MODEL_SQLITE_AUTHORITY',
      read_only: true,
      schema_version: 'quantos.mft-stream-notice.v1',
      status: 'CURRENT',
      as_of_ms: 4_000,
      cursor: `0:${'0'.repeat(64)}`,
      watermark: { sequence: 0, event_at_ms: 3_000, event_hash: '0'.repeat(64) },
      freshness: { status: 'CURRENT', age_ms: 1_000 },
      reason: null,
    };
    expect(parseMftStreamNotice(notice)).toEqual(notice);
    expect(() => parseMftStreamNotice({ ...notice, schema_version: 'v2' })).toThrow(/SCHEMA/);
  });
});
