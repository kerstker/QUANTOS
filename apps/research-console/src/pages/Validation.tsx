import React, { useEffect, useState } from 'react';
import { DecisionBadge, RiskBadge, MetricCard } from '@/components/status-badges';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchValidationSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockValidationReports } from '@/lib/mock-data/research-mocks';
import { bi, statusLabel } from '@/lib/i18n/bilingual';
import type { ValidationDashboardPayload } from '@/lib/types/research';

export default function Validation() {
  const [payload, setPayload] = useState<ValidationDashboardPayload | null>(null);
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
    fetchValidationSnapshot(controller.signal)
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
  }, [apiEnabled]);

  const usingBackend = apiState === 'connected' && payload !== null;
  const reports = usingBackend ? payload.reports : mockValidationReports;
  const r = usingBackend ? payload.selected_report : mockValidationReports[0];
  const gateLabels: Array<[string, string, boolean]> = [
    [bi('泄露检查', 'Leakage'), 'leakage', r.leakage_pass],
    [bi('滚动样本外', 'Walk-Forward'), 'walk-forward', r.walk_forward_pass],
    [bi('多重检验', 'Multiple Testing'), 'multiple-testing', r.multiple_testing_pass],
    [bi('成本检查', 'Costs'), 'costs', r.costs_pass],
    [bi('容量检查', 'Capacity'), 'capacity', r.capacity_pass],
    [bi('衰减检查', 'Decay'), 'decay', r.decay_pass],
  ];

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <h1 className="text-lg font-bold text-slate-100">{bi('Alpha 验证', 'Alpha Validation')}</h1>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MetricCard label={bi('决策', 'Decision')} value={statusLabel(r.decision)} />
        <MetricCard label={bi('门禁', 'Gates')} value={`${r.gates_passed}/${r.gates_total}`} />
        <MetricCard label={bi('样本外夏普', 'OOS Sharpe')} value={r.oos_sharpe.toFixed(2)} />
        <MetricCard label={bi('净夏普', 'Net Sharpe')} value={r.net_sharpe.toFixed(2)} />
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-2 text-sm uppercase text-slate-400">{bi('门禁结果', 'Gate Results')}</h2>
        <div className="grid gap-2 md:grid-cols-3">
          {gateLabels.map(([label, key, passed]) => (
            <div key={key} className="flex justify-between gap-2 rounded bg-slate-800 px-3 py-2 text-xs">
              <span className="text-slate-300">{label}</span>
              <RiskBadge passed={passed} />
            </div>
          ))}
        </div>
        {r.reasons.length > 0 && (
          <div className="mt-3 space-y-1 text-xs text-red-300">
            {r.reasons.map(reason => <div key={reason}>{reason}</div>)}
          </div>
        )}
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-2 text-sm uppercase text-slate-400">{bi('全部报告', 'All Reports')}</h2>
        {reports.map(rpt => (
          <div key={rpt.run_id} className="flex flex-wrap items-center gap-3 border-b border-slate-800 py-2 text-xs">
            <span className="font-mono text-slate-300">{rpt.alpha_id}</span>
            <DecisionBadge decision={rpt.decision} />
            <span className="text-slate-500">{bi('夏普', 'Sharpe')}: {rpt.net_sharpe.toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
