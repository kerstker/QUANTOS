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
    <div className="flex flex-col gap-2 text-xs text-slate-500">
      {/* Neither data source is real research output: mock mode = frontend fixtures,
          backend mode = the console API's deterministic SYNTHETIC snapshots. Label it
          explicitly so a synthetic Sharpe / PnL / data-quality figure is never mistaken
          for measured alpha (audit P1). */}
      <div className="rounded border border-amber-400/60 bg-amber-50 px-2 py-1 text-amber-700">
        ⚠ {bi('合成演示数据 — 非真实研究结果或盈亏', 'Synthetic demo data — not real research results or PnL')}
        {usingBackend
          ? bi('（后端确定性合成快照）', ' (backend deterministic synthetic snapshot)')
          : bi('（前端 mock）', ' (frontend mock)')}
      </div>
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
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
    </div>
  );
}
