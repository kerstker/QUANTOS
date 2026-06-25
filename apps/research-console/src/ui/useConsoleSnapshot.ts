import { useEffect, useState } from 'react';
import { isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';

export type ConsoleApiState = 'disabled' | 'loading' | 'connected' | 'error';

export interface SnapshotResult<T> {
  /** Backend payload when connected, else null (caller falls back to mock). */
  payload: T | null;
  apiState: ConsoleApiState;
  error: string | null;
  /** True only when a live backend snapshot is in hand. */
  usingBackend: boolean;
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

  return { payload, apiState, error, usingBackend: apiState === 'connected' && payload !== null };
}
