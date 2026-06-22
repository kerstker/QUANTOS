import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchBacktestExplainabilitySnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { BacktestExplainabilityPayload, ChartPoint } from '@/lib/types/research';

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

function pct(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function compactMoney(value: number) {
  if (Math.abs(value) >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`;
  }
  return value.toLocaleString();
}

function linePath(points: ChartPoint[], width: number, height: number, invert = false) {
  if (points.length === 0) {
    return '';
  }
  const xs = points.map(point => point.x);
  const ys = points.map(point => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const xRange = maxX - minX || 1;
  const yRange = maxY - minY || 1;
  return points.map((point, index) => {
    const x = ((point.x - minX) / xRange) * width;
    const normalizedY = (point.y - minY) / yRange;
    const y = invert ? normalizedY * height : height - normalizedY * height;
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
}

function CurvePanel({payload}: {payload: BacktestExplainabilityPayload}) {
  const width = 760;
  const height = 190;
  const equityPath = linePath(payload.equity_curve, width, height);
  const drawdownPath = linePath(payload.drawdown_curve, width, 76, true);
  const start = payload.equity_curve[0]?.y ?? 0;
  const end = payload.equity_curve[payload.equity_curve.length - 1]?.y ?? 0;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm uppercase text-slate-400">{bi('权益 / 回撤曲线', 'Equity / Drawdown Curve')}</h2>
          <div className="mt-1 text-xs text-slate-500">{compactMoney(start)} -&gt; {compactMoney(end)}</div>
        </div>
        <DecisionBadge decision={payload.run.execution_mode} />
      </div>
      <div className="overflow-hidden rounded border border-slate-800 bg-slate-950/70 p-3">
        <svg viewBox={`0 0 ${width} ${height + 86}`} className="h-64 w-full" role="img" aria-label="Backtest equity and drawdown curve">
          <path d={equityPath} fill="none" stroke="#22d3ee" strokeWidth="3" vectorEffect="non-scaling-stroke" />
          <line x1="0" y1={height + 10} x2={width} y2={height + 10} stroke="#334155" strokeWidth="1" />
          <path d={drawdownPath} transform={`translate(0 ${height + 10})`} fill="none" stroke="#f97316" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
    </div>
  );
}

function CostBreakdown({payload}: {payload: BacktestExplainabilityPayload}) {
  const max = Math.max(...payload.cost_breakdown.map(item => item.value), 0.0001);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('成本拆解', 'Cost Breakdown')}</h2>
      <div className="space-y-3 text-xs">
        {payload.cost_breakdown.map(item => (
          <div key={item.component}>
            <div className="mb-1 flex justify-between gap-2">
              <span className="text-slate-300">{item.component}</span>
              <span className="font-bold text-orange-300">{pct(item.value)}</span>
            </div>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-orange-700" style={{width: `${Math.max(5, item.value / max * 100)}%`}} />
            </div>
            <div className="mt-1 text-slate-500">{item.method}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function DriverBoard({payload}: {payload: BacktestExplainabilityPayload}) {
  const colors: Record<string, string> = {
    POSITIVE: 'border-emerald-800 bg-emerald-950/20 text-emerald-300',
    NEGATIVE: 'border-orange-800 bg-orange-950/20 text-orange-300',
    NEUTRAL: 'border-slate-700 bg-slate-950/60 text-slate-300',
  };
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('决策驱动', 'Decision Drivers')}</h2>
      <div className="grid gap-3 md:grid-cols-2">
        {payload.decision_drivers.map(driver => (
          <div key={driver.driver} className={`rounded border p-3 text-xs ${colors[driver.impact] ?? colors.NEUTRAL}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="font-bold text-slate-100">{driver.driver}</div>
              <span>{driver.impact}</span>
            </div>
            <div className="mt-2 text-slate-300">{driver.explanation}</div>
            <div className="mt-2 text-slate-500">score: {driver.score}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MonthlyReturns({payload}: {payload: BacktestExplainabilityPayload}) {
  const maxAbs = Math.max(...payload.monthly_returns.map(item => Math.abs(item.return)), 0.001);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('月度收益', 'Monthly Returns')}</h2>
      <div className="space-y-2 text-xs">
        {payload.monthly_returns.map(item => (
          <div key={item.month} className="grid grid-cols-[4.8rem_1fr_3.5rem] items-center gap-2">
            <span className="text-slate-400">{item.month}</span>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div
                className={item.return >= 0 ? 'h-full bg-emerald-700' : 'h-full bg-red-700'}
                style={{width: `${Math.max(5, Math.abs(item.return) / maxAbs * 100)}%`}}
              />
            </div>
            <span className={item.return >= 0 ? 'text-right text-emerald-300' : 'text-right text-red-300'}>{pct(item.return)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

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
  const segmentPassRate = report.summary.segments_passed / Math.max(report.summary.segments_total, 1);
  const policyRows: Array<[string, boolean]> = useMemo(() => [
    ['EQUITY_CURVE', report.explainability_policy.requires_equity_curve],
    ['COST_MODEL', report.explainability_policy.requires_cost_model],
    ['DRAWDOWN_PERIODS', report.explainability_policy.requires_drawdown_periods],
    ['REGIME_ATTRIBUTION', report.explainability_policy.requires_regime_attribution],
    ['IMMUTABLE_RESULTS', !report.explainability_policy.mutable_results],
  ], [report.explainability_policy]);

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('回测可解释性', 'Backtest Explainability')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={report.explainability_mode} />
            </div>
          </div>
          <div className="flex flex-col gap-2 text-xs text-slate-500 lg:items-end">
            <label className="flex items-center gap-2">
              <span>{bi('Alpha', 'Alpha')}</span>
              <select
                className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
                value={selectedAlpha}
                onChange={event => setSelectedAlpha(event.target.value)}
              >
                {alphaOptions.map(option => <option key={option} value={option}>{option}</option>)}
              </select>
            </label>
            <div>{bi('引擎', 'Engine')}: {report.run.engine}</div>
            <div>{bi('快照', 'Snapshot')}: {report.run.data_snapshot_id}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={report.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('总收益', 'Gross Return')} value={pct(report.summary.gross_return)} />
        <MetricCard label={bi('净收益', 'Net Return')} value={pct(report.summary.net_return)} />
        <MetricCard label={bi('夏普', 'Sharpe')} value={report.summary.gross_sharpe} />
        <MetricCard label={bi('最大回撤', 'Max DD')} value={pct(report.summary.max_drawdown)} />
        <MetricCard label={bi('胜率', 'Win Rate')} value={pct(report.summary.win_rate)} />
        <MetricCard label={bi('成本拖累', 'Cost Drag')} value={pct(report.summary.cost_drag)} />
        <MetricCard label={bi('分段通过', 'Segments')} value={`${report.summary.segments_passed}/${report.summary.segments_total}`} />
        <MetricCard label={bi('稳定性', 'Stability')} value={pct(segmentPassRate)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.35fr_0.65fr]">
        <CurvePanel payload={report} />
        <div className="space-y-4">
          <CostBreakdown payload={report} />
          <MonthlyReturns payload={report} />
        </div>
      </div>

      <DriverBoard payload={report} />

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('解释策略', 'Explainability Policy')}</h2>
          <div className="space-y-2 text-xs">
            {policyRows.map(([label, passed]) => (
              <div key={label} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
                <span className="text-slate-300">{label}</span>
                <span className={passed ? 'text-emerald-300' : 'text-red-300'}>{passed ? 'PASS' : 'FAIL'}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('市场状态归因', 'Regime Attribution')}</h2>
          <div className="space-y-3 text-xs">
            {report.regime_attribution.map(item => (
              <div key={item.regime} className="rounded bg-slate-950/60 p-3">
                <div className="mb-2 flex justify-between gap-2">
                  <span className="font-bold text-slate-200">{item.regime}</span>
                  <span className={item.contribution >= 0 ? 'text-emerald-300' : 'text-red-300'}>{pct(item.contribution)}</span>
                </div>
                <div className="text-slate-500">{item.days} days / hit rate {pct(item.hit_rate)}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('假设边界', 'Assumption Boundary')}</h2>
          <div className="space-y-2 text-xs text-slate-400">
            {report.assumptions.map(item => <div key={item} className="rounded bg-slate-950/60 px-3 py-2">{item}</div>)}
          </div>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase">
                <th className="p-2 text-left">{bi('分段', 'Segment')}</th>
                <th className="p-2 text-right">{bi('区间', 'Window')}</th>
                <th className="p-2 text-right">{bi('收益', 'Return')}</th>
                <th className="p-2 text-right">{bi('夏普', 'Sharpe')}</th>
                <th className="p-2 text-right">{bi('胜率', 'Win Rate')}</th>
                <th className="p-2 text-right">{bi('波动', 'Vol')}</th>
              </tr>
            </thead>
            <tbody>
              {report.segments.map(segment => (
                <tr key={segment.segment_id} className="border-b border-slate-800/50">
                  <td className="p-2 font-bold text-cyan-300">{segment.segment_id}</td>
                  <td className="p-2 text-right text-slate-400">{segment.start_day}-{segment.end_day}</td>
                  <td className={segment.return >= 0 ? 'p-2 text-right text-emerald-300' : 'p-2 text-right text-red-300'}>{pct(segment.return)}</td>
                  <td className="p-2 text-right text-slate-300">{segment.sharpe}</td>
                  <td className="p-2 text-right text-slate-300">{pct(segment.win_rate)}</td>
                  <td className="p-2 text-right text-slate-300">{pct(segment.volatility)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
          <table className="w-full min-w-[620px] text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase">
                <th className="p-2 text-left">{bi('回撤', 'Drawdown')}</th>
                <th className="p-2 text-right">{bi('开始', 'Start')}</th>
                <th className="p-2 text-right">{bi('谷底', 'Trough')}</th>
                <th className="p-2 text-right">{bi('恢复', 'Recovery')}</th>
                <th className="p-2 text-right">{bi('深度', 'Depth')}</th>
                <th className="p-2 text-right">{bi('天数', 'Days')}</th>
              </tr>
            </thead>
            <tbody>
              {report.drawdown_periods.map(period => (
                <tr key={`${period.rank}-${period.trough_day}`} className="border-b border-slate-800/50">
                  <td className="p-2 font-bold text-orange-300">#{period.rank}</td>
                  <td className="p-2 text-right text-slate-400">{period.start_day}</td>
                  <td className="p-2 text-right text-slate-400">{period.trough_day}</td>
                  <td className="p-2 text-right text-slate-400">{period.recovery_day}</td>
                  <td className="p-2 text-right text-red-300">{pct(period.depth)}</td>
                  <td className="p-2 text-right text-slate-300">{period.duration_days}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
