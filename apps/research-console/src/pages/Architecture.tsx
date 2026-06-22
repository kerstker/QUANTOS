import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchArchitectureSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ArchitectureModule, ArchitecturePayload } from '@/lib/types/research';

const fallbackArchitecture: ArchitecturePayload = {
  source: 'FRONTEND_ARCHITECTURE_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  current_level: 'L2_DRY_RUN_ONLY',
  target_level: 'L3_BROKER_TEST_ENV_READY',
  phase3a_decision: 'NO_GO',
  implementation_authorized: false,
  architecture_score: 0.52,
  modules: [
    {id: 'frontend-console', name: 'Research Console', layer: 'Frontend', status: 'READY', maturity: 0.72, owner: 'Research Platform', capability: 'Bilingual read-only dashboards', next_gap: 'Role-aware workflows'},
    {id: 'console-api', name: 'Console API', layer: 'API', status: 'READY', maturity: 0.68, owner: 'Research Platform', capability: 'Read-only computed snapshots', next_gap: 'Versioned contracts and auth'},
    {id: 'alpha-engine', name: 'Alpha / Factor Engine', layer: 'Research Compute', status: 'READY', maturity: 0.46, owner: 'Alpha Research', capability: 'IC, screening, candidate scoring', next_gap: 'Feature store and alpha DAG'},
    {id: 'portfolio-research', name: 'Portfolio Research', layer: 'Simulation', status: 'READY', maturity: 0.48, owner: 'Portfolio Research', capability: 'Weights, drawdown, attribution', next_gap: 'Optimizer and factor risk model'},
    {id: 'risk-gate', name: 'Research Risk Gate', layer: 'Risk', status: 'READY', maturity: 0.58, owner: 'Risk', capability: 'Fail-closed gates', next_gap: 'Policy versioning'},
    {id: 'phase3a-governance', name: 'Phase 3A Governance', layer: 'Governance', status: 'BLOCKED', maturity: 0.42, owner: 'CTO / Compliance / Security', capability: 'Readiness matrix and blockers', next_gap: 'Close E01/E02/E03/E04/E06'},
  ],
  data_flows: [
    {from: 'frontend-console', to: 'console-api', label: 'GET read-only snapshots', mode: 'READ_ONLY'},
    {from: 'console-api', to: 'alpha-engine', label: 'factor metrics and candidates', mode: 'COMPUTED_SNAPSHOT'},
    {from: 'alpha-engine', to: 'portfolio-research', label: 'validated research candidates', mode: 'SIMULATION'},
    {from: 'portfolio-research', to: 'risk-gate', label: 'returns, weights, capacity', mode: 'RISK_REVIEW'},
    {from: 'risk-gate', to: 'phase3a-governance', label: 'gate evidence', mode: 'GOVERNANCE'},
  ],
  gaps: [
    {id: 'G01', area: 'Market Data Lake', severity: 'HIGH', status: 'MISSING', next_action: 'Add versioned market data snapshots'},
    {id: 'G02', area: 'Feature Store', severity: 'HIGH', status: 'MISSING', next_action: 'Persist factor definitions and lineage hashes'},
    {id: 'G03', area: 'Backtest Engine', severity: 'HIGH', status: 'PARTIAL', next_action: 'Add event-driven backtests and artifacts'},
  ],
  safety_boundary: {
    read_only_console: true,
    execution_boundary: 'LIVE_TRADING_DISALLOWED',
    forbidden_capabilities: [
      'No real CTP/FIX implementation',
      'No broker SDK imports',
      'No socket connections',
      'No credential files',
      'No live order APIs',
    ],
  },
};

function statusClass(status: string) {
  if (status === 'READY') {
    return 'border-emerald-800 bg-emerald-950/20 text-emerald-300';
  }
  if (status === 'BLOCKED') {
    return 'border-red-800 bg-red-950/20 text-red-300';
  }
  if (status === 'PARTIAL') {
    return 'border-amber-800 bg-amber-950/20 text-amber-300';
  }
  return 'border-slate-700 bg-slate-950/60 text-slate-300';
}

function severityClass(severity: string) {
  if (severity === 'HIGH') {
    return 'text-red-300';
  }
  if (severity === 'MEDIUM') {
    return 'text-amber-300';
  }
  return 'text-slate-300';
}

function layerLabel(layer: string) {
  const labels: Record<string, string> = {
    Frontend: bi('前端', 'Frontend'),
    API: bi('API', 'API'),
    'Research Compute': bi('研究计算', 'Research Compute'),
    Simulation: bi('模拟', 'Simulation'),
    Risk: bi('风险', 'Risk'),
    Governance: bi('治理', 'Governance'),
  };
  return labels[layer] ?? layer;
}

