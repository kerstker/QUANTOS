import {
  MftContractError,
  parseMftCursor,
  parseMftDelta,
  parseMftSnapshot,
  type MftDeltaResponse,
  type MftDeltaEvent,
  type MftFreshness,
  type MftSnapshot,
  type MftWatermark,
} from './contracts';
import { applyMftDelta } from './mft-reducer';

export const MFT_STREAM_NOTICE_SCHEMA = 'quantos.mft-stream-notice.v1' as const;

export type MftSyncPhase =
  | 'LOADING'
  | 'LIVE'
  | 'RECONNECTING'
  | 'STALE'
  | 'RESNAPSHOT_REQUIRED'
  | 'UNAVAILABLE';

export interface MftSyncResult {
  phase: Exclude<MftSyncPhase, 'LOADING' | 'RECONNECTING'>;
  snapshot: MftSnapshot | null;
  reason: string | null;
  pages: number;
  reloaded: boolean;
  events: MftDeltaEvent[];
  eventHistory: MftEventHistory;
}

export type MftEventHistory = 'SESSION_DELTA' | 'RESET_BY_RESNAPSHOT';

const MFT_EVENT_TAPE_LIMIT = 200;

export interface MftSyncTransport {
  fetchSnapshot: (signal?: AbortSignal) => Promise<MftSnapshot>;
  fetchDelta: (cursor: string, signal?: AbortSignal) => Promise<MftDeltaResponse>;
}

export interface MftSyncOptions {
  current?: MftSnapshot | null;
  events?: readonly MftDeltaEvent[];
  eventHistory?: MftEventHistory;
  signal?: AbortSignal;
  maxPages?: number;
}

