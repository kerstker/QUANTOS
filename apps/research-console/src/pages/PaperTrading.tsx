import React, { useEffect, useState } from 'react';
import { fetchPaperTradingSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockPaperRuns } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { PaperTradingPayload, PaperRun, CalculationLineageItem } from '@/lib/types/research';
import type { ConsoleApiState } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, Chip, KeyValue, type Column,
} from '@/ui/primitives';
import { AreaChart, UnderwaterChart } from '@/ui/charts';
import { num, pct, pctPoints, fixed, money, statusTone, type Tone } from '@/ui/format';

const ALPHA_IDS = ['alpha-1', 'alpha-2', 'alpha-3', 'alpha-4'];

const TONE_TEXT: Record<Tone, string> = {
  pos: 'text-pos', neg: 'text-neg', warn: 'text-warn', info: 'text-info',
  accent: 'text-accent', special: 'text-special', muted: 'text-muted',
};

/** Known metric keys get a bilingual label + display formatter; unknowns fall through. */
const METRIC_META: Record<string, { zh: string; en: string; fmt: (v: number) => string; tone?: Tone }> = {
  sharpe: { zh: '夏普', en: 'Sharpe', fmt: v => fixed(v, 2), tone: 'accent' },
  max_drawdown: { zh: '最大回撤', en: 'Max Drawdown', fmt: v => pctPoints(v, 1), tone: 'neg' },
  annual_return: { zh: '年化收益', en: 'Annual Return', fmt: v => pctPoints(v, 1), tone: 'pos' },
  volatility: { zh: '波动率', en: 'Volatility', fmt: v => pctPoints(v, 1), tone: 'warn' },
  win_rate: { zh: '胜率', en: 'Win Rate', fmt: v => pct(v, 1), tone: 'info' },
  sortino: { zh: '索提诺', en: 'Sortino', fmt: v => fixed(v, 2), tone: 'special' },
};
const metricLabel = (k: string) => {
  const m = METRIC_META[k];
  return m ? bi(m.zh, m.en) : k.replace(/_/g, ' ');
};
const metricFmt = (k: string, v: number) => (METRIC_META[k]?.fmt ?? ((x: number) => fixed(x, 2)))(v);

