import type { ConsoleApiState } from './useConsoleSnapshot';

/**
 * The only display states a console snapshot may expose. These are intentionally
 * separate from backend business verdicts: a rejected alpha, a stale dataset,
 * and an unavailable API must never look like a healthy mock or real result.
 */
export type SnapshotDisplayState =
  | 'MOCK'
  | 'BACKEND_SYNTHETIC'
  | 'REAL'
  | 'EVIDENCE'
  | 'BLOCKED'
  | 'STALE'
  | 'UNAVAILABLE'
  | 'NO_SNAPSHOT'
  | 'LOADING';

export interface SnapshotStateInput {
  apiState: ConsoleApiState;
  usingBackend: boolean;
  realData?: boolean;
  evidenceData?: boolean;
  /** Explicit state from a domain payload (preferred over string heuristics). */
  dataStatus?: SnapshotDisplayState;
  noDataYet?: boolean;
}

/**
 * Normalize the backend's wire-level admission values to the UI state model.
 * The API deliberately uses DATA_BLOCKED while the presentation component
 * uses BLOCKED; keeping this conversion in one place prevents pages from
 * accidentally treating an unknown status as a healthy snapshot.
 */
export function normalizeSnapshotDisplayState(value: unknown): SnapshotDisplayState | undefined {
  if (typeof value !== 'string') return undefined;
  const normalized = value.trim().toUpperCase();
  if (normalized === 'DATA_BLOCKED' || normalized === 'BLOCK') return 'BLOCKED';
  if (normalized === 'SYNTHETIC' || normalized === 'SYNTHETIC_DETERMINISTIC') return 'BACKEND_SYNTHETIC';
  if (normalized === 'REAL' || normalized === 'REAL_DATA') return 'REAL';
  if (normalized === 'EVIDENCE' || normalized === 'VERIFIED_EVIDENCE') return 'EVIDENCE';
  if (normalized === 'STALE' || normalized === 'STALE_DATA') return 'STALE';
  if (normalized === 'UNAVAILABLE') return 'UNAVAILABLE';
  if (normalized === 'NO_SNAPSHOT' || normalized === 'NO_DATA' || normalized === 'NO_RUN') return 'NO_SNAPSHOT';
  if (normalized === 'LOADING') return 'LOADING';
  return undefined;
}

/**
 * Convert transport + provenance flags into a single fail-closed display state.
 * Explicit `dataStatus` is authoritative; otherwise transport errors/loading are
 * resolved before the ordinary mock/backend/real distinction.
 */
export function deriveSnapshotDisplayState(input: SnapshotStateInput): SnapshotDisplayState {
  // Transport state is authoritative during refresh/failure.  A retained REAL
  // payload may still be rendered for continuity, but it must not keep actions
  // enabled until revalidation completes.
  if (input.apiState === 'loading') return 'LOADING';
  if (input.apiState === 'error') return 'UNAVAILABLE';
  if (input.dataStatus) return input.dataStatus;
  if (!input.usingBackend) return 'MOCK';
  if (input.noDataYet) return 'NO_SNAPSHOT';
  if (input.evidenceData) return 'EVIDENCE';
  if (input.realData) return 'REAL';
  return 'BACKEND_SYNTHETIC';
}

/** A mutation must be denied whenever the snapshot is not explicitly trusted. */
export function isSnapshotActionAllowed(state: SnapshotDisplayState): boolean {
  // Synthetic snapshots are useful for rendering only; they are never an
  // authorization or admission source for a mutating workflow.
  return state === 'REAL' || state === 'EVIDENCE';
}
