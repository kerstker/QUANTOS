import React, { useEffect, useState } from 'react';
import { fetchBacktestExplainabilitySnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { BacktestExplainabilityPayload } from '@/lib/types/research';
import type { ConsoleApiState } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, Chip, type Column,
} from '@/ui/primitives';
import { AreaChart, UnderwaterChart, BarSeries, Histogram, DivergingBars } from '@/ui/charts';
import { num, pct, fixed, money, deltaTone, statusTone } from '@/ui/format';

const alphaOptions = ['alpha-1', 'alpha-2', 'alpha-3', 'alpha-4'];

const fallbackBacktest: BacktestExplainabilityPayload = {
  source: 'FRONTEND_BACKTEST_EXPLAINABILITY_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  explainability_mode: 'READ_ONLY_BACKTEST_EXPLAINABILITY',
  selected_alpha_id: 'alpha-1',
  run: {
    run_id: 'bt-alpha-1-explain-fallback',
    alpha_id: 'alpha-1',
    execution_mode: 'L2_DRY_RUN_ONLY',
    engine: 'QUANTOS_DETERMINISTIC_REPLAY',
    data_snapshot_id: 'md-snapshot-fallback',
    config_hash: 'fallback-config-hash',
    reproducibility_hash: 'fallback-repro-hash',
  },
  alpha: {
    candidate_id: 'alpha-1',
    expression: '(ts_mean :close 5)',
    ic: 0.062,
    rank_ic: 0.058,
    icir: 1.12,
    sharpe: 1.28,
    turnover: 0.34,
    decay_half_life: 18,
    capacity_estimate: 84_000_000,
    correlation_penalty: 0.05,
    composite_score: 0.71,
    lifecycle_state: 'ACTIVE',
  },
  summary: {
    gross_return: 0.112,
    net_return: 0.106,
    gross_sharpe: 1.28,
    max_drawdown: 0.038,
    win_rate: 0.61,
    turnover: 0.34,
    cost_drag: 0.006,
    segments_passed: 5,
    segments_total: 6,
  },
  equity_curve: Array.from({length: 40}, (_, index) => ({x: index, y: 10_000_000 + index * 18_500 + Math.sin(index / 3) * 90_000})),
  drawdown_curve: Array.from({length: 40}, (_, index) => ({x: index, y: Math.max(0, Math.sin(index / 5) * 0.02)})),
  segments: [
    {segment_id: 'S01', start_day: 0, end_day: 19, return: 0.019, sharpe: 1.22, win_rate: 0.6, volatility: 0.12},
    {segment_id: 'S02', start_day: 20, end_day: 39, return: 0.026, sharpe: 1.48, win_rate: 0.65, volatility: 0.11},
    {segment_id: 'S03', start_day: 40, end_day: 59, return: -0.006, sharpe: -0.32, win_rate: 0.45, volatility: 0.14},
  ],
  monthly_returns: [
    {month: '2026-01', return: 0.022, positive_days: 14, negative_days: 7},
    {month: '2026-02', return: 0.018, positive_days: 13, negative_days: 8},
    {month: '2026-03', return: -0.004, positive_days: 9, negative_days: 12},
  ],
  cost_breakdown: [
    {component: 'turnover_cost', value: 0.0032, method: 'turnover * 18bps'},
    {component: 'slippage_cost', value: 0.0013, method: 'volume proxy + turnover adjustment'},
    {component: 'borrow_cost', value: 0.0004, method: 'research borrow proxy'},
    {component: 'market_impact', value: 0.0014, method: 'turnover capacity proxy'},
  ],
  drawdown_periods: [
    {rank: 1, start_day: 38, trough_day: 46, recovery_day: 58, depth: 0.038, duration_days: 20},
    {rank: 2, start_day: 64, trough_day: 72, recovery_day: 84, depth: 0.024, duration_days: 20},
  ],
  trade_distribution: [
    {bucket: '< -1%', count: 2, avg_return: -0.014},
    {bucket: '-1% to 0%', count: 7, avg_return: -0.004},
    {bucket: '0% to 1%', count: 13, avg_return: 0.004},
    {bucket: '> 1%', count: 5, avg_return: 0.017},
  ],
  regime_attribution: [
    {regime: 'normal', days: 72, contribution: 0.071, hit_rate: 0.61},
    {regime: 'stress', days: 24, contribution: -0.014, hit_rate: 0.46},
    {regime: 'recovery', days: 23, contribution: 0.049, hit_rate: 0.68},
  ],
  decision_drivers: [
    {driver: 'Information coefficient', impact: 'POSITIVE', score: 0.062, explanation: 'Forward-return correlation remains positive across most segments'},
    {driver: 'Transaction cost drag', impact: 'NEGATIVE', score: 0.0045, explanation: 'Cost model subtracts turnover, slippage, borrow, and market impact'},
  ],
  assumptions: ['Offline deterministic replay only', 'No broker venue routing', 'No live market connectivity'],
  explainability_policy: {
    requires_equity_curve: true,
    requires_cost_model: true,
    requires_drawdown_periods: true,
    requires_regime_attribution: true,
    mutable_results: false,
  },
};

