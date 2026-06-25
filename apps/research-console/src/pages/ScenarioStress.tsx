import React, { useEffect, useMemo, useState } from 'react';
import { fetchScenarioStressSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ConsoleApiState } from '@/ui/useConsoleSnapshot';
import type { ScenarioStressCase, ScenarioStressPayload } from '@/lib/types/research';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, Chip, GateDot, ProgressBar, KeyValue, type Column,
} from '@/ui/primitives';
import { AreaChart, Sparkline, DivergingBars } from '@/ui/charts';
import { num, pct, fixed, signed, money, shortHash, statusTone, deltaTone, type Tone } from '@/ui/format';

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

const severityTone = (s: string): Tone => statusTone(s) === 'muted' ? 'info' : statusTone(s);

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

  const worst = useMemo(
    () => stress.scenarios.find(s => s.scenario_id === stress.summary.worst_scenario_id) ?? stress.scenarios[0],
    [stress.scenarios, stress.summary.worst_scenario_id],
  );

  const exposureItems = useMemo(
    () => stress.exposure_buckets.map(b => ({ label: b.factor, value: b.estimated_pnl })),
    [stress.exposure_buckets],
  );
  const contribItems = useMemo(
    () => stress.risk_factor_contributions.map(b => ({ label: b.factor, value: b.estimated_pnl })),
    [stress.risk_factor_contributions],
  );

  const policyRows: Array<{ label: React.ReactNode; ok: boolean }> = [
    { label: bi('多情景重放', 'Multi-scenario replay'), ok: stress.scenario_policy.requires_multi_scenario_replay },
    { label: bi('流动性折价', 'Liquidity haircut'), ok: stress.scenario_policy.requires_liquidity_haircut },
    { label: bi('成本扩大', 'Cost widening'), ok: stress.scenario_policy.requires_cost_widening },
    { label: bi('相关性冲击', 'Correlation shift'), ok: stress.scenario_policy.requires_correlation_shift },
    { label: bi('结果不可变', 'Immutable results'), ok: !stress.scenario_policy.mutable_results },
    { label: bi('禁止实盘授权', 'No live authorization'), ok: !stress.scenario_policy.authorizes_live_trading },
  ];

  const scenarioCols: Column<ScenarioStressCase>[] = [
    { key: 'scenario', header: bi('情景', 'Scenario'), render: s => (
      <div className="min-w-0">
        <div className="truncate font-medium text-ink">{s.name_zh} <span className="text-muted">/ {s.name}</span></div>
        <div className="mono text-2xs text-faint">{s.scenario_id} · D{num(s.shock_window.start_day)}–D{num(s.shock_window.end_day)}</div>
      </div>
    ) },
    { key: 'severity', header: bi('严重度', 'Severity'), render: s => <Chip label={s.severity} tone={severityTone(s.severity)} /> },
    { key: 'decision', header: bi('决策', 'Decision'), render: s => <StatusChip value={s.decision} /> },
    { key: 'path', header: bi('压力路径', 'Stress Path'), width: '108px', render: s => (
      <Sparkline data={s.curve} tone={statusTone(s.decision)} width={104} height={26} />
    ) },
    { key: 'sret', header: bi('压力收益', 'Stress Ret'), align: 'right', render: s => (
      <span className={s.metrics.stressed_return >= 0 ? 'text-pos' : 'text-neg'}>{pct(s.metrics.stressed_return)}</span>
    ) },
    { key: 'loss', header: bi('损失差额', 'Loss Δ'), align: 'right', render: s => <span className="text-warn">{pct(s.metrics.loss_delta)}</span> },
    { key: 'sharpe', header: bi('压力夏普', 'Stress Sharpe'), align: 'right', render: s => (
      <span className={s.metrics.stressed_sharpe >= 0 ? 'text-ink' : 'text-neg'}>{signed(s.metrics.stressed_sharpe)}</span>
    ) },
    { key: 'dd', header: bi('压力回撤', 'Stress DD'), align: 'right', render: s => <span className="text-neg">{pct(s.metrics.stressed_max_drawdown)}</span> },
    { key: 'liq', header: bi('流动性使用', 'Liq Used'), align: 'right', width: '120px', render: s => (
      <div className="flex items-center justify-end gap-2">
        <ProgressBar value={s.metrics.liquidity_buffer_used} tone={s.metrics.liquidity_buffer_used > 0.3 ? 'warn' : 'info'} />
        <span className="tnum w-10 text-right text-muted">{pct(s.metrics.liquidity_buffer_used, 0)}</span>
      </div>
    ) },
    { key: 'cap', header: bi('折后容量', 'Capacity'), align: 'right', mono: true, render: s => money(s.metrics.capacity_after_haircut) },
    { key: 'drivers', header: bi('主因', 'Top Driver'), render: s => {
      const top = [...s.drivers].sort((a, b) => b.impact - a.impact)[0];
      return top ? <span className="text-2xs text-muted">{top.driver} <span className="tnum text-faint">{pct(top.impact)}</span></span> : <span className="text-faint">—</span>;
    } },
  ];

  const passRate = stress.summary.scenarios > 0 ? stress.summary.passes / stress.summary.scenarios : 0;

  return (
    <PageShell>
      <PageHeader title="Scenario Stress Library" titleZh="场景压力库"
        subtitle={bi('确定性多情景重放:流动性折价 · 成本扩大 · 相关性冲击下的只读压力评估', 'Deterministic multi-scenario replay — read-only stress under liquidity haircut, cost widening and correlation shift')}
        actions={
          <label className="flex items-center gap-2 text-2xs text-muted">
            <span className="uppercase tracking-wide text-faint">Alpha</span>
            <select
              className="rounded-md border border-line bg-inset px-2 py-1 text-xs text-ink"
              value={selectedAlpha}
              onChange={event => setSelectedAlpha(event.target.value)}
            >
              {alphaOptions.map(option => <option key={option} value={option}>{option}</option>)}
            </select>
            <Chip label={stress.stress_mode} tone={statusTone(stress.stress_mode)} />
          </label>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={stress.source} error={apiError} />

      <KpiStrip cols={8}>
        <StatTile label={bi('情景数', 'Scenarios')} value={num(stress.summary.scenarios)} accent />
        <StatTile label={bi('通过', 'Pass')} value={num(stress.summary.passes)} tone="pos" sub={pct(passRate, 0)} />
        <StatTile label={bi('观察', 'Watch')} value={num(stress.summary.watches)} tone="warn" />
        <StatTile label={bi('失败', 'Fail')} value={num(stress.summary.fails)} tone="neg" />
        <StatTile label={bi('最差损失', 'Worst Loss')} value={pct(stress.summary.worst_loss_delta)} tone="neg" sub={stress.summary.worst_scenario_id} />
        <StatTile label={bi('平均回撤', 'Avg DD')} value={pct(stress.summary.average_stressed_drawdown)} tone="warn" />
        <StatTile label={bi('平均流动性使用', 'Avg Liq Used')} value={pct(stress.summary.average_liquidity_buffer_used)} tone="info" />
        <StatTile label={bi('基线夏普', 'Base Sharpe')} value={fixed(stress.baseline.sharpe)} sub={bi(`收益 ${pct(stress.baseline.total_return)}`, `ret ${pct(stress.baseline.total_return)}`)} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2"
          eyebrow={bi('最差情景', 'Worst Scenario')}
          title={worst ? <>{worst.name_zh} <span className="text-muted">/ {worst.name}</span></> : bi('无情景', 'No scenario')}
          tone={worst ? statusTone(worst.decision) : undefined}
          right={worst && <><Chip label={worst.severity} tone={severityTone(worst.severity)} /><StatusChip value={worst.decision} /></>}>
          {worst ? (
            <>
              <AreaChart data={worst.curve} height={208} tone={statusTone(worst.decision)} yFormat={v => money(v)} />
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatTile label={bi('损失差额', 'Loss Delta')} value={pct(worst.metrics.loss_delta)} tone="neg" />
                <StatTile label={bi('压力回撤', 'Stress DD')} value={pct(worst.metrics.stressed_max_drawdown)} tone="warn" />
                <StatTile label={bi('压力夏普', 'Stress Sharpe')} value={signed(worst.metrics.stressed_sharpe)} tone={deltaTone(worst.metrics.stressed_sharpe)} />
                <StatTile label={bi('折后容量', 'Capacity')} value={money(worst.metrics.capacity_after_haircut)} />
              </div>
              <div className="mt-3">
                <KeyValue cols={4} items={[
                  { k: bi('收益乘子', 'Return ×'), v: fixed(worst.shocks.return_multiplier) },
                  { k: bi('成本位移', 'Cost shift'), v: pct(worst.shocks.daily_cost_shift, 3) },
                  { k: bi('流动性折价', 'Liq haircut'), v: pct(worst.shocks.liquidity_haircut) },
                  { k: bi('相关性冲击', 'Corr shift'), v: pct(worst.shocks.correlation_shift) },
                ]} />
              </div>
            </>
          ) : null}
        </Panel>

        <Panel eyebrow={bi('压力策略', 'Stress Policy')} title={bi('门控不变量', 'Gate Invariants')}>
          <div className="flex flex-col gap-2.5">
            {policyRows.map((p, i) => (
              <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset/40 px-3 py-2">
                <span className="text-xs text-muted">{p.label}</span>
                <GateDot ok={p.ok} label={p.ok ? bi('满足', 'OK') : bi('未满足', 'FAIL')} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel eyebrow={bi('暴露归因', 'Exposure Attribution')} title={bi('暴露桶估算盈亏', 'Exposure Bucket Estimated PnL')}>
          <DivergingBars items={exposureItems} valueFormat={v => pct(v, 2)} />
        </Panel>
        <Panel eyebrow={bi('风险因子', 'Risk Factors')} title={bi('风险因子贡献', 'Risk Factor Contributions')}>
          <DivergingBars items={contribItems} valueFormat={v => pct(v, 2)} />
        </Panel>
      </div>

      <Panel eyebrow={bi('情景矩阵', 'Scenario Matrix')} title={bi('压力情景全表', 'All Stress Scenarios')}
        right={<Chip label={bi(`${stress.scenarios.length} 个情景`, `${stress.scenarios.length} scenarios`)} tone="info" />}>
        <DataTable columns={scenarioCols} rows={stress.scenarios} getKey={s => s.scenario_id} />
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('研究边界', 'Research Boundary')} title={bi('假设与限制', 'Assumptions & Limits')}>
          <div className="flex flex-col gap-2">
            {stress.assumptions.map(item => (
              <div key={item} className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-xs text-muted">{item}</div>
            ))}
          </div>
        </Panel>
        <Panel eyebrow={bi('可复现性', 'Reproducibility')} title={bi('运行溯源', 'Run Provenance')}>
          <KeyValue cols={1} items={[
            { k: bi('引擎', 'Engine'), v: stress.run.engine },
            { k: bi('执行模式', 'Execution mode'), v: stress.run.execution_mode },
            { k: bi('情景集', 'Scenario set'), v: stress.run.scenario_set_id },
            { k: bi('数据快照', 'Data snapshot'), v: stress.run.data_snapshot_id },
            { k: 'config_hash', v: shortHash(stress.run.config_hash, 16) },
            { k: 'reproducibility_hash', v: shortHash(stress.run.reproducibility_hash, 16) },
            { k: bi('选定 Alpha', 'Selected alpha'), v: stress.alpha.candidate_id },
            { k: bi('生命周期', 'Lifecycle'), v: <StatusChip value={stress.alpha.lifecycle_state} /> },
          ]} />
        </Panel>
      </div>
    </PageShell>
  );
}
