import {
  fetchAlphaLifecycle,
  fetchAlphaZooSnapshot,
} from '@/lib/api/research-console-api';
import type { AlphaLifecyclePayload, AlphaZooPayload } from '@/lib/types/research';
import { useConsoleSnapshot, type SnapshotResult } from '@/ui/useConsoleSnapshot';
import { useHourlyAlphaReadModel, type HourlyAlphaReadModelState } from './useHourlyAlphaReadModel';

export interface AlphaEnrichment<T> {
  status: 'LOADING' | 'AVAILABLE' | 'UNAVAILABLE';
  payload: T | null;
  reason: string | null;
}

export interface AlphaControlPlaneData {
  authority: HourlyAlphaReadModelState;
  zoo: AlphaEnrichment<AlphaZooPayload>;
  lifecycle: AlphaEnrichment<AlphaLifecyclePayload>;
}

function unavailableReason<T>(result: SnapshotResult<T>): string {
  if (result.apiState === 'loading') return 'LOADING';
  if (result.apiState === 'disabled') return 'REAL_API_NOT_CONFIGURED';
  return result.error ?? 'UNAVAILABLE';
}

function zooEnrichment(result: SnapshotResult<AlphaZooPayload>): AlphaEnrichment<AlphaZooPayload> {
  if (!result.usingBackend || result.payload === null) {
    return {
      status: result.apiState === 'loading' ? 'LOADING' : 'UNAVAILABLE',
      payload: null,
      reason: unavailableReason(result),
    };
  }
  const provenance = result.payload.data_provenance;
  const accepted = provenance === 'EXPERIMENT_REGISTRY'
    || provenance === 'REAL'
    || provenance === 'REPOSITORY_EVIDENCE'
    || provenance.startsWith('REAL_');
  if (!accepted) {
    return { status: 'UNAVAILABLE', payload: null, reason: `PROVENANCE:${provenance}` };
  }
  return { status: 'AVAILABLE', payload: result.payload, reason: null };
}

function lifecycleEnrichment(
  result: SnapshotResult<AlphaLifecyclePayload>,
): AlphaEnrichment<AlphaLifecyclePayload> {
  if (!result.usingBackend || result.payload === null) {
    return {
      status: result.apiState === 'loading' ? 'LOADING' : 'UNAVAILABLE',
      payload: null,
      reason: unavailableReason(result),
    };
  }
  if (result.payload.data_provenance !== 'REAL_ALPHA_LIFECYCLE'
    || result.payload.snapshot_present !== true) {
    return {
      status: 'UNAVAILABLE', payload: null,
      reason: `PROVENANCE:${result.payload.data_provenance}`,
    };
  }
  return { status: 'AVAILABLE', payload: result.payload, reason: null };
}

export function useAlphaControlPlaneData(): AlphaControlPlaneData {
  const authority = useHourlyAlphaReadModel();
  const zooResult = useConsoleSnapshot<AlphaZooPayload>(fetchAlphaZooSnapshot);
  const lifecycleResult = useConsoleSnapshot<AlphaLifecyclePayload>(fetchAlphaLifecycle);
  return {
    authority,
    zoo: zooEnrichment(zooResult),
    lifecycle: lifecycleEnrichment(lifecycleResult),
  };
}
