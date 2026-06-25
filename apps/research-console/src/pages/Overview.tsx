import React from 'react';
import { fetchOverviewSnapshot } from '@/lib/api/research-console-api';
import { mockStats, mockAgents } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { FunnelPoint, OverviewPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, ProgressBar, Chip, type Column,
} from '@/ui/primitives';
import { Funnel, Sparkline } from '@/ui/charts';
import { num, pct, money, statusTone, type Tone } from '@/ui/format';

const funnelTone: Record<string, Tone> = {
  hypotheses: 'muted', candidates: 'info', validated: 'accent',
  paper_passed: 'warn', portfolio_candidates: 'special', risk_approved: 'pos',
};
const funnelZh: Record<string, string> = {
  hypotheses: bi('假设', 'Hypotheses'), candidates: bi('候选', 'Candidates'),
  validated: bi('已验证', 'Validated'), paper_passed: bi('纸面通过', 'Paper'),
  portfolio_candidates: bi('组合候选', 'Portfolio'), risk_approved: bi('风险通过', 'Risk OK'),
};

export default function Overview() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<OverviewPayload>(
    s => fetchOverviewSnapshot(s));

  const stats = usingBackend ? payload!.stats : mockStats;
  const agents = usingBackend ? payload!.agents : mockAgents;
  const funnel: FunnelPoint[] = usingBackend ? payload!.funnel : [
    { id: 'hypotheses', label: 'Hypotheses', value: mockStats.total_hypotheses },
    { id: 'candidates', label: 'Candidates', value: mockStats.alpha_candidates },
    { id: 'validated', label: 'Validated', value: Math.round(mockStats.alpha_candidates * mockStats.validation_pass_rate) },
    { id: 'paper_passed', label: 'Paper', value: Math.round(mockStats.alpha_candidates * mockStats.validation_pass_rate * mockStats.paper_pass_rate) },
    { id: 'portfolio_candidates', label: 'Portfolio', value: mockStats.portfolio_candidates },
    { id: 'risk_approved', label: 'Risk OK', value: mockStats.risk_approved },
  ];

  const rejections = Object.entries(stats.rejection_reasons).sort((a, b) => b[1] - a[1]);
  const maxRej = Math.max(...rejections.map(([, c]) => c), 1);

  const agentCols: Column<typeof agents[number]>[] = [
    { key: 'name', header: bi('智能体', 'Agent'), render: a => <span className="font-medium text-ink">{a.name}</span> },
    { key: 'state', header: bi('状态', 'State'), render: a => <StatusChip value={a.state} /> },
    { key: 'runs', header: bi('运行', 'Runs'), align: 'right', mono: true, render: a => num(a.runs_completed) },
    { key: 'appr', header: bi('已批', 'Approved'), align: 'right', mono: true, render: a => num(a.alpha_approved) },
    { key: 'perf', header: bi('表现', 'Performance'), width: '170px', render: a => (
      <div className="flex items-center gap-2"><ProgressBar value={a.performance_score} tone="accent" /><span className="tnum w-9 text-right text-muted">{pct(a.performance_score, 0)}</span></div>
    ) },
    { key: 'budget', header: bi('预算', 'Budget'), align: 'right', mono: true, render: a => money(a.budget_spent_cny) },
  ];

  return (
    <PageShell>
      <PageHeader title="Research Overview" titleZh="研究总览"
        subtitle={bi('从假设到风险通过的端到端研究流水线快照', 'End-to-end research pipeline snapshot, hypothesis → risk-approved')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={error} />

      <KpiStrip cols={7}>
        <StatTile label={bi('假设总数', 'Hypotheses')} value={num(stats.total_hypotheses)} />
        <StatTile label={bi('候选因子', 'Candidates')} value={num(stats.alpha_candidates)} accent />
        <StatTile label={bi('验证通过率', 'Validation Rate')} value={pct(stats.validation_pass_rate)} tone="pos" />
        <StatTile label={bi('纸面通过率', 'Paper Pass Rate')} value={pct(stats.paper_pass_rate)} tone="warn" />
        <StatTile label={bi('组合候选', 'Portfolio Cand.')} value={num(stats.portfolio_candidates)} />
        <StatTile label={bi('风险通过', 'Risk Approved')} value={num(stats.risk_approved)} tone="pos" />
        <StatTile label={bi('活跃运行', 'Active Runs')} value={num(stats.active_runs)} tone="info" />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('转化', 'Conversion')} title={bi('研究管线漏斗', 'Research Pipeline Funnel')}>
          <Funnel height={236} stages={funnel.map(f => ({ label: funnelZh[f.id] ?? f.label, value: f.value, tone: funnelTone[f.id] }))} />
        </Panel>

        <Panel eyebrow={bi('质量门', 'Quality Gates')} title={bi('拒绝原因分布', 'Rejection Reasons')}>
          <div className="flex flex-col gap-3">
            {rejections.map(([reason, count]) => (
              <div key={reason} className="flex flex-col gap-1">
                <div className="flex items-center justify-between gap-2">
                  <StatusChip value={reason} />
                  <span className="tnum text-xs text-muted">{count}</span>
                </div>
                <ProgressBar value={count} max={maxRej} tone={statusTone(reason)} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('自治研究', 'Autonomous Research')} title={bi('活跃研究智能体', 'Active Research Agents')}
        right={<Chip label={bi(`${agents.length} 个`, `${agents.length} agents`)} tone="info" />}>
        <DataTable columns={agentCols} rows={agents} getKey={a => a.agent_id} />
      </Panel>
    </PageShell>
  );
}
