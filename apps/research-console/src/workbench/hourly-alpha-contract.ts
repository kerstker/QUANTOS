export const HOURLY_ALPHA_SCHEMA = 'quantos.hourly-alpha-console.v1' as const;

export type HourlyAlphaPhase = 'LOADING' | 'AVAILABLE' | 'NO_SNAPSHOT' | 'STALE' | 'QUARANTINED' | 'UNAVAILABLE';

export interface HourlyAlphaSafety {
  read_only: true;
  readiness: 'L2_DRY_RUN_ONLY';
  allow_order_submission: false;
  allow_ai_direct_trading: false;
  require_human_approval: true;
}

export interface HourlyAlphaFreshness {
  status: 'CURRENT' | 'STALE' | 'NO_RUN' | 'UNAVAILABLE';
  age_seconds: number | null;
}

export interface HourlyAlphaWatermark {
  event_at: number;
  batch_id: string;
}

export interface HourlyAlphaFunnel {
  generated: number;
  representatives: number;
  validated: number;
  blocked: number;
  forward_decisions: number;
  forward_realizations: number;
}

export interface HourlyAlphaCandidate {
  ordinal: number;
  candidate_id: string;
  state: string;
  trial_id: string | null;
  artifact_commitment: string | null;
}

export interface HourlyAlphaStoreStatus {
  status: 'AVAILABLE' | 'UNAVAILABLE';
  reason?: string;
  trials?: number;
}

export interface HourlyAlphaSnapshot {
  source: 'HOURLY_ALPHA_SQLITE_AUTHORITIES';
  read_only: true;
  api_version: 'v1';
  data_provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES';
  snapshot_id: string | null;
  snapshot_ids: string[];
  snapshot_status: 'REAL' | 'STALE' | 'NO_SNAPSHOT' | 'UNAVAILABLE';
  data_quality: {
    status: HourlyAlphaSnapshot['snapshot_status'];
    provenance: 'REAL_HOURLY_ALPHA_AUTHORITIES';
    snapshot_ids: string[];
  };
  schema_version: typeof HOURLY_ALPHA_SCHEMA;
  as_of: number;
  status: 'AVAILABLE' | 'BLOCKED';
  authority_mode?: 'QUARANTINED_LEGACY_AUTHORITY';
  authoritative?: false;
  evidence_status?: 'DIAGNOSTIC_ONLY';
  invalidation_code?: 'INVALIDATED_PERP_LEDGER_V1';
  freshness: HourlyAlphaFreshness;
  watermark: HourlyAlphaWatermark | null;
  batch_funnel: HourlyAlphaFunnel | null;
  candidate_states: HourlyAlphaCandidate[];
  stores: {
    workflow: HourlyAlphaStoreStatus;
    trials: HourlyAlphaStoreStatus;
    forward: HourlyAlphaStoreStatus;
  };
  safety: HourlyAlphaSafety;
}

const HASH = /^[0-9a-f]{64}$/;
const SNAPSHOT_STATUSES = new Set(['REAL', 'STALE', 'NO_SNAPSHOT', 'UNAVAILABLE']);
const FRESHNESS_STATUSES = new Set(['CURRENT', 'STALE', 'NO_RUN', 'UNAVAILABLE']);
const SAFETY: Readonly<Record<keyof HourlyAlphaSafety, string | boolean>> = {
  read_only: true,
  readiness: 'L2_DRY_RUN_ONLY',
  allow_order_submission: false,
  allow_ai_direct_trading: false,
  require_human_approval: true,
};

export class HourlyAlphaContractError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = 'HourlyAlphaContractError';
    this.code = code;
  }
}

function fail(code: string): never {
  throw new HourlyAlphaContractError(code);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function safeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function boundedString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= 512;
}

function validateSafety(value: unknown): asserts value is HourlyAlphaSafety {
  if (!isRecord(value)) fail('HOURLY_ALPHA_SAFETY_INVALID');
  for (const [key, expected] of Object.entries(SAFETY)) {
    if (value[key] !== expected) fail('HOURLY_ALPHA_SAFETY_INVALID');
  }
}

function validateSnapshotIds(value: unknown): asserts value is string[] {
  if (!Array.isArray(value)
    || value.some(item => typeof item !== 'string' || !HASH.test(item))
    || new Set(value).size !== value.length) fail('HOURLY_ALPHA_SNAPSHOT_IDS_INVALID');
}

function validateFreshness(value: unknown): asserts value is HourlyAlphaFreshness {
  if (!isRecord(value)
    || typeof value.status !== 'string'
    || !FRESHNESS_STATUSES.has(value.status)
    || (value.age_seconds !== null && !safeInteger(value.age_seconds))) {
    fail('HOURLY_ALPHA_FRESHNESS_INVALID');
  }
  if ((value.status === 'CURRENT' || value.status === 'STALE') && value.age_seconds === null) {
    fail('HOURLY_ALPHA_FRESHNESS_INVALID');
  }
  if ((value.status === 'NO_RUN' || value.status === 'UNAVAILABLE') && value.age_seconds !== null) {
    fail('HOURLY_ALPHA_FRESHNESS_INVALID');
  }
}

