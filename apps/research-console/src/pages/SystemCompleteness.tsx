import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchSystemCompletenessSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { SystemCompletenessModule, SystemCompletenessPayload } from '@/lib/types/research';

const fallbackModuleSeeds: Array<[string, string, string, string, string, string, number, string]> = [
  ['overview', '研究总览', 'Research Overview', '/research', '/api/v1/console/overview', 'Console', 0.78, 'Persisted experiment history and role-aware drilldowns'],
  ['alpha-factory', '因子工厂', 'Alpha Factory', '/research/alpha-factory', '/api/v1/console/alpha-factory', 'Research Compute', 0.7, 'Persisted alpha DAG and feature store integration'],
  ['experiments', '实验注册表', 'Experiment Registry', '/research/experiments', '/api/v1/console/experiments', 'Research Governance', 0.68, 'Durable artifact storage and reproducibility replay'],
  ['data-catalog', '数据目录', 'Data Catalog', '/research/data-catalog', '/api/v1/console/data-catalog', 'Data Platform', 0.62, 'Real market data lake, entitlements, and schema registry'],
  ['validation', '验证看板', 'Validation Dashboard', '/research/validation', '/api/v1/console/validation', 'Validation', 0.67, 'Statistical test suite expansion and independent validation signoff'],
  ['backtest-explainability', '回测可解释性', 'Backtest Explainability', '/research/backtest-explainability', '/api/v1/console/backtest-explainability', 'Simulation', 0.72, 'Event-driven execution simulation and persisted reports'],
  ['scenario-stress', '情景压力', 'Scenario Stress', '/research/scenario-stress', '/api/v1/console/scenario-stress', 'Risk Simulation', 0.7, 'Governed scenario library and external regime feeds'],
  ['model-risk-review', '模型风险复核', 'Model Risk Review', '/research/model-risk-review', '/api/v1/console/model-risk-review', 'Model Risk', 0.66, 'Signed independent reviewer evidence packets'],
  ['evidence-ledger', '证据链', 'Evidence Ledger', '/research/evidence-ledger', '/api/v1/console/evidence-ledger', 'Governance', 0.74, 'External append-only storage and signature verification'],
  ['reviewer-workflow', '复核流程', 'Reviewer Workflow', '/research/reviewer-workflow', '/api/v1/console/reviewer-workflow', 'Governance', 0.64, 'Identity-backed assignments, SLA enforcement, and attestations'],
  ['access-control', '权限控制', 'Access Control', '/research/access-control', '/api/v1/console/access-control', 'Security', 0.61, 'Auth provider, session enforcement, and immutable audit logs'],
  ['monitoring-drift', '漂移监控', 'Monitoring Drift', '/research/monitoring-drift', '/api/v1/console/monitoring-drift', 'Monitoring', 0.63, 'Scheduler, metric persistence, and alert routing'],
  ['incident-response', '事件响应', 'Incident Response', '/research/incident-response', '/api/v1/console/incident-response', 'Operations', 0.62, 'Paging integration and incident mutation workflow'],
  ['promotion-control', '晋级控制', 'Promotion Control', '/research/promotion-control', '/api/v1/console/promotion-control', 'Release Governance', 0.69, 'Signed approvals and broker test environment evidence'],
  ['release-committee', '发布委员会', 'Release Committee', '/research/release-committee', '/api/v1/console/release-committee', 'Release Governance', 0.64, 'Quorum workflow, cryptographic signatures, and compliance approvals'],
  ['agents', '研究智能体', 'Research Agents', '/research/agents', '/api/v1/console/agents', 'AI Research Ops', 0.58, 'Agent planner, sandboxed tools, budget controls, and write path'],
  ['paper-trading', '纸面交易', 'Paper Trading', '/research/paper-trading', '/api/v1/console/paper-trading', 'Simulation', 0.61, 'Broker test adapter after Phase 3A approvals'],
  ['portfolio', '组合研究', 'Portfolio Research', '/research/portfolio', '/api/v1/console/portfolio', 'Portfolio', 0.65, 'Optimizer, constraints engine, and factor risk model'],
  ['risk', '风险看板', 'Risk Dashboard', '/research/risk', '/api/v1/console/risk-dashboard', 'Risk', 0.69, 'Policy versioning and independent approval history'],
  ['events', '事件', 'Events', '/research/events', '/api/v1/console/events', 'Observability', 0.56, 'Durable event bus, retention policy, and replay tooling'],
  ['computation-lab', '计算实验室', 'Computation Lab', '/research/computation-lab', '/api/v1/console/computation-lab', 'Research Compute', 0.73, 'Persisted scenario runs and comparison workspaces'],
  ['architecture', '系统架构', 'System Architecture', '/research/architecture', '/api/v1/console/architecture', 'Architecture', 0.66, 'Deployment topology and runtime dependency graph'],
  ['phase-3a', '3A 准入', 'Phase 3A Readiness', '/research/phase-3a', '/api/v1/console/phase-3a-readiness', 'Governance', 0.52, 'Broker, legal, compliance, credentials, and network approvals'],
  ['system-health', '系统健康', 'System Health', '/research/system-health', '/api/v1/console/system-health', 'Operations', 0.6, 'Real probes, uptime SLOs, dependency health, and CI status'],
  ['system-completeness', '系统完整度审查', 'System Completeness Audit', '/research/system-completeness', '/api/v1/console/system-completeness', 'Governance', 0.76, 'Automated extraction from route manifests, OpenAPI, and CI evidence'],
];

