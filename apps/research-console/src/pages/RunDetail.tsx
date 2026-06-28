import React from 'react';
import { Link, useParams } from 'react-router-dom';
import { fetchResearchRun } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ResearchRunPayload, ResearchRunDetail } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, StatusChip, Chip,
} from '@/ui/primitives';
import { BarSeries } from '@/ui/charts';
import { fixed, stamp } from '@/ui/format';

/**
 * RunDetail — the GENERIC, data-driven viewer for ANY research run. It renders purely from the
 * ResearchRun record's fields: metrics -> KPI tiles, each series -> a bar chart, verdict -> status chip,
 * caveats -> list, params/provenance -> footer. No per-backtest layout: a new run kind shows up here with
 * zero new code. Read-only.
 */

const fbDetail: ResearchRunDetail = {
  run_id: 'fb_combo', kind: 'combo_book', title: 'carry × crowd 双因子组合', status: 'done',
  params: { universe: 14, weights: { carry: 0.2, crowd_contra: 0.36, top_contra: 0.44 } },
  created_ts: 1782700000, finished_ts: 1782700050,
  metrics: { combo_sharpe: 1.5, max_drawdown: 0.12, net_10bps: 1.02, lift_vs_best: 0.23 },
  series: { per_year: [{ label: '21', value: 1.62 }, { label: '22', value: 2.2 }, { label: '23', value: 2.07 },
                       { label: '24', value: 1.09 }, { label: '25', value: 1.48 }, { label: '26', value: 2.02 }] },
  verdict: 'diversification helps', caveats: ['继承 crowd 幸存者折扣', '历史回测≠未来'],
  provenance: 'tools/crypto_combo_book.py', error: null,
};

function metricLabel(k: string): string { return k.replace(/_/g, ' '); }
function metricValue(v: number | string | null): string {
  return v == null ? '—' : typeof v === 'number' ? fixed(v, 3) : String(v);
}

export default function RunDetail() {
  const { id = '' } = useParams();
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<ResearchRunPayload>(
    s => fetchResearchRun(id, s));
  const run = usingBackend ? (payload?.run ?? null) : fbDetail;

  if (!run) {
    return (
      <PageShell>
        <PageHeader title="Run Detail" titleZh="运行详情" subtitle={bi('未找到该运行', 'Run not found')} />
        <Panel eyebrow={bi('未找到', 'Not found')} title={id}>
          <div className="py-6 text-center text-xs text-faint">
            {bi('登记表中无此运行 · ', 'No such run in the registry · ')}
            <Link to="/research/runs" className="text-accent hover:underline">{bi('返回研究中心', 'back to Research Runs')}</Link>
          </div>
        </Panel>
      </PageShell>
    );
  }

  const metricKeys = Object.keys(run.metrics);
  const seriesKeys = Object.keys(run.series);

  return (
    <PageShell>
      <PageHeader title={run.title} titleZh={run.title}
        subtitle={bi(`${run.kind} · 研究运行(只读,数据驱动渲染)`, `${run.kind} · research run (read-only, data-driven)`)}
        actions={<div className="flex items-center gap-2"><StatusChip value={run.status.toUpperCase()} />
          <Link to="/research/runs" className="text-2xs text-accent hover:underline">{bi('← 研究中心', '← Runs')}</Link></div>} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} realData
        source={usingBackend ? payload?.source : 'FRONTEND_RUN_DETAIL_FALLBACK'} error={error} />

      {run.error && (
        <Panel eyebrow={bi('错误', 'Error')} title={bi('运行出错', 'Run errored')} tone="neg">
          <div className="mono text-2xs text-neg">{run.error}</div>
        </Panel>
      )}

      {metricKeys.length > 0 && (
        <Panel eyebrow={bi('指标', 'Metrics')} title={bi('头条指标', 'Headline Metrics')}>
          <KpiStrip cols={Math.min(4, metricKeys.length) as 1 | 2 | 3 | 4}>
            {metricKeys.map((k, i) => (
              <StatTile key={k} label={metricLabel(k)} value={metricValue(run.metrics[k])}
                tone={i === 0 ? undefined : 'info'} accent={i === 0} />
            ))}
          </KpiStrip>
        </Panel>
      )}

      {seriesKeys.map(sk => (
        <Panel key={sk} eyebrow={bi('序列', 'Series')} title={metricLabel(sk)}>
          <BarSeries data={run.series[sk].map(p => p.value)} labels={run.series[sk].map(p => p.label)}
            valueFormat={v => fixed(v, 2)} />
        </Panel>
      ))}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel eyebrow={bi('裁决', 'Verdict')} title={bi('诚实裁决', 'Honest Verdict')}>
          <div className="mb-2"><StatusChip value={run.status.toUpperCase()} /></div>
          <div className="text-xs text-ink">{run.verdict}</div>
          {run.caveats.length > 0 && (
            <div className="mt-3 flex flex-col gap-1.5">
              <div className="text-2xs text-muted">{bi('强制折扣', 'Mandatory discounts')}:</div>
              {run.caveats.map((c, i) => (
                <div key={i} className="flex items-center gap-2 text-2xs text-warn"><span>▸</span>{c}</div>
              ))}
            </div>
          )}
        </Panel>

        <Panel eyebrow={bi('参数与来源', 'Params & Provenance')} title={bi('运行元数据', 'Run Metadata')}>
          <div className="mono rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
            {JSON.stringify(run.params)}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-2xs text-faint">
            <Chip label={run.kind} tone="muted" />
            <span>{bi('来源', 'src')}: <span className="mono text-info">{run.provenance}</span></span>
            <span className="ml-auto">{stamp(run.created_ts)}</span>
          </div>
        </Panel>
      </div>
    </PageShell>
  );
}
