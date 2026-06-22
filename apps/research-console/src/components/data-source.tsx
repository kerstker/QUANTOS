import React from 'react';
import { DecisionBadge } from '@/components/status-badges';
import { RESEARCH_CONSOLE_API_BASE } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';

export type ConsoleApiState = 'disabled' | 'loading' | 'connected' | 'error';

export function apiStateDecision(apiState: ConsoleApiState) {
  if (apiState === 'connected') {
    return 'API_CONNECTED';
  }
  if (apiState === 'loading') {
    return 'API_LOADING';
  }
  if (apiState === 'error') {
    return 'API_ERROR';
  }
  return 'NOT_CONNECTED';
}

export function DataSourceHeader({
  usingBackend,
  apiState,
  source,
  error,
}: {
  usingBackend: boolean;
  apiState: ConsoleApiState;
  source?: string;
  error?: string | null;
}) {
  return (
    <div className="flex flex-col gap-2 text-xs text-slate-500 md:flex-row md:items-center md:justify-between">
      <div className="flex flex-wrap gap-2">
        <DecisionBadge decision={usingBackend ? 'BACKEND_COMPUTED' : 'MOCK_MODE'} />
        <DecisionBadge decision={apiStateDecision(apiState)} />
        <DecisionBadge decision="READ_ONLY" />
      </div>
      <div className="text-left md:text-right">
        <div>{bi('数据源', 'Source')}: {source ?? (usingBackend ? 'BACKEND_COMPUTED_SNAPSHOT' : 'FRONTEND_MOCK_FALLBACK')}</div>
        <div>{error ? `${bi('错误', 'Error')}: ${error}` : `API: ${RESEARCH_CONSOLE_API_BASE || bi('未启用', 'disabled')}`}</div>
      </div>
    </div>
  );
}
