import React, { useEffect, useMemo, useState } from 'react';
import { StatusBadge, MetricCard } from '@/components/status-badges';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchAgentsSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockAgents } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { AgentsPayload } from '@/lib/types/research';

export default function Agents() {
  const [payload, setPayload] = useState<AgentsPayload | null>(null);
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
    fetchAgentsSnapshot(controller.signal)
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
  const agents = usingBackend ? payload.agents : mockAgents;
  const summary = useMemo(() => {
    if (usingBackend && payload) {
      return payload.summary;
    }

    return {
      active: mockAgents.filter(agent => agent.state === 'RUNNING').length,
      total_runs: mockAgents.reduce((sum, agent) => sum + agent.runs_completed, 0),
      approved: mockAgents.reduce((sum, agent) => sum + agent.alpha_approved, 0),
      budget_spent_cny: mockAgents.reduce((sum, agent) => sum + agent.budget_spent_cny, 0),
    };
  }, [payload, usingBackend]);

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <h1 className="text-lg font-bold text-slate-100">{bi('研究智能体', 'Research Agents')}</h1>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MetricCard label={bi('运行中', 'Active')} value={summary.active} />
        <MetricCard label={bi('总运行次数', 'Total Runs')} value={summary.total_runs} />
        <MetricCard label={bi('已批准 Alpha', 'Approved')} value={summary.approved} />
        <MetricCard label={bi('预算', 'Budget')} value={`CNY ${summary.budget_spent_cny}`} />
      </div>
      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[760px] text-xs font-mono">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('智能体', 'Agent')}</th>
              <th className="p-2 text-left">{bi('状态', 'State')}</th>
              <th className="p-2 text-right">{bi('运行次数', 'Runs')}</th>
              <th className="p-2 text-right">{bi('已批准', 'Approved')}</th>
              <th className="p-2 text-right">{bi('评分', 'Score')}</th>
              <th className="p-2 text-right">{bi('预算(CNY)', 'Budget(CNY)')}</th>
            </tr>
          </thead>
          <tbody>
            {agents.map(agent => (
              <tr key={agent.agent_id} className="border-b border-slate-800/50 hover:bg-slate-800/50">
                <td className="p-2 text-slate-300">{agent.name}</td>
                <td className="p-2"><StatusBadge status={agent.state} /></td>
                <td className="p-2 text-right text-cyan-400">{agent.runs_completed}</td>
                <td className="p-2 text-right text-emerald-400">{agent.alpha_approved}</td>
                <td className="p-2 text-right">{(agent.performance_score * 100).toFixed(0)}%</td>
                <td className="p-2 text-right text-amber-400">CNY {agent.budget_spent_cny}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
