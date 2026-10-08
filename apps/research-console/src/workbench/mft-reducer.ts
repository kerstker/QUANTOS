import {
  MFT_READ_MODEL_SECTIONS,
  MftContractError,
  parseMftCursor,
  parseMftDelta,
  parseMftSnapshot,
  type JsonValue,
  type MftDeltaResponse,
  type MftSnapshot,
  validateMftPayload,
} from './contracts';

export type MftApplyResult =
  | { status: 'APPLIED'; snapshot: MftSnapshot }
  | { status: 'RESNAPSHOT_REQUIRED'; reason: string };

function canonicalize(value: JsonValue | Record<string, unknown>): JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value : Number(value.toFixed(10));
  }
  if (Array.isArray(value)) return value.map(item => canonicalize(item as JsonValue));
  return Object.fromEntries(
    Object.keys(value).sort().map(key => [key, canonicalize(value[key] as JsonValue)]),
  );
}

function asciiJson(value: JsonValue): string {
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, character => (
    `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`
  ));
}

export function canonicalMftJson(value: JsonValue | Record<string, unknown>): string {
  return asciiJson(canonicalize(value));
}

async function sha256Text(value: string): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new MftContractError('MFT_HASH_UNAVAILABLE');
  const digest = await globalThis.crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(value),
  );
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

export async function stableMftHash(value: JsonValue | Record<string, unknown>): Promise<string> {
  return sha256Text(canonicalMftJson(value));
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function reason(error: unknown): string {
  return error instanceof MftContractError ? error.code : 'MFT_DELTA_INVALID';
}

export async function applyMftDelta(
  snapshotValue: unknown,
  deltaValue: unknown,
): Promise<MftApplyResult> {
  try {
    const snapshot = parseMftSnapshot(snapshotValue);
    const delta: MftDeltaResponse = parseMftDelta(deltaValue);
    if (delta.status === 'RESNAPSHOT_REQUIRED') {
      return { status: 'RESNAPSHOT_REQUIRED', reason: delta.reason };
    }
    if (snapshot.cursor === null || delta.from_cursor !== snapshot.cursor) {
      throw new MftContractError('MFT_DELTA_GAP');
    }
    if (delta.as_of_ms < snapshot.as_of_ms) throw new MftContractError('MFT_TIME_ROLLBACK');
    const base = parseMftCursor(snapshot.cursor);
    if (!base) throw new MftContractError('MFT_SNAPSHOT_CURSOR_INVALID');
    let sequence = base.sequence;
    let previousHash = base.hash;
    let previousEventAt = snapshot.watermark?.event_at_ms ?? 0;
    const next: Record<string, unknown> = clone(snapshot);

    for (const event of delta.events) {
      sequence += 1;
      if (event.sequence !== sequence || event.prev_hash !== previousHash) {
        throw new MftContractError('MFT_DELTA_GAP');
      }
      if (event.event_at_ms < previousEventAt) throw new MftContractError('MFT_TIME_ROLLBACK');
      if (await sha256Text(event.payload_hash_input) !== event.payload_hash) {
        throw new MftContractError('MFT_PAYLOAD_HASH_MISMATCH');
      }
      let hashInputPayload: unknown;
      try {
        hashInputPayload = JSON.parse(event.payload_hash_input) as unknown;
      } catch {
        throw new MftContractError('MFT_PAYLOAD_HASH_INPUT_INVALID');
      }
      validateMftPayload(hashInputPayload);
      if (canonicalMftJson(hashInputPayload) !== canonicalMftJson(event.payload)) {
        throw new MftContractError('MFT_PAYLOAD_HASH_INPUT_MISMATCH');
      }
      const eventHash = await stableMftHash({
        sequence: event.sequence,
        event_id: event.event_id,
        section: event.section,
        event_at_ms: event.event_at_ms,
        payload_hash: event.payload_hash,
        prev_hash: event.prev_hash,
      });
      if (eventHash !== event.event_hash) throw new MftContractError('MFT_EVENT_HASH_MISMATCH');
      next[event.section] = clone(event.payload);
      previousHash = event.event_hash;
      previousEventAt = event.event_at_ms;
    }

    const terminalCursor = `${sequence}:${previousHash}`;
    if (delta.cursor !== terminalCursor || delta.result_metadata.cursor !== terminalCursor) {
      throw new MftContractError('MFT_TERMINAL_CURSOR_MISMATCH');
    }
    for (const section of MFT_READ_MODEL_SECTIONS) {
      if (Object.prototype.hasOwnProperty.call(delta.result_metadata, section)) {
        throw new MftContractError('MFT_DELTA_METADATA_SHADOWS_SECTION');
      }
    }
    Object.assign(next, clone(delta.result_metadata));
    const parsed = parseMftSnapshot(next);
    return { status: 'APPLIED', snapshot: parsed };
  } catch (error) {
    return { status: 'RESNAPSHOT_REQUIRED', reason: reason(error) };
  }
}