export interface MftStreamNotice {
  source: 'MFT_READ_MODEL_SQLITE_AUTHORITY';
  read_only: true;
  schema_version: typeof MFT_STREAM_NOTICE_SCHEMA;
  status: 'CURRENT' | 'CHANGED' | 'UNAVAILABLE';
  as_of_ms: number;
  cursor: string | null;
  watermark: MftWatermark | null;
  freshness: Pick<MftFreshness, 'status' | 'age_ms'>;
  reason: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function safeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

export function parseMftStreamNotice(value: unknown): MftStreamNotice {
  if (!isRecord(value)) throw new MftContractError('MFT_STREAM_NOTICE_INVALID');
  if (value.schema_version !== MFT_STREAM_NOTICE_SCHEMA) {
    throw new MftContractError('MFT_STREAM_NOTICE_SCHEMA_INVALID');
  }
  if (value.source !== 'MFT_READ_MODEL_SQLITE_AUTHORITY'
    || value.read_only !== true
    || !safeInteger(value.as_of_ms)
    || !['CURRENT', 'CHANGED', 'UNAVAILABLE'].includes(String(value.status))
    || (value.reason !== null && typeof value.reason !== 'string')) {
    throw new MftContractError('MFT_STREAM_NOTICE_INVALID');
  }
  if (value.cursor !== null && parseMftCursor(value.cursor) === null) {
    throw new MftContractError('MFT_STREAM_NOTICE_CURSOR_INVALID');
  }
  if (!isRecord(value.freshness)
    || typeof value.freshness.status !== 'string'
    || (value.freshness.age_ms !== null && !safeInteger(value.freshness.age_ms))) {
    throw new MftContractError('MFT_STREAM_NOTICE_FRESHNESS_INVALID');
  }
  if (value.watermark !== null) {
    if (!isRecord(value.watermark)
      || !safeInteger(value.watermark.sequence)
      || !safeInteger(value.watermark.event_at_ms)
      || typeof value.watermark.event_hash !== 'string'
      || !/^[0-9a-f]{64}$/.test(value.watermark.event_hash)) {
      throw new MftContractError('MFT_STREAM_NOTICE_WATERMARK_INVALID');
    }
    const cursor = parseMftCursor(value.cursor);
    if (!cursor
      || cursor.sequence !== value.watermark.sequence
      || cursor.hash !== value.watermark.event_hash) {
      throw new MftContractError('MFT_STREAM_NOTICE_CURSOR_INVALID');
    }
  }
  if ((value.status === 'CURRENT' || value.status === 'CHANGED')
    && (value.cursor === null || value.watermark === null)) {
    throw new MftContractError('MFT_STREAM_NOTICE_INVALID');
  }
  return value as unknown as MftStreamNotice;
}

export function mergeMftEventTape(
  current: readonly MftDeltaEvent[],
  incoming: readonly MftDeltaEvent[],
): MftDeltaEvent[] {
  const byId = new Map<string, MftDeltaEvent>();
  for (const event of [...current, ...incoming].sort((a, b) => a.sequence - b.sequence)) {
    const retained = byId.get(event.event_id);
    if (retained) {
      if (retained.section !== event.section || retained.payload_hash !== event.payload_hash) {
        throw new MftContractError('MFT_DUPLICATE_EVENT_ID_CONFLICT');
      }
      continue;
    }
    byId.set(event.event_id, event);
  }
  return [...byId.values()]
    .sort((a, b) => a.sequence - b.sequence)
    .slice(-MFT_EVENT_TAPE_LIMIT);
}

function snapshotResult(
  snapshot: MftSnapshot,
  pages: number,
  reloaded: boolean,
  events: MftDeltaEvent[],
  eventHistory: MftEventHistory,
): MftSyncResult {
  if (snapshot.status === 'AVAILABLE') {
    return { phase: 'LIVE', snapshot, reason: null, pages, reloaded, events, eventHistory };
  }
  const stale = snapshot.freshness.status === 'STALE';
  return {
    phase: stale ? 'STALE' : 'UNAVAILABLE',
    snapshot,
    reason: typeof snapshot.reason === 'string' ? snapshot.reason : snapshot.snapshot_status,
    pages,
    reloaded,
    events,
    eventHistory,
  };
}

function failureReason(error: unknown, signal?: AbortSignal): string {
  if (signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) return 'ABORTED';
  if (error instanceof MftContractError) return error.code;
  const message = error instanceof Error ? error.message : String(error);
  if (/\b429\b/.test(message)) return 'HTTP_429';
  if (/timeout/i.test(message)) return 'TIMEOUT';
  return 'TRANSPORT_ERROR';
}

export async function synchronizeMftReadModel(
  transport: MftSyncTransport,
  options: MftSyncOptions = {},
): Promise<MftSyncResult> {
  const maxPages = options.maxPages ?? 100;
  if (!Number.isSafeInteger(maxPages) || maxPages < 1 || maxPages > 1_000) {
    return {
      phase: 'RESNAPSHOT_REQUIRED', snapshot: null, reason: 'DELTA_PAGE_LIMIT_INVALID',
      pages: 0, reloaded: false, events: [], eventHistory: 'SESSION_DELTA',
    };
  }
  let snapshot: MftSnapshot | null = null;
  let pages = 0;
  let reloaded = false;
  let events: MftDeltaEvent[] = [];
  let eventHistory: MftEventHistory = options.eventHistory ?? 'SESSION_DELTA';
  try {
    events = mergeMftEventTape([], options.events ?? []);
    snapshot = options.current ? parseMftSnapshot(options.current) : null;
    if (snapshot === null || snapshot.status !== 'AVAILABLE') {
      snapshot = parseMftSnapshot(await transport.fetchSnapshot(options.signal));
    }
    if (snapshot.status !== 'AVAILABLE') {
      return snapshotResult(snapshot, pages, reloaded, events, eventHistory);
    }

    while (pages < maxPages) {
      if (options.signal?.aborted) throw new DOMException('aborted', 'AbortError');
      if (snapshot.cursor === null) throw new MftContractError('MFT_SNAPSHOT_CURSOR_INVALID');
      const delta = parseMftDelta(await transport.fetchDelta(snapshot.cursor, options.signal));
      pages += 1;
      if (delta.status === 'RESNAPSHOT_REQUIRED') {
        if (reloaded) {
          return {
            phase: 'RESNAPSHOT_REQUIRED', snapshot, reason: delta.reason, pages, reloaded,
            events, eventHistory,
          };
        }
        snapshot = parseMftSnapshot(await transport.fetchSnapshot(options.signal));
        reloaded = true;
        events = [];
        eventHistory = 'RESET_BY_RESNAPSHOT';
        if (snapshot.status !== 'AVAILABLE') {
          return snapshotResult(snapshot, pages, reloaded, events, eventHistory);
        }
        continue;
      }
      const applied = await applyMftDelta(snapshot, delta);
      if (applied.status === 'RESNAPSHOT_REQUIRED') {
        if (reloaded) {
          return {
            phase: 'RESNAPSHOT_REQUIRED', snapshot, reason: applied.reason, pages, reloaded,
            events, eventHistory,
          };
        }
        snapshot = parseMftSnapshot(await transport.fetchSnapshot(options.signal));
        reloaded = true;
        events = [];
        eventHistory = 'RESET_BY_RESNAPSHOT';
        if (snapshot.status !== 'AVAILABLE') {
          return snapshotResult(snapshot, pages, reloaded, events, eventHistory);
        }
        continue;
      }
      try {
        events = mergeMftEventTape(events, delta.events);
      } catch (error) {
        return {
          phase: 'RESNAPSHOT_REQUIRED', snapshot,
          reason: error instanceof MftContractError ? error.code : 'MFT_EVENT_TAPE_INVALID',
          pages, reloaded, events: [], eventHistory: 'RESET_BY_RESNAPSHOT',
        };
      }
      const previousCursor = snapshot.cursor;
      snapshot = applied.snapshot;
      if (!delta.has_more) return snapshotResult(snapshot, pages, reloaded, events, eventHistory);
      if (snapshot.cursor === previousCursor) {
        return {
          phase: 'RESNAPSHOT_REQUIRED', snapshot, reason: 'DELTA_NO_PROGRESS', pages, reloaded,
          events, eventHistory,
        };
      }
    }
    return {
      phase: 'RESNAPSHOT_REQUIRED', snapshot, reason: 'DELTA_PAGE_LIMIT', pages, reloaded,
      events, eventHistory,
    };
  } catch (error) {
    return {
      phase: 'UNAVAILABLE', snapshot, reason: failureReason(error, options.signal),
      pages, reloaded,
      events,
      eventHistory,
    };
  }
}
