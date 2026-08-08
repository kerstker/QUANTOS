import { useEffect, useState } from 'react';

import {
  fetchHourlyAlphaSnapshot,
  getConsoleRuntimeMode,
  isResearchConsoleApiEnabled,
} from '@/lib/api/research-console-api';
import {
  classifyHourlyAlphaSnapshot,
  type HourlyAlphaPhase,
  type HourlyAlphaSnapshot,
} from './hourly-alpha-contract';

export interface HourlyAlphaReadModelState {
  phase: HourlyAlphaPhase;
  snapshot: HourlyAlphaSnapshot | null;
  reason: string | null;
}

const REQUEST_TIMEOUT_MS = 10_000;

export function useHourlyAlphaReadModel(): HourlyAlphaReadModelState {
  const [state, setState] = useState<HourlyAlphaReadModelState>({
    phase: 'LOADING', snapshot: null, reason: null,
  });

  useEffect(() => {
    if (!isResearchConsoleApiEnabled()) {
      setState({
        phase: 'UNAVAILABLE',
        snapshot: null,
        reason: getConsoleRuntimeMode() === 'MOCK'
          ? 'SYNTHETIC_FALLBACK_FORBIDDEN'
          : 'REAL_API_NOT_CONFIGURED',
      });
      return undefined;
    }
    const controller = new AbortController();
    let active = true;
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);
    setState({ phase: 'LOADING', snapshot: null, reason: null });
    fetchHourlyAlphaSnapshot(controller.signal)
      .then(snapshot => {
        if (!active) return;
        const classification = classifyHourlyAlphaSnapshot(snapshot);
        setState({ ...classification, snapshot });
      })
      .catch(error => {
        if (!active) return;
        setState({
          phase: 'UNAVAILABLE',
          snapshot: null,
          reason: timedOut
            ? 'HOURLY_ALPHA_REQUEST_TIMEOUT'
            : error instanceof Error ? error.message : String(error),
        });
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      active = false;
      clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  return state;
}