export default function PaperTrading() {
  const [selectedAlpha, setSelectedAlpha] = useState('alpha-1');
  const [payload, setPayload] = useState<PaperTradingPayload | null>(null);
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
    fetchPaperTradingSnapshot(selectedAlpha, controller.signal)
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
  }, [apiEnabled, selectedAlpha]);

  const usingBackend = apiState === 'connected' && payload !== null;
  const r: PaperRun = usingBackend ? payload.run : mockPaperRuns[0];
  const lineage: CalculationLineageItem[] = usingBackend ? payload.lineage ?? [] : [];

  // Equity series (deterministic; derived only from payload/mock fields).
  const equity = r.equity_curve.map(p => ({ x: p.timestamp, y: p.equity }));
  const startEquity = equity.length ? equity[0].y : r.final_equity;
  const peakEquity = equity.reduce((m, p) => Math.max(m, p.y), startEquity);
  const totalPnl = r.final_equity - startEquity;
  const totalReturn = startEquity > 0 ? totalPnl / startEquity : 0;

  // Underwater drawdown derived from the running peak (deterministic, no lookahead).
  let peak = startEquity;
  const drawdown = equity.map(p => {
    peak = Math.max(peak, p.y);
    return { x: p.x, y: peak > 0 ? (p.y - peak) / peak : 0 };
  });
  const troughDd = drawdown.reduce((m, p) => Math.min(m, p.y), 0);

  // Order the metrics map: known keys first (in a stable preferred order), then the rest.
  const preferred = ['sharpe', 'annual_return', 'max_drawdown', 'volatility', 'sortino', 'win_rate'];
  const metricKeys = Object.keys(r.metrics).sort((a, b) => {
    const ia = preferred.indexOf(a), ib = preferred.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
  });

  const lineageCols: Column<CalculationLineageItem>[] = [
    { key: 'layer', header: bi('层', 'Layer'), render: l => <Chip label={l.layer} tone="info" /> },
    { key: 'id', header: bi('节点', 'Node'), mono: true, render: l => <span className="mono text-ink">{l.id}</span> },
    { key: 'formula', header: bi('公式', 'Formula'), render: l => <span className="mono text-2xs text-muted">{l.formula}</span> },
    { key: 'backend', header: bi('后端', 'Backend'), mono: true, render: l => <span className="text-muted">{l.backend}</span> },
    { key: 'inputs', header: bi('输入', 'Inputs'), render: l => <span className="text-2xs text-faint">{l.inputs.join(', ')}</span> },
    { key: 'output', header: bi('输出', 'Output'), mono: true, render: l => <span className="text-accent">{l.output}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Paper Trading" titleZh="纸面交易"
        subtitle={bi('纸面回放运行的只读快照:权益曲线、回撤、绩效指标与计算血缘(无真实下单、无券商)',
          'Read-only snapshot of a paper-replay run — equity, drawdown, metrics, calculation lineage (no live orders, no broker)')}
        actions={
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 text-2xs uppercase tracking-wide text-faint">{bi('因子', 'Alpha')}</span>
            {ALPHA_IDS.map(alphaId => {
              const active = selectedAlpha === alphaId;
              return (
                <button key={alphaId} onClick={() => setSelectedAlpha(alphaId)}
                  className={`rounded-md border px-2.5 py-1 text-2xs font-semibold transition-colors ${
                    active ? 'border-accent/50 bg-accent/15 text-accent'
                           : 'border-line bg-elevated/40 text-muted hover:bg-elevated/70'}`}>
                  {alphaId}
                </button>
              );
            })}
          </div>
        } />

      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />

      <KpiStrip cols={7}>
        <StatTile label={bi('运行状态', 'Run Status')} value={<StatusChip value={r.status} />} tone={statusTone(r.status)} />
        <StatTile label={bi('最终权益', 'Final Equity')} value={money(r.final_equity)} accent />
        <StatTile label={bi('累计盈亏', 'Total PnL')} value={money(totalPnl)} tone={totalPnl >= 0 ? 'pos' : 'neg'}
          sub={pct(totalReturn, 1)} />
        <StatTile label={bi('成交数', 'Trades')} value={num(r.total_trades)} tone="info" />
        <StatTile label={bi('夏普', 'Sharpe')} value={r.metrics.sharpe !== undefined ? fixed(r.metrics.sharpe, 2) : '—'} accent />
        <StatTile label={bi('最大回撤', 'Max Drawdown')}
          value={r.metrics.max_drawdown !== undefined ? pctPoints(r.metrics.max_drawdown, 1) : pct(troughDd, 1)} tone="neg" />
        <StatTile label={bi('年化收益', 'Annual Return')}
          value={r.metrics.annual_return !== undefined ? pctPoints(r.metrics.annual_return, 1) : '—'} tone="pos" />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('回放', 'Replay')} title={bi('权益曲线', 'Equity Curve')}
          right={<Chip label={bi(`峰值 ${money(peakEquity)}`, `peak ${money(peakEquity)}`)} tone="special" />}>
          <AreaChart data={equity} height={236} tone="accent" yFormat={v => money(v)} />
        </Panel>

        <Panel eyebrow={bi('运行', 'Run')} title={bi('运行明细', 'Run Detail')}>
          <KeyValue cols={1} items={[
            { k: bi('运行 ID', 'Run ID'), v: r.run_id },
            { k: bi('因子 ID', 'Alpha ID'), v: r.alpha_id },
            { k: bi('选中因子', 'Selected'), v: usingBackend ? payload.selected_alpha_id : selectedAlpha },
            { k: bi('起始权益', 'Start Equity'), v: money(startEquity) },
            { k: bi('最终权益', 'Final Equity'), v: money(r.final_equity) },
            { k: bi('累计盈亏', 'Total PnL'), v: money(totalPnl) },
            { k: bi('成交数', 'Trades'), v: num(r.total_trades) },
            { k: bi('状态', 'Status'), v: <StatusChip value={r.status} /> },
          ]} />
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('风险', 'Risk')} title={bi('水下回撤', 'Underwater Drawdown')}
          right={<Chip label={bi(`谷底 ${pct(troughDd, 1)}`, `trough ${pct(troughDd, 1)}`)} tone="neg" />}>
          <UnderwaterChart data={drawdown} height={176} yFormat={v => pct(v, 1)} />
        </Panel>

        <Panel eyebrow={bi('绩效', 'Performance')} title={bi('指标', 'Metrics')}>
          {metricKeys.length === 0
            ? <div className="py-6 text-center text-2xs text-faint">{bi('无指标', 'No metrics')}</div>
            : (
              <div className="flex flex-col divide-y divide-line/60">
                {metricKeys.map(k => (
                  <div key={k} className="flex items-center justify-between gap-3 py-2">
                    <span className="text-xs capitalize text-muted">{metricLabel(k)}</span>
                    <span className={`tnum text-sm font-semibold ${
                      METRIC_META[k]?.tone ? TONE_TEXT[METRIC_META[k]!.tone!] : 'text-ink'}`}>
                      {metricFmt(k, r.metrics[k])}
                    </span>
                  </div>
                ))}
              </div>
            )}
        </Panel>
      </div>

      <Panel eyebrow={bi('可复现性', 'Reproducibility')} title={bi('计算血缘', 'Calculation Lineage')}
        right={lineage.length > 0
          ? <Chip label={bi(`${lineage.length} 节点`, `${lineage.length} nodes`)} tone="info" />
          : <Chip label={bi('mock 无血缘', 'no lineage in mock')} tone="muted" />}>
        <DataTable columns={lineageCols} rows={lineage} getKey={l => l.id}
          empty={bi('后端连接后显示计算血缘', 'Calculation lineage appears when backend is connected')} />
      </Panel>
    </PageShell>
  );
}
