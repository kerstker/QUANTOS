import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchResearchRuns } from '@/lib/api/research-console-api';
import { triggerRun, runnerEnabled, RUNNABLE_KINDS, type RunParamSpec } from '@/lib/api/research-runner-api';
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
  const [reloadKey, setReloadKey] = useState(0);
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<ResearchRunsPayload>(
    s => fetchResearchRuns(s), [reloadKey]);
  const data = usingBackend ? payload! : fallbackRuns;
  const [kind, setKind] = useState('全部');
  const [trigMsg, setTrigMsg] = useState<{ tone: 'pos' | 'neg' | 'info'; text: string } | null>(null);
  // per-kind param values for kinds that expose a typed schema (e.g. factory search depth)
  const [paramVals, setParamVals] = useState<Record<string, Record<string, number | boolean>>>(() => {
    const init: Record<string, Record<string, number | boolean>> = {};
    for (const rk of RUNNABLE_KINDS) if (rk.params) {
      init[rk.kind] = {};
      for (const p of rk.params) init[rk.kind][p.name] = p.default;
    }
    return init;
  });
  function setParam(k: string, name: string, value: number | boolean) {
    setParamVals(prev => ({ ...prev, [k]: { ...prev[k], [name]: value } }));
  }

  // live polling: while any run is queued/running, re-fetch the list so status updates without a manual reload
  const active = data.runs.some(r => r.status === 'queued' || r.status === 'running');
  useEffect(() => {
    if (!usingBackend || !active) return;
    const t = setInterval(() => setReloadKey(k => k + 1), 3000);
    return () => clearInterval(t);
  }, [usingBackend, active]);

  async function onRun(k: string) {
    const params = paramVals[k] ?? {};
    setTrigMsg({ tone: 'info', text: bi(`已提交 ${k} …`, `submitting ${k} …`) });
    try {
      const res = await triggerRun(k, params);
      setTrigMsg({ tone: 'pos', text: bi(`已排队:${res.kind}`, `queued: ${res.kind}`) });
      setReloadKey(x => x + 1);            // surface the queued run immediately; polling then tracks it
    } catch (e) {
      setTrigMsg({ tone: 'neg', text: (e instanceof Error ? e.message : String(e)) });
    }
  }

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

      <Panel eyebrow={bi('从前端运行', 'Operate')} title={bi('触发研究运行', 'Trigger a Research Run')}
        right={<Chip label={runnerEnabled() ? bi('runner 已连接', 'runner online') : bi('runner 未启动', 'runner offline')}
          tone={runnerEnabled() ? 'pos' : 'muted'} />}>
        <div className="mb-2 rounded-lg border border-info/30 bg-info/5 px-3 py-1.5 text-2xs text-muted">
          {bi('研究运行 · 只跑回测/搜索/取数 · 不下单 / 不碰实盘 / 不碰凭证(L2)',
            'Research only — backtests / searches / data refresh · no orders / no live / no credentials (L2)')}
        </div>
        <div className="flex flex-wrap items-start gap-2">
          {RUNNABLE_KINDS.map(rk => (
            <div key={rk.kind} className={`rounded-md border px-2 py-1.5 ${rk.params ? 'border-line/70' : 'border-transparent'}`}>
              <button onClick={() => onRun(rk.kind)} disabled={!runnerEnabled()}
                title={runnerEnabled() ? rk.kind : bi('启动 research_runner(端口 8420)后可用', 'start research_runner (port 8420) to enable')}
                className={`rounded-md border px-2.5 py-1.5 text-xs transition ${runnerEnabled()
                  ? 'border-accent/50 text-accent hover:bg-accent/10' : 'cursor-not-allowed border-line text-faint'}`}>
                ▷ {rk.label}
              </button>
              {rk.params && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  {rk.params.map((p: RunParamSpec) => (
                    <label key={p.name} className="flex items-center gap-1 text-2xs text-faint">
                      <span>{p.label}</span>
                      {p.type === 'bool' ? (
                        <input type="checkbox" checked={Boolean(paramVals[rk.kind]?.[p.name])}
                          disabled={!runnerEnabled()}
                          onChange={e => setParam(rk.kind, p.name, e.target.checked)} />
                      ) : (
                        <input type="number" min={p.min} max={p.max}
                          value={Number(paramVals[rk.kind]?.[p.name] ?? p.default)}
                          disabled={!runnerEnabled()}
                          onChange={e => { const n = parseInt(e.target.value, 10); setParam(rk.kind, p.name, Number.isNaN(n) ? 0 : n); }}
                          className="w-14 rounded border border-line bg-inset/40 px-1 py-0.5 text-2xs text-ink disabled:opacity-50" />
                      )}
                    </label>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
        {trigMsg && <div className={`mt-2 text-2xs ${({ pos: 'text-pos', neg: 'text-neg', info: 'text-info' } as const)[trigMsg.tone]}`}>{trigMsg.text}</div>}
        {active && <div className="mt-1 text-2xs text-info">{bi('● 有运行进行中,自动刷新状态…', '● a run is in progress — auto-refreshing…')}</div>}
      </Panel>

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