function ModuleCard({module}: {module: ArchitectureModule}) {
  return (
    <div className={`min-w-0 rounded border p-3 ${statusClass(module.status)}`}>
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-wider text-cyan-300">{module.id}</div>
          <div className="mt-1 break-words text-sm font-bold text-slate-100">{module.name}</div>
        </div>
        <span className="rounded bg-slate-950/60 px-2 py-0.5 text-[10px]">{module.status}</span>
      </div>
      <div className="mb-2 h-2 overflow-hidden rounded bg-slate-800">
        <div className="h-full bg-cyan-700" style={{width: `${Math.round(module.maturity * 100)}%`}} />
      </div>
      <div className="grid gap-2 text-xs">
        <div className="text-slate-400">{bi('负责人', 'Owner')}: {module.owner}</div>
        <div className="text-slate-300">{module.capability}</div>
        <div className="text-slate-500">{bi('下一缺口', 'Next gap')}: {module.next_gap}</div>
      </div>
    </div>
  );
}

export default function Architecture() {
  const [payload, setPayload] = useState<ArchitecturePayload | null>(null);
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
    fetchArchitectureSnapshot(controller.signal)
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
  const architecture = usingBackend && payload ? payload : fallbackArchitecture;
  const modulesByLayer = useMemo(() => architecture.modules.reduce<Record<string, ArchitectureModule[]>>((acc, module) => {
    acc[module.layer] = [...(acc[module.layer] ?? []), module];
    return acc;
  }, {}), [architecture.modules]);
  const readyModules = architecture.modules.filter(module => module.status === 'READY').length;
  const blockedModules = architecture.modules.filter(module => module.status === 'BLOCKED').length;
  const highGaps = architecture.gaps.filter(gap => gap.severity === 'HIGH').length;

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('系统架构', 'System Architecture')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision={architecture.current_level} />
              <DecisionBadge decision={architecture.phase3a_decision} />
              <DecisionBadge decision={architecture.safety_boundary.execution_boundary} />
            </div>
          </div>
          <div className="text-xs text-slate-500 xl:text-right">
            <div>{bi('目标等级', 'Target')}: {architecture.target_level}</div>
            <div>{bi('实现授权', 'Authorized')}: {architecture.implementation_authorized ? 'true' : 'false'}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={architecture.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MetricCard label={bi('架构成熟度', 'Architecture Score')} value={Math.round(architecture.architecture_score * 100)} unit="%" />
        <MetricCard label={bi('模块数量', 'Modules')} value={architecture.modules.length} />
        <MetricCard label={bi('就绪模块', 'Ready')} value={readyModules} />
        <MetricCard label={bi('高优缺口', 'High Gaps')} value={highGaps + blockedModules} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <div className="mb-4">
            <h2 className="text-sm uppercase text-slate-400">{bi('分层模块图', 'Layered Module Map')}</h2>
            <div className="mt-1 text-xs text-slate-500">{bi('当前系统的研究、模拟、风险与治理模块边界。', 'Current research, simulation, risk, and governance module boundaries.')}</div>
          </div>
          <div className="space-y-4">
            {Object.entries(modulesByLayer).map(([layer, modules]) => (
              <div key={layer}>
                <div className="mb-2 text-[10px] uppercase tracking-wider text-slate-500">{layerLabel(layer)}</div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {modules.map(module => <ModuleCard key={module.id} module={module} />)}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('数据流', 'Data Flow')}</h2>
            <div className="space-y-3 text-xs">
              {architecture.data_flows.map(flow => (
                <div key={`${flow.from}-${flow.to}`} className="rounded border border-slate-800 bg-slate-950/60 p-3">
                  <div className="break-words text-cyan-300">{flow.from} → {flow.to}</div>
                  <div className="mt-1 text-slate-300">{flow.label}</div>
                  <div className="mt-2 inline-flex rounded bg-slate-800 px-2 py-0.5 text-[10px] text-slate-400">{flow.mode}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-red-950/30 border border-red-900/50 rounded-lg p-4">
            <h2 className="mb-3 text-sm uppercase text-red-300">{bi('安全边界', 'Safety Boundary')}</h2>
            <div className="space-y-2 text-xs text-red-200">
              {architecture.safety_boundary.forbidden_capabilities.map(item => <div key={item}>{item}</div>)}
            </div>
          </div>
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[860px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">ID</th>
              <th className="p-2 text-left">{bi('领域', 'Area')}</th>
              <th className="p-2 text-left">{bi('严重度', 'Severity')}</th>
              <th className="p-2 text-left">{bi('状态', 'Status')}</th>
              <th className="p-2 text-left">{bi('下一步', 'Next Action')}</th>
            </tr>
          </thead>
          <tbody>
            {architecture.gaps.map(gap => (
              <tr key={gap.id} className="border-b border-slate-800/50">
                <td className="p-2 font-bold text-cyan-300">{gap.id}</td>
                <td className="p-2 text-slate-200">{gap.area}</td>
                <td className={`p-2 font-bold ${severityClass(gap.severity)}`}>{gap.severity}</td>
                <td className="p-2 text-slate-400">{gap.status}</td>
                <td className="p-2 text-slate-500">{gap.next_action}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
