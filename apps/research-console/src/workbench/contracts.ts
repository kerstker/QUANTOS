export const MFT_CONSOLE_SCHEMA = 'quantos.mft-execution-console.v1' as const;
export const MFT_READ_MODEL_SECTIONS = [
  'market_gap',
  'scheduler',
  'intent_funnel',
  'oms',
  'fills',
  'positions',
  'risk',
  'reconciliation',
  'tca',
  'mandate_expiry',
] as const;

export type MftSection = typeof MFT_READ_MODEL_SECTIONS[number];
export type JsonValue = string | number | boolean | null | JsonObject | JsonValue[];
export interface JsonObject { [key: string]: JsonValue }

export interface MftSafety {
  read_only: true;
  readiness: 'L2_DRY_RUN_ONLY';
  testnet_only: true;
  live_trading_enabled: false;
  real_broker_enabled: false;
  allow_order_submission: false;
  allow_ai_direct_trading: false;
  require_human_approval: true;
  submit_capability: false;
  reset_capability: false;
  approve_capability: false;
  microstructure_scope: 'TESTNET_MICROSTRUCTURE_NOT_PRODUCTION';
}

export interface MftFreshness {
  status: string;
  age_ms: number | null;
  policy_id: string | null;
  policy_version: string | null;
  max_age_ms: number | null;
}

export interface MftWatermark {
  sequence: number;
  event_at_ms: number;
  event_hash: string;
}

export interface MftSnapshot {
  [key: string]: unknown;
  source: 'MFT_READ_MODEL_SQLITE_AUTHORITY';
  read_only: true;
  api_version: 'v1';
  data_provenance: string;
  snapshot_id: string | null;
  snapshot_ids: string[];
  snapshot_status: string;
  data_quality: Record<string, unknown>;
  schema_version: typeof MFT_CONSOLE_SCHEMA;
  as_of_ms: number;
  status: 'AVAILABLE' | 'BLOCKED';
  freshness: MftFreshness;
  watermark: MftWatermark | null;
  cursor: string | null;
  missing_sections: MftSection[];
  market_gap: JsonObject;
  scheduler: JsonObject;
  intent_funnel: JsonObject;
  oms: JsonObject;
  fills: JsonObject;
  positions: JsonObject;
  risk: JsonObject;
  reconciliation: JsonObject;
  tca: JsonObject;
  mandate_expiry: JsonObject;
  safety: MftSafety;
}

export interface MftDeltaEvent {
  sequence: number;
  event_id: string;
  section: MftSection;
  event_at_ms: number;
  payload: JsonObject;
  payload_hash_input: string;
  payload_hash: string;
  prev_hash: string;
  event_hash: string;
}

export interface MftDelta {
  source: 'MFT_READ_MODEL_SQLITE_AUTHORITY';
  read_only: true;
  api_version: 'v1';
  data_provenance: 'REAL_MFT_READ_MODEL';
  schema_version: typeof MFT_CONSOLE_SCHEMA;
  status: 'OK';
  from_cursor: string;
  cursor: string;
  as_of_ms: number;
  events: MftDeltaEvent[];
  has_more: boolean;
  result_metadata: Record<string, unknown>;
}

export interface MftResnapshotRequired {
  schema_version: typeof MFT_CONSOLE_SCHEMA;
  status: 'RESNAPSHOT_REQUIRED';
  reason: string;
  refetch: '/api/v1/console/mft-execution';
  read_only: true;
}

export type MftDeltaResponse = MftDelta | MftResnapshotRequired;

const HASH = /^[0-9a-f]{64}$/;
const CURSOR = /^(0|[1-9][0-9]*):([0-9a-f]{64})$/;
const EVENT_ID = /^[A-Za-z0-9_.:-]{1,128}$/;
const LOCAL_PATH = /^(?:[A-Za-z]:[\\/]|\\\\|\/|file:\/\/)/i;
const SENSITIVE_KEYS = new Set([
  '__proto__', 'api_key', 'api_secret', 'constructor', 'credential', 'credentials',
  'database_path', 'db_path', 'file_path', 'password', 'path', 'private_key',
  'prototype', 'secret',
]);
const SECTION_SET = new Set<string>(MFT_READ_MODEL_SECTIONS);

