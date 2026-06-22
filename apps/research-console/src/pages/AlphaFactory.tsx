import React, { useEffect, useMemo, useState } from 'react';
import { DecisionBadge, StatusBadge, MetricCard } from '@/components/status-badges';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { fetchAlphaFactorySnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { mockAlphaCandidates } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { AlphaCandidate, AlphaFactoryPayload } from '@/lib/types/research';

type AlphaScreenDecision = 'APPROVED' | 'NEEDS_MORE_DATA' | 'REJECTED_LOW_CAPACITY' | 'REJECTED_UNSTABLE' | 'REJECTED_HIGH_CORRELATION';

function screenDecision(candidate: AlphaCandidate): AlphaScreenDecision {
  if (candidate.ic < 0.05) {
    return 'REJECTED_LOW_CAPACITY';
  }
  if (candidate.turnover > 0.75) {
    return 'REJECTED_UNSTABLE';
  }
  if (candidate.correlation_penalty > 0.16) {
    return 'REJECTED_HIGH_CORRELATION';
  }
  if (candidate.sharpe > 0.7) {
    return 'APPROVED';
  }
  return 'NEEDS_MORE_DATA';
}

function operatorName(expression: string) {
  const match = expression.match(/\(([^ :]+)/);
  return match?.[1] ?? 'unknown';
}

function scoreWidth(value: number, max = 1) {
  return `${Math.max(3, Math.min(100, (value / max) * 100))}%`;
}

function decisionReason(decision: string) {
  const reasons: Record<string, string> = {
    APPROVED: bi('通过初筛', 'Screen passed'),
    NEEDS_MORE_DATA: bi('需要更多样本', 'Needs more samples'),
    REJECTED_LOW_CAPACITY: bi('IC 低于研究门槛', 'IC below research gate'),
    REJECTED_UNSTABLE: bi('换手率过高', 'Turnover too high'),
    REJECTED_HIGH_CORRELATION: bi('相关性惩罚过高', 'Correlation penalty too high'),
  };
  return reasons[decision];
}

function AlphaQualityScatter({candidates}: {candidates: AlphaCandidate[]}) {
  const width = 680;
  const height = 240;
  const pad = 32;
  const maxSharpe = Math.max(...candidates.map(candidate => candidate.sharpe), 1);
  const maxTurnover = Math.max(...candidates.map(candidate => candidate.turnover), 1);
  const capacityMax = Math.max(...candidates.map(candidate => candidate.capacity_estimate), 1);

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3 flex flex-col gap-1 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-sm uppercase text-slate-400">{bi('Alpha 质量散点', 'Alpha Quality Scatter')}</h2>
          <div className="mt-1 text-xs text-slate-500">{bi('横轴换手率，纵轴夏普，点大小表示容量估计。', 'X is turnover, Y is Sharpe, bubble size is capacity estimate.')}</div>
        </div>
        <DecisionBadge decision="BACKEND_COMPUTED" />
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-64 w-full">
        {[0, 1, 2, 3].map(index => (
          <line key={`h-${index}`} x1={pad} x2={width - pad} y1={pad + index * 54} y2={pad + index * 54} stroke="#1e293b" />
        ))}
        {[0, 1, 2, 3].map(index => (
          <line key={`v-${index}`} x1={pad + index * 150} x2={pad + index * 150} y1={pad} y2={height - pad} stroke="#1e293b" />
        ))}
        <line x1={pad} x2={width - pad} y1={height - pad} y2={height - pad} stroke="#334155" />
        <line x1={pad} x2={pad} y1={pad} y2={height - pad} stroke="#334155" />
        {candidates.map(candidate => {
          const x = pad + (candidate.turnover / maxTurnover) * (width - pad * 2);
          const y = height - pad - (candidate.sharpe / maxSharpe) * (height - pad * 2);
          const radius = 4 + (candidate.capacity_estimate / capacityMax) * 10;
          const decision = screenDecision(candidate);
          const fill = decision === 'APPROVED'
            ? '#10b981'
            : decision === 'NEEDS_MORE_DATA'
              ? '#64748b'
              : '#f97316';
          return (
            <g key={candidate.candidate_id}>
              <circle cx={x} cy={y} r={radius} fill={fill} fillOpacity="0.75" stroke="#0f172a" strokeWidth="2">
                <title>{`${candidate.candidate_id}: Sharpe ${candidate.sharpe.toFixed(2)}, Turnover ${candidate.turnover.toFixed(2)}`}</title>
              </circle>
              <text x={x + radius + 3} y={y + 4} fill="#94a3b8" fontSize="10">{candidate.candidate_id.replace('alpha-', 'a')}</text>
            </g>
          );
        })}
        <text x={width - pad - 90} y={height - 8} fill="#64748b" fontSize="11">{bi('换手率', 'Turnover')}</text>
        <text x={6} y={pad - 10} fill="#64748b" fontSize="11">{bi('夏普', 'Sharpe')}</text>
      </svg>
    </div>
  );
}

function CorrelationProxyMatrix({
  candidates,
  matrixPayload,
}: {
  candidates: AlphaCandidate[];
  matrixPayload?: AlphaFactoryPayload['correlation_matrix'];
}) {
  const matrixCandidates = candidates.slice(0, 8);
  const proxyCorrelation = (left: AlphaCandidate, right: AlphaCandidate, i: number, j: number) => {
    if (left.candidate_id === right.candidate_id) {
      return 1;
    }
    const sameOperatorBonus = operatorName(left.expression) === operatorName(right.expression) ? 0.28 : 0;
    const penalty = (left.correlation_penalty + right.correlation_penalty) / 2;
    const scoreDistance = Math.abs(left.composite_score - right.composite_score);
    const proximity = Math.max(0, 0.16 - Math.abs(i - j) * 0.015);
    return Math.max(0.05, Math.min(0.95, 0.18 + sameOperatorBonus + penalty + proximity - scoreDistance * 0.35));
  };
  const ids = matrixPayload?.ids ?? matrixCandidates.map(candidate => candidate.candidate_id);
  const matrix = matrixPayload?.matrix ?? matrixCandidates.map((row, rowIndex) => (
    matrixCandidates.map((column, columnIndex) => proxyCorrelation(row, column, rowIndex, columnIndex))
  ));
  const method = matrixPayload?.method ?? 'FRONTEND_RESEARCH_PROXY_EXPRESSION_SCORE_PENALTY';

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="mb-3">
        <h2 className="text-sm uppercase text-slate-400">{bi('相关性代理矩阵', 'Correlation Proxy Matrix')}</h2>
        <div className="mt-1 text-xs text-slate-500">{bi('由表达式族、综合分与相关性惩罚派生；用于研究排查，不代表实盘持仓相关。', 'Derived from expression family, score, and correlation penalty; research triage only, not live exposure correlation.')}</div>
        <div className="mt-1 text-[10px] text-cyan-500">{method}</div>
      </div>
      <div className="grid gap-1 overflow-auto" style={{gridTemplateColumns: `4rem repeat(${ids.length}, minmax(2.2rem, 1fr))`}}>
        <div />
        {ids.map(alphaId => (
          <div key={`head-${alphaId}`} className="truncate text-center text-[10px] text-slate-500">{alphaId.replace('alpha-', 'a')}</div>
        ))}
        {ids.map((rowId, rowIndex) => (
          <React.Fragment key={rowId}>
            <div className="truncate text-[10px] text-cyan-300">{rowId}</div>
            {ids.map((columnId, columnIndex) => {
              const value = matrix[rowIndex]?.[columnIndex] ?? 0;
              const red = Math.round(40 + value * 150);
              const green = Math.round(80 + (1 - value) * 120);
              return (
                <div
                  key={`${rowId}-${columnId}`}
                  className="flex aspect-square items-center justify-center rounded text-[10px] text-slate-950"
                  style={{backgroundColor: `rgb(${red}, ${green}, 120)`}}
                  title={`${rowId} / ${columnId}: ${value.toFixed(2)}`}
                >
                  {value.toFixed(2)}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function AlphaScreeningBoard({
  candidates,
  screening,
}: {
  candidates: AlphaCandidate[];
  screening?: AlphaFactoryPayload['screening'];
}) {
  const decisions = candidates.reduce<Record<string, number>>((acc, candidate) => {
    const decision = screenDecision(candidate);
    acc[decision] = (acc[decision] ?? 0) + 1;
    return acc;
  }, {
    APPROVED: 0,
    NEEDS_MORE_DATA: 0,
    REJECTED_LOW_CAPACITY: 0,
    REJECTED_UNSTABLE: 0,
    REJECTED_HIGH_CORRELATION: 0,
  });
  const buckets = screening?.buckets ?? decisions;
  const ranked = screening?.top_candidates ?? [...candidates]
    .sort((left, right) => right.composite_score - left.composite_score)
    .slice(0, 5)
    .map(candidate => ({
      candidate_id: candidate.candidate_id,
      decision: screenDecision(candidate),
      reason: decisionReason(screenDecision(candidate)),
      composite_score: candidate.composite_score,
      expression: candidate.expression,
    }));

  return (
    <div className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('筛选结果桶', 'Screening Buckets')}</h2>
        <div className="space-y-2 text-xs">
          {Object.entries(buckets).map(([decision, count]) => (
            <div key={decision} className="flex items-center justify-between gap-3 rounded bg-slate-800 px-3 py-2">
              <DecisionBadge decision={decision} />
              <span className="font-bold text-slate-200">{count}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('Top Alpha 候选', 'Top Alpha Candidates')}</h2>
        <div className="space-y-3 text-xs">
          {ranked.map(candidate => {
            const decision = candidate.decision;
            return (
              <div key={candidate.candidate_id} className="grid gap-2 border-b border-slate-800 pb-3 md:grid-cols-[6rem_1fr_8rem]">
                <div className="font-bold text-cyan-300">{candidate.candidate_id}</div>
                <div className="min-w-0">
                  <div className="truncate text-slate-200">{candidate.expression}</div>
                  <div className="mt-1 h-2 overflow-hidden rounded bg-slate-800">
                    <div className="h-full bg-cyan-700" style={{width: scoreWidth(candidate.composite_score)}} />
                  </div>
                  <div className="mt-1 text-slate-500">{candidate.reason ?? decisionReason(decision)}</div>
                </div>
                <div className="flex items-start justify-start md:justify-end">
                  <DecisionBadge decision={decision} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function AlphaFactory() {
  const [payload, setPayload] = useState<AlphaFactoryPayload | null>(null);
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
    fetchAlphaFactorySnapshot(controller.signal)
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
  const candidates = usingBackend ? payload.candidates : mockAlphaCandidates;
  const summary = usingBackend
    ? payload.summary
    : {
      candidates: mockAlphaCandidates.length,
      avg_ic: 0.032,
      avg_sharpe: 0.87,
      active: mockAlphaCandidates.filter(candidate => candidate.lifecycle_state === 'ACTIVE').length,
    };
  const screening = usingBackend ? payload.screening : undefined;
  const correlationMatrix = usingBackend ? payload.correlation_matrix : undefined;
  const approvedCount = useMemo(
    () => screening?.buckets.APPROVED ?? candidates.filter(candidate => screenDecision(candidate) === 'APPROVED').length,
    [candidates, screening],
  );
  const avgComposite = useMemo(
    () => candidates.reduce((sum, candidate) => sum + candidate.composite_score, 0) / Math.max(candidates.length, 1),
    [candidates],
  );

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <h1 className="text-lg font-bold text-slate-100">{bi('Alpha 因子工厂', 'Alpha Factory')}</h1>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={apiError} />
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <MetricCard label={bi('候选数量', 'Candidates')} value={summary.candidates} />
        <MetricCard label={bi('平均 IC', 'Avg IC')} value={summary.avg_ic.toFixed(4)} />
        <MetricCard label={bi('平均夏普', 'Avg Sharpe')} value={summary.avg_sharpe.toFixed(2)} />
        <MetricCard label={bi('初筛通过', 'Screen Passed')} value={approvedCount} />
        <MetricCard label={bi('平均综合分', 'Avg Score')} value={avgComposite.toFixed(2)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <AlphaQualityScatter candidates={candidates} />
        <CorrelationProxyMatrix candidates={candidates} matrixPayload={correlationMatrix} />
      </div>

      <AlphaScreeningBoard candidates={candidates} screening={screening} />

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[980px] text-xs font-mono">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">ID</th>
              <th className="p-2 text-left">{bi('表达式', 'Expression')}</th>
              <th className="p-2 text-right">IC</th>
              <th className="p-2 text-right">{bi('夏普', 'Sharpe')}</th>
              <th className="p-2 text-right">{bi('换手率', 'Turnover')}</th>
              <th className="p-2 text-right">{bi('相关惩罚', 'Corr Penalty')}</th>
              <th className="p-2 text-right">{bi('综合分', 'Score')}</th>
              <th className="p-2 text-right">{bi('容量(CNY M)', 'Cap(CNY M)')}</th>
              <th className="p-2 text-left">{bi('状态', 'State')}</th>
              <th className="p-2 text-left">{bi('筛选', 'Screen')}</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map(candidate => {
              const decision = screenDecision(candidate);
              return (
                <tr key={candidate.candidate_id} className="border-b border-slate-800/50 hover:bg-slate-800/50">
                  <td className="p-2 text-cyan-400">{candidate.candidate_id}</td>
                  <td className="max-w-xs truncate p-2 text-slate-300">{candidate.expression.slice(0, 50)}</td>
                  <td className="p-2 text-right text-emerald-400">{candidate.ic.toFixed(4)}</td>
                  <td className="p-2 text-right">{candidate.sharpe.toFixed(2)}</td>
                  <td className="p-2 text-right text-amber-400">{candidate.turnover.toFixed(2)}</td>
                  <td className="p-2 text-right text-purple-300">{candidate.correlation_penalty.toFixed(2)}</td>
                  <td className="p-2 text-right text-cyan-300">{candidate.composite_score.toFixed(2)}</td>
                  <td className="p-2 text-right">{(candidate.capacity_estimate / 1e6).toFixed(1)}</td>
                  <td className="p-2"><StatusBadge status={candidate.lifecycle_state} /></td>
                  <td className="p-2"><DecisionBadge decision={decision} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
