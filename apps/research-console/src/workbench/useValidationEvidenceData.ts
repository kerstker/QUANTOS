import {
  fetchExperimentsSnapshot,
  fetchValidationSnapshot,
} from '@/lib/api/research-console-api';
import type { ExperimentsPayload, ValidationDashboardPayload } from '@/lib/types/research';
import { useConsoleSnapshot, type SnapshotResult } from '@/ui/useConsoleSnapshot';
import { useHourlyAlphaReadModel, type HourlyAlphaReadModelState } from './useHourlyAlphaReadModel';
import {
  parseExperimentsEvidence,
  parseValidationEvidence,
} from './validation-evidence-contract';

export interface EvidenceAuxiliary<T> {
  status: 'LOADING' | 'AVAILABLE' | 'UNAVAILABLE';
  payload: T | null;
  reason: string | null;
}

export interface ValidationEvidenceData {
  authority: HourlyAlphaReadModelState;
  validation: EvidenceAuxiliary<ValidationDashboardPayload>;
  experiments: EvidenceAuxiliary<ExperimentsPayload>;
}

function baseUnavailable<T>(result: SnapshotResult<T>): EvidenceAuxiliary<T> | null {
  if (result.usingBackend && result.payload !== null) return null;
  return {
    status: result.apiState === 'loading' ? 'LOADING' : 'UNAVAILABLE',
    payload: null,
    reason: result.apiState === 'loading'
      ? 'LOADING'
      : result.apiState === 'disabled'
        ? 'REAL_API_NOT_CONFIGURED'
        : result.error ?? 'UNAVAILABLE',
  };
}

function blockedSnapshotStatus(payload: { snapshot_status?: string }): string | null {
  const status = payload.snapshot_status;
  if (status === undefined || status === 'REAL') return null;
  return `SNAPSHOT_STATUS:${status}`;
}

export function admitValidationEvidence(
  result: SnapshotResult<ValidationDashboardPayload>,
): EvidenceAuxiliary<ValidationDashboardPayload> {
  const unavailable = baseUnavailable(result);
  if (unavailable) return unavailable;
  try {
    const payload = parseValidationEvidence(result.payload);
    const blocked = blockedSnapshotStatus(payload);
    if (blocked) return { status: 'UNAVAILABLE', payload: null, reason: blocked };
    const provenance = payload.data_provenance ?? '';
    if (!provenance.startsWith('REAL_') && provenance !== 'REPOSITORY_EVIDENCE') {
      return { status: 'UNAVAILABLE', payload: null, reason: `PROVENANCE:${provenance}` };
    }
    return { status: 'AVAILABLE', payload, reason: null };
  } catch (error) {
    return {
      status: 'UNAVAILABLE', payload: null,
      reason: `CONTRACT:${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

export function admitExperimentsEvidence(
  result: SnapshotResult<ExperimentsPayload>,
): EvidenceAuxiliary<ExperimentsPayload> {
  const unavailable = baseUnavailable(result);
  if (unavailable) return unavailable;
  try {
    const payload = parseExperimentsEvidence(result.payload);
    const blocked = blockedSnapshotStatus(payload);
    if (blocked) return { status: 'UNAVAILABLE', payload: null, reason: blocked };
    if (payload.data_provenance !== 'EXPERIMENT_REGISTRY') {
      return {
        status: 'UNAVAILABLE', payload: null,
        reason: `PROVENANCE:${payload.data_provenance ?? 'MISSING'}`,
      };
    }
    return { status: 'AVAILABLE', payload, reason: null };
  } catch (error) {
    return {
      status: 'UNAVAILABLE', payload: null,
      reason: `CONTRACT:${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

export function useValidationEvidenceData(): ValidationEvidenceData {
  const authority = useHourlyAlphaReadModel();
  const validation = useConsoleSnapshot<ValidationDashboardPayload>(fetchValidationSnapshot);
  const experiments = useConsoleSnapshot<ExperimentsPayload>(fetchExperimentsSnapshot);
  return {
    authority,
    validation: admitValidationEvidence(validation),
    experiments: admitExperimentsEvidence(experiments),
  };
}