const SAFETY: Readonly<Record<keyof MftSafety, string | boolean>> = {
  read_only: true,
  readiness: 'L2_DRY_RUN_ONLY',
  testnet_only: true,
  live_trading_enabled: false,
  real_broker_enabled: false,
  allow_order_submission: false,
  allow_ai_direct_trading: false,
  require_human_approval: true,
  submit_capability: false,
  reset_capability: false,
  approve_capability: false,
  microstructure_scope: 'TESTNET_MICROSTRUCTURE_NOT_PRODUCTION',
};

export class MftContractError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = 'MftContractError';
    this.code = code;
  }
}

function fail(code: string): never {
  throw new MftContractError(code);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function safeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

export function parseMftCursor(value: unknown): { sequence: number; hash: string } | null {
  if (typeof value !== 'string') return null;
  const match = CURSOR.exec(value);
  if (!match) return null;
  const sequence = Number(match[1]);
  if (!Number.isSafeInteger(sequence)) return null;
  return { sequence, hash: match[2] };
}

export function validateMftPayload(value: unknown, depth = 0): asserts value is JsonObject {
  if (!isRecord(value) || depth > 32) fail('MFT_PAYLOAD_INVALID');
  for (const [key, child] of Object.entries(value)) {
    if (SENSITIVE_KEYS.has(key.toLowerCase())) fail('MFT_PAYLOAD_SENSITIVE');
    validateJsonValue(child, depth + 1);
  }
}

function validateJsonValue(value: unknown, depth: number): asserts value is JsonValue {
  if (depth > 32) fail('MFT_PAYLOAD_INVALID');
  if (value === null || typeof value === 'boolean') return;
  if (typeof value === 'string') {
    if (LOCAL_PATH.test(value)) fail('MFT_PAYLOAD_LOCAL_PATH');
    return;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
      fail('MFT_PAYLOAD_NUMBER_INVALID');
    }
    return;
  }
  if (Array.isArray(value)) {
    for (const child of value) validateJsonValue(child, depth + 1);
    return;
  }
  validateMftPayload(value, depth + 1);
}

function validateSafety(value: unknown): asserts value is MftSafety {
  if (!isRecord(value)) fail('MFT_SAFETY_INVALID');
  for (const [key, expected] of Object.entries(SAFETY)) {
    if (value[key] !== expected) fail('MFT_SAFETY_INVALID');
  }
}

function validateFreshness(value: unknown): asserts value is MftFreshness {
  if (!isRecord(value) || !nonEmptyString(value.status)) fail('MFT_FRESHNESS_INVALID');
  for (const key of ['age_ms', 'max_age_ms'] as const) {
    if (value[key] !== null && !safeInteger(value[key])) fail('MFT_FRESHNESS_INVALID');
  }
  for (const key of ['policy_id', 'policy_version'] as const) {
    if (value[key] !== null && !nonEmptyString(value[key])) fail('MFT_FRESHNESS_INVALID');
  }
}

function validateWatermark(value: unknown): asserts value is MftWatermark | null {
  if (value === null) return;
  if (!isRecord(value)
    || !safeInteger(value.sequence)
    || !safeInteger(value.event_at_ms)
    || typeof value.event_hash !== 'string'
    || !HASH.test(value.event_hash)) fail('MFT_WATERMARK_INVALID');
}

function validateQuality(value: unknown, body: Record<string, unknown>): void {
  if (!isRecord(value)
    || value.status !== body.snapshot_status
    || value.provenance !== body.data_provenance
    || !Array.isArray(value.snapshot_ids)
    || JSON.stringify(value.snapshot_ids) !== JSON.stringify(body.snapshot_ids)) {
    fail('MFT_DATA_QUALITY_INVALID');
  }
}

