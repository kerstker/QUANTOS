import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchResearchRuns } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ResearchRunsPayload, ResearchRunSummary } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, StatusChip, Chip,
  type Column,
} from '@/ui/primitives';
import { stamp } from '@/ui/format';

/**
 * Research Runs — the dynamic, registry-backed home for every backtest/research run. The list reflects
 * whatever is in the run_registry (existing results indexed on first read; new runs the research_runner
 * writes appear here automatically). Read-only; one generic detail viewer renders any run. No per-backtest
 * page is ever hand-coded again. (Trigger buttons + live polling land in the next increment.)
 */

const fallbackRuns: ResearchRunsPayload = {
  source: 'FRONTEND_RESEARCH_RUNS_FALLBACK', read_only: true, api_version: '0.1.0',
  data_provenance: 'RESEARCH_RUN_REGISTRY',
  runs: [
    { run_id: 'fb_carry', kind: 'carry_strategy', title: '资金费 Carry 策略', status: 'done',
      created_ts: 1782700000, headline: { label: 'net_sharpe_5bps', value: 2.03 }, verdict: 'DEPLOYABLE' },
    { run_id: 'fb_combo', kind: 'combo_book', title: 'carry × crowd 双因子组合', status: 'done',
      created_ts: 1782700000, headline: { label: 'combo_sharpe', value: 1.5 }, verdict: 'diversification helps' },
    { run_id: 'fb_surv', kind: 'survivorship', title: 'crowd_contra 幸存者重测', status: 'done',
      created_ts: 1782700000, headline: { label: 'complete_sharpe_raw', value: 0.933 }, verdict: 'WEAKENS but survives' },
  ],
};

const KINDS = ['全部', 'carry_strategy', 'oi_research', 'combo_book', 'survivorship', 'factory_orthogonal', 'factory_loop'];

function fmtHeadline(h: ResearchRunSummary['headline']): string {
  if (h.value == null) return '—';
  return `${h.label}: ${typeof h.value === 'number' ? h.value : h.value}`;
}

export default function ResearchRuns() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<ResearchRunsPayload>(
    s => fetchResearchRuns(s));
  const data = usingBackend ? payload! : fallbackRuns;
  const [kind, setKind] = useState('全部');

  const runs = useMemo(() => data.runs.filter(r => kind === '全部' || r.kind === kind), [data.runs, kind]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of data.runs) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [data.runs]);

  const cols: Column<ResearchRunSummary>[] = [
    { key: 'kind', header: bi('类型', 'Kind'), render: r => <Chip label={r.kind} tone="muted" /> },
    { key: 'title', header: bi('运行', 'Run'), render: r => (
      <Link to={`/research/runs/${r.run_id}`} className="font-medium text-accent hover:underline">{r.title}</Link>) },
    { key: 'status', header: bi('状态', 'Status'), render: r => <StatusChip value={r.status.toUpperCase()} /> },
    { key: 'headline', header: bi('头条指标', 'Headline'), mono: true, align: 'right',
      render: r => <span className="text-ink">{fmtHeadline(r.headline)}</span> },
    { key: 'verdict', header: bi('裁决', 'Verdict'), render: r => (
      <span className="text-2xs text-muted line-clamp-1">{r.verdict}</span>) },
    { key: 'created', header: bi('时间', 'When'), align: 'right', mono: true,
      render: r => <span className="text-faint text-2xs">{stamp(r.created_ts)}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Research Runs" titleZh="研究中心"
        subtitle={bi('登记表驱动的研究运行总台 — 每个回测/搜索自动出现,点开通用查看器看结果(只读)',
          'Registry-backed home for every research run — each backtest/search appears automatically; one generic viewer renders any result (read-only)')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={data.source} error={error} realData />

      <KpiStrip cols={4}>
        <StatTile label={bi('运行总数', 'Total runs')} value={String(data.runs.length)} accent />
        <StatTile label={bi('完成', 'Done')} value={String(counts['done'] ?? 0)} tone="pos" />
        <StatTile label={bi('运行中', 'Running')} value={String((counts['running'] ?? 0) + (counts['queued'] ?? 0))} tone="info" />
        <StatTile label={bi('出错', 'Error')} value={String(counts['error'] ?? 0)} tone={counts['error'] ? 'neg' : 'muted'} />
      </KpiStrip>

      <Panel eyebrow={bi('全部运行', 'All Runs')} title={bi('研究运行登记表', 'Research Run Registry')}
        right={<Chip label={bi(`${runs.length} 项`, `${runs.length} shown`)} tone="info" />}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {KINDS.map(k => (
            <button key={k} onClick={() => setKind(k)}
              className={`rounded-md border px-2.5 py-1 text-xs transition ${kind === k ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-ink'}`}>{k}</button>
          ))}
        </div>
        <DataTable columns={cols} rows={runs} dense getKey={r => r.run_id} />
      </Panel>
    </PageShell>
  );
}
