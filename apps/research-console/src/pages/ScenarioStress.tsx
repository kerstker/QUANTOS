import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchScenarioStressSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ScenarioStressCase, ScenarioStressPayload } from '@/lib/types/research';

const alphaOptions = ['alpha-1', 'alpha-2', 'alpha-3', 'alpha-4'];

const fallbackStress: ScenarioStressPayload = {
  source: 'FRONTEND_SCENARIO_STRESS_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  stress_mode: 'READ_ONLY_SCENARIO_STRESS',
  selected_alpha_id: 'alpha-1',
  run: {
    run_id: 'stress-alpha-1-fallback',
    execution_mode: 'L2_DRY_RUN_ONLY',
    engine: 'QUANTOS_SCENARIO_REPLAY',
    scenario_set_id: 'scenario-library-fallback',
    data_snapshot_id: 'md-snapshot-fallback',
    config_hash: 'fallback-config',
    reproducibility_hash: 'fallback-repro',
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
  baseline: {total_return: 0.112, sharpe: 1.28, max_drawdown: 0.038, turnover: 0.34, capacity_estimate: 84_000_000},
  summary: {scenarios: 3, passes: 1, watches: 1, fails: 1, worst_scenario_id: 'SCN-LIQ-FALLBACK', worst_loss_delta: 0.088, average_stressed_drawdown: 0.095, average_liquidity_buffer_used: 0.31},
  scenarios: [
    {
      scenario_id: 'SCN-VOL-FALLBACK',
      name: 'Volatility spike',
      name_zh: '波动率冲击',
      severity: 'HIGH',
      decision: 'WATCH',
      shock_window: {start_day: 28, end_day: 54},
      shocks: {return_multiplier: 0.62, daily_cost_shift: 0.00055, liquidity_haircut: 0.12, correlation_shift: 0.18},
      metrics: {stressed_return: 0.043, loss_delta: 0.069, stressed_sharpe: 0.42, stressed_max_drawdown: 0.082, liquidity_buffer_used: 0.19, capacity_after_haircut: 73_920_000},
      curve: Array.from({length: 20}, (_, index) => ({x: index * 6, y: 10_000_000 + index * 8_500 - Math.sin(index / 2) * 55_000})),
      drivers: [{driver: 'return_shock', impact: 0.041}, {driver: 'cost_shift', impact: 0.066}],
    },
    {
      scenario_id: 'SCN-LIQ-FALLBACK',
      name: 'Liquidity freeze',
      name_zh: '流动性冻结',
      severity: 'CRITICAL',
      decision: 'FAIL',
      shock_window: {start_day: 44, end_day: 72},
      shocks: {return_multiplier: 0.48, daily_cost_shift: 0.00085, liquidity_haircut: 0.28, correlation_shift: 0.22},
      metrics: {stressed_return: 0.024, loss_delta: 0.088, stressed_sharpe: -0.12, stressed_max_drawdown: 0.143, liquidity_buffer_used: 0.34, capacity_after_haircut: 60_480_000},
      curve: Array.from({length: 20}, (_, index) => ({x: index * 6, y: 10_000_000 + index * 4_200 - Math.sin(index / 1.7) * 95_000})),
      drivers: [{driver: 'return_shock', impact: 0.052}, {driver: 'liquidity_haircut', impact: 0.095}],
    },
    {
      scenario_id: 'SCN-COST-FALLBACK',
      name: 'Transaction cost widening',
      name_zh: '交易成本扩大',
      severity: 'MEDIUM',
      decision: 'PASS',
      shock_window: {start_day: 0, end_day: 119},
      shocks: {return_multiplier: 0.78, daily_cost_shift: 0.00105, liquidity_haircut: 0.08, correlation_shift: 0.1},
      metrics: {stressed_return: 0.061, loss_delta: 0.051, stressed_sharpe: 0.7, stressed_max_drawdown: 0.06, liquidity_buffer_used: 0.12, capacity_after_haircut: 77_280_000},
      curve: Array.from({length: 20}, (_, index) => ({x: index * 6, y: 10_000_000 + index * 12_000 - Math.cos(index / 2) * 35_000})),
      drivers: [{driver: 'cost_shift', impact: 0.126}, {driver: 'return_shock', impact: 0.025}],
    },
  ],
  exposure_buckets: [
    {factor: 'market_beta', exposure: 0.22, shock: -0.08, estimated_pnl: -0.017},
    {factor: 'liquidity', exposure: 0.34, shock: -0.18, estimated_pnl: -0.0075},
    {factor: 'crowding', exposure: 0.05, shock: -0.24, estimated_pnl: -0.0022},
    {factor: 'cost_model', exposure: 0.18, shock: -0.12, estimated_pnl: -0.0064},
  ],
  risk_factor_contributions: [
    {factor: 'market_beta', exposure: 0.22, shock: -0.08, estimated_pnl: -0.017},
    {factor: 'liquidity', exposure: 0.34, shock: -0.18, estimated_pnl: -0.0075},
    {factor: 'cost_model', exposure: 0.18, shock: -0.12, estimated_pnl: -0.0064},
    {factor: 'crowding', exposure: 0.05, shock: -0.24, estimated_pnl: -0.0022},
  ],
  scenario_policy: {
    requires_multi_scenario_replay: true,
    requires_liquidity_haircut: true,
    requires_cost_widening: true,
    requires_correlation_shift: true,
    mutable_results: false,
    authorizes_live_trading: false,
  },
  assumptions: ['Research-only deterministic scenario replay', 'No live market connectivity', 'No order routing or broker integration'],
};

function pct(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function money(value: number) {
  if (Math.abs(value) >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`;
  }
  return value.toLocaleString();
}

function decisionClass(decision: string) {
  if (decision === 'PASS') {
    return 'border-emerald-800 bg-emerald-950/20 text-emerald-300';
  }
  if (decision === 'WATCH') {
    return 'border-amber-800 bg-amber-950/20 text-amber-300';
  }
  return 'border-red-800 bg-red-950/20 text-red-300';
}

function curvePath(points: ScenarioStressCase['curve'], width: number, height: number) {
  if (!points.length) {
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
    const y = height - ((point.y - minY) / yRange) * height;
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
}

function WorstScenario({scenario}: {scenario: ScenarioStressCase}) {
  return (
    <div className={`rounded-lg border p-4 ${decisionClass(scenario.decision)}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-wider">{scenario.scenario_id}</div>
          <h2 className="mt-1 text-base font-bold text-slate-100">{scenario.name_zh} / {scenario.name}</h2>
        </div>
        <span className="rounded bg-slate-950/60 px-2 py-0.5 text-xs">{scenario.decision}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
        <div>{bi('损失差额', 'Loss Delta')}: <span className="font-bold">{pct(scenario.metrics.loss_delta)}</span></div>
        <div>{bi('压力回撤', 'Stress DD')}: <span className="font-bold">{pct(scenario.metrics.stressed_max_drawdown)}</span></div>
        <div>{bi('压力夏普', 'Stress Sharpe')}: <span className="font-bold">{scenario.metrics.stressed_sharpe}</span></div>
        <div>{bi('剩余容量', 'Capacity')}: <span className="font-bold">{money(scenario.metrics.capacity_after_haircut)}</span></div>
      </div>
    </div>
  );
}

function ScenarioMatrix({scenarios}: {scenarios: ScenarioStressCase[]}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
      <table className="w-full min-w-[1040px] text-xs">
        <thead>
          <tr className="border-b border-slate-800 text-slate-400 uppercase">
            <th className="p-2 text-left">{bi('情景', 'Scenario')}</th>
            <th className="p-2 text-left">{bi('严重度', 'Severity')}</th>
            <th className="p-2 text-left">{bi('决策', 'Decision')}</th>
            <th className="p-2 text-right">{bi('压力收益', 'Stress Return')}</th>
            <th className="p-2 text-right">{bi('损失差额', 'Loss Delta')}</th>
            <th className="p-2 text-right">{bi('压力回撤', 'Stress DD')}</th>
            <th className="p-2 text-right">{bi('流动性使用', 'Liquidity Used')}</th>
            <th className="p-2 text-right">{bi('相关性冲击', 'Corr Shift')}</th>
          </tr>
        </thead>
        <tbody>
          {scenarios.map(scenario => (
            <tr key={scenario.scenario_id} className="border-b border-slate-800/50">
              <td className="p-2">
                <div className="font-bold text-slate-200">{scenario.name_zh} / {scenario.name}</div>
                <div className="text-slate-500">{scenario.scenario_id}</div>
              </td>
              <td className="p-2 text-cyan-300">{scenario.severity}</td>
              <td className="p-2"><span className={`rounded px-2 py-0.5 ${decisionClass(scenario.decision)}`}>{scenario.decision}</span></td>
              <td className={scenario.metrics.stressed_return >= 0 ? 'p-2 text-right text-emerald-300' : 'p-2 text-right text-red-300'}>{pct(scenario.metrics.stressed_return)}</td>
              <td className="p-2 text-right text-orange-300">{pct(scenario.metrics.loss_delta)}</td>
              <td className="p-2 text-right text-red-300">{pct(scenario.metrics.stressed_max_drawdown)}</td>
              <td className="p-2 text-right text-slate-300">{pct(scenario.metrics.liquidity_buffer_used)}</td>
              <td className="p-2 text-right text-slate-300">{pct(scenario.shocks.correlation_shift)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScenarioCurves({scenarios}: {scenarios: ScenarioStressCase[]}) {
  const width = 720;
  const height = 180;
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('压力路径', 'Stress Paths')}</h2>
      <div className="overflow-hidden rounded border border-slate-800 bg-slate-950/70 p-3">
        <svg viewBox={`0 0 ${width} ${height}`} className="h-52 w-full" role="img" aria-label="Scenario stress curves">
          {scenarios.map((scenario, index) => (
            <path
              key={scenario.scenario_id}
              d={curvePath(scenario.curve, width, height)}
              fill="none"
              stroke={['#22d3ee', '#fb923c', '#ef4444', '#a78bfa', '#34d399', '#facc15'][index % 6]}
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
      </div>
      <div className="mt-3 grid gap-2 text-[10px] text-slate-400 md:grid-cols-3">
        {scenarios.map(scenario => <div key={scenario.scenario_id}>{scenario.scenario_id}: {scenario.name}</div>)}
      </div>
    </div>
  );
}

function FactorContributions({payload}: {payload: ScenarioStressPayload}) {
  const maxAbs = Math.max(...payload.risk_factor_contributions.map(item => Math.abs(item.estimated_pnl)), 0.001);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('风险因子贡献', 'Risk Factor Contributions')}</h2>
      <div className="space-y-3 text-xs">
        {payload.risk_factor_contributions.map(item => (
          <div key={item.factor}>
            <div className="mb-1 flex justify-between gap-2">
              <span className="text-slate-300">{item.factor}</span>
              <span className={item.estimated_pnl >= 0 ? 'text-emerald-300' : 'text-red-300'}>{pct(item.estimated_pnl)}</span>
            </div>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-red-700" style={{width: `${Math.max(5, Math.abs(item.estimated_pnl) / maxAbs * 100)}%`}} />
            </div>
            <div className="mt-1 text-slate-500">{bi('暴露', 'Exposure')}: {item.exposure.toFixed(3)} / {bi('冲击', 'Shock')}: {pct(item.shock)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PolicyBoard({payload}: {payload: ScenarioStressPayload}) {
  const policies: Array<[string, boolean]> = [
    ['MULTI_SCENARIO_REPLAY', payload.scenario_policy.requires_multi_scenario_replay],
    ['LIQUIDITY_HAIRCUT', payload.scenario_policy.requires_liquidity_haircut],
    ['COST_WIDENING', payload.scenario_policy.requires_cost_widening],
    ['CORRELATION_SHIFT', payload.scenario_policy.requires_correlation_shift],
    ['IMMUTABLE_RESULTS', !payload.scenario_policy.mutable_results],
    ['NO_LIVE_AUTHORIZATION', !payload.scenario_policy.authorizes_live_trading],
  ];
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('压力策略', 'Stress Policy')}</h2>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        {policies.map(([label, passed]) => (
          <div key={label} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
            <span className="text-slate-300">{label}</span>
            <span className={passed ? 'text-emerald-300' : 'text-red-300'}>{passed ? 'PASS' : 'FAIL'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ScenarioStress() {
  const [selectedAlpha, setSelectedAlpha] = useState('alpha-1');
  const [payload, setPayload] = useState<ScenarioStressPayload | null>(null);
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
    fetchScenarioStressSnapshot(selectedAlpha, controller.signal)
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
  const stress = usingBackend && payload ? payload : fallbackStress;
  const worstScenario = useMemo(
    () => stress.scenarios.find(scenario => scenario.scenario_id === stress.summary.worst_scenario_id) ?? stress.scenarios[0],
    [stress.scenarios, stress.summary.worst_scenario_id],
  );

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('场景压力库', 'Scenario Stress Library')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={stress.stress_mode} />
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
            <div>{bi('情景集', 'Scenario set')}: {stress.run.scenario_set_id}</div>
            <div>{bi('快照', 'Snapshot')}: {stress.run.data_snapshot_id}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={stress.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('情景', 'Scenarios')} value={stress.summary.scenarios} />
        <MetricCard label={bi('通过', 'Pass')} value={stress.summary.passes} />
        <MetricCard label={bi('观察', 'Watch')} value={stress.summary.watches} />
        <MetricCard label={bi('失败', 'Fail')} value={stress.summary.fails} />
        <MetricCard label={bi('最差损失', 'Worst Loss')} value={pct(stress.summary.worst_loss_delta)} />
        <MetricCard label={bi('平均回撤', 'Avg DD')} value={pct(stress.summary.average_stressed_drawdown)} />
        <MetricCard label={bi('流动性使用', 'Liq Used')} value={pct(stress.summary.average_liquidity_buffer_used)} />
        <MetricCard label={bi('基线夏普', 'Base Sharpe')} value={stress.baseline.sharpe} />
      </div>

      {worstScenario && <WorstScenario scenario={worstScenario} />}

      <div className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <ScenarioCurves scenarios={stress.scenarios} />
        <FactorContributions payload={stress} />
      </div>

      <ScenarioMatrix scenarios={stress.scenarios} />

      <div className="grid gap-4 xl:grid-cols-[1fr_0.75fr]">
        <PolicyBoard payload={stress} />
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('假设边界', 'Assumption Boundary')}</h2>
          <div className="space-y-2 text-xs text-slate-400">
            {stress.assumptions.map(item => <div key={item} className="rounded bg-slate-950/60 px-3 py-2">{item}</div>)}
          </div>
          <div className="mt-3 space-y-1 text-[10px] text-slate-500">
            <div>config_hash: {stress.run.config_hash}</div>
            <div>reproducibility_hash: {stress.run.reproducibility_hash}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
