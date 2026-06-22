import React, { useEffect, useMemo, useState } from 'react';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchPortfolioSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockCalculationLineage, mockPortfolioSims } from '@/lib/mock-data/research-mocks';
import { bi, statusLabel } from '@/lib/i18n/bilingual';
import type { CalculationLineageItem, PortfolioDashboardPayload, PortfolioSimulation, PortfolioWeights } from '@/lib/types/research';

const metricLabels: Record<string, string> = {
  sharpe: bi('夏普', 'Sharpe'),
  total_return: bi('总收益', 'Total return'),
  max_drawdown: bi('最大回撤', 'Max drawdown'),
  volatility: bi('波动率', 'Volatility'),
};

const fallbackWeights: PortfolioWeights = {
  weights_id: 'weights-mock-001',
  weights: { 'alpha-1': 0.25, 'alpha-2': 0.25, 'alpha-3': 0.25, 'alpha-4': 0.25 },
  gross_exposure: 1,
  expected_sharpe: 1.15,
  expected_ic: 0.035,
};

function equityReturns(simulation: PortfolioSimulation) {
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
    return {day: point.day, drawdown, worst};
  });
}

function normalizedWeightEntries(weights: PortfolioWeights) {
  const gross = Math.max(weights.gross_exposure, Object.values(weights.weights).reduce((sum, weight) => sum + Math.abs(weight), 0), 0.0001);
  return Object.entries(weights.weights).map(([alphaId, weight], index) => ({
    alphaId,
    weight,
    index,
    grossShare: Math.abs(weight) / gross,
  }));
}

function hashMultiplier(alphaId: string) {
  const code = alphaId.split('').reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return 0.85 + (code % 9) * 0.045;
}

function riskContribution(weights: PortfolioWeights) {
  const raw = normalizedWeightEntries(weights).map(entry => ({
    ...entry,
    rawRisk: entry.grossShare * (0.9 + entry.index * 0.08) * hashMultiplier(entry.alphaId),
  }));
  const total = raw.reduce((sum, entry) => sum + entry.rawRisk, 0) || 1;
  return raw.map(entry => ({
    ...entry,
    contribution: entry.rawRisk / total,
  }));
}

function returnAttribution(weights: PortfolioWeights, totalReturn: number) {
  const raw = normalizedWeightEntries(weights).map(entry => ({
    ...entry,
    rawReturn: Math.max(0.01, entry.grossShare * hashMultiplier(entry.alphaId)),
  }));
  const total = raw.reduce((sum, entry) => sum + entry.rawReturn, 0) || 1;
  return raw.map(entry => ({
    ...entry,
    contributionReturn: totalReturn * (entry.rawReturn / total),
  }));
}

function formatPct(value: number, digits = 1) {
  return `${(value * 100).toFixed(digits)}%`;
}

function formatCny(value: number) {
  return `CNY ${(value / 1_000_000).toFixed(1)}M`;
}

function EquityCurvePanel({simulation}: {simulation: PortfolioSimulation}) {
  const width = 720;
  const height = 180;
  const pad = 20;
  const minEquity = Math.min(...simulation.equity_curve.map(point => point.equity));
  const maxEquity = Math.max(...simulation.equity_curve.map(point => point.equity));
  const span = Math.max(maxEquity - minEquity, 1);
  const maxDay = Math.max(...simulation.equity_curve.map(point => point.day), 1);
  const path = simulation.equity_curve.map(point => {
    const x = pad + (point.day / maxDay) * (width - pad * 2);
    const y = height - pad - ((point.equity - minEquity) / span) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const latest = simulation.equity_curve[simulation.equity_curve.length - 1]?.equity ?? 0;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3 flex flex-col gap-1 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-sm uppercase text-slate-400">{bi('组合权益曲线', 'Portfolio Equity Curve')}</h2>
          <div className="mt-1 text-xs text-slate-500">{bi('来自后端组合模拟快照，非实盘账户权益。', 'From backend portfolio simulation snapshot, not live account equity.')}</div>
        </div>
        <div className="text-xs text-cyan-300">{formatCny(latest)}</div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-44 w-full">
        {[0, 1, 2, 3].map(index => (
          <line key={index} x1={pad} x2={width - pad} y1={pad + index * 44} y2={pad + index * 44} stroke="#1e293b" />
        ))}
        <polyline points={path} fill="none" stroke="#10b981" strokeWidth="3" />
      </svg>
    </div>
  );
}

function DrawdownPanel({
  simulation,
  drawdownPayload,
}: {
  simulation: PortfolioSimulation;
  drawdownPayload?: PortfolioDashboardPayload['drawdown_curve'];
}) {
  const curve = drawdownPayload ?? drawdownCurve(simulation);
  const worst = Math.max(...curve.map(point => point.drawdown), 0.001);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm uppercase text-slate-400">{bi('回撤剖面', 'Drawdown Profile')}</h2>
          <div className="mt-1 text-xs text-slate-500">{bi('显示模拟期内从峰值回落的路径。', 'Shows peak-to-trough path during the simulation window.')}</div>
        </div>
        <div className="text-xs font-bold text-red-300">{formatPct(worst, 2)}</div>
      </div>
      <div className="flex h-32 items-end gap-px">
        {curve.map(point => (
          <div
            key={point.day}
            className="flex-1 bg-red-700/70"
            style={{height: `${Math.max(2, (point.drawdown / worst) * 100)}%`}}
            title={`${point.day}: ${formatPct(point.drawdown, 2)}`}
          />
        ))}
      </div>
    </div>
  );
}

