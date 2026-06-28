import React from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { fetchResearchRunsCompare } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ResearchRunsComparePayload, ResearchRunDetail } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, DataTable, StatusChip, Chip, type Column,
} from '@/ui/primitives';
import { BarSeries } from '@/ui/charts';
import { fixed, stamp } from '@/ui/format';

/**
 * CompareRuns — side-by-side comparison of 2+ research runs selected in the registry. Reads run_ids from
 * the URL (`?ids=a,b,c`) so a comparison is shareable/bookmarkable. Renders a metric matrix (union of all
 * metric keys × one column per run) and, for each shared series, per-run charts. Read-only: pure aggregation
 * over the contract-guarded read endpoint — no mutating surface, no per-comparison hand-coded layout.
 */

const fbCompare: ResearchRunsComparePayload = {
  source: 'FRONTEND_COMPARE_FALLBACK', read_only: true, data_provenance: 'RESEARCH_RUN_REGISTRY',
  runs: [
    {
      run_id: 'fb_combo', kind: 'combo_book', title: 'carry × crowd 双因子组合', status: 'done',
      params: { universe: 14 }, created_ts: 1782700000, finished_ts: 1782700050,
      metrics: { combo_sharpe: 1.5, max_drawdown: 0.12, net_10bps: 1.02, lift_vs_best: 0.23 },
      series: { per_year: [{ label: '21', value: 1.62 }, { label: '22', value: 2.2 }, { label: '23', value: 2.07 },
                           { label: '24', value: 1.09 }, { label: '25', value: 1.48 }, { label: '26', value: 2.02 }] },
      verdict: 'diversification helps', caveats: ['继承 crowd 幸存者折扣'],
      provenance: 'tools/crypto_combo_book.py', error: null, log: ['[done]'], code_version: 'abc1234def0',
    },
    {
      run_id: 'fb_carry', kind: 'carry_strategy', title: '资金费 Carry 策略', status: 'done',
      params: { universe: 14 }, created_ts: 1782700000, finished_ts: 1782700040,
      metrics: { net_sharpe_5bps: 2.03, max_drawdown: 0.26, ann_return: 0.47 },
      series: { per_year: [{ label: '21', value: 2.4 }, { label: '22', value: 1.8 }, { label: '23', value: 2.1 },
                           { label: '24', value: 1.5 }, { label: '25', value: 2.0 }, { label: '26', value: 2.3 }] },
      verdict: 'DEPLOYABLE', caveats: ['历史回测≠未来'],
      provenance: 'tools/crypto_carry_strategy.py', error: null, log: ['[done]'], code_version: 'abc1234def0',
    },
  ],
};

function metricLabel(k: string): string { return k.replace(/_/g, ' '); }
function metricValue(v: number | string | null | undefined): string {
  return v == null ? '—' : typeof v === 'number' ? fixed(v, 3) : String(v);
}

type MetricRow = { metric: string } & Record<string, number | string | null>;

export default function CompareRuns() {
  const [search] = useSearchParams();
  const idsKey = search.get('ids') ?? '';
  const ids = idsKey.split(',').map(s => s.trim()).filter(Boolean);

  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<ResearchRunsComparePayload>(
    s => fetchResearchRunsCompare(ids, s), [idsKey]);
  const data = usingBackend ? payload! : fbCompare;
  const runs = data.runs;

  const metricKeys = Array.from(new Set(runs.flatMap(r => Object.keys(r.metrics))));
  const seriesKeys = Array.from(new Set(runs.flatMap(r => Object.keys(r.series))));

  const metricRows: MetricRow[] = metricKeys.map(k => {
    const row: MetricRow = { metric: k };
    for (const r of runs) row[r.run_id] = r.metrics[k] ?? null;
    return row;
  });

  const cols: Column<MetricRow>[] = [
    { key: 'metric', header: bi('指标', 'Metric'), render: r => <span className="text-ink">{metricLabel(r.metric)}</span> },
    ...runs.map<Column<MetricRow>>(run => ({
      key: run.run_id, header: run.title, mono: true, align: 'right',
      render: r => <span className="text-ink">{metricValue(r[run.run_id])}</span>,
    })),
  ];

  return (
    <PageShell>
      <PageHeader title="Compare Runs" titleZh="运行对比"
        subtitle={bi(`并排对比 ${runs.length} 个研究运行(只读,数据驱动渲染)`,
          `${runs.length} research runs side by side (read-only, data-driven)`)}
        actions={<Link to="/research/runs" className="text-2xs text-accent hover:underline">{bi('← 研究中心', '← Runs')}</Link>} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} realData
        source={usingBackend ? payload?.source : 'FRONTEND_COMPARE_FALLBACK'} error={error} />

      {runs.length < 2 ? (
        <Panel eyebrow={bi('对比', 'Compare')} title={bi('请选择至少两个运行', 'Pick at least two runs')}>
          <div className="py-6 text-center text-xs text-faint">
            {bi('在研究中心勾选 2 个以上运行再对比 · ', 'Select 2+ runs in the registry to compare · ')}
            <Link to="/research/runs" className="text-accent hover:underline">{bi('返回研究中心', 'back to Research Runs')}</Link>
          </div>
        </Panel>
      ) : (
        <>
          <Panel eyebrow={bi('对比的运行', 'Runs')} title={bi('被对比的运行', 'Runs Being Compared')}>
            <div className="flex flex-wrap gap-3">
              {runs.map(r => (
                <Link key={r.run_id} to={`/research/runs/${r.run_id}`}
                  className="flex items-center gap-2 rounded-lg border border-line px-3 py-1.5 hover:border-accent/50">
                  <Chip label={r.kind} tone="muted" />
                  <span className="text-xs font-medium text-accent">{r.title}</span>
                  <StatusChip value={r.status.toUpperCase()} />
                  <span className="text-2xs text-faint">{stamp(r.created_ts)}</span>
                </Link>
              ))}
            </div>
          </Panel>

          <Panel eyebrow={bi('指标矩阵', 'Metric Matrix')} title={bi('指标并排对比', 'Metrics Side by Side')}
            right={<Chip label={bi(`${metricKeys.length} 指标`, `${metricKeys.length} metrics`)} tone="info" />}>
            <DataTable columns={cols} rows={metricRows} dense getKey={r => r.metric} />
            <div className="mt-2 text-2xs text-faint">
              {bi('注:不同指标方向不同(夏普越高越好、回撤越低越好),不做"最优"高亮以免误导。',
                'Note: metric directions differ (higher Sharpe vs lower drawdown); no "best" highlight to avoid misleading.')}
            </div>
          </Panel>

          {seriesKeys.map(sk => (
            <Panel key={sk} eyebrow={bi('序列对比', 'Series')} title={metricLabel(sk)}>
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                {runs.filter(r => r.series[sk]?.length).map(r => (
                  <div key={r.run_id}>
                    <div className="mb-1 text-2xs text-muted">{r.title}</div>
                    <BarSeries data={r.series[sk].map(p => p.value)} labels={r.series[sk].map(p => p.label)}
                      valueFormat={v => fixed(v, 2)} />
                  </div>
                ))}
              </div>
            </Panel>
          ))}
        </>
      )}
    </PageShell>
  );
}
