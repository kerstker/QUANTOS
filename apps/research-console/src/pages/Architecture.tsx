import React from 'react';
import { fetchArchitectureSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ArchitectureModule, ArchitectureFlow, ArchitectureGap, ArchitecturePayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, Chip, GateDot, ProgressBar, type Column,
} from '@/ui/primitives';
import { RadialGauge, Histogram } from '@/ui/charts';
import { num, pct, statusTone, type Tone } from '@/ui/format';

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

const layerLabels: Record<string, string> = {
  Frontend: bi('前端', 'Frontend'),
  API: bi('API', 'API'),
  'Research Compute': bi('研究计算', 'Research Compute'),
  Simulation: bi('模拟', 'Simulation'),
  Risk: bi('风险', 'Risk'),
  Governance: bi('治理', 'Governance'),
};
const layerLabel = (layer: string) => layerLabels[layer] ?? layer;

const maturityTone = (m: number): Tone => (m >= 0.66 ? 'pos' : m >= 0.5 ? 'accent' : m >= 0.33 ? 'warn' : 'neg');

export default function Architecture() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<ArchitecturePayload>(
    s => fetchArchitectureSnapshot(s));

  const data = usingBackend ? payload! : fallbackArchitecture;

  const readyModules = data.modules.filter(m => m.status === 'READY').length;
  const blockedModules = data.modules.filter(m => m.status === 'BLOCKED').length;
  const highGaps = data.gaps.filter(g => g.severity === 'HIGH').length;
  const avgMaturity = data.modules.length
    ? data.modules.reduce((s, m) => s + m.maturity, 0) / data.modules.length
    : 0;

  // status distribution across modules -> Histogram
  const statusOrder = ['READY', 'PARTIAL', 'BLOCKED'];
  const statusBins = (() => {
    const counts = new Map<string, number>();
    for (const m of data.modules) counts.set(m.status, (counts.get(m.status) ?? 0) + 1);
    const ordered = statusOrder.filter(s => counts.has(s)).map(s => ({ label: s, count: counts.get(s)! }));
    const extra = [...counts.keys()].filter(s => !statusOrder.includes(s)).map(s => ({ label: s, count: counts.get(s)! }));
    return [...ordered, ...extra];
  })();

  const moduleCols: Column<ArchitectureModule>[] = [
    { key: 'id', header: 'ID', mono: true, render: m => <span className="text-accent">{m.id}</span> },
    { key: 'name', header: bi('模块', 'Module'), render: m => <span className="font-medium text-ink">{m.name}</span> },
    { key: 'layer', header: bi('层', 'Layer'), render: m => <Chip label={layerLabel(m.layer)} tone="info" /> },
    { key: 'status', header: bi('状态', 'Status'), render: m => <StatusChip value={m.status} /> },
    { key: 'maturity', header: bi('成熟度', 'Maturity'), width: '180px', render: m => (
      <div className="flex items-center gap-2">
        <ProgressBar value={m.maturity} tone={maturityTone(m.maturity)} />
        <span className="tnum w-10 text-right text-muted">{pct(m.maturity, 0)}</span>
      </div>
    ) },
    { key: 'owner', header: bi('负责人', 'Owner'), render: m => <span className="text-muted">{m.owner}</span> },
    { key: 'capability', header: bi('能力', 'Capability'), render: m => <span className="text-ink">{m.capability}</span> },
    { key: 'gap', header: bi('下一缺口', 'Next Gap'), render: m => <span className="text-faint">{m.next_gap}</span> },
  ];

  const gapCols: Column<ArchitectureGap>[] = [
    { key: 'id', header: 'ID', mono: true, render: g => <span className="text-accent">{g.id}</span> },
    { key: 'area', header: bi('领域', 'Area'), render: g => <span className="font-medium text-ink">{g.area}</span> },
    { key: 'severity', header: bi('严重度', 'Severity'), render: g => <Chip label={g.severity} tone={statusTone(g.severity)} /> },
    { key: 'status', header: bi('状态', 'Status'), render: g => <StatusChip value={g.status} /> },
    { key: 'next', header: bi('下一步', 'Next Action'), render: g => <span className="text-muted">{g.next_action}</span> },
  ];

  const flowCols: Column<ArchitectureFlow>[] = [
    { key: 'from', header: bi('来源', 'From'), mono: true, render: f => <span className="text-info">{f.from}</span> },
    { key: 'arrow', header: '', align: 'center', width: '28px', render: () => <span className="text-faint">→</span> },
    { key: 'to', header: bi('去向', 'To'), mono: true, render: f => <span className="text-accent">{f.to}</span> },
    { key: 'label', header: bi('内容', 'Payload'), render: f => <span className="text-ink">{f.label}</span> },
    { key: 'mode', header: bi('模式', 'Mode'), render: f => <Chip label={f.mode} tone={statusTone(f.mode)} /> },
  ];

  return (
    <PageShell>
      <PageHeader title="System Architecture" titleZh="系统架构"
        subtitle={bi('研究、模拟、风险与治理模块的只读边界图 · 成熟度、数据流与安全护栏',
          'Read-only boundary map of research, simulation, risk and governance modules — maturity, data flow and safety guardrails')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip value={data.current_level} />
            <span className="text-faint">→</span>
            <Chip label={data.target_level} tone="warn" />
            <StatusChip value={data.phase3a_decision} />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && data.data_provenance === 'REAL_REPO_INVENTORY'}
        source={usingBackend && data.data_provenance === 'REAL_REPO_INVENTORY' ? 'REAL_REPO_INVENTORY' : data.source}
        error={error} />

      {data.inventory && data.inventory.domains.length > 0 && (
        <Panel eyebrow={bi('真实代码盘点', 'REAL REPO INVENTORY')}
          title={bi('对仓库的只读内省 · 硬计数(非策划成熟度)', 'Read-only introspection of the checked-out repo · hard counts (not curated maturity)')}>
          <KpiStrip cols={6}>
            <StatTile label={bi('后端模块', 'Backend modules')} value={num(data.inventory.total_services_modules)} tone="info" />
            <StatTile label={bi('后端代码行', 'Backend LOC')} value={num(data.inventory.total_services_loc)} />
            <StatTile label={bi('测试文件', 'Test files')} value={num(data.inventory.n_test_files)} tone="pos" />
            <StatTile label={bi('测试函数', 'Test fns')} value={num(data.inventory.n_test_functions)} tone="pos" />
            <StatTile label={bi('前端页', 'Console pages')} value={num(data.inventory.n_console_pages)} />
            <StatTile label={bi('只读端点', 'GET endpoints')} value={num(data.inventory.n_console_endpoints)} />
          </KpiStrip>
          <div className="mt-3">
            <div className="mb-2 text-2xs uppercase tracking-wider text-faint">{bi('各域模块数(真实)', 'Modules per domain (real)')}</div>
            <Histogram bins={[...data.inventory.domains].sort((a, b) => b.n_modules - a.n_modules)
              .map(d => ({ label: d.domain, count: d.n_modules }))} height={170} tone="accent" />
          </div>
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('就绪度', 'Readiness')} title={bi('架构指标', 'Architecture Indicators')}>
          <KpiStrip cols={6}>
            <StatTile label={bi('当前等级', 'Current Level')} value={<span className="text-base">{data.current_level}</span>} tone="info" />
            <StatTile label={bi('目标等级', 'Target Level')} value={<span className="text-base">{data.target_level}</span>} tone="warn" />
            <StatTile label={bi('模块数量', 'Modules')} value={num(data.modules.length)} />
            <StatTile label={bi('就绪模块', 'Ready')} value={num(readyModules)} tone="pos" />
            <StatTile label={bi('受阻模块', 'Blocked')} value={num(blockedModules)} tone="neg" />
            <StatTile label={bi('高优缺口', 'High Gaps')} value={num(highGaps)} tone="warn" />
          </KpiStrip>
          <div className="mt-4">
            <div className="mb-2 text-2xs uppercase tracking-wider text-faint">{bi('模块状态分布', 'Module Status Distribution')}</div>
            <Histogram bins={statusBins} height={150} tone="info" />
          </div>
        </Panel>

        <Panel eyebrow={bi('综合评分', 'Composite')} title={bi('架构成熟度评分', 'Architecture Score')}>
          <div className="flex flex-col items-center gap-3">
            <RadialGauge value={data.architecture_score} max={1} size={172} label={bi('成熟度', 'maturity')} />
            <div className="grid w-full grid-cols-2 gap-2 text-center">
              <div className="rounded-lg border border-line bg-inset/40 px-2 py-2">
                <div className="text-2xs uppercase tracking-wide text-faint">{bi('平均成熟度', 'Avg Maturity')}</div>
                <div className="tnum mt-1 text-lg font-bold text-ink">{pct(avgMaturity, 0)}</div>
              </div>
              <div className="rounded-lg border border-line bg-inset/40 px-2 py-2">
                <div className="text-2xs uppercase tracking-wide text-faint">{bi('实现授权', 'Authorized')}</div>
                <div className="mt-1.5 flex items-center justify-center">
                  <GateDot ok={data.implementation_authorized} label={data.implementation_authorized ? 'TRUE' : 'FALSE'} />
                </div>
              </div>
            </div>
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('分层模块图', 'Layered Modules')} title={bi('模块成熟度矩阵', 'Module Maturity Matrix')}
        right={<Chip label={bi(`${data.modules.length} 模块`, `${data.modules.length} modules`)} tone="info" />}>
        <DataTable columns={moduleCols} rows={data.modules} getKey={m => m.id} />
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('管线', 'Pipeline')} title={bi('数据流(只读)', 'Data Flow (Read-Only)')}>
          <DataTable columns={flowCols} rows={data.data_flows} getKey={f => `${f.from}-${f.to}`} dense />
        </Panel>

        <Panel tone="neg" eyebrow={bi('护栏', 'Guardrail')} title={bi('安全边界', 'Safety Boundary')}
          right={<Chip label={data.safety_boundary.execution_boundary} tone="neg" />}>
          <div className="flex flex-col gap-2.5">
            <div className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset/40 px-3 py-2">
              <span className="text-xs text-muted">{bi('只读控制台', 'Read-only console')}</span>
              <GateDot ok={data.safety_boundary.read_only_console} label={data.safety_boundary.read_only_console ? 'TRUE' : 'FALSE'} />
            </div>
            <div className="text-2xs uppercase tracking-wider text-faint">{bi('禁止能力', 'Forbidden Capabilities')}</div>
            {data.safety_boundary.forbidden_capabilities.map(cap => (
              <div key={cap} className="flex items-center gap-2 rounded-lg border border-neg/25 bg-neg/5 px-3 py-2">
                <GateDot ok={false} />
                <span className="text-xs text-neg">{cap}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('缺口', 'Gaps')} title={bi('架构缺口登记', 'Architecture Gap Register')}
        right={<Chip label={bi(`${highGaps} 高优`, `${highGaps} HIGH`)} tone="warn" />}>
        <DataTable columns={gapCols} rows={data.gaps} getKey={g => g.id} />
      </Panel>
    </PageShell>
  );
}