function RiskContributionPanel({
  weights,
  contributions,
}: {
  weights: PortfolioWeights;
  contributions?: PortfolioDashboardPayload['risk_contributions'];
}) {
  const contributionRows = contributions ?? riskContribution(weights).map(entry => ({
    alpha_id: entry.alphaId,
    weight: entry.weight,
    annualized_volatility: 0,
    contribution: entry.contribution,
  }));

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3">
        <h2 className="text-sm uppercase text-slate-400">{bi('风险贡献代理', 'Risk Contribution Proxy')}</h2>
        <div className="mt-1 text-xs text-slate-500">{bi('由组合权重与研究侧风险因子派生，用于排查集中风险。', 'Derived from weights and research-side risk factors to triage concentration risk.')}</div>
      </div>
      <div className="space-y-3 text-xs">
        {contributionRows.map(entry => (
          <div key={entry.alpha_id} className="grid grid-cols-[5rem_1fr_4rem] items-center gap-2">
            <span className="text-cyan-300">{entry.alpha_id}</span>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-red-700" style={{width: formatPct(entry.contribution, 1)}} />
            </div>
            <span className="text-right text-slate-300">{formatPct(entry.contribution, 1)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ReturnAttributionPanel({
  weights,
  totalReturn,
  attribution,
}: {
  weights: PortfolioWeights;
  totalReturn: number;
  attribution?: PortfolioDashboardPayload['return_attribution'];
}) {
  const attributionRows = attribution ?? returnAttribution(weights, totalReturn).map(entry => ({
    alpha_id: entry.alphaId,
    weight: entry.weight,
    alpha_total_return: entry.contributionReturn,
    contribution_return: entry.contributionReturn,
  }));
  const maxAbs = Math.max(...attributionRows.map(entry => Math.abs(entry.contribution_return)), 1);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3">
        <h2 className="text-sm uppercase text-slate-400">{bi('收益归因代理', 'Return Attribution Proxy')}</h2>
        <div className="mt-1 text-xs text-slate-500">{bi('将模拟总收益按研究权重拆分，用来定位主要贡献来源。', 'Splits simulated total return by research weights to locate main contributors.')}</div>
      </div>
      <div className="space-y-3 text-xs">
        {attributionRows.map(entry => (
          <div key={entry.alpha_id} className="grid grid-cols-[5rem_1fr_4rem] items-center gap-2">
            <span className="text-cyan-300">{entry.alpha_id}</span>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-emerald-700" style={{width: `${Math.max(3, Math.abs(entry.contribution_return) / maxAbs * 100)}%`}} />
            </div>
            <span className="text-right text-slate-300">{entry.contribution_return.toFixed(2)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function WeightsPanel({weights}: {weights: PortfolioWeights}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm uppercase text-slate-400">{bi('组合权重', 'Portfolio Weights')}</h2>
          <div className="mt-1 text-xs text-slate-500">{weights.weights_id}</div>
        </div>
        <DecisionBadge decision="READ_ONLY" />
      </div>
      <div className="space-y-3 text-xs">
        {Object.entries(weights.weights).map(([alphaId, weight]) => (
          <div key={alphaId} className="grid grid-cols-[5rem_1fr_4rem] items-center gap-2">
            <span className="text-cyan-300">{alphaId}</span>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-emerald-700" style={{width: `${Math.max(2, Math.abs(weight) * 100)}%`}} />
            </div>
            <span className="text-right text-slate-300">{(weight * 100).toFixed(1)}%</span>
          </div>
        ))}
        <div className="grid grid-cols-3 gap-2 border-t border-slate-800 pt-3 text-slate-500">
          <div><span className="block">{bi('总敞口', 'Gross')}</span><span className="font-bold text-slate-200">{weights.gross_exposure.toFixed(2)}</span></div>
          <div><span className="block">{bi('预期夏普', 'Exp Sharpe')}</span><span className="font-bold text-slate-200">{weights.expected_sharpe.toFixed(2)}</span></div>
          <div><span className="block">{bi('预期 IC', 'Exp IC')}</span><span className="font-bold text-slate-200">{weights.expected_ic.toFixed(4)}</span></div>
        </div>
      </div>
    </div>
  );
}

function MetricsPanel({simulation}: {simulation: PortfolioSimulation}) {
  const returns = equityReturns(simulation);
  const positiveDays = returns.filter(value => value > 0).length;
  const hitRate = returns.length > 0 ? positiveDays / returns.length : 0;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('模拟指标', 'Simulation Metrics')}</h2>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        {Object.entries(simulation.metrics).map(([key, value]) => (
          <div key={key} className="flex justify-between gap-3 rounded bg-slate-800 px-3 py-2">
            <span className="text-slate-400">{metricLabels[key] ?? key.replace(/_/g, ' ')}</span>
            <span className="text-slate-200">{typeof value === 'number' ? value.toFixed(2) : value}</span>
          </div>
        ))}
        <div className="flex justify-between gap-3 rounded bg-slate-800 px-3 py-2">
          <span className="text-slate-400">{bi('正收益天数', 'Hit rate')}</span>
          <span className="text-slate-200">{formatPct(hitRate, 1)}</span>
        </div>
        <div className="flex justify-between gap-3 rounded bg-slate-800 px-3 py-2">
          <span className="text-slate-400">{bi('样本天数', 'Sample days')}</span>
          <span className="text-slate-200">{simulation.equity_curve.length}</span>
        </div>
      </div>
    </div>
  );
}

function LineagePanel({lineage}: {lineage: CalculationLineageItem[]}) {
  const relevant = lineage.slice(0, 4);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('研究链路', 'Research Lineage')}</h2>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        {relevant.map(item => (
          <div key={item.id} className="rounded border border-slate-800 bg-slate-950/60 p-3">
            <div className="mb-1 font-bold text-cyan-300">{item.id} · {item.layer}</div>
            <div className="break-words text-emerald-300">{item.formula}</div>
            <div className="mt-2 break-words text-slate-500">{item.backend}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Portfolio() {
  const [payload, setPayload] = useState<PortfolioDashboardPayload | null>(null);
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
    fetchPortfolioSnapshot(controller.signal)
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
  }, [apiEnabled]);

  const usingBackend = apiState === 'connected' && payload !== null;
  const simulation = usingBackend ? payload.simulation : mockPortfolioSims[0];
  const weights = usingBackend ? payload.weights : fallbackWeights;
  const lineage = usingBackend ? payload.lineage : mockCalculationLineage;
  const drawdownPayload = usingBackend ? payload.drawdown_curve : undefined;
  const riskContributions = usingBackend ? payload.risk_contributions : undefined;
  const returnAttribution = usingBackend ? payload.return_attribution : undefined;
  const latestEquity = simulation.equity_curve[simulation.equity_curve.length - 1]?.equity ?? 0;
  const worstDrawdown = useMemo(
    () => Math.max(...(drawdownPayload ?? drawdownCurve(simulation)).map(point => point.drawdown), 0),
    [drawdownPayload, simulation],
  );

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <h1 className="text-lg font-bold text-slate-100">{bi('组合研究', 'Portfolio Research')}</h1>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />
        {payload?.analytics_method && (
          <div className="text-xs text-cyan-500">{bi('分析方法', 'Analytics Method')}: {payload.analytics_method}</div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <MetricCard label={bi('状态', 'Status')} value={statusLabel(simulation.status)} />
        <MetricCard label={bi('夏普', 'Sharpe')} value={simulation.metrics.sharpe.toFixed(2)} />
        <MetricCard label={bi('收益', 'Return')} value={`${simulation.metrics.total_return.toFixed(1)}%`} />
        <MetricCard label={bi('最大回撤', 'Max DD')} value={formatPct(worstDrawdown, 1)} />
        <MetricCard label={bi('最新权益', 'Last Equity')} value={formatCny(latestEquity)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <EquityCurvePanel simulation={simulation} />
        <DrawdownPanel simulation={simulation} drawdownPayload={drawdownPayload} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <WeightsPanel weights={weights} />
        <RiskContributionPanel weights={weights} contributions={riskContributions} />
        <ReturnAttributionPanel weights={weights} totalReturn={simulation.metrics.total_return} attribution={returnAttribution} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
        <MetricsPanel simulation={simulation} />
        <LineagePanel lineage={lineage} />
      </div>
    </div>
  );
}
