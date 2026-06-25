import React, { useMemo } from 'react';
import { fetchSystemCompletenessSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { SystemCompletenessModule, SystemCompletenessPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, KeyValue, type Column,
} from '@/ui/primitives';
import { RadialGauge, Donut, Histogram, DivergingBars } from '@/ui/charts';
import { num, pct, statusTone, type Tone } from '@/ui/format';

/* ---------------------------------------------------------------------------
 * Mock fallback — preserved verbatim from the prior data wiring. Synthetic
 * audit snapshot; not real research output (see ProvenanceRibbon).
 * ------------------------------------------------------------------------- */
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

/* ---------------------------------------------------------------------------
 * Presentation helpers (deterministic; no random / no Date.now).
 * ------------------------------------------------------------------------- */
const severityTone: Record<string, Tone> = {
  CRITICAL: 'neg', HIGH: 'warn', MEDIUM: 'info', LOW: 'muted',
};

function completionTone(score: number): Tone {
  if (score >= 0.72) return 'pos';
  if (score >= 0.6) return 'accent';
  return 'warn';
}

export default function SystemCompleteness() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<SystemCompletenessPayload>(
    s => fetchSystemCompletenessSnapshot(s));

  const audit = usingBackend ? payload! : fallbackCompleteness;
  const sb = audit.safety_boundary;
  const sm = audit.summary;

  /* Layer composition donut (module count by layer). */
  const layerSegments = useMemo(() => {
    const counts = audit.modules.reduce<Record<string, number>>((acc, m) => {
      acc[m.layer] = (acc[m.layer] ?? 0) + 1;
      return acc;
    }, {});
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([label, value]) => ({ label, value }));
  }, [audit.modules]);

  /* Completion-score distribution histogram across coverage bands. */
  const completionBins = useMemo(() => {
    const bands: Array<{ label: string; lo: number; hi: number }> = [
      { label: '<0.6', lo: -Infinity, hi: 0.6 },
      { label: '0.6–0.7', lo: 0.6, hi: 0.7 },
      { label: '0.7–0.75', lo: 0.7, hi: 0.75 },
      { label: '≥0.75', lo: 0.75, hi: Infinity },
    ];
    return bands.map(b => ({
      label: b.label,
      count: audit.modules.filter(m => m.completion_score >= b.lo && m.completion_score < b.hi).length,
    }));
  }, [audit.modules]);

  /* Open-gap attribution by severity weight (CRITICAL heavier than HIGH). */
  const gapWeight: Record<string, number> = { CRITICAL: 3, HIGH: 2, MEDIUM: 1, LOW: 0.5 };
  const gapAttribution = useMemo(() =>
    audit.gaps.map(g => ({ label: g.id, value: gapWeight[g.severity.toUpperCase()] ?? 1 })),
    [audit.gaps]);

  const moduleCols: Column<SystemCompletenessModule>[] = [
    {
      key: 'module', header: bi('模块', 'Module'),
      render: m => (
        <div className="min-w-0">
          <div className="truncate font-medium text-ink">{m.name_zh} <span className="text-muted">/ {m.name}</span></div>
          <div className="mono text-2xs text-faint">{m.id}</div>
        </div>
      ),
    },
    { key: 'layer', header: bi('分层', 'Layer'), render: m => <Chip label={m.layer} tone="info" /> },
    { key: 'route', header: bi('前端路径', 'Route'), mono: true, render: m => <span className="text-muted">{m.route}</span> },
    { key: 'endpoint', header: bi('后端接口', 'Endpoint'), mono: true, render: m => <span className="text-faint">{m.endpoint}</span> },
    { key: 'backend', header: bi('后端', 'Backend'), render: m => <StatusChip value={m.status} /> },
    { key: 'frontend', header: bi('前端', 'Frontend'), render: m => <StatusChip value={m.frontend_status} /> },
    {
      key: 'completion', header: bi('完成度', 'Completion'), width: '180px',
      render: m => (
        <div className="flex items-center gap-2">
          <ProgressBar value={m.completion_score} tone={completionTone(m.completion_score)} />
          <span className="tnum w-10 text-right text-muted">{pct(m.completion_score, 0)}</span>
        </div>
      ),
    },
    { key: 'gap', header: bi('剩余工程缺口', 'Remaining Gap'), render: m => <span className="text-2xs text-warn">{m.gap}</span> },
  ];

  const gapCols: Column<SystemCompletenessPayload['gaps'][number]>[] = [
    { key: 'id', header: 'ID', mono: true, render: g => <span className="font-semibold text-accent">{g.id}</span> },
    { key: 'area', header: bi('领域', 'Area'), render: g => <span className="font-medium text-ink">{g.area}</span> },
    { key: 'severity', header: bi('严重度', 'Severity'), render: g => <Chip label={g.severity} tone={severityTone[g.severity.toUpperCase()] ?? 'muted'} /> },
    { key: 'status', header: bi('状态', 'Status'), render: g => <StatusChip value={g.status} /> },
    { key: 'impact', header: bi('影响', 'Impact'), render: g => <span className="text-2xs text-muted">{g.impact}</span> },
    { key: 'next', header: bi('下一步', 'Next Action'), render: g => <span className="text-2xs text-faint">{g.next_action}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="System Completeness Audit" titleZh="系统完整度审查"
        subtitle={bi('从路由清单、OpenAPI 与 CI 证据自动提取的只读完整度审查 · 实盘交易始终禁止',
          'Read-only completeness audit auto-extracted from route manifests, OpenAPI, and CI evidence · live trading always disallowed')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Chip label={bi('只读', 'READ_ONLY')} tone="info" />
            <Chip label={sb.authorized_level} tone="warn" />
            <Chip label={sb.execution_boundary} tone="neg" />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={audit.source} error={error} />

      <KpiStrip cols={8}>
        <StatTile label={bi('前端路径', 'Routes')} value={num(sm.frontend_routes)} accent />
        <StatTile label={bi('API 接口', 'API Endpoints')} value={num(sm.api_endpoints)} />
        <StatTile label={bi('后端支撑', 'Backend Backed')} value={num(sm.backend_backed_modules)} tone="info" />
        <StatTile label={bi('可运行模块', 'Operational')} value={num(sm.fully_operational_modules)} tone="pos" />
        <StatTile label={bi('只读模块', 'Read-only')} value={num(sm.read_only_modules)} tone="info" />
        <StatTile label={bi('高风险缺口', 'High Gaps')} value={num(sm.open_high_gaps)} tone="warn" />
        <StatTile label={bi('实盘授权', 'Live Auth')} value={sm.live_trading_authorized ? bi('是', 'YES') : bi('否', 'NO')}
          tone={sm.live_trading_authorized ? 'neg' : 'pos'} />
        <StatTile label={bi('当前等级', 'Current Level')} value={audit.current_level} sub={audit.institutional_target} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('综合评分', 'Composite')} title={bi('系统就绪度', 'System Readiness')}>
          <div className="flex flex-col items-center gap-3">
            <RadialGauge value={audit.readiness_score} max={1} size={168}
              label={bi('就绪度', 'readiness')}
              tone={audit.readiness_score >= 0.7 ? 'pos' : audit.readiness_score >= 0.55 ? 'accent' : 'warn'} />
            <div className="w-full">
              <KeyValue cols={1} items={[
                { k: bi('审查模式', 'Audit Mode'), v: audit.audit_mode },
                { k: bi('目标等级', 'Target'), v: audit.institutional_target },
                { k: bi('API 版本', 'API Version'), v: audit.api_version },
              ]} />
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('分层构成', 'Layer Mix')} title={bi('模块分层分布', 'Modules by Layer')}>
          <div className="flex items-center gap-5">
            <Donut size={150} segments={layerSegments} />
            <div className="flex max-h-44 flex-1 flex-col gap-1.5 overflow-y-auto pr-1">
              {layerSegments.map(s => (
                <div key={s.label} className="flex items-center justify-between gap-2 text-2xs">
                  <span className="truncate text-muted">{s.label}</span>
                  <span className="tnum text-ink">{num(s.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('覆盖深度', 'Coverage Depth')} title={bi('完成度分布', 'Completion Distribution')}>
          <Histogram bins={completionBins} height={172} tone="accent" />
          <div className="mt-2 text-2xs text-faint">
            {bi('按模块 completion_score 分箱;阈值 0.72 视为可运行。',
              'Modules binned by completion_score; ≥0.72 counts as operational.')}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('门禁', 'Gates')} title={bi('审查检查', 'Audit Checks')}
          right={<Chip label={bi(`${audit.checks.length} 项`, `${audit.checks.length} checks`)} tone="info" />}>
          <div className="flex flex-col gap-2.5">
            {audit.checks.map(c => {
              const ok = statusTone(c.status) === 'pos';
              return (
                <div key={c.id} className="flex items-start gap-3 rounded-lg border border-line bg-inset/40 px-3 py-2.5">
                  <div className="mt-0.5"><GateDot ok={ok} /></div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink">{c.name}</span>
                      <StatusChip value={c.status} />
                    </div>
                    <div className="mt-1 text-2xs text-faint">
                      <span className="mono text-muted">{c.id}</span> · {c.evidence}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>

        <Panel tone="neg" eyebrow={bi('不可逾越', 'Hard Boundary')} title={bi('执行安全边界', 'Execution Safety Boundary')}>
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-2.5 rounded-lg border border-neg/30 bg-neg/5 px-3 py-3">
              <GateDot ok={sb.read_only_console} label={bi('控制台只读', 'Read-only console')} />
              <GateDot ok={!sm.live_trading_authorized} label={bi('实盘交易禁止', 'Live trading disallowed')} />
            </div>
            <KeyValue cols={1} items={[
              { k: bi('执行边界', 'Execution Boundary'), v: <span className="text-neg">{sb.execution_boundary}</span> },
              { k: bi('授权等级', 'Authorized Level'), v: sb.authorized_level },
            ]} />
            <div>
              <div className="mb-1.5 text-2xs uppercase tracking-wide text-faint">{bi('阻塞项', 'Blocked Until')}</div>
              <div className="flex flex-wrap gap-1.5">
                {sb.blocked_until.map(b => <Chip key={b} label={b} tone="neg" />)}
              </div>
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('风险敞口', 'Open Risk')} title={bi('未决缺口(按严重度权重)', 'Open Gaps by Severity Weight')}>
          <DivergingBars items={gapAttribution} valueFormat={v => v.toFixed(1)}
            height={Math.max(120, gapAttribution.length * 26 + 12)} />
        </Panel>

        <Panel eyebrow={bi('路线图', 'Roadmap')} title={bi('交付阶段', 'Delivery Phases')}>
          <ol className="flex flex-col gap-3">
            {audit.roadmap.map((r, i) => (
              <li key={r.phase} className="relative pl-6">
                <span className="absolute left-0 top-0.5 flex h-4 w-4 items-center justify-center rounded-full border border-line bg-inset text-[9px] text-muted">{i + 1}</span>
                {i < audit.roadmap.length - 1 && <span className="absolute left-[7.5px] top-5 h-[calc(100%-0.5rem)] w-px bg-line" />}
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-ink">{r.phase}</span>
                  <StatusChip value={r.status} />
                </div>
                <div className="mt-0.5 text-2xs text-muted">{r.objective}</div>
                <div className="mt-0.5 text-2xs text-faint">{r.exit_criteria}</div>
              </li>
            ))}
          </ol>
        </Panel>
      </div>

      <Panel eyebrow={bi('覆盖矩阵', 'Coverage Matrix')} title={bi('模块完整度清单', 'Module Completeness Inventory')}
        right={<Chip label={bi(`${audit.modules.length} 个模块`, `${audit.modules.length} modules`)} tone="info" />}>
        <DataTable columns={moduleCols} rows={audit.modules} dense getKey={m => m.id} />
      </Panel>

      <Panel eyebrow={bi('工程缺口', 'Engineering Gaps')} title={bi('未决工程缺口登记', 'Open Engineering Gap Register')}
        right={<Chip label={bi(`${audit.gaps.length} 项`, `${audit.gaps.length} gaps`)} tone="warn" />}>
        <DataTable columns={gapCols} rows={audit.gaps} getKey={g => g.id} />
      </Panel>
    </PageShell>
  );
}
