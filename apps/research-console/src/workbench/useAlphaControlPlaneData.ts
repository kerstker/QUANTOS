import {
  fetchAlphaLifecycle,
  fetchAlphaZooSnapshot,
  fetchAShareInstitutionalSearch,
} from '@/lib/api/research-console-api';
import type {
  AlphaLifecyclePayload,
  AlphaZooPayload,
  AShareInstitutionalSearchPayload,
} from '@/lib/types/research';
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
  ashare: AlphaEnrichment<AShareInstitutionalSearchPayload>;
}

function unavailableReason<T>(result: SnapshotResult<T>): string {
  if (result.apiState === 'loading') return 'LOADING';
  if (result.apiState === 'disabled') return 'REAL_API_NOT_CONFIGURED';
  return result.error ?? 'UNAVAILABLE';
}

function authorityDenial(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null) return 'MALFORMED_PAYLOAD';
  const record = payload as Record<string, unknown>;
  if (record.authoritative === false) return 'AUTHORITATIVE_FALSE';
  if (typeof record.evidence_status === 'string'
    && record.evidence_status.trim().toUpperCase() === 'DIAGNOSTIC_ONLY') {
    return 'DIAGNOSTIC_ONLY';
  }
  if (typeof record.invalidation_code === 'string' && record.invalidation_code.trim()) {
    return record.invalidation_code.trim();
  }
  return null;
}

export function zooEnrichment(result: SnapshotResult<AlphaZooPayload>): AlphaEnrichment<AlphaZooPayload> {
  if (!result.usingBackend || result.payload === null) {
    return {
      status: result.apiState === 'loading' ? 'LOADING' : 'UNAVAILABLE',
      payload: null,
      reason: unavailableReason(result),
    };
  }
  const denied = authorityDenial(result.payload);
  if (denied !== null) {
    return { status: 'UNAVAILABLE', payload: null, reason: `AUTHORITY_DENIED:${denied}` };
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

export function lifecycleEnrichment(
  result: SnapshotResult<AlphaLifecyclePayload>,
): AlphaEnrichment<AlphaLifecyclePayload> {
  if (!result.usingBackend || result.payload === null) {
    return {
      status: result.apiState === 'loading' ? 'LOADING' : 'UNAVAILABLE',
      payload: null,
      reason: unavailableReason(result),
    };
  }
  const denied = authorityDenial(result.payload);
  if (denied !== null) {
    return { status: 'UNAVAILABLE', payload: null, reason: `AUTHORITY_DENIED:${denied}` };
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

export function ashareEnrichment(
  result: SnapshotResult<AShareInstitutionalSearchPayload>,
): AlphaEnrichment<AShareInstitutionalSearchPayload> {
  if (!result.usingBackend || result.payload === null) {
    return {
      status: result.apiState === 'loading' ? 'LOADING' : 'UNAVAILABLE',
      payload: null,
      reason: unavailableReason(result),
    };
  }
  const payload = result.payload;
  if (payload.snapshot_present !== true) {
    return { status: 'UNAVAILABLE', payload: null, reason: payload.availability_mode };
  }
  if (payload.data_provenance !== 'REPOSITORY_EVIDENCE'
    || payload.diagnostic_only !== true
    || payload.evidence_status !== 'TECHNICAL_VALIDATION_ONLY') {
    return {
      status: 'UNAVAILABLE', payload: null,
      reason: `CONTRACT:${payload.data_provenance}:${payload.evidence_status}`,
    };
  }
  return { status: 'AVAILABLE', payload, reason: null };
}

export function useAlphaControlPlaneData(): AlphaControlPlaneData {
  const authority = useHourlyAlphaReadModel();
  const zooResult = useConsoleSnapshot<AlphaZooPayload>(fetchAlphaZooSnapshot);
  const lifecycleResult = useConsoleSnapshot<AlphaLifecyclePayload>(fetchAlphaLifecycle);
  const ashareResult = useConsoleSnapshot<AShareInstitutionalSearchPayload>(
    fetchAShareInstitutionalSearch,
  );
  return {
    authority,
    zoo: zooEnrichment(zooResult),
    lifecycle: lifecycleEnrichment(lifecycleResult),
    ashare: ashareEnrichment(ashareResult),
  };
}
