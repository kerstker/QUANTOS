import React, { useMemo } from 'react';
import { fetchAgentsSnapshot } from '@/lib/api/research-console-api';
import { mockAgents } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { AgentsPayload, ResearchAgent } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, ProgressBar, Chip, type Column,
} from '@/ui/primitives';
import { Donut, BarSeries, DivergingBars, RadialGauge } from '@/ui/charts';
import { num, pct, money, statusTone, type Tone } from '@/ui/format';

const stateTone: Record<string, Tone> = {
  RUNNING: 'info', REFLECTING: 'special', IDLE: 'muted', PAUSED: 'warn',
};

export default function Agents() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<AgentsPayload>(
    s => fetchAgentsSnapshot(s));

  const agents: ResearchAgent[] = usingBackend ? payload!.agents : mockAgents;
  const summary = useMemo(() => {
    if (usingBackend && payload) return payload.summary;
    return {
      active: mockAgents.filter(a => a.state === 'RUNNING').length,
      total_runs: mockAgents.reduce((s, a) => s + a.runs_completed, 0),
      approved: mockAgents.reduce((s, a) => s + a.alpha_approved, 0),
      budget_spent_cny: mockAgents.reduce((s, a) => s + a.budget_spent_cny, 0),
    };
  }, [payload, usingBackend]);

  // Fleet-level derivations (deterministic) ---------------------------------
  const fleetSize = agents.length;
  const totalBudget = agents.reduce((s, a) => s + a.budget_spent_cny, 0);
  const avgScore = fleetSize ? agents.reduce((s, a) => s + a.performance_score, 0) / fleetSize : 0;
  const approvalRate = summary.total_runs > 0 ? summary.approved / summary.total_runs : 0;
  const topPerformer = agents.reduce<ResearchAgent | null>(
    (best, a) => (best === null || a.performance_score > best.performance_score ? a : best), null);

  // State composition (Donut) ------------------------------------------------
  const stateCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of agents) m.set(a.state, (m.get(a.state) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [agents]);
  const stateSegments = stateCounts.map(([state, count]) => ({
    label: state, value: count, tone: stateTone[state] ?? statusTone(state),
  }));

  // Per-agent score bars + budget attribution --------------------------------
  const scoreData = agents.map(a => a.performance_score);
  const scoreLabels = agents.map(a => a.name.split(' ')[0]);
  const budgetItems = agents.map(a => ({ label: a.name, value: a.budget_spent_cny }));

  const agentCols: Column<ResearchAgent>[] = [
    { key: 'name', header: bi('智能体', 'Agent'), render: a => (
      <div className="flex flex-col">
        <span className="font-medium text-ink">{a.name}</span>
        <span className="mono text-2xs text-faint">{a.agent_id}</span>
      </div>
    ) },
    { key: 'state', header: bi('状态', 'State'), render: a => <StatusChip value={a.state} /> },
    { key: 'runs', header: bi('运行', 'Runs'), align: 'right', mono: true, render: a => num(a.runs_completed) },
    { key: 'appr', header: bi('已批 Alpha', 'Approved'), align: 'right', mono: true, render: a => num(a.alpha_approved) },
    { key: 'hit', header: bi('批准率', 'Hit Rate'), align: 'right', mono: true, render: a =>
      <span className="text-muted">{pct(a.runs_completed > 0 ? a.alpha_approved / a.runs_completed : 0, 0)}</span> },
    { key: 'perf', header: bi('表现评分', 'Performance'), width: '190px', render: a => (
      <div className="flex items-center gap-2">
        <ProgressBar value={a.performance_score} tone={a.performance_score >= 0.7 ? 'pos' : a.performance_score >= 0.5 ? 'accent' : 'warn'} />
        <span className="tnum w-9 shrink-0 text-right text-muted">{pct(a.performance_score, 0)}</span>
      </div>
    ) },
    { key: 'budget', header: bi('预算花费', 'Budget'), align: 'right', mono: true, render: a => money(a.budget_spent_cny) },
  ];

  return (
    <PageShell>
      <PageHeader title="Research Agents" titleZh="研究智能体"
        subtitle={bi('自治研究智能体舰队的只读运行态快照 · 状态、产出、表现与预算消耗',
          'Read-only operating snapshot of the autonomous research agent fleet — state, output, performance and budget burn')}
        actions={<Chip label={bi(`舰队 ${fleetSize}`, `Fleet ${fleetSize}`)} tone="info" />} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={error} />

      <KpiStrip cols={6}>
        <StatTile label={bi('运行中', 'Active')} value={num(summary.active)} tone="info"
          sub={bi(`共 ${fleetSize} 个智能体`, `of ${fleetSize} agents`)} />
        <StatTile label={bi('总运行次数', 'Total Runs')} value={num(summary.total_runs)} accent />
        <StatTile label={bi('已批准 Alpha', 'Approved')} value={num(summary.approved)} tone="pos"
          sub={bi(`批准率 ${pct(approvalRate, 1)}`, `hit rate ${pct(approvalRate, 1)}`)} />
        <StatTile label={bi('平均表现评分', 'Avg Score')} value={pct(avgScore, 0)}
          tone={avgScore >= 0.7 ? 'pos' : avgScore >= 0.5 ? 'accent' : 'warn'} />
        <StatTile label={bi('预算消耗', 'Budget Spent')} value={money(summary.budget_spent_cny)} tone="warn"
          sub={topPerformer ? bi(`领先 ${topPerformer.name}`, `top ${topPerformer.name}`) : undefined} />
        <StatTile label={bi('单次产出成本', 'Cost / Run')} value={money(summary.total_runs > 0 ? totalBudget / summary.total_runs : 0)}
          sub={bi('预算 / 运行', 'budget / run')} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('表现', 'Performance')} title={bi('各智能体表现评分', 'Performance Score by Agent')}
          right={<Chip label={bi(`均值 ${pct(avgScore, 0)}`, `avg ${pct(avgScore, 0)}`)} tone="accent" />}>
          <BarSeries data={scoreData} labels={scoreLabels} height={236} posTone="accent"
            valueFormat={v => pct(v, 0)} />
        </Panel>

        <Panel eyebrow={bi('舰队状态', 'Fleet State')} title={bi('运行状态构成', 'State Composition')}>
          <div className="flex items-center gap-5">
            <Donut segments={stateSegments} size={150} />
            <div className="flex min-w-0 flex-1 flex-col gap-2.5">
              {stateCounts.map(([state, count]) => (
                <div key={state} className="flex items-center justify-between gap-2">
                  <StatusChip value={state} />
                  <span className="tnum text-xs text-muted">{num(count)}
                    <span className="ml-1 text-2xs text-faint">{pct(fleetSize ? count / fleetSize : 0, 0)}</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('成本', 'Cost')} title={bi('预算消耗归因', 'Budget Burn Attribution')}
          right={<Chip label={money(totalBudget)} tone="warn" />}>
          <DivergingBars items={budgetItems} valueFormat={v => money(v)} />
        </Panel>

        <Panel eyebrow={bi('综合', 'Aggregate')} title={bi('舰队批准率', 'Fleet Approval Rate')}>
          <div className="flex flex-col items-center gap-3 py-2">
            <RadialGauge value={approvalRate} max={1} size={148} label="%"
              tone={approvalRate >= 0.2 ? 'pos' : approvalRate >= 0.1 ? 'accent' : 'warn'} />
            <div className="text-center text-2xs text-faint">
              {bi(`${num(summary.approved)} 个 Alpha 通过 / ${num(summary.total_runs)} 次运行`,
                `${num(summary.approved)} approved alpha / ${num(summary.total_runs)} runs`)}
            </div>
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('自治研究', 'Autonomous Research')} title={bi('智能体运行明细', 'Agent Operating Detail')}
        right={<Chip label={bi(`${fleetSize} 个`, `${fleetSize} agents`)} tone="info" />}>
        <DataTable columns={agentCols} rows={agents} getKey={a => a.agent_id}
          empty={bi('暂无活跃智能体', 'No active agents')} />
      </Panel>
    </PageShell>
  );
}
