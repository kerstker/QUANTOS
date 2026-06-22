import React, { useEffect, useMemo, useState } from 'react';
import { DecisionBadge, MetricCard, RiskBadge } from '@/components/status-badges';
import {
  RESEARCH_CONSOLE_API_BASE,
  fetchComputationLabSnapshot,
  isResearchConsoleApiEnabled,
} from '@/lib/api/research-console-api';
import {
  mockAlphaCandidates,
  mockCalculationLineage,
  mockRiskReports,
  mockValidationReports,
} from '@/lib/mock-data/research-mocks';
import { bi, statusLabel } from '@/lib/i18n/bilingual';
import type {
  CalculationLineageItem,
  ChartPoint,
  ComputationAlphaSummary,
  ComputationLabPayload,
  ComputationRiskReport,
  ComputationValidation,
  DrawdownPoint,
  RiskReport,
} from '@/lib/types/research';

function dailyReturns(points: ChartPoint[]) {
  return points.slice(1).map((p, i) => (p.y - points[i].y) / points[i].y);
}

function sharpe(points: ChartPoint[]) {
  const returns = dailyReturns(points);
  const mean = returns.reduce((s, r) => s + r, 0) / Math.max(returns.length, 1);
  const variance = returns.reduce((s, r) => s + Math.pow(r - mean, 2), 0) / Math.max(returns.length - 1, 1);
  const std = Math.sqrt(variance);
  return std > 0 ? (mean / std) * Math.sqrt(252) : 0;
}

function maxDrawdown(points: ChartPoint[]): DrawdownPoint[] {
  let peak = points[0]?.y ?? 0;
  let worst = 0;
  return points.map(p => {
    peak = Math.max(peak, p.y);
    const dd = peak > 0 ? (peak - p.y) / peak : 0;
    worst = Math.max(worst, dd);
    return { x: p.x, y: dd, worst };
  });
}

function scenarioEquity(index: number, sharpeSeed: number, turnover: number): ChartPoint[] {
  let equity = 10_000_000;
  const points: ChartPoint[] = [];
  const drift = 0.00085 + Math.min(sharpeSeed, 2) * 0.00016;
  const vol = 0.0015 + turnover * 0.0012;
  for (let day = 0; day < 120; day += 1) {
    const cycle = Math.sin(day / 4.8 + index) * vol;
    const regime = Math.cos(day / 17 + index * 0.7) * vol * 0.45;
    const liquidityShock = day > 34 + index * 3 && day < 48 + index * 3 ? -0.0025 - index * 0.0008 : 0;
    const stressShock = day > 86 && day < 94 ? -0.0018 * (1 + turnover) : 0;
    const rebound = day > 52 && day < 70 ? 0.0014 + index * 0.0002 : 0;
    equity *= 1 + drift + cycle + regime + liquidityShock + stressShock + rebound;
    points.push({ x: day, y: equity });
  }
  return points;
}

function formatNumber(value: number | undefined, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : 'n/a';
}

function formatCapacity(value: number | undefined) {
  return typeof value === 'number' ? `CNY ${(value / 1_000_000).toFixed(1)}M` : 'n/a';
}

function gateLabel(gate: string) {
  const labels: Record<string, string> = {
    market: bi('市场', 'Market'),
    liquidity: bi('流动性', 'Liquidity'),
    concentration: bi('集中度', 'Concentration'),
    correlation: bi('相关性', 'Correlation'),
    drawdown: bi('回撤', 'Drawdown'),
    regime: bi('市场状态', 'Regime'),
    stress: bi('压力测试', 'Stress'),
    capacity: bi('容量', 'Capacity'),
    model_alpha: bi('模型Alpha', 'Model Alpha'),
  };
  return labels[gate] ?? gate.replace(/_/g, ' ');
}

function compactModule(path: string) {
  const parts = path.split('/');
  return parts.length > 2 ? parts.slice(-3).join('/') : path;
}

function formulaFrom(lineage: CalculationLineageItem[], id: string, fallback: string) {
  return lineage.find(item => item.id === id)?.formula ?? fallback;
}