function validateWatermark(value: unknown): asserts value is HourlyAlphaWatermark | null {
  if (value === null) return;
  if (!isRecord(value) || !safeInteger(value.event_at)
    || typeof value.batch_id !== 'string' || !HASH.test(value.batch_id)) {
    fail('HOURLY_ALPHA_WATERMARK_INVALID');
  }
}

function parseFunnel(value: unknown): HourlyAlphaFunnel | null {
  if (value === null) return null;
  if (!isRecord(value)) fail('HOURLY_ALPHA_FUNNEL_INVALID');
  const keys: readonly (keyof HourlyAlphaFunnel)[] = [
    'generated', 'representatives', 'validated', 'blocked',
    'forward_decisions', 'forward_realizations',
  ];
  if (keys.some(key => !safeInteger(value[key]))) fail('HOURLY_ALPHA_FUNNEL_INVALID');
  return value as unknown as HourlyAlphaFunnel;
}

function parseCandidates(value: unknown): HourlyAlphaCandidate[] {
  if (!Array.isArray(value) || value.length > 100_000) fail('HOURLY_ALPHA_CANDIDATES_INVALID');
  const rows = value.map(item => {
    if (!isRecord(item) || !safeInteger(item.ordinal)
      || !boundedString(item.candidate_id) || !boundedString(item.state)
      || (item.trial_id !== null && !boundedString(item.trial_id))
      || (item.artifact_commitment !== null && !boundedString(item.artifact_commitment))) {
      fail('HOURLY_ALPHA_CANDIDATE_INVALID');
    }
    return item as unknown as HourlyAlphaCandidate;
  });
  if (new Set(rows.map(row => row.ordinal)).size !== rows.length
    || new Set(rows.map(row => row.candidate_id)).size !== rows.length) {
    fail('HOURLY_ALPHA_CANDIDATES_DUPLICATE');
  }
  return rows;
}

function parseStore(value: unknown, name: string): HourlyAlphaStoreStatus {
  if (!isRecord(value) || (value.status !== 'AVAILABLE' && value.status !== 'UNAVAILABLE')) {
    fail(`HOURLY_ALPHA_${name}_STORE_INVALID`);
  }
  if (value.status === 'UNAVAILABLE') {
    if (!boundedString(value.reason)) fail(`HOURLY_ALPHA_${name}_STORE_INVALID`);
  } else if (value.reason !== undefined) {
    fail(`HOURLY_ALPHA_${name}_STORE_INVALID`);
  }
  if (name === 'TRIALS' && value.status === 'AVAILABLE' && !safeInteger(value.trials)) {
    fail('HOURLY_ALPHA_TRIALS_STORE_INVALID');
  }
  if (value.trials !== undefined && !safeInteger(value.trials)) {
    fail(`HOURLY_ALPHA_${name}_STORE_INVALID`);
  }
  return value as unknown as HourlyAlphaStoreStatus;
}

function allZero(funnel: HourlyAlphaFunnel): boolean {
  return Object.values(funnel).every(value => value === 0);
}

