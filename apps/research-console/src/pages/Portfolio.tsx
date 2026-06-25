import React, { useMemo } from 'react';
import { fetchPortfolioSnapshot } from '@/lib/api/research-console-api';
import { mockCalculationLineage, mockPortfolioSims } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { CalculationLineageItem, PortfolioDashboardPayload, PortfolioSimulation, PortfolioWeights } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, Chip, ProgressBar, KeyValue, type Column,
} from '@/ui/primitives';
import { AreaChart, UnderwaterChart, Donut, DivergingBars } from '@/ui/charts';
import { num, pct, pctPoints, fixed, signed, money, shortHash, statusTone, type Tone } from '@/ui/format';

/* ---------- mock fallback (preserved from the original data wiring) -------- */
const fallbackWeights: PortfolioWeights = {
  weights_id: 'weights-mock-001',
  weights: { 'alpha-1': 0.25, 'alpha-2': 0.25, 'alpha-3': 0.25, 'alpha-4': 0.25 },
  gross_exposure: 1,
  expected_sharpe: 1.15,
  expected_ic: 0.035,
};

const metricLabels: Record<string, string> = {
  sharpe: bi('夏普', 'Sharpe'),
  total_return: bi('总收益', 'Total Return'),
  max_drawdown: bi('最大回撤', 'Max Drawdown'),
  volatility: bi('波动率', 'Volatility'),
  sortino: bi('索提诺', 'Sortino'),
  calmar: bi('卡玛', 'Calmar'),
  turnover: bi('换手率', 'Turnover'),
};

const donutTones: Tone[] = ['accent', 'info', 'warn', 'special', 'pos', 'neg', 'muted'];

/* ---------- derivations (deterministic; no Math.random / Date.now) --------- */
function equityReturns(simulation: PortfolioSimulation): number[] {
  return simulation.equity_curve.slice(1).map((point, index) => {
    const previous = simulation.equity_curve[index].equity;
    return previous > 0 ? (point.equity - previous) / previous : 0;
  });
}

function drawdownCurve(simulation: PortfolioSimulation) {
  let peak = simulation.equity_curve[0]?.equity ?? 0;
  let worst = 0;
  return simulation.equity_curve.map(point => {
    peak = Math.max(peak, point.equity);
    const drawdown = peak > 0 ? (peak - point.equity) / peak : 0;
    worst = Math.max(worst, drawdown);
    return { day: point.day, drawdown, worst };
  });
}

/** Hash-stable factor so the proxy fallbacks stay deterministic (no RNG). */
function hashMultiplier(alphaId: string): number {
  const code = alphaId.split('').reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return 0.85 + (code % 9) * 0.045;
}

function normalizedWeightEntries(weights: PortfolioWeights) {
  const gross = Math.max(
    weights.gross_exposure,
    Object.values(weights.weights).reduce((sum, weight) => sum + Math.abs(weight), 0),
    0.0001,
  );
  return Object.entries(weights.weights).map(([alphaId, weight], index) => ({
    alphaId, weight, index, grossShare: Math.abs(weight) / gross,
  }));
}

function riskContributionProxy(weights: PortfolioWeights) {
  const raw = normalizedWeightEntries(weights).map(entry => ({
    ...entry,
    rawRisk: entry.grossShare * (0.9 + entry.index * 0.08) * hashMultiplier(entry.alphaId),
  }));
  const total = raw.reduce((sum, entry) => sum + entry.rawRisk, 0) || 1;
  return raw.map(entry => ({
    alpha_id: entry.alphaId,
    weight: entry.weight,
    annualized_volatility: 0,
    contribution: entry.rawRisk / total,
  }));
}

function returnAttributionProxy(weights: PortfolioWeights, totalReturn: number) {
  const raw = normalizedWeightEntries(weights).map(entry => ({
    ...entry,
    rawReturn: Math.max(0.01, entry.grossShare * hashMultiplier(entry.alphaId)),
  }));
  const total = raw.reduce((sum, entry) => sum + entry.rawReturn, 0) || 1;
  return raw.map(entry => ({
    alpha_id: entry.alphaId,
    weight: entry.weight,
    alpha_total_return: totalReturn * (entry.rawReturn / total),
    contribution_return: totalReturn * (entry.rawReturn / total),
  }));
}

