import { useEffect, useState } from 'react';
import { isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import {
  deriveSnapshotDisplayState,
  isSnapshotActionAllowed,
  normalizeSnapshotDisplayState,
  type SnapshotDisplayState,
} from './snapshot-state';

export type ConsoleApiState = 'disabled' | 'loading' | 'connected' | 'error';

export interface SnapshotResult<T> {
  /** Latest backend payload, retained while a refresh is in flight. */
  payload: T | null;
  apiState: ConsoleApiState;
  error: string | null;
  /** True when a backend snapshot is in hand, including during revalidation. */
  usingBackend: boolean;
  /** Normalized admission state derived from the transport and payload. */
  snapshotState?: SnapshotDisplayState;
  /** True only for REAL/EVIDENCE snapshots; synthetic or missing data is never actionable. */
  actionsAllowed?: boolean;
}

/**
 * Unified read-only data hook for every console page. Preserves the existing
 * contract behaviour: when the API is disabled (mock mode) it stays `disabled`
 * and the page renders mock fixtures; when enabled it fetches the snapshot,
 * and ANY error (including a non-snapshot body rejected by assertConsoleSnapshot)
 * surfaces visibly and the page honestly falls back to mock. Never throws.
 *
 * `fetcher` must be stable across renders OR depend only on `deps`.
 */
export function useConsoleSnapshot<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: ReadonlyArray<unknown> = [],
): SnapshotResult<T> {
  const [payload, setPayload] = useState<T | null>(null);
  const [apiState, setApiState] = useState<ConsoleApiState>('disabled');
  const [error, setError] = useState<string | null>(null);
  const apiEnabled = isResearchConsoleApiEnabled();

  useEffect(() => {
    if (!apiEnabled) {
      setPayload(null);
      setApiState('disabled');
      setError(null);
      return;
    }
    const controller = new AbortController();
    setApiState('loading');
    setError(null);
    fetcher(controller.signal)
      .then(next => {
        setPayload(next);
        setApiState('connected');
      })
      .catch(err => {
        if (controller.signal.aborted) return;
        setPayload(null);
        setApiState('error');
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiEnabled, ...deps]);

  // Keep the last verified snapshot visible while a periodic refresh is in
  // flight.  The display state remains LOADING, so mutating actions stay
  // fail-closed, but operators never see a fabricated zero/mock flash between
  // two healthy read-only snapshots.
  const usingBackend = payload !== null
    && (apiState === 'connected' || apiState === 'loading');
  const payloadRecord = payload as Record<string, unknown> | null;
  const quality = payloadRecord?.data_quality;
  const qualityRecord = quality && typeof quality === 'object'
    ? quality as Record<string, unknown>
    : null;
  const explicitStatus = normalizeSnapshotDisplayState(
    payloadRecord?.snapshot_status
      ?? qualityRecord?.status
      ?? payloadRecord?.verdict,
  );
  const snapshotState = deriveSnapshotDisplayState({
    apiState,
    usingBackend,
    dataStatus: explicitStatus,
    noDataYet: usingBackend && payloadRecord?.snapshot_present === false,
    realData: usingBackend && (
      payloadRecord?.data_provenance === 'REAL'
      || (typeof payloadRecord?.data_provenance === 'string' && payloadRecord.data_provenance.startsWith('REAL_'))
    ),
    evidenceData: usingBackend && (
      payloadRecord?.data_provenance === 'EVIDENCE'
      || payloadRecord?.data_provenance === 'REPOSITORY_EVIDENCE'
    ),
  });

  return {
    payload,
    apiState,
    error,
    usingBackend,
    snapshotState,
    actionsAllowed: isSnapshotActionAllowed(snapshotState),
  };
}
