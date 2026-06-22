import React, { useEffect, useMemo, useState } from 'react';
import { DecisionBadge, StatusBadge, MetricCard } from '@/components/status-badges';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchOverviewSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockStats, mockAgents } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { FunnelPoint, OverviewPayload } from '@/lib/types/research';

const funnelColors: Record<string, string> = {
  hypotheses: 'bg-slate-700',
  candidates: 'bg-cyan-800',
  validated: 'bg-blue-800',
  paper_passed: 'bg-amber-800',
  portfolio_candidates: 'bg-orange-800',
  risk_approved: 'bg-emerald-800',
};

const fallbackFunnel: FunnelPoint[] = [
  {id: 'hypotheses', label: 'Hypotheses', value: mockStats.total_hypotheses},
  {id: 'candidates', label: 'Candidates', value: mockStats.alpha_candidates},
  {id: 'validated', label: 'Validated', value: Math.round(mockStats.alpha_candidates * mockStats.validation_pass_rate)},
  {id: 'paper_passed', label: 'Paper Passed', value: Math.round(mockStats.alpha_candidates * mockStats.validation_pass_rate * mockStats.paper_pass_rate)},
  {id: 'portfolio_candidates', label: 'Portfolio Cand.', value: mockStats.portfolio_candidates},
  {id: 'risk_approved', label: 'Risk Approved', value: mockStats.risk_approved},
];

function funnelLabel(point: FunnelPoint) {
  const labels: Record<string, string> = {
    hypotheses: bi('假设', 'Hypotheses'),
    candidates: bi('候选', 'Candidates'),
    validated: bi('已验证', 'Validated'),
    paper_passed: bi('纸面通过', 'Paper Passed'),
    portfolio_candidates: bi('组合候选', 'Portfolio Cand.'),
    risk_approved: bi('风险通过', 'Risk Approved'),
  };
  return labels[point.id] ?? point.label;
}

export default function Overview() {
  const [payload, setPayload] = useState<OverviewPayload | null>(null);
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
    fetchOverviewSnapshot(controller.signal)
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
  const stats = usingBackend ? payload.stats : mockStats;
  const agents = usingBackend ? payload.agents : mockAgents;
  const funnel = useMemo(() => usingBackend ? payload.funnel : fallbackFunnel, [payload, usingBackend]);

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <h1 className="text-lg font-bold text-slate-100">{bi('研究总览', 'Research Overview')}</h1>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="text-sm text-slate-400 uppercase mb-3">{bi('管线漏斗', 'Pipeline Funnel')}</h2>
        <div className="flex gap-1 items-end h-36">
          {funnel.map((s, i) => (
            <div key={s.id} className="flex h-full flex-1 flex-col items-center justify-end">
              <div className={`${funnelColors[s.id] ?? 'bg-slate-700'} w-full rounded-t`} style={{height:`${Math.max(12, 100 - (i * 15))}%`, minHeight: 12}} />
              <span className="mt-1 text-center text-[10px] leading-tight text-slate-400">{funnelLabel(s)}</span>
              <span className="text-xs font-bold text-slate-200">{s.value.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MetricCard label={bi('假设', 'Hypotheses')} value={stats.total_hypotheses} />
        <MetricCard label={bi('候选因子', 'Candidates')} value={stats.alpha_candidates} />
        <MetricCard label={bi('验证通过率', 'Validation Rate')} value={(stats.validation_pass_rate*100).toFixed(1)} unit="%" />
        <MetricCard label={bi('纸面通过率', 'Paper Pass Rate')} value={(stats.paper_pass_rate*100).toFixed(1)} unit="%" />
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="text-sm text-slate-400 uppercase mb-2">{bi('拒绝原因', 'Rejection Reasons')}</h2>
        <div className="flex flex-wrap gap-2">
          {Object.entries(stats.rejection_reasons).map(([r, c]) => (
            <div key={r} className="flex items-center gap-1 rounded bg-slate-800 px-2 py-1 text-xs">
              <DecisionBadge decision={r} />
              <span className="text-slate-400">{c}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="text-sm text-slate-400 uppercase mb-2">{bi('活跃智能体', 'Active Agents')}</h2>
        {agents.slice(0, 4).map(a => (
          <div key={a.agent_id} className="flex items-center justify-between gap-2 border-b border-slate-800 py-1 text-xs">
            <span className="text-slate-300">{a.name}</span>
            <StatusBadge status={a.state} />
            <span className="text-slate-500">{a.runs_completed} {bi('次', 'runs')}</span>
            <span className="text-slate-500">CNY {a.budget_spent_cny}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
