import React, { useEffect, useMemo, useState } from 'react';
import { fetchComputationLabSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
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
import type { ConsoleApiState } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, KeyValue, type Column,
} from '@/ui/primitives';
import { AreaChart, UnderwaterChart, DivergingBars, RadialGauge } from '@/ui/charts';
import { num, pct, fixed, signed, money, shortHash, statusTone, type Tone } from '@/ui/format';

/* ---------- deterministic mock-fallback math (no Math.random / Date.now) ---- */
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

/* ---------- bilingual gate / connection helpers --------------------------- */
function gateLabel(gate: string): string {
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

export default function ComputationLab() {
  const [alphaIndex, setAlphaIndex] = useState(0);
  const [costBps, setCostBps] = useState(18);
  const [stress, setStress] = useState(1.2);
  const [snapshot, setSnapshot] = useState<ComputationLabPayload | null>(null);
  const [apiState, setApiState] = useState<ConsoleApiState>('disabled');
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

  // Preserve the original abortable fetch with alphaId/costBps/stress query params.
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
    fetchComputationLabSnapshot({ alphaId: selectedAlphaId, costBps, stress, signal: controller.signal })
      .then(payload => {
        setSnapshot(payload);
        setApiState('connected');
      })
      .catch(error => {
        if (controller.signal.aborted) return;
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
  const gatesPassed = gateEntries.filter(([, ok]) => ok).length;
  const gatesTotal = gateEntries.length;
  const sourceLabel = usingBackend ? snapshot.source : 'FRONTEND_MOCK_FALLBACK';
  const lastEquity = activePoints[activePoints.length - 1]?.y ?? 0;

  // Net Sharpe attribution: gross → cost drag → stress drag → net (diverging).
  const costDrag = metricValues.costBps / 100;
  const stressDrag = Math.max(0, metricValues.stress - 1) * 0.18;
  const attribution: { label: string; value: number }[] = [
    { label: bi('原始夏普', 'Gross Sharpe'), value: metricValues.grossSharpe },
    { label: bi('成本扣减', 'Cost Drag'), value: -costDrag },
    { label: bi('压力扣减', 'Stress Drag'), value: -stressDrag },
    { label: bi('净夏普', 'Net Sharpe'), value: metricValues.netSharpe },
  ];

  // Equity curve normalised to a simple index for the chart axis.
  const equityData = activePoints.map(p => ({ x: p.x, y: p.y / 1_000_000 }));
  const ddData = drawdownPoints.map(p => ({ x: p.x, y: -p.y }));

  const netTone: Tone = metricValues.netSharpe >= 1 ? 'pos' : metricValues.netSharpe >= 0 ? 'warn' : 'neg';

  const lineageCols: Column<CalculationLineageItem>[] = [
    { key: 'id', header: 'ID', render: l => <span className="mono font-semibold text-accent">{l.id}</span> },
    { key: 'layer', header: bi('层级', 'Layer'), render: l => <Chip label={l.layer} tone="info" /> },
    { key: 'formula', header: bi('公式', 'Formula'), render: l => <span className="mono text-2xs text-pos">{l.formula}</span> },
    { key: 'inputs', header: bi('输入', 'Inputs'), render: l => <span className="text-2xs text-muted">{l.inputs.join(', ')}</span> },
    { key: 'output', header: bi('输出', 'Output'), render: l => <span className="text-2xs text-ink">{l.output}</span> },
    { key: 'backend', header: bi('后端模块', 'Backend Module'), render: l => <span className="mono text-2xs text-faint">{compactModule(l.backend)}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Computation Lab" titleZh="计算实验室"
        subtitle={bi('从因子输入到风险门禁的端到端复算链路;成本与压力情景可调,门禁与谱系可审计',
          'End-to-end recompute chain, factor inputs → risk gate; tunable cost / stress scenarios, auditable gates + lineage')}
        actions={<>
          <Chip label={usingBackend ? 'BACKEND_COMPUTED' : 'MOCK_MODE'} tone={usingBackend ? 'pos' : 'warn'} />
          <Chip label={shortHash(selectedAlphaId, 12)} tone="info" />
        </>} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} realData={(snapshot as { data_provenance?: string } | null | undefined)?.data_provenance === 'REAL_CRYPTO_SERIES'} source={sourceLabel} error={apiError} />

      <KpiStrip cols={6}>
        <StatTile label={bi('原始夏普', 'Gross Sharpe')} value={fixed(metricValues.grossSharpe)} tone="pos" />
        <StatTile label={bi('净夏普', 'Net Sharpe')} value={fixed(metricValues.netSharpe)} tone={netTone}
          sub={bi('扣成本与压力', 'after cost + stress')} />
        <StatTile label={bi('最大回撤', 'Max Drawdown')} value={pct(metricValues.worstDrawdown)} tone="neg" />
        <StatTile label={bi('总收益', 'Total Return')} value={`${signed(metricValues.totalReturn, 1)}%`}
          tone={metricValues.totalReturn >= 0 ? 'pos' : 'neg'} />
        <StatTile label={bi('成本拖累', 'Cost Drag')} value={`${num(metricValues.costBps)}`} unit="bps" tone="warn" />
        <StatTile label={bi('压力倍数', 'Stress Mult.')} value={`${fixed(metricValues.stress, 1)}×`} accent />
      </KpiStrip>

      {/* equity + scenario controls */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('纸面权益', 'Paper Equity')}
          title={bi('权益曲线', 'Equity Curve')}
          right={<StatusChip value={validation.decision} />}>
          <div className="mb-2 mono text-2xs text-faint truncate">{alpha.expression}</div>
          <AreaChart data={equityData} height={236} tone="accent" yFormat={v => `${v.toFixed(1)}M`} />
        </Panel>

        <Panel eyebrow={bi('情景控制', 'Scenario Controls')} title={bi('候选 · 成本 · 压力', 'Candidate · Cost · Stress')}>
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap gap-1.5">
              {alphaCandidates.slice(0, 4).map((candidate, idx) => (
                <button key={candidate.candidate_id} onClick={() => setAlphaIndex(idx)}
                  className={`rounded-md border px-2.5 py-1.5 mono text-2xs transition-colors ${
                    idx === alphaIndex
                      ? 'border-accent/50 bg-accent/15 text-accent'
                      : 'border-line bg-inset text-muted hover:bg-elevated/60'}`}>
                  {candidate.candidate_id}
                </button>
              ))}
            </div>
            <label className="block">
              <div className="flex items-center justify-between text-2xs uppercase tracking-wide text-faint">
                <span>{bi('成本拖累', 'Cost Drag')}</span>
                <span className="tnum font-semibold text-ink">{costBps} bps</span>
              </div>
              <input className="mt-2 w-full accent-[var(--accent)]" type="range" min={0} max={80}
                value={costBps} onChange={e => setCostBps(Number(e.target.value))} />
            </label>
            <label className="block">
              <div className="flex items-center justify-between text-2xs uppercase tracking-wide text-faint">
                <span>{bi('压力倍数', 'Stress Multiplier')}</span>
                <span className="tnum font-semibold text-ink">{stress.toFixed(1)}×</span>
              </div>
              <input className="mt-2 w-full accent-[var(--accent)]" type="range" min={1} max={2.5} step={0.1}
                value={stress} onChange={e => setStress(Number(e.target.value))} />
            </label>
            <div className="border-t border-line pt-3">
              <KeyValue cols={2} items={[
                { k: 'IC', v: <span className="text-accent">{fixed(alpha.ic, 4)}</span> },
                { k: bi('换手率', 'Turnover'), v: <span className="text-warn">{fixed(alpha.turnover)}</span> },
                { k: bi('容量', 'Capacity'), v: <span className="text-pos">{money(alpha.capacity_estimate)}</span> },
                { k: bi('生命周期', 'Lifecycle'), v: statusLabel(alpha.lifecycle_state) },
              ]} />
            </div>
          </div>
        </Panel>
      </div>

      {/* drawdown + sharpe attribution + risk gate gauge */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('回撤路径', 'Drawdown Path')} title={bi('水下曲线', 'Underwater Curve')}>
          <UnderwaterChart data={ddData} height={150} yFormat={v => pct(v, 1)} />
          <div className="mt-3 grid grid-cols-3 gap-3 border-t border-line pt-3">
            <div>
              <div className="text-2xs uppercase tracking-wide text-faint">{bi('压力回撤', 'Stress DD')}</div>
              <div className="tnum text-sm font-semibold text-neg">{pct(metricValues.worstDrawdown)}</div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-faint">{bi('成本拖累', 'Cost Drag')}</div>
              <div className="tnum text-sm font-semibold text-warn">{num(metricValues.costBps)} bps</div>
            </div>
            <div>
              <div className="text-2xs uppercase tracking-wide text-faint">{bi('最新权益', 'Last Equity')}</div>
              <div className="tnum text-sm font-semibold text-accent">{money(lastEquity)}</div>
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('风险门禁', 'Risk Gate')} title={bi('门禁通过率', 'Gates Passed')}
          right={<StatusChip value={riskReport.decision} />}>
          <div className="flex flex-col items-center gap-2">
            <RadialGauge value={gatesPassed} max={Math.max(gatesTotal, 1)} size={140}
              label={`${gatesPassed}/${gatesTotal}`}
              tone={gatesPassed === gatesTotal ? 'pos' : gatesPassed >= gatesTotal * 0.6 ? 'warn' : 'neg'} />
            <div className="text-2xs text-faint">{bi('全部必需门禁须通过(fail-closed)', 'all required gates must pass (fail-closed)')}</div>
          </div>
        </Panel>
      </div>

      {/* net sharpe attribution + gate matrix */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('指标分解', 'Metric Decomposition')}
          title={bi('净夏普归因', 'Net Sharpe Attribution')}
          right={<Chip label={bi('净 = 原始 − 成本 − 压力', 'net = gross − cost − stress')} tone="muted" />}>
          <DivergingBars items={attribution} height={150} valueFormat={v => signed(v, 3)} />
        </Panel>

        <Panel eyebrow={bi('门禁矩阵', 'Gate Matrix')} title={bi('风险门禁明细', 'Risk Gate Detail')}>
          <div className="flex flex-col gap-2">
            {gateEntries.map(([gate, passed]) => (
              <div key={gate} className="flex items-center justify-between rounded-md bg-inset px-3 py-1.5">
                <span className="text-2xs text-ink">{gateLabel(gate)}</span>
                <GateDot ok={passed} label={passed ? bi('通过', 'PASS') : bi('未过', 'FAIL')} />
              </div>
            ))}
            {riskReport.reasons.length > 0 && (
              <div className="mt-1 flex flex-col gap-1 border-t border-line pt-2">
                {riskReport.reasons.map(reason => (
                  <div key={reason} className="text-2xs text-neg">• {reason}</div>
                ))}
              </div>
            )}
            {'reproducibility_hash' in riskReport && riskReport.reproducibility_hash && (
              <div className="mt-1 truncate border-t border-line pt-2 mono text-2xs text-faint">
                {bi('复现哈希', 'Repro Hash')}: {shortHash(riskReport.reproducibility_hash, 16)}
              </div>
            )}
          </div>
        </Panel>
      </div>

      {/* validation summary */}
      <Panel eyebrow={bi('验证报告', 'Validation Report')} title={bi('样本外与净表现门禁', 'OOS & Net-Performance Gates')}
        right={<StatusChip value={validation.decision} />}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
          <StatTile label={bi('门禁通过', 'Gates Passed')} value={`${num(validation.gates_passed)}/${num(validation.gates_total)}`}
            tone={validation.gates_passed === validation.gates_total ? 'pos' : 'warn'} />
          {validation.oos_sharpe !== undefined && (
            <StatTile label={bi('样本外夏普', 'OOS Sharpe')} value={fixed(validation.oos_sharpe)} tone="info" />
          )}
          {validation.oos_ic !== undefined && (
            <StatTile label={bi('样本外IC', 'OOS IC')} value={fixed(validation.oos_ic, 4)} accent />
          )}
          {validation.net_sharpe !== undefined && (
            <StatTile label={bi('净夏普', 'Net Sharpe')} value={fixed(validation.net_sharpe)}
              tone={validation.net_sharpe >= 1 ? 'pos' : 'warn'} />
          )}
          <StatTile label={bi('决策', 'Decision')} value={<StatusChip value={validation.decision} />} />
        </div>
        {validation.reasons.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-3">
            {validation.reasons.map(reason => (
              <Chip key={reason} label={reason} tone={statusTone(reason)} />
            ))}
          </div>
        )}
      </Panel>

      {/* calculation lineage */}
      <Panel eyebrow={bi('计算谱系', 'Calculation Lineage')} title={bi('每个指标的后端公式与模块', 'Per-metric backend formula & module')}
        right={<Chip label={bi(`${lineage.length} 步`, `${lineage.length} steps`)} tone="info" />}>
        <DataTable columns={lineageCols} rows={lineage} dense getKey={l => l.id} />
      </Panel>
    </PageShell>
  );
}