const driverImpactTone: Record<string, 'pos' | 'neg' | 'muted'> = {
  POSITIVE: 'pos', NEGATIVE: 'neg', NEUTRAL: 'muted',
};

export default function BacktestExplainability() {
  const [selectedAlpha, setSelectedAlpha] = useState('alpha-1');
  const [payload, setPayload] = useState<BacktestExplainabilityPayload | null>(null);
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
    fetchBacktestExplainabilitySnapshot(selectedAlpha, controller.signal)
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
  const report = usingBackend && payload ? payload : fallbackBacktest;
  const s = report.summary;
  const segmentPassRate = s.segments_passed / Math.max(s.segments_total, 1);

  const equityStart = report.equity_curve[0]?.y ?? 0;
  const equityEnd = report.equity_curve[report.equity_curve.length - 1]?.y ?? 0;

  const policyRows: Array<{ label: string; ok: boolean }> = [
    { label: bi('权益曲线', 'EQUITY_CURVE'), ok: report.explainability_policy.requires_equity_curve },
    { label: bi('成本模型', 'COST_MODEL'), ok: report.explainability_policy.requires_cost_model },
    { label: bi('回撤周期', 'DRAWDOWN_PERIODS'), ok: report.explainability_policy.requires_drawdown_periods },
    { label: bi('市场状态归因', 'REGIME_ATTRIBUTION'), ok: report.explainability_policy.requires_regime_attribution },
    { label: bi('结果不可变', 'IMMUTABLE_RESULTS'), ok: !report.explainability_policy.mutable_results },
  ];

  const alphaSelector = (
    <label className="flex items-center gap-2 text-2xs uppercase tracking-wide text-faint">
      <span>{bi('因子', 'Alpha')}</span>
      <select
        className="rounded-md border border-line bg-inset px-2 py-1 text-xs text-ink mono"
        value={selectedAlpha}
        onChange={event => setSelectedAlpha(event.target.value)}
      >
        {alphaOptions.map(option => <option key={option} value={option}>{option}</option>)}
      </select>
    </label>
  );

  const segmentCols: Column<typeof report.segments[number]>[] = [
    { key: 'seg', header: bi('分段', 'Segment'), render: r => <span className="font-semibold text-accent mono">{r.segment_id}</span> },
    { key: 'win', header: bi('区间', 'Window'), align: 'right', mono: true, render: r => `${r.start_day}–${r.end_day}` },
    { key: 'ret', header: bi('收益', 'Return'), align: 'right', render: r => <span className={r.return >= 0 ? 'text-pos tnum' : 'text-neg tnum'}>{pct(r.return)}</span> },
    { key: 'shp', header: bi('夏普', 'Sharpe'), align: 'right', mono: true, render: r => <span className={r.sharpe >= 0 ? 'text-ink' : 'text-neg'}>{fixed(r.sharpe)}</span> },
    { key: 'wr', header: bi('胜率', 'Win Rate'), align: 'right', mono: true, render: r => pct(r.win_rate, 0) },
    { key: 'vol', header: bi('波动', 'Vol'), align: 'right', mono: true, render: r => pct(r.volatility) },
  ];

  const drawdownCols: Column<typeof report.drawdown_periods[number]>[] = [
    { key: 'rank', header: bi('排名', 'Rank'), render: r => <span className="font-semibold text-warn mono">#{r.rank}</span> },
    { key: 'start', header: bi('开始', 'Start'), align: 'right', mono: true, render: r => num(r.start_day) },
    { key: 'trough', header: bi('谷底', 'Trough'), align: 'right', mono: true, render: r => num(r.trough_day) },
    { key: 'rec', header: bi('恢复', 'Recovery'), align: 'right', mono: true, render: r => num(r.recovery_day) },
    { key: 'depth', header: bi('深度', 'Depth'), align: 'right', render: r => <span className="text-neg tnum">{pct(r.depth)}</span> },
    { key: 'dur', header: bi('天数', 'Days'), align: 'right', mono: true, render: r => num(r.duration_days) },
  ];

  const regimeCols: Column<typeof report.regime_attribution[number]>[] = [
    { key: 'reg', header: bi('市场状态', 'Regime'), render: r => <StatusChip value={r.regime} /> },
    { key: 'days', header: bi('天数', 'Days'), align: 'right', mono: true, render: r => num(r.days) },
    { key: 'contrib', header: bi('贡献', 'Contribution'), align: 'right', render: r => <span className={r.contribution >= 0 ? 'text-pos tnum' : 'text-neg tnum'}>{pct(r.contribution)}</span> },
    { key: 'hit', header: bi('命中率', 'Hit Rate'), align: 'right', mono: true, render: r => pct(r.hit_rate, 0) },
  ];

  return (
    <PageShell>
      <PageHeader
        title="Backtest Explainability" titleZh="回测可解释性"
        subtitle={bi('确定性离线重放的权益、成本、回撤与决策驱动归因(只读)', 'Deterministic offline-replay equity, cost, drawdown and decision-driver attribution (read-only)')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {alphaSelector}
            <Chip label={report.explainability_mode} tone="info" />
            <Chip label={report.run.execution_mode} tone={statusTone(report.run.execution_mode)} />
          </div>
        }
      />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={report.source} error={apiError} />

      <KpiStrip cols={8}>
        <StatTile label={bi('总收益', 'Gross Return')} value={pct(s.gross_return)} tone={deltaTone(s.gross_return)} />
        <StatTile label={bi('净收益', 'Net Return')} value={pct(s.net_return)} accent />
        <StatTile label={bi('夏普', 'Sharpe')} value={fixed(s.gross_sharpe)} tone="pos" />
        <StatTile label={bi('最大回撤', 'Max DD')} value={pct(s.max_drawdown)} tone="neg" />
        <StatTile label={bi('胜率', 'Win Rate')} value={pct(s.win_rate, 0)} />
        <StatTile label={bi('换手率', 'Turnover')} value={pct(s.turnover, 0)} tone="info" />
        <StatTile label={bi('成本拖累', 'Cost Drag')} value={pct(s.cost_drag, 2)} tone="warn" />
        <StatTile label={bi('分段通过', 'Segments')} value={`${num(s.segments_passed)}/${num(s.segments_total)}`} sub={pct(segmentPassRate, 0)} tone={segmentPassRate >= 0.8 ? 'pos' : 'warn'} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('权益', 'Equity')} title={bi('权益曲线', 'Equity Curve')}
          right={<span className="text-2xs text-faint mono">{money(equityStart)} → {money(equityEnd)}</span>}>
          <AreaChart data={report.equity_curve} height={236} tone="accent" yFormat={v => money(v)} />
          <div className="mt-3 border-t border-line pt-3">
            <div className="mb-1.5 text-2xs uppercase tracking-wider text-faint">{bi('回撤(水下)', 'Drawdown (underwater)')}</div>
            <UnderwaterChart data={report.drawdown_curve.map(p => ({ x: p.x, y: -Math.abs(p.y) }))} height={108} yFormat={v => pct(v)} />
          </div>
        </Panel>

        <Panel eyebrow={bi('因子', 'Alpha')} title={report.alpha.candidate_id}
          right={<StatusChip value={report.alpha.lifecycle_state} />}>
          <div className="mb-3 rounded-lg border border-line bg-inset px-3 py-2 text-2xs text-muted mono break-all">
            {report.alpha.expression}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <StatTile label="IC" value={fixed(report.alpha.ic, 3)} tone={deltaTone(report.alpha.ic)} />
            <StatTile label="Rank IC" value={fixed(report.alpha.rank_ic, 3)} />
            <StatTile label="ICIR" value={fixed(report.alpha.icir)} tone="pos" />
            <StatTile label={bi('衰减半衰期', 'Decay HL')} value={num(report.alpha.decay_half_life)} unit={bi('天', 'd')} />
            <StatTile label={bi('容量', 'Capacity')} value={money(report.alpha.capacity_estimate)} />
            <StatTile label={bi('综合分', 'Composite')} value={pct(report.alpha.composite_score, 0)} accent />
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('月度', 'Monthly')} title={bi('月度收益', 'Monthly Returns')}>
          <BarSeries
            data={report.monthly_returns.map(m => m.return)}
            labels={report.monthly_returns.map(m => m.month)}
            valueFormat={v => pct(v, 1)}
            height={200}
          />
        </Panel>

        <Panel eyebrow={bi('成本', 'Costs')} title={bi('成本拆解', 'Cost Breakdown')}
          right={<Chip label={pct(s.cost_drag, 2)} tone="warn" />}>
          <DivergingBars
            items={report.cost_breakdown.map(c => ({ label: c.component, value: -c.value }))}
            valueFormat={v => pct(Math.abs(v), 2)}
          />
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('分布', 'Distribution')} title={bi('交易收益分布', 'Trade Return Distribution')}>
          <Histogram
            bins={report.trade_distribution.map(t => ({ label: t.bucket, count: t.count }))}
            tone="info" height={200}
          />
        </Panel>

        <Panel className="xl:col-span-2" eyebrow={bi('归因', 'Attribution')} title={bi('决策驱动', 'Decision Drivers')}>
          <DivergingBars
            items={report.decision_drivers.map(d => ({
              label: d.driver,
              value: d.impact === 'NEGATIVE' ? -Math.abs(d.score) : Math.abs(d.score),
            }))}
            valueFormat={v => fixed(Math.abs(v), 4)}
            height={Math.max(120, report.decision_drivers.length * 30 + 16)}
          />
          <div className="mt-3 flex flex-col gap-2 border-t border-line pt-3">
            {report.decision_drivers.map(d => (
              <div key={d.driver} className="flex items-start justify-between gap-3 text-2xs">
                <div className="min-w-0">
                  <span className="font-semibold text-ink">{d.driver}</span>
                  <p className="mt-0.5 text-faint">{d.explanation}</p>
                </div>
                <Chip label={d.impact} tone={driverImpactTone[d.impact] ?? 'muted'} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel eyebrow={bi('稳健性', 'Robustness')} title={bi('分段表现', 'Segment Performance')}>
          <DataTable columns={segmentCols} rows={report.segments} getKey={r => r.segment_id} dense />
        </Panel>
        <Panel eyebrow={bi('风险', 'Risk')} title={bi('最大回撤周期', 'Worst Drawdown Periods')}>
          <DataTable columns={drawdownCols} rows={report.drawdown_periods} getKey={r => `${r.rank}-${r.trough_day}`} dense />
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('市场状态', 'Regime')} title={bi('市场状态归因', 'Regime Attribution')}>
          <DataTable columns={regimeCols} rows={report.regime_attribution} getKey={r => r.regime} dense />
        </Panel>

        <Panel eyebrow={bi('治理', 'Governance')} title={bi('解释策略门', 'Explainability Policy Gates')}>
          <div className="flex flex-col gap-2.5">
            {policyRows.map(row => (
              <div key={row.label} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset px-3 py-2">
                <span className="text-xs text-muted">{row.label}</span>
                <GateDot ok={row.ok} label={row.ok ? bi('通过', 'PASS') : bi('未满足', 'FAIL')} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('边界', 'Boundary')} title={bi('回测假设边界', 'Backtest Assumption Boundary')}
        right={
          <div className="flex items-center gap-3">
            <span className="text-2xs text-faint mono">{bi('引擎', 'Engine')}: {report.run.engine}</span>
            <span className="text-2xs text-faint mono">{bi('快照', 'Snapshot')}: {report.run.data_snapshot_id}</span>
          </div>
        }>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          {report.assumptions.map(item => (
            <div key={item} className="flex items-center gap-2 rounded-lg border border-line bg-inset px-3 py-2 text-2xs text-muted">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-info" />
              {item}
            </div>
          ))}
        </div>
      </Panel>
    </PageShell>
  );
}