const fallbackModules: SystemCompletenessModule[] = fallbackModuleSeeds.map(([id, nameZh, name, route, endpoint, layer, completionScore, gap]) => ({
  id,
  name,
  name_zh: nameZh,
  route,
  endpoint,
  layer,
  status: id === 'phase-3a' ? 'BLOCKED' : 'BACKEND_BACKED',
  backend_contract: 'Read-only computed console snapshot',
  frontend_status: 'ROUTED',
  completion_score: completionScore,
  gap,
}));

const fallbackCompleteness: SystemCompletenessPayload = {
  source: 'FRONTEND_SYSTEM_COMPLETENESS_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  audit_mode: 'READ_ONLY_SYSTEM_COMPLETENESS_AUDIT',
  current_level: 'L2_5_RESEARCH_CONTROL_PLANE',
  institutional_target: 'TOP_TIER_AI_QUANT_TRADING_SYSTEM',
  readiness_score: 0.66,
  summary: {
    frontend_routes: fallbackModules.length,
    api_endpoints: fallbackModules.length,
    backend_backed_modules: fallbackModules.length,
    fully_operational_modules: fallbackModules.filter(module => module.completion_score >= 0.72).length,
    read_only_modules: fallbackModules.length,
    open_high_gaps: 7,
    live_trading_authorized: false,
  },
  modules: fallbackModules,
  checks: [
    {id: 'CHK-001', name: 'Frontend route inventory', status: 'PASS', evidence: '25 research console routes are represented in navigation and router'},
    {id: 'CHK-002', name: 'Backend endpoint inventory', status: 'PASS', evidence: '25 FastAPI read-only console endpoints plus /health are represented'},
    {id: 'CHK-003', name: 'Execution safety boundary', status: 'PASS', evidence: 'All console payloads are read_only and live trading remains disallowed'},
    {id: 'CHK-004', name: 'Top-tier institutional readiness', status: 'FAIL', evidence: 'The system is L2/L2.5 research control plane, not a full top-tier trading stack yet'},
  ],
  gaps: [
    {id: 'G01', area: 'Institutional Data Platform', severity: 'CRITICAL', status: 'OPEN', impact: 'No real market data lake, survivorship-bias controls, corporate actions, or vendor entitlement workflow yet', next_action: 'Implement versioned point-in-time market data ingestion, validation, and feature store storage'},
    {id: 'G02', area: 'Research Execution Engine', severity: 'HIGH', status: 'PARTIAL', impact: 'Backtests are deterministic computed snapshots, not a full event-driven execution simulator', next_action: 'Add event-driven backtest kernel, cost models, slippage, borrow, and artifact persistence'},
    {id: 'G03', area: 'AI Agent Platform', severity: 'HIGH', status: 'PARTIAL', impact: 'Agent dashboards exist, but planner, sandboxed execution, budget limits, and experiment write paths are not integrated', next_action: 'Build governed research-agent execution service'},
    {id: 'G04', area: 'Security And Access', severity: 'HIGH', status: 'PARTIAL', impact: 'RBAC is a read-only audit view rather than enforced auth/session middleware', next_action: 'Add identity provider, session enforcement, signed exports, and immutable access audit logs'},
    {id: 'G05', area: 'Production Trading Boundary', severity: 'CRITICAL', status: 'INTENTIONALLY_BLOCKED', impact: 'No broker test environment, credentials, order routing, or live trading capability is authorized', next_action: 'Close Phase 3A E01/E02/E03/E04/E06 first'},
    {id: 'G06', area: 'Observability And Operations', severity: 'HIGH', status: 'PARTIAL', impact: 'Monitoring, incidents, and health are snapshots, not scheduler-backed workflows', next_action: 'Add metrics persistence, probes, alert routing, incident lifecycle, and postmortem evidence'},
    {id: 'G07', area: 'CI, Release, And Evidence Automation', severity: 'HIGH', status: 'PARTIAL', impact: 'Full CI provenance, signed release packs, and automated evidence capture are incomplete', next_action: 'Wire all tests and governance gates into reproducible CI evidence'},
  ],
  roadmap: [
    {phase: 'Phase A', objective: 'Harden read-only research console and API contracts', exit_criteria: 'Typed contracts, tests, browser smoke checks, route inventory, and gap dashboard are stable', status: 'IN_PROGRESS'},
    {phase: 'Phase B', objective: 'Build institutional research data and feature platform', exit_criteria: 'Point-in-time market data, feature store, lineage, and quality gates are persisted', status: 'NOT_STARTED'},
    {phase: 'Phase C', objective: 'Upgrade simulation, validation, portfolio, and risk engines', exit_criteria: 'Event-driven backtests, cost models, optimizer, risk model, and stress libraries are governed', status: 'NOT_STARTED'},
    {phase: 'Phase D', objective: 'Add governed AI research-agent execution', exit_criteria: 'Agents can create reproducible experiments under budget, sandbox, and reviewer controls', status: 'NOT_STARTED'},
    {phase: 'Phase E', objective: 'Prepare broker test environment only after Phase 3A approvals', exit_criteria: 'Broker, legal, compliance, credentials, security, and network evidence are approved', status: 'BLOCKED'},
  ],
  safety_boundary: {
    read_only_console: true,
    execution_boundary: 'LIVE_TRADING_DISALLOWED',
    authorized_level: 'L2_DRY_RUN_ONLY',
    blocked_until: ['E01', 'E02', 'E03', 'E04', 'E06'],
  },
};

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