function moduleFrom(lineage: CalculationLineageItem[], id: string, fallback: string) {
  return compactModule(lineage.find(item => item.id === id)?.backend ?? fallback);
}

function LineChart({points, color = '#0891b2'}:{points: ChartPoint[]; color?: string}) {
  const width = 720;
  const height = 180;
  const pad = 18;
  if (points.length === 0) {
    return <div className="flex h-44 items-center justify-center text-xs text-slate-500">{bi('暂无曲线数据', 'No curve data')}</div>;
  }
  const minY = Math.min(...points.map(p => p.y));
  const maxY = Math.max(...points.map(p => p.y));
  const spanY = Math.max(maxY - minY, 1);
  const maxX = Math.max(...points.map(p => p.x), 1);
  const path = points.map(p => {
    const x = pad + (p.x / maxX) * (width - pad * 2);
    const y = height - pad - ((p.y - minY) / spanY) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const lastPoint = points[points.length - 1];
  const lastX = pad + (lastPoint.x / maxX) * (width - pad * 2);
  const lastY = height - pad - ((lastPoint.y - minY) / spanY) * (height - pad * 2);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-44 w-full">
      {[0, 1, 2, 3].map(i => <line key={i} x1={pad} x2={width-pad} y1={pad+i*44} y2={pad+i*44} stroke="#1e293b" />)}
      <polyline points={path} fill="none" stroke={color} strokeWidth="3" />
      <circle cx={lastX} cy={lastY} r="4" fill={color} />
    </svg>
  );
}

function MetricDecomposition({
  grossSharpe,
  netSharpe,
  costBps,
  stress,
}: {
  grossSharpe: number;
  netSharpe: number;
  costBps: number;
  stress: number;
}) {
  const costDrag = costBps / 100;
  const stressDrag = Math.max(0, stress - 1) * 0.18;
  const explainedNet = grossSharpe - costDrag - stressDrag;
  const denominator = Math.max(Math.abs(grossSharpe), costDrag + stressDrag + Math.abs(netSharpe), 1);
  const rows = [
    {
      id: 'gross',
      label: bi('原始夏普', 'Gross Sharpe'),
      value: grossSharpe,
      color: 'bg-emerald-500',
      width: Math.min(100, Math.abs(grossSharpe) / denominator * 100),
    },
    {
      id: 'cost',
      label: bi('交易成本扣减', 'Cost Drag'),
      value: -costDrag,
      color: 'bg-amber-500',
      width: Math.min(100, costDrag / denominator * 100),
    },
    {
      id: 'stress',
      label: bi('压力情景扣减', 'Stress Drag'),
      value: -stressDrag,
      color: 'bg-red-500',
      width: Math.min(100, stressDrag / denominator * 100),
    },
    {
      id: 'net',
      label: bi('净夏普', 'Net Sharpe'),
      value: netSharpe,
      color: netSharpe >= 0 ? 'bg-cyan-500' : 'bg-red-500',
      width: Math.min(100, Math.abs(netSharpe) / denominator * 100),
    },
  ];

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3 flex flex-col gap-1 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-sm uppercase text-slate-400">{bi('指标分解', 'Metric Decomposition')}</h2>
          <div className="mt-1 text-xs text-slate-500">
            {bi('净夏普 = 原始夏普 - 成本扣减 - 压力扣减', 'Net Sharpe = Gross Sharpe - Cost Drag - Stress Drag')}
          </div>
        </div>
        <div className="text-xs text-slate-500 md:text-right">
          {bi('复算值', 'Recomputed')}: {explainedNet.toFixed(3)}
        </div>
      </div>
      <div className="space-y-3">
        {rows.map(row => (
          <div key={row.id} className="grid grid-cols-[8rem_1fr_4rem] items-center gap-3 text-xs">
            <div className="text-slate-400">{row.label}</div>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className={`h-full ${row.color}`} style={{width: `${Math.max(3, row.width)}%`}} />
            </div>
            <div className="text-right font-bold text-slate-200">{row.value.toFixed(3)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ComputationFlow({
  lineage,
  validation,
  riskReport,
}: {
  lineage: CalculationLineageItem[];
  validation: ComputationValidation | { decision: string };
  riskReport: ComputationRiskReport | RiskReport;
}) {
  const gateEntries = Object.entries(riskReport.gate_results);
  const passedGates = gateEntries.filter(([, passed]) => passed).length;
  const nodes = [
    {
      id: 'DATA',
      title: bi('研究输入', 'Research Inputs'),
      formula: 'factor_values + forward_returns',
      module: 'console_api/main.py',
      decision: 'READ_ONLY',
    },
    {
      id: 'IC',
      title: bi('Alpha 相关性', 'Alpha Correlation'),
      formula: formulaFrom(lineage, 'IC', 'corr(factor_values, forward_returns)'),
      module: moduleFrom(lineage, 'IC', 'factor_engine/domain/model.py'),
      decision: 'BACKEND_COMPUTED',
    },
    {
      id: 'SHARPE',
      title: bi('纸面收益', 'Paper PnL'),
      formula: formulaFrom(lineage, 'SHARPE', 'mean(returns) / std(returns) * sqrt(252)'),
      module: moduleFrom(lineage, 'SHARPE', 'paper_trading/domain/pnl.py'),
      decision: validation.decision,
    },
    {
      id: 'RISK',
      title: bi('风险门禁', 'Risk Gate'),
      formula: formulaFrom(lineage, 'RISK_GATE', 'all(required_gates.pass) && fail_closed_on_unknown'),
      module: moduleFrom(lineage, 'RISK_GATE', 'research_risk/domain/gate.py'),
      decision: riskReport.decision,
    },
    {
      id: 'AUDIT',
      title: bi('审计输出', 'Audit Output'),
      formula: `${passedGates}/${gateEntries.length} ${bi('门禁通过', 'gates passed')}`,
      module: 'read-only snapshot',
      decision: 'READ_ONLY',
    },
  ];

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-4 flex flex-col gap-1 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-sm uppercase text-slate-400">{bi('后端计算链路', 'Backend Computation Flow')}</h2>
          <div className="mt-1 text-xs text-slate-500">
            {bi('从因子输入到审计输出，每个节点绑定后端公式或模块。', 'Each node maps inputs to backend formulas, gates, and audit output.')}
          </div>
        </div>
        <DecisionBadge decision="BACKEND_COMPUTED" />
      </div>
      <div className="grid gap-3 lg:grid-cols-5">
        {nodes.map((node, index) => (
          <div key={node.id} className="relative min-w-0 rounded border border-slate-800 bg-slate-950/60 p-3">
            {index < nodes.length - 1 && <div className="pointer-events-none absolute right-[-0.7rem] top-1/2 hidden h-px w-3 bg-cyan-800 lg:block" />}
            <div className="mb-2 flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-[10px] font-bold text-cyan-400">{node.id}</div>
                <div className="mt-1 text-xs font-bold text-slate-100">{node.title}</div>
              </div>
              <DecisionBadge decision={node.decision} />
            </div>
            <div className="break-words text-xs text-emerald-300">{node.formula}</div>
            <div className="mt-2 break-words text-[10px] text-slate-500">{node.module}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function DrawdownChart({points, drawdowns}:{points: ChartPoint[]; drawdowns?: DrawdownPoint[]}) {
  const curve = drawdowns && drawdowns.length > 0 ? drawdowns : maxDrawdown(points);
  const maxY = Math.max(...curve.map(p => p.y), 0.001);
  return (
    <div className="flex h-32 items-end gap-px">
      {curve.map(p => (
        <div
          key={p.x}
          className="flex-1 bg-red-700/70"
          style={{height:`${Math.max(2, (p.y / maxY) * 100)}%`}}
          title={`${(p.y*100).toFixed(2)}%`}
        />
      ))}
    </div>
  );
}

export default function ComputationLab() {
  const [alphaIndex, setAlphaIndex] = useState(0);
  const [costBps, setCostBps] = useState(18);
  const [stress, setStress] = useState(1.2);
  const [snapshot, setSnapshot] = useState<ComputationLabPayload | null>(null);
  const [apiState, setApiState] = useState<'disabled' | 'loading' | 'connected' | 'error'>('disabled');
  const [apiError, setApiError] = useState<string | null>(null);

  const mockCandidateList = useMemo<ComputationAlphaSummary[]>(
    () => mockAlphaCandidates.slice(0, 4).map(candidate => ({
      candidate_id: candidate.candidate_id,
      expression: candidate.expression,
      turnover: candidate.turnover,
      capacity_estimate: candidate.capacity_estimate,
      lifecycle_state: candidate.lifecycle_state,
      ic: candidate.ic,
      rank_ic: candidate.rank_ic,
      icir: candidate.icir,
      sharpe: candidate.sharpe,
      composite_score: candidate.composite_score,
    })),
    [],
  );
  const fallbackAlpha = mockCandidateList[alphaIndex] ?? mockCandidateList[0];
  const selectedAlphaId = fallbackAlpha.candidate_id;
  const apiEnabled = isResearchConsoleApiEnabled();

  useEffect(() => {
    if (!apiEnabled) {
      setSnapshot(null);
      setApiState('disabled');
      setApiError(null);
      return;
    }

    const controller = new AbortController();
    setApiState('loading');
    setApiError(null);

    fetchComputationLabSnapshot({
      alphaId: selectedAlphaId,
      costBps,
      stress,
      signal: controller.signal,
    })
      .then(payload => {
        setSnapshot(payload);
        setApiState('connected');
      })
      .catch(error => {
        if (controller.signal.aborted) {
          return;
        }
        setSnapshot(null);
        setApiState('error');
        setApiError(error instanceof Error ? error.message : String(error));
      });

    return () => controller.abort();
  }, [apiEnabled, selectedAlphaId, costBps, stress]);

  const fallbackValidation = mockValidationReports.find(r => r.alpha_id === selectedAlphaId) ?? mockValidationReports[0];
  const fallbackPoints = useMemo(
    () => scenarioEquity(alphaIndex, fallbackAlpha.sharpe ?? 1, fallbackAlpha.turnover),
    [alphaIndex, fallbackAlpha.sharpe, fallbackAlpha.turnover],
  );
  const fallbackDrawdowns = useMemo(() => maxDrawdown(fallbackPoints), [fallbackPoints]);
  const fallbackMetrics = useMemo(() => {
    const grossSharpe = sharpe(fallbackPoints);
    const costDrag = costBps / 100;
    const stressPenalty = Math.max(0, stress - 1) * 0.18;
    const netSharpe = grossSharpe - costDrag - stressPenalty;
    const worstDrawdown = Math.max(...fallbackDrawdowns.map(p => p.y)) * stress;
    const totalReturn = ((fallbackPoints[fallbackPoints.length - 1].y / fallbackPoints[0].y) - 1) * 100;
    return { grossSharpe, netSharpe, worstDrawdown, totalReturn };
  }, [costBps, fallbackDrawdowns, fallbackPoints, stress]);

  const usingBackend = apiState === 'connected' && snapshot !== null;
  const alphaCandidates = usingBackend ? snapshot.alpha_candidates : mockCandidateList;
  const alpha = usingBackend ? snapshot.alpha : fallbackAlpha;
  const validation: ComputationValidation | typeof fallbackValidation = usingBackend ? snapshot.validation : fallbackValidation;
  const activePoints = usingBackend ? snapshot.equity_curve : fallbackPoints;
  const drawdownPoints = usingBackend ? snapshot.drawdown_curve : fallbackDrawdowns;
  const riskReport: ComputationRiskReport | RiskReport = usingBackend
    ? snapshot.risk_report
    : mockRiskReports[alphaIndex % mockRiskReports.length];
  const lineage: CalculationLineageItem[] = usingBackend ? snapshot.lineage : mockCalculationLineage;
  const metricValues = usingBackend
    ? {
      grossSharpe: snapshot.metrics.gross_sharpe,
      netSharpe: snapshot.metrics.net_sharpe,
      worstDrawdown: snapshot.metrics.max_drawdown,
      totalReturn: snapshot.metrics.total_return,
      costBps: snapshot.metrics.cost_bps,
      stress: snapshot.metrics.stress_multiplier,
    }
    : {
      grossSharpe: fallbackMetrics.grossSharpe,
      netSharpe: fallbackMetrics.netSharpe,
      worstDrawdown: fallbackMetrics.worstDrawdown,
      totalReturn: fallbackMetrics.totalReturn,
      costBps,
      stress,
    };
  const gateEntries = Object.entries(riskReport.gate_results);
  const connectionDecision = apiState === 'connected'
    ? 'API_CONNECTED'
    : apiState === 'loading'
      ? 'API_LOADING'
      : apiState === 'error'
        ? 'API_ERROR'
        : 'NOT_CONNECTED';
  const sourceLabel = usingBackend ? snapshot.source : 'FRONTEND_MOCK_FALLBACK';
  const lastEquity = activePoints[activePoints.length - 1]?.y ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <h1 className="text-lg font-bold text-slate-100">{bi('计算实验室', 'Computation Lab')}</h1>
          <div className="mt-2 flex flex-wrap gap-2">
            <DecisionBadge decision={usingBackend ? 'BACKEND_COMPUTED' : 'MOCK_MODE'} />
            <DecisionBadge decision={connectionDecision} />
            <DecisionBadge decision="READ_ONLY" />
          </div>
        </div>
        <div className="grid grid-cols-1 gap-2 text-xs text-slate-500 md:grid-cols-3 xl:text-right">
          <div>{bi('数据源', 'Source')}: {sourceLabel}</div>
          <div>API: {apiEnabled ? RESEARCH_CONSOLE_API_BASE : bi('未启用', 'disabled')}</div>
          <div>{apiError ? apiError : `${bi('已选', 'Selected')}: ${selectedAlphaId}`}</div>
        </div>
      </div>

      <div className="grid gap-2 md:grid-cols-4">
        <MetricCard label={bi('总夏普', 'Gross Sharpe')} value={metricValues.grossSharpe.toFixed(2)} />
        <MetricCard label={bi('净夏普', 'Net Sharpe')} value={metricValues.netSharpe.toFixed(2)} />
        <MetricCard label={bi('最大回撤', 'Max Drawdown')} value={(metricValues.worstDrawdown*100).toFixed(1)} unit="%" />
        <MetricCard label={bi('总收益', 'Total Return')} value={metricValues.totalReturn.toFixed(1)} unit="%" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.75fr_1.25fr]">
        <MetricDecomposition
          grossSharpe={metricValues.grossSharpe}
          netSharpe={metricValues.netSharpe}
          costBps={metricValues.costBps}
          stress={metricValues.stress}
        />
        <ComputationFlow lineage={lineage} validation={validation} riskReport={riskReport} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.75fr_1.25fr]">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('场景控制', 'Scenario Controls')}</h2>
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {alphaCandidates.slice(0, 4).map((candidate, idx) => (
                <button
                  key={candidate.candidate_id}
                  onClick={() => setAlphaIndex(idx)}
                  className={`rounded px-3 py-2 text-xs ${idx === alphaIndex ? 'bg-cyan-800 text-cyan-100' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}
                >
                  {candidate.candidate_id}
                </button>
              ))}
            </div>
            <label className="block text-xs text-slate-400">
              {bi('成本拖累', 'Cost Drag')}: <span className="font-bold text-slate-200">{costBps} bps</span>
              <input className="mt-2 w-full" type="range" min="0" max="80" value={costBps} onChange={e => setCostBps(Number(e.target.value))} />
            </label>
            <label className="block text-xs text-slate-400">
              {bi('压力倍数', 'Stress Multiplier')}: <span className="font-bold text-slate-200">{stress.toFixed(1)}x</span>
              <input className="mt-2 w-full" type="range" min="1" max="2.5" step="0.1" value={stress} onChange={e => setStress(Number(e.target.value))} />
            </label>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-800 pt-3 text-xs">
              <div className="text-slate-500">IC</div>
              <div className="text-right font-bold text-cyan-300">{formatNumber(alpha.ic, 4)}</div>
              <div className="text-slate-500">{bi('换手率', 'Turnover')}</div>
              <div className="text-right font-bold text-amber-300">{formatNumber(alpha.turnover, 2)}</div>
              <div className="text-slate-500">{bi('容量', 'Capacity')}</div>
              <div className="text-right font-bold text-emerald-300">{formatCapacity(alpha.capacity_estimate)}</div>
              <div className="text-slate-500">{bi('生命周期', 'Lifecycle')}</div>
              <div className="text-right font-bold text-slate-200">{statusLabel(alpha.lifecycle_state)}</div>
            </div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm uppercase text-slate-400">{bi('权益曲线', 'Equity Curve')}</h2>
              <div className="mt-1 text-xs text-slate-500">{alpha.expression}</div>
            </div>
            <DecisionBadge decision={validation.decision} />
          </div>
          <LineChart points={activePoints} />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('回撤路径', 'Drawdown Path')}</h2>
          <DrawdownChart points={activePoints} drawdowns={drawdownPoints} />
          <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
            <div className="border-t border-slate-800 pt-2"><div className="text-slate-500">{bi('压力回撤', 'Stress DD')}</div><div className="font-bold text-red-300">{(metricValues.worstDrawdown*100).toFixed(2)}%</div></div>
            <div className="border-t border-slate-800 pt-2"><div className="text-slate-500">{bi('成本拖累', 'Cost drag')}</div><div className="font-bold text-amber-300">{metricValues.costBps} bps</div></div>
            <div className="border-t border-slate-800 pt-2"><div className="text-slate-500">{bi('最新权益', 'Last equity')}</div><div className="font-bold text-cyan-300">{formatCapacity(lastEquity)}</div></div>
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm uppercase text-slate-400">{bi('风险门禁矩阵', 'Risk Gate Matrix')}</h2>
            <DecisionBadge decision={riskReport.decision} />
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            {gateEntries.map(([gate, passed]) => (
              <div key={gate} className="flex items-center justify-between rounded bg-slate-800 px-3 py-2 text-xs">
                <span className="capitalize text-slate-300">{gateLabel(gate)}</span>
                <RiskBadge passed={passed} />
              </div>
            ))}
          </div>
          {riskReport.reasons.length > 0 && (
            <div className="mt-3 space-y-1 text-xs text-red-300">
              {riskReport.reasons.map(reason => <div key={reason}>{reason}</div>)}
            </div>
          )}
          {'reproducibility_hash' in riskReport && riskReport.reproducibility_hash && (
            <div className="mt-3 truncate text-xs text-slate-500">{bi('复现哈希', 'Hash')}: {riskReport.reproducibility_hash}</div>
          )}
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[900px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">ID</th>
              <th className="p-2 text-left">{bi('层级', 'Layer')}</th>
              <th className="p-2 text-left">{bi('公式', 'Formula')}</th>
              <th className="p-2 text-left">{bi('输入', 'Inputs')}</th>
              <th className="p-2 text-left">{bi('输出', 'Output')}</th>
              <th className="p-2 text-left">{bi('后端模块', 'Backend Module')}</th>
            </tr>
          </thead>
          <tbody>
            {lineage.map(item => (
              <tr key={item.id} className="border-b border-slate-800/50">
                <td className="p-2 font-bold text-cyan-400">{item.id}</td>
                <td className="p-2 text-slate-300">{item.layer}</td>
                <td className="p-2 text-emerald-300">{item.formula}</td>
                <td className="p-2 text-slate-500">{item.inputs.join(', ')}</td>
                <td className="p-2 text-slate-300">{item.output}</td>
                <td className="p-2 text-slate-500">{item.backend}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
