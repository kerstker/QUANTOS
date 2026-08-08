import { useEffect, useRef, useState } from 'react';

import {
  RESEARCH_CONSOLE_API_BASE,
  fetchMftExecutionDelta,
  fetchMftExecutionSnapshot,
} from '@/lib/api/research-console-api';
import type { MftSnapshot } from './contracts';
import {
  parseMftStreamNotice,
  synchronizeMftReadModel,
  type MftSyncPhase,
  type MftSyncResult,
  type MftSyncTransport,
} from './mft-sync';

export interface MftReadModelState {
  phase: MftSyncPhase;
  snapshot: MftSnapshot | null;
  reason: string | null;
  pages: number;
  reloaded: boolean;
  transport: 'SSE' | 'POLLING' | 'NONE';
}

const INITIAL: MftReadModelState = {
  phase: 'LOADING',
  snapshot: null,
  reason: null,
  pages: 0,
  reloaded: false,
  transport: 'NONE',
};

const MFT_REQUEST_TIMEOUT_MS = 10_000;

async function withRequestTimeout<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  parent?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abortFromParent = () => controller.abort();
  if (parent?.aborted) controller.abort();
  else parent?.addEventListener('abort', abortFromParent, { once: true });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, MFT_REQUEST_TIMEOUT_MS);
  try {
    return await operation(controller.signal);
  } catch (error) {
    if (timedOut) throw new Error('MFT request timeout');
    throw error;
  } finally {
    clearTimeout(timeout);
    parent?.removeEventListener('abort', abortFromParent);
  }
}

const TRANSPORT: MftSyncTransport = {
  fetchSnapshot: signal => withRequestTimeout(
    requestSignal => fetchMftExecutionSnapshot(requestSignal), signal,
  ),
  fetchDelta: (cursor, signal) => withRequestTimeout(
    requestSignal => fetchMftExecutionDelta(cursor, requestSignal), signal,
  ),
};

export function useMftReadModel(): MftReadModelState {
  const [state, setState] = useState<MftReadModelState>(INITIAL);
  const snapshotRef = useRef<MftSnapshot | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let source: EventSource | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let synchronizing = false;
    let queued = false;
    let queuedForceSnapshot = false;
    let streamDegraded = false;

    const commit = (result: MftSyncResult, transport: MftReadModelState['transport']) => {
      if (!active || controller.signal.aborted) return;
      snapshotRef.current = result.snapshot;
      setState({ ...result, transport });
    };

    const synchronize = async (forceSnapshot = false) => {
      if (synchronizing) {
        queued = true;
        queuedForceSnapshot = queuedForceSnapshot || forceSnapshot;
        return;
      }
      synchronizing = true;
      do {
        queued = false;
        forceSnapshot = forceSnapshot || queuedForceSnapshot;
        queuedForceSnapshot = false;
        const result = await synchronizeMftReadModel(TRANSPORT, {
          current: forceSnapshot ? null : snapshotRef.current,
          signal: controller.signal,
        });
        forceSnapshot = false;
        commit(result, source ? 'SSE' : 'POLLING');
      } while (queued && active && !controller.signal.aborted);
      synchronizing = false;
    };

    const openStream = (snapshot: MftSnapshot) => {
      if (typeof EventSource === 'undefined') {
        poll = setInterval(() => { void synchronize(); }, 5_000);
        setState(current => ({ ...current, transport: 'POLLING' }));
        return;
      }
      const url = new URL('/api/v1/console/stream/mft-execution', RESEARCH_CONSOLE_API_BASE);
      if (snapshot.cursor !== null) url.searchParams.set('cursor', snapshot.cursor);
      source = new EventSource(url);
      setState(current => ({ ...current, transport: 'SSE' }));
      source.onopen = () => {
        if (!active || snapshotRef.current?.status !== 'AVAILABLE') return;
        if (streamDegraded) {
          setState(current => ({
            ...current, phase: 'RECONNECTING', reason: 'STREAM_AWAITING_WATERMARK',
            transport: 'SSE',
          }));
        }
      };
      source.onmessage = event => {
        void (async () => {
          try {
            const notice = parseMftStreamNotice(JSON.parse(event.data) as unknown);
            if (notice.status === 'UNAVAILABLE') {
              if (!active) return;
              setState(current => ({
                ...current, phase: 'UNAVAILABLE', reason: notice.reason ?? 'STREAM_UNAVAILABLE',
                transport: 'SSE',
              }));
              return;
            }
            if (notice.cursor !== snapshotRef.current?.cursor) {
              if (active) {
                setState(current => ({
                  ...current, phase: 'RECONNECTING', reason: 'DELTA_CATCHUP', transport: 'SSE',
                }));
              }
              await synchronize();
            } else if (active && snapshotRef.current?.status === 'AVAILABLE') {
              setState(current => ({
                ...current, phase: 'LIVE', reason: null, transport: 'SSE',
              }));
            }
            streamDegraded = false;
          } catch {
            if (active) {
              setState(current => ({
                ...current, phase: 'RESNAPSHOT_REQUIRED', reason: 'STREAM_NOTICE_INVALID',
                transport: 'SSE',
              }));
            }
            await synchronize(true);
          }
        })();
      };
      source.onerror = () => {
        if (!active || snapshotRef.current === null) return;
        streamDegraded = true;
        setState(current => ({
          ...current, phase: 'RECONNECTING', reason: 'STREAM_RECONNECTING', transport: 'SSE',
        }));
      };
    };

    void (async () => {
      const result = await synchronizeMftReadModel(TRANSPORT, { signal: controller.signal });
      commit(result, 'NONE');
      if (result.phase === 'LIVE' && result.snapshot) openStream(result.snapshot);
    })();

    return () => {
      active = false;
      controller.abort();
      source?.close();
      if (poll !== null) clearInterval(poll);
    };
  }, []);

  return state;
}