function validateSnapshotIds(value: unknown): asserts value is string[] {
  if (!Array.isArray(value) || value.some(item => typeof item !== 'string' || !HASH.test(item))) {
    fail('MFT_SNAPSHOT_IDS_INVALID');
  }
}

export function parseMftSnapshot(value: unknown): MftSnapshot {
  if (!isRecord(value)) fail('MFT_SNAPSHOT_INVALID');
  if (value.schema_version !== MFT_CONSOLE_SCHEMA) fail('MFT_UNKNOWN_SCHEMA');
  if (value.source !== 'MFT_READ_MODEL_SQLITE_AUTHORITY'
    || value.read_only !== true
    || value.api_version !== 'v1') fail('MFT_SNAPSHOT_MARKERS_INVALID');
  if (!nonEmptyString(value.data_provenance)) fail('MFT_PROVENANCE_INVALID');
  if (!safeInteger(value.as_of_ms)) fail('MFT_AS_OF_INVALID');
  if (value.status !== 'AVAILABLE' && value.status !== 'BLOCKED') fail('MFT_STATUS_INVALID');
  if (!nonEmptyString(value.snapshot_status)) fail('MFT_SNAPSHOT_STATUS_INVALID');
  if (value.snapshot_id !== null && (typeof value.snapshot_id !== 'string' || !HASH.test(value.snapshot_id))) {
    fail('MFT_SNAPSHOT_ID_INVALID');
  }
  validateSnapshotIds(value.snapshot_ids);
  validateQuality(value.data_quality, value);
  validateFreshness(value.freshness);
  validateWatermark(value.watermark);
  validateSafety(value.safety);
  if (value.cursor !== null && parseMftCursor(value.cursor) === null) fail('MFT_CURSOR_INVALID');
  if (!Array.isArray(value.missing_sections)
    || value.missing_sections.some(item => typeof item !== 'string' || !SECTION_SET.has(item))
    || new Set(value.missing_sections).size !== value.missing_sections.length) {
    fail('MFT_MISSING_SECTIONS_INVALID');
  }
  for (const section of MFT_READ_MODEL_SECTIONS) {
    if (!Object.prototype.hasOwnProperty.call(value, section)) fail('MFT_SECTION_MISSING');
    validateMftPayload(value[section]);
  }
  if (value.watermark !== null && value.cursor !== null) {
    const cursor = parseMftCursor(value.cursor);
    if (!cursor
      || cursor.sequence !== value.watermark.sequence
      || cursor.hash !== value.watermark.event_hash) fail('MFT_WATERMARK_CURSOR_MISMATCH');
  }
  if (value.status === 'AVAILABLE') {
    if (value.snapshot_status !== 'REAL'
      || value.data_provenance !== 'REAL_MFT_READ_MODEL'
      || value.freshness.status !== 'CURRENT'
      || value.watermark === null
      || value.cursor === null
      || value.missing_sections.length !== 0) fail('MFT_AVAILABLE_INVARIANT');
  }
  return value as unknown as MftSnapshot;
}

function parseMftEvent(value: unknown): MftDeltaEvent {
  if (!isRecord(value)
    || !safeInteger(value.sequence)
    || value.sequence < 1
    || typeof value.event_id !== 'string'
    || !EVENT_ID.test(value.event_id)
    || typeof value.section !== 'string'
    || !SECTION_SET.has(value.section)
    || !safeInteger(value.event_at_ms)
    || typeof value.payload_hash_input !== 'string'
    || value.payload_hash_input.length < 2
    || value.payload_hash_input.length > 1_000_000
    || typeof value.payload_hash !== 'string'
    || !HASH.test(value.payload_hash)
    || typeof value.prev_hash !== 'string'
    || !HASH.test(value.prev_hash)
    || typeof value.event_hash !== 'string'
    || !HASH.test(value.event_hash)) fail('MFT_DELTA_EVENT_INVALID');
  validateMftPayload(value.payload);
  return value as unknown as MftDeltaEvent;
}

