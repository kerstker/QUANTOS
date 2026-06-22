import React, { useEffect, useState } from 'react';
import { StatusBadge, MetricCard } from '@/components/status-badges';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchPaperTradingSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockPaperRuns } from '@/lib/mock-data/research-mocks';
import { bi, statusLabel } from '@/lib/i18n/bilingual';
import type { PaperTradingPayload } from '@/lib/types/research';

export default function PaperTrading() {
  const [selectedAlpha, setSelectedAlpha] = useState('alpha-1');
  const [payload, setPayload] = useState<PaperTradingPayload | null>(null);
  const [apiState, setApiState] = useState<ConsoleApiState>('disabled');
  const [apiError, setApiError] = useState<string | null>(null);
  const apiEnabled = isResearchConsoleApiEnabled();

  useEffect(() => {
    if (!apiEnabled) {
      setPayload(null);
      setApiState('disabled');
      setApiError(null);
      return;
    }

    const controller = new AbortController();
    setApiState('loading');
    setApiError(null);
    fetchPaperTradingSnapshot(selectedAlpha, controller.signal)
      .then(nextPayload => {
        setPayload(nextPayload);
        setApiState('connected');
      })
      .catch(error => {
        if (controller.signal.aborted) {
          return;
        }
        setPayload(null);
        setApiState('error');
        setApiError(error instanceof Error ? error.message : String(error));
      });

    return () => controller.abort();
  }, [apiEnabled, selectedAlpha]);

  const usingBackend = apiState === 'connected' && payload !== null;
  const r = usingBackend ? payload.run : mockPaperRuns[0];
  const maxEquity = Math.max(...r.equity_curve.map(point => point.equity), 1);
  const minEquity = Math.min(...r.equity_curve.map(point => point.equity), maxEquity - 1);
  const span = Math.max(maxEquity - minEquity, 1);

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <h1 className="text-lg font-bold text-slate-100">{bi('纸面交易', 'Paper Trading')}</h1>
          <div className="flex flex-wrap gap-2">
            {['alpha-1', 'alpha-2', 'alpha-3', 'alpha-4'].map(alphaId => (
              <button
                key={alphaId}
                onClick={() => setSelectedAlpha(alphaId)}
                className={`rounded px-3 py-2 text-xs ${selectedAlpha === alphaId ? 'bg-cyan-800 text-cyan-100' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}
              >
                {alphaId}
              </button>
            ))}
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MetricCard label={bi('状态', 'Status')} value={statusLabel(r.status)} />
        <MetricCard label={bi('夏普', 'Sharpe')} value={r.metrics.sharpe.toFixed(2)} />
        <MetricCard label={bi('最大回撤', 'MaxDD')} value={`${r.metrics.max_drawdown.toFixed(1)}%`} />
        <MetricCard label={bi('收益', 'Return')} value={`${r.metrics.annual_return.toFixed(1)}%`} />
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-2 text-sm uppercase text-slate-400">{bi('权益曲线', 'Equity Curve')}</h2>
        <div className="flex h-24 items-end gap-px">
          {r.equity_curve.filter((_, i) => i % 4 === 0).map((pt, i) => {
            const h = ((pt.equity - minEquity) / span) * 100;
            return <div key={`${pt.timestamp}-${i}`} className="flex-1 rounded-t bg-cyan-700" style={{height:`${Math.max(4, h)}%`}} />;
          })}
        </div>
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-2 text-sm uppercase text-slate-400">{bi('详情', 'Details')}</h2>
        <div className="space-y-1 text-xs text-slate-300">
          <div>Alpha: <span className="text-cyan-400">{r.alpha_id}</span></div>
          <div>{bi('成交数', 'Trades')}: <span className="text-cyan-400">{r.total_trades}</span></div>
          <div>{bi('权益', 'Equity')}: <span className="text-emerald-400">CNY {r.final_equity.toLocaleString()}</span></div>
          <div>{bi('状态', 'Status')}: <StatusBadge status={r.status} /></div>
        </div>
      </div>
    </div>
  );
}