function statusClass(status: string) {
  if (status === 'PASS' || status === 'BACKEND_BACKED' || status === 'ROUTED') {
    return 'border-emerald-800 bg-emerald-950/20 text-emerald-300';
  }
  if (status === 'FAIL' || status === 'BLOCKED' || status === 'CRITICAL') {
    return 'border-red-800 bg-red-950/20 text-red-300';
  }
  if (status === 'PARTIAL' || status === 'IN_PROGRESS' || status === 'OPEN') {
    return 'border-amber-800 bg-amber-950/20 text-amber-300';
  }
  return 'border-slate-700 bg-slate-950/60 text-slate-300';
}

function severityClass(severity: string) {
  if (severity === 'CRITICAL') {
    return 'text-red-300';
  }
  if (severity === 'HIGH') {
    return 'text-orange-300';
  }
  return 'text-slate-300';
}

function StatusPill({status}: {status: string}) {
  return <span className={`inline-flex rounded border px-2 py-0.5 text-[10px] font-bold ${statusClass(status)}`}>{status}</span>;
}

export default function SystemCompleteness() {
  const [payload, setPayload] = useState<SystemCompletenessPayload | null>(null);
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
    fetchSystemCompletenessSnapshot(controller.signal)
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
  const audit = usingBackend && payload ? payload : fallbackCompleteness;
  const modulesByLayer = useMemo(() => audit.modules.reduce<Record<string, SystemCompletenessModule[]>>((acc, module) => {
    acc[module.layer] = [...(acc[module.layer] ?? []), module];
    return acc;
  }, {}), [audit.modules]);

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('系统完整度审查', 'System Completeness Audit')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision={audit.safety_boundary.authorized_level} />
              <DecisionBadge decision={audit.safety_boundary.execution_boundary} />
            </div>
          </div>
          <div className="text-xs text-slate-500 xl:text-right">
            <div>{bi('当前等级', 'Current level')}: {audit.current_level}</div>
            <div>{bi('目标等级', 'Target level')}: {audit.institutional_target}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={audit.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('完整度', 'Readiness')} value={pct(audit.readiness_score)} />
        <MetricCard label={bi('前端路径', 'Routes')} value={audit.summary.frontend_routes} />
        <MetricCard label={bi('API 接口', 'API Endpoints')} value={audit.summary.api_endpoints} />
        <MetricCard label={bi('后端支撑', 'Backend Backed')} value={audit.summary.backend_backed_modules} />
        <MetricCard label={bi('只读模块', 'Read-only')} value={audit.summary.read_only_modules} />
        <MetricCard label={bi('可运行模块', 'Operational')} value={audit.summary.fully_operational_modules} />
        <MetricCard label={bi('高风险缺口', 'High Gaps')} value={audit.summary.open_high_gaps} />
        <MetricCard label={bi('实盘授权', 'Live Auth')} value={audit.summary.live_trading_authorized ? 'YES' : 'NO'} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.3fr_0.7fr]">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <div className="mb-3 flex flex-col gap-1 md:flex-row md:items-end md:justify-between">
            <h2 className="text-sm uppercase text-slate-400">{bi('分层覆盖图', 'Layer Coverage Map')}</h2>
            <div className="text-xs text-slate-500">{bi('按模块展示前端路径、后端接口和剩余工程缺口。', 'Shows route, endpoint, and remaining engineering gap by module.')}</div>
          </div>
          <div className="space-y-4">
            {Object.entries(modulesByLayer).map(([layer, modules]) => (
              <div key={layer}>
                <div className="mb-2 text-[10px] uppercase tracking-wider text-cyan-400">{layer}</div>
                <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                  {modules.map(module => (
                    <div key={module.id} className="rounded border border-slate-800 bg-slate-950/50 p-3">
                      <div className="mb-2 flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-[10px] uppercase text-slate-500">{module.id}</div>
                          <div className="mt-1 break-words text-sm font-bold text-slate-100">{module.name_zh} / {module.name}</div>
                        </div>
                        <StatusPill status={module.status} />
                      </div>
                      <div className="mb-2 h-2 overflow-hidden rounded bg-slate-800">
                        <div className="h-full bg-cyan-700" style={{width: pct(module.completion_score)}} />
                      </div>
                      <div className="grid gap-1 text-xs">
                        <div className="text-slate-400">{module.route}</div>
                        <div className="text-slate-500">{module.endpoint}</div>
                        <div className="text-slate-300">{pct(module.completion_score)} - {module.backend_contract}</div>
                        <div className="text-amber-300">{module.gap}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
            <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('审查检查', 'Audit Checks')}</h2>
            <div className="space-y-3">
              {audit.checks.map(check => (
                <div key={check.id} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs">
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <div>
                      <div className="font-bold text-slate-100">{check.id}</div>
                      <div className="mt-1 text-slate-300">{check.name}</div>
                    </div>
                    <StatusPill status={check.status} />
                  </div>
                  <div className="text-slate-500">{check.evidence}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-red-950/30 border border-red-900/50 rounded-lg p-4">
            <h2 className="mb-3 text-sm uppercase text-red-300">{bi('安全边界', 'Safety Boundary')}</h2>
            <div className="space-y-2 text-xs text-red-200">
              <div>{bi('控制台只读', 'Read-only console')}: {String(audit.safety_boundary.read_only_console)}</div>
              <div>{bi('执行边界', 'Execution boundary')}: {audit.safety_boundary.execution_boundary}</div>
              <div>{bi('授权等级', 'Authorized level')}: {audit.safety_boundary.authorized_level}</div>
              <div>{bi('阻塞项', 'Blocked by')}: {audit.safety_boundary.blocked_until.join(', ')}</div>
            </div>
          </div>
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[980px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">ID</th>
              <th className="p-2 text-left">{bi('领域', 'Area')}</th>
              <th className="p-2 text-left">{bi('严重度', 'Severity')}</th>
              <th className="p-2 text-left">{bi('状态', 'Status')}</th>
              <th className="p-2 text-left">{bi('影响', 'Impact')}</th>
              <th className="p-2 text-left">{bi('下一步', 'Next Action')}</th>
            </tr>
          </thead>
          <tbody>
            {audit.gaps.map(gap => (
              <tr key={gap.id} className="border-b border-slate-800/50">
                <td className="p-2 font-bold text-cyan-300">{gap.id}</td>
                <td className="p-2 text-slate-200">{gap.area}</td>
                <td className={`p-2 font-bold ${severityClass(gap.severity)}`}>{gap.severity}</td>
                <td className="p-2"><StatusPill status={gap.status} /></td>
                <td className="p-2 text-slate-400">{gap.impact}</td>
                <td className="p-2 text-slate-500">{gap.next_action}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-3 lg:grid-cols-5">
        {audit.roadmap.map(item => (
          <div key={item.phase} className="rounded border border-slate-800 bg-slate-900 p-3 text-xs">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div className="font-bold text-cyan-300">{item.phase}</div>
              <StatusPill status={item.status} />
            </div>
            <div className="font-bold text-slate-100">{item.objective}</div>
            <div className="mt-2 text-slate-500">{item.exit_criteria}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