export default function Portfolio() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<PortfolioDashboardPayload>(
    s => fetchPortfolioSnapshot(s));

  const simulation = usingBackend ? payload!.simulation : mockPortfolioSims[0];
  const weights = usingBackend ? payload!.weights : fallbackWeights;
  const lineage: CalculationLineageItem[] = usingBackend ? payload!.lineage : mockCalculationLineage;
  const analyticsMethod = payload?.analytics_method;

  // Drawdown: prefer backend curve, else derive deterministically.
  const ddRows = useMemo(
    () => payload?.drawdown_curve ?? drawdownCurve(simulation),
    [payload?.drawdown_curve, simulation],
  );
  const worstDrawdown = useMemo(() => Math.max(...ddRows.map(p => p.drawdown), 0), [ddRows]);

  // Risk / return attribution: backend rows or deterministic proxy.
  const riskRows = useMemo(
    () => payload?.risk_contributions ?? riskContributionProxy(weights),
    [payload?.risk_contributions, weights],
  );
  const attribRows = useMemo(
    () => payload?.return_attribution
      ?? returnAttributionProxy(weights, simulation.metrics.total_return ?? 0),
    [payload?.return_attribution, weights, simulation.metrics.total_return],
  );

  // Equity series + KPIs.
  const equitySeries = useMemo(
    () => simulation.equity_curve.map(p => ({ x: p.day, y: p.equity })),
    [simulation],
  );
  const underwaterSeries = useMemo(
    () => ddRows.map(p => ({ x: p.day, y: -p.drawdown })),
    [ddRows],
  );
  const latestEquity = simulation.equity_curve[simulation.equity_curve.length - 1]?.equity ?? 0;
  const firstEquity = simulation.equity_curve[0]?.equity ?? 0;
  const returns = useMemo(() => equityReturns(simulation), [simulation]);
  const hitRate = returns.length > 0 ? returns.filter(v => v > 0).length / returns.length : 0;

  const sharpe = simulation.metrics.sharpe ?? 0;
  const totalReturn = simulation.metrics.total_return ?? 0; // expressed in percent points
  const volatility = simulation.metrics.volatility;

  // Composition donut from weights (absolute gross shares).
  const weightSegments = useMemo(() => {
    const entries = Object.entries(weights.weights);
    const grossSum = entries.reduce((s, [, w]) => s + Math.abs(w), 0) || 1;
    return entries.map(([alphaId, w], i) => ({
      label: alphaId,
      value: Math.abs(w) / grossSum,
      tone: donutTones[i % donutTones.length],
    }));
  }, [weights]);

  // Extra simulation metrics not surfaced as headline KPIs.
  const extraMetrics = Object.entries(simulation.metrics).filter(
    ([k]) => !['sharpe', 'total_return', 'max_drawdown', 'volatility'].includes(k),
  );

  /* ---------- table columns ---------------------------------------------- */
  type RiskRow = typeof riskRows[number];
  const maxRisk = Math.max(...riskRows.map(r => r.contribution), 1e-9);
  const riskCols: Column<RiskRow>[] = [
    { key: 'id', header: bi('因子', 'Alpha'), render: r => <span className="mono text-accent">{r.alpha_id}</span> },
    { key: 'w', header: bi('权重', 'Weight'), align: 'right', mono: true, render: r => pct(r.weight, 1) },
    { key: 'vol', header: bi('年化波动', 'Ann. Vol'), align: 'right', mono: true,
      render: r => (r.annualized_volatility ? pct(r.annualized_volatility, 1) : '—') },
    { key: 'rc', header: bi('风险贡献', 'Risk Contrib.'), width: '200px', render: r => (
      <div className="flex items-center gap-2">
        <ProgressBar value={r.contribution} max={maxRisk} tone="neg" />
        <span className="tnum w-12 text-right text-muted">{pct(r.contribution, 1)}</span>
      </div>
    ) },
  ];

  type AttribRow = typeof attribRows[number];
  const attribCols: Column<AttribRow>[] = [
    { key: 'id', header: bi('因子', 'Alpha'), render: r => <span className="mono text-accent">{r.alpha_id}</span> },
    { key: 'w', header: bi('权重', 'Weight'), align: 'right', mono: true, render: r => pct(r.weight, 1) },
    { key: 'ar', header: bi('因子收益', 'Alpha Return'), align: 'right', mono: true,
      render: r => pctPoints(r.alpha_total_return, 2) },
    { key: 'cr', header: bi('收益贡献', 'Contribution'), align: 'right', mono: true,
      render: r => <span className={r.contribution_return >= 0 ? 'text-pos' : 'text-neg'}>{signed(r.contribution_return, 2)}</span> },
  ];

  const lineageCols: Column<CalculationLineageItem>[] = [
    { key: 'id', header: 'ID', render: it => <span className="mono text-accent">{it.id}</span> },
    { key: 'layer', header: bi('层', 'Layer'), render: it => <Chip label={it.layer} tone="info" /> },
    { key: 'formula', header: bi('公式', 'Formula'), render: it => <span className="mono text-special break-words">{it.formula}</span> },
    { key: 'backend', header: bi('后端实现', 'Backend'), render: it => <span className="mono text-2xs text-muted break-words">{it.backend}</span> },
  ];

  const grossDirectional = Object.values(weights.weights).reduce((s, w) => s + w, 0);

  return (
    <PageShell>
      <PageHeader title="Portfolio Research" titleZh="组合研究"
        subtitle={bi('组合模拟权益、回撤、权重构成与风险/收益归因(只读合成快照)。',
          'Portfolio simulation equity, drawdown, weight composition and risk/return attribution (read-only synthetic snapshot).')}
        actions={analyticsMethod ? <Chip label={analyticsMethod} tone="special" /> : undefined} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={error} />

      <KpiStrip cols={6}>
        <StatTile label={bi('模拟状态', 'Status')} value={<StatusChip value={simulation.status} />} />
        <StatTile label={bi('夏普', 'Sharpe')} value={fixed(sharpe, 2)} tone={statusTone(sharpe >= 1 ? 'OK' : 'WATCH')} accent={sharpe >= 1} />
        <StatTile label={bi('总收益', 'Total Return')} value={pctPoints(totalReturn, 1)} tone={totalReturn >= 0 ? 'pos' : 'neg'} />
        <StatTile label={bi('最大回撤', 'Max DD')} value={pct(worstDrawdown, 1)} tone="neg" />
        <StatTile label={bi('胜率', 'Hit Rate')} value={pct(hitRate, 1)} tone="info" />
        <StatTile label={bi('最新权益', 'Last Equity')} value={money(latestEquity)} sub={bi(`${num(simulation.equity_curve.length)} 个交易日`, `${num(simulation.equity_curve.length)} sessions`)} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('权益', 'Equity')} title={bi('组合权益曲线', 'Portfolio Equity Curve')}
          right={<span className="tnum text-xs text-accent">{money(firstEquity)} → {money(latestEquity)}</span>}>
          <AreaChart data={equitySeries} height={232} tone="accent" yFormat={v => money(v)} xCount={5} />
        </Panel>

        <Panel eyebrow={bi('构成', 'Composition')} title={bi('组合权重', 'Portfolio Weights')}
          right={<Chip label={shortHash(weights.weights_id, 12)} tone="muted" />}>
          <div className="flex flex-col items-center gap-4">
            <Donut segments={weightSegments} size={150} />
            <div className="w-full space-y-2">
              {weightSegments.map(seg => (
                <div key={seg.label} className="flex items-center justify-between gap-2 text-2xs">
                  <span className="flex items-center gap-2 min-w-0">
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: `var(--${seg.tone})` }} />
                    <span className="mono truncate text-ink">{seg.label}</span>
                  </span>
                  <span className="tnum text-muted">{pct(weights.weights[seg.label], 1)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('风险', 'Risk')} title={bi('回撤剖面', 'Drawdown Profile')}
          right={<span className="tnum text-xs text-neg">{bi('最深', 'Trough')} {pct(worstDrawdown, 2)}</span>}>
          <UnderwaterChart data={underwaterSeries} height={150} yFormat={v => pct(v, 1)} />
        </Panel>

        <Panel eyebrow={bi('特征', 'Profile')} title={bi('权重特征', 'Weight Characteristics')}>
          <KeyValue cols={2} items={[
            { k: bi('总敞口', 'Gross Exposure'), v: fixed(weights.gross_exposure, 2) },
            { k: bi('净敞口', 'Net Exposure'), v: signed(grossDirectional, 2) },
            { k: bi('预期夏普', 'Exp. Sharpe'), v: fixed(weights.expected_sharpe, 2) },
            { k: bi('预期 IC', 'Exp. IC'), v: fixed(weights.expected_ic, 4) },
            { k: bi('持仓数', 'Holdings'), v: num(Object.keys(weights.weights).length) },
            { k: bi('波动率', 'Volatility'), v: volatility !== undefined ? pctPoints(volatility, 1) : '—' },
          ]} />
          {extraMetrics.length > 0 && (
            <div className="mt-4 space-y-2 border-t border-line pt-3">
              {extraMetrics.map(([k, v]) => (
                <div key={k} className="flex items-center justify-between text-2xs">
                  <span className="text-faint uppercase tracking-wide">{metricLabels[k] ?? k.replace(/_/g, ' ')}</span>
                  <span className="tnum text-ink">{fixed(v, 3)}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel eyebrow={bi('集中度', 'Concentration')} title={bi('风险贡献代理', 'Risk Contribution Proxy')}
          right={<Chip label={payload?.risk_contributions ? bi('后端', 'BACKEND') : bi('派生', 'DERIVED')} tone={payload?.risk_contributions ? 'pos' : 'warn'} />}>
          <DataTable columns={riskCols} rows={riskRows} dense getKey={r => r.alpha_id} />
        </Panel>

        <Panel eyebrow={bi('归因', 'Attribution')} title={bi('收益归因代理', 'Return Attribution Proxy')}
          right={<Chip label={payload?.return_attribution ? bi('后端', 'BACKEND') : bi('派生', 'DERIVED')} tone={payload?.return_attribution ? 'pos' : 'warn'} />}>
          <DivergingBars
            items={attribRows.map(r => ({ label: r.alpha_id, value: r.contribution_return }))}
            valueFormat={v => signed(v, 2)} />
          <div className="mt-3 border-t border-line pt-3">
            <DataTable columns={attribCols} rows={attribRows} dense getKey={r => r.alpha_id} />
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('可复现性', 'Reproducibility')} title={bi('研究链路', 'Research Lineage')}
        right={<Chip label={bi(`${lineage.length} 条`, `${lineage.length} steps`)} tone="info" />}>
        <DataTable columns={lineageCols} rows={lineage} dense getKey={it => it.id} />
      </Panel>
    </PageShell>
  );
}
