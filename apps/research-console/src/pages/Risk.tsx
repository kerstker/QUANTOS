import React, { useEffect, useState } from 'react';
import { DecisionBadge, RiskBadge, MetricCard } from '@/components/status-badges';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchRiskDashboardSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockRiskReports } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { ComputationRiskReport, RiskDashboardPayload, RiskReport } from '@/lib/types/research';

const gateLabels: Record<string, string> = {
  market: bi('市场', 'Market'),
  liquidity: bi('流动性', 'Liquidity'),
  concentration: bi('集中度', 'Concentration'),
  correlation: bi('相关性', 'Correlation'),
  drawdown: bi('回撤', 'Drawdown'),
  regime: bi('市场状态', 'Regime'),
  stress: bi('压力测试', 'Stress'),
  capacity: bi('容量', 'Capacity'),
  model_alpha: bi('模型Alpha', 'Model Alpha'),
};

export default function Risk() {
  const [selectedAlpha, setSelectedAlpha] = useState('alpha-1');
  const [payload, setPayload] = useState<RiskDashboardPayload | null>(null);
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
    fetchRiskDashboardSnapshot(selectedAlpha, controller.signal)
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
  const primaryReport: ComputationRiskReport | RiskReport = usingBackend ? payload.primary_report : mockRiskReports[0];
  const reports: Array<ComputationRiskReport | RiskReport> = usingBackend ? payload.reports : mockRiskReports;

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <h1 className="text-lg font-bold text-slate-100">{bi('研究风险', 'Research Risk')}</h1>
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

      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        <MetricCard label={bi('门禁', 'Gates')} value={`${primaryReport.gates_passed}/${primaryReport.gates_total}`} />
        <MetricCard label={bi('已选 Alpha', 'Selected Alpha')} value={usingBackend ? payload.selected_alpha_id : selectedAlpha} />
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-3">
          <div className="mb-1 text-xs uppercase text-slate-500">{bi('决策', 'Decision')}</div>
          <DecisionBadge decision={primaryReport.decision} />
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-2 text-sm uppercase text-slate-400">{bi('风险门禁', 'Risk Gates')}</h2>
        <div className="grid gap-2 md:grid-cols-3">
          {Object.entries(primaryReport.gate_results).map(([g, p]) => (
            <div key={g} className="flex justify-between gap-2 rounded bg-slate-800 px-3 py-2 text-xs">
              <span className="text-slate-300">{gateLabels[g] ?? g.replace(/_/g, ' ')}</span>
              <RiskBadge passed={p} />
            </div>
          ))}
        </div>
        {primaryReport.reasons.length > 0 && (
          <div className="mt-3 space-y-1 text-xs text-red-300">
            {primaryReport.reasons.map(reason => <div key={reason}>{reason}</div>)}
          </div>
        )}
        {'reproducibility_hash' in primaryReport && primaryReport.reproducibility_hash && (
          <div className="mt-3 truncate text-xs text-slate-500">{bi('复现哈希', 'Hash')}: {primaryReport.reproducibility_hash}</div>
        )}
      </div>

      {reports[1] && (
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-2 text-sm uppercase text-slate-400">{bi('对照组合', 'Control Portfolio')}</h2>
          <DecisionBadge decision={reports[1].decision} />
          {reports[1].reasons.map((reason, i) => (
            <div key={i} className="py-1 text-xs text-red-300">{reason}</div>
          ))}
        </div>
      )}
    </div>
  );
}