function validateResultMetadata(value: unknown): asserts value is Record<string, unknown> {
  if (!isRecord(value)) fail('MFT_DELTA_METADATA_INVALID');
  const required = [
    'source', 'read_only', 'api_version', 'data_provenance', 'snapshot_id',
    'snapshot_ids', 'snapshot_status', 'data_quality', 'schema_version', 'as_of_ms',
    'status', 'freshness', 'watermark', 'cursor', 'missing_sections', 'safety',
  ];
  if (required.some(key => !Object.prototype.hasOwnProperty.call(value, key))) {
    fail('MFT_DELTA_METADATA_INVALID');
  }
  if (MFT_READ_MODEL_SECTIONS.some(
    section => Object.prototype.hasOwnProperty.call(value, section),
  )) {
    fail('MFT_DELTA_METADATA_SHADOWS_SECTION');
  }
  if (value.source !== 'MFT_READ_MODEL_SQLITE_AUTHORITY'
    || value.read_only !== true
    || value.api_version !== 'v1'
    || value.schema_version !== MFT_CONSOLE_SCHEMA
    || !nonEmptyString(value.data_provenance)
    || !safeInteger(value.as_of_ms)
    || (value.status !== 'AVAILABLE' && value.status !== 'BLOCKED')
    || !nonEmptyString(value.snapshot_status)
    || (value.snapshot_id !== null
      && (typeof value.snapshot_id !== 'string' || !HASH.test(value.snapshot_id)))) {
    fail('MFT_DELTA_METADATA_INVALID');
  }
  validateSnapshotIds(value.snapshot_ids);
  validateQuality(value.data_quality, value);
  validateFreshness(value.freshness);
  validateWatermark(value.watermark);
  validateSafety(value.safety);
  if (value.cursor !== null && parseMftCursor(value.cursor) === null) {
    fail('MFT_DELTA_METADATA_INVALID');
  }
  if (!Array.isArray(value.missing_sections)
    || value.missing_sections.some(item => typeof item !== 'string' || !SECTION_SET.has(item))) {
    fail('MFT_DELTA_METADATA_INVALID');
  }
}

export function parseMftDelta(value: unknown): MftDeltaResponse {
  if (!isRecord(value)) fail('MFT_DELTA_INVALID');
  if (value.schema_version !== MFT_CONSOLE_SCHEMA) fail('MFT_UNKNOWN_SCHEMA');
  if (value.status === 'RESNAPSHOT_REQUIRED') {
    if (value.read_only !== true
      || !nonEmptyString(value.reason)
      || value.refetch !== '/api/v1/console/mft-execution') fail('MFT_RESNAPSHOT_INVALID');
    return value as unknown as MftResnapshotRequired;
  }
  if (value.status !== 'OK'
    || value.source !== 'MFT_READ_MODEL_SQLITE_AUTHORITY'
    || value.read_only !== true
    || value.api_version !== 'v1'
    || value.data_provenance !== 'REAL_MFT_READ_MODEL'
    || parseMftCursor(value.from_cursor) === null
    || parseMftCursor(value.cursor) === null
    || !safeInteger(value.as_of_ms)
    || typeof value.has_more !== 'boolean'
    || !Array.isArray(value.events)
    || value.events.length > 1_000) fail('MFT_DELTA_INVALID');
  const events = value.events.map(parseMftEvent);
  validateResultMetadata(value.result_metadata);
  if (value.result_metadata.cursor !== value.cursor
    || value.result_metadata.as_of_ms !== value.as_of_ms) fail('MFT_DELTA_METADATA_INVALID');
  return { ...value, events } as unknown as MftDelta;
}