export function parseHourlyAlphaSnapshot(value: unknown): HourlyAlphaSnapshot {
  if (!isRecord(value)) fail('HOURLY_ALPHA_SNAPSHOT_INVALID');
  if (value.schema_version !== HOURLY_ALPHA_SCHEMA) fail('HOURLY_ALPHA_UNKNOWN_SCHEMA');
  if (value.source !== 'HOURLY_ALPHA_SQLITE_AUTHORITIES'
    || value.read_only !== true || value.api_version !== 'v1'
    || value.data_provenance !== 'REAL_HOURLY_ALPHA_AUTHORITIES') {
    fail('HOURLY_ALPHA_MARKERS_INVALID');
  }
  if (!safeInteger(value.as_of)) fail('HOURLY_ALPHA_AS_OF_INVALID');
  if (value.status !== 'AVAILABLE' && value.status !== 'BLOCKED') fail('HOURLY_ALPHA_STATUS_INVALID');
  if (typeof value.snapshot_status !== 'string' || !SNAPSHOT_STATUSES.has(value.snapshot_status)) {
    fail('HOURLY_ALPHA_SNAPSHOT_STATUS_INVALID');
  }
  if (value.snapshot_id !== null
    && (typeof value.snapshot_id !== 'string' || !HASH.test(value.snapshot_id))) {
    fail('HOURLY_ALPHA_SNAPSHOT_ID_INVALID');
  }
  validateSnapshotIds(value.snapshot_ids);
  if (!isRecord(value.data_quality)
    || value.data_quality.status !== value.snapshot_status
    || value.data_quality.provenance !== value.data_provenance
    || JSON.stringify(value.data_quality.snapshot_ids) !== JSON.stringify(value.snapshot_ids)) {
    fail('HOURLY_ALPHA_DATA_QUALITY_INVALID');
  }
  validateFreshness(value.freshness);
  validateWatermark(value.watermark);
  validateSafety(value.safety);
  if (!isRecord(value.stores)) fail('HOURLY_ALPHA_STORES_INVALID');
  const stores = {
    workflow: parseStore(value.stores.workflow, 'WORKFLOW'),
    trials: parseStore(value.stores.trials, 'TRIALS'),
    forward: parseStore(value.stores.forward, 'FORWARD'),
  };
  const funnel = parseFunnel(value.batch_funnel);
  const candidates = parseCandidates(value.candidate_states);
  const availableStores = Object.values(stores).every(store => store.status === 'AVAILABLE');

  if (value.status === 'BLOCKED') {
    if (value.snapshot_status !== 'UNAVAILABLE' || value.freshness.status !== 'UNAVAILABLE'
      || value.watermark !== null || funnel !== null || candidates.length !== 0
      || availableStores) fail('HOURLY_ALPHA_BLOCKED_INVARIANT');
    if ((value.snapshot_ids.length === 0 && value.snapshot_id !== null)
      || (value.snapshot_ids.length === 1 && value.snapshot_id !== value.snapshot_ids[0])
      || value.snapshot_ids.length > 1) fail('HOURLY_ALPHA_BLOCKED_INVARIANT');
  } else {
    if (!availableStores || value.snapshot_status === 'UNAVAILABLE') {
      fail('HOURLY_ALPHA_AVAILABLE_INVARIANT');
    }
    if (value.snapshot_status === 'NO_SNAPSHOT') {
      if (value.snapshot_id !== null || value.snapshot_ids.length !== 0
        || value.watermark !== null || value.freshness.status !== 'NO_RUN'
        || funnel === null || !allZero(funnel) || candidates.length !== 0) {
        fail('HOURLY_ALPHA_NO_SNAPSHOT_INVARIANT');
      }
    } else {
      if (value.snapshot_id === null || value.snapshot_ids.length !== 1
        || value.snapshot_ids[0] !== value.snapshot_id
        || value.watermark === null || value.watermark.batch_id !== value.snapshot_id
        || funnel === null || funnel.representatives !== candidates.length
        || funnel.generated < funnel.representatives
        || funnel.validated !== candidates.filter(row => row.state === 'VALIDATED').length
        || funnel.blocked !== candidates.filter(row => row.state === 'BLOCKED').length
        || funnel.validated + funnel.blocked > funnel.representatives) {
        fail('HOURLY_ALPHA_FUNNEL_INVARIANT');
      }
      if (value.snapshot_status === 'REAL' && value.freshness.status !== 'CURRENT') {
        fail('HOURLY_ALPHA_CURRENT_INVARIANT');
      }
      if (value.snapshot_status === 'STALE' && value.freshness.status !== 'STALE') {
        fail('HOURLY_ALPHA_STALE_INVARIANT');
      }
    }
  }
  return {
    ...value,
    stores,
    batch_funnel: funnel,
    candidate_states: candidates,
  } as unknown as HourlyAlphaSnapshot;
}

export function classifyHourlyAlphaSnapshot(
  snapshot: HourlyAlphaSnapshot,
): { phase: Exclude<HourlyAlphaPhase, 'LOADING'>; reason: string | null } {
  if (snapshot.authority_mode === 'QUARANTINED_LEGACY_AUTHORITY') {
    return { phase: 'QUARANTINED', reason: snapshot.invalidation_code ?? 'LEGACY_AUTHORITY_QUARANTINED' };
  }
  if (snapshot.status === 'BLOCKED') {
    const failed = (['workflow', 'trials', 'forward'] as const)
      .find(name => snapshot.stores[name].status === 'UNAVAILABLE');
    const store = failed ? snapshot.stores[failed] : null;
    return {
      phase: 'UNAVAILABLE',
      reason: failed ? `${failed.toUpperCase()}:${store?.reason ?? 'UNAVAILABLE'}` : 'AUTHORITY_BLOCKED',
    };
  }
  if (snapshot.snapshot_status === 'STALE') return { phase: 'STALE', reason: 'FRESHNESS_STALE' };
  if (snapshot.snapshot_status === 'NO_SNAPSHOT') return { phase: 'NO_SNAPSHOT', reason: 'NO_RUN' };
  return { phase: 'AVAILABLE', reason: null };
}
