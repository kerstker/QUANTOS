import React, { useMemo } from 'react';
import { fetchAlphaFactorySnapshot } from '@/lib/api/research-console-api';
import { mockAlphaCandidates } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { AlphaCandidate, AlphaFactoryPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, Chip, GateDot, ProgressBar, KeyValue, type Column,
} from '@/ui/primitives';
import { Donut, Heatmap, Histogram } from '@/ui/charts';
import { num, pct, fixed, money, statusTone, type Tone } from '@/ui/format';

/* ----- Frontend research-proxy screening (unchanged decision logic) -------- */
type AlphaScreenDecision =
  | 'APPROVED' | 'NEEDS_MORE_DATA'
  | 'REJECTED_LOW_CAPACITY' | 'REJECTED_UNSTABLE' | 'REJECTED_HIGH_CORRELATION';

function screenDecision(c: AlphaCandidate): AlphaScreenDecision {
  if (c.ic < 0.05) return 'REJECTED_LOW_CAPACITY';
  if (c.turnover > 0.75) return 'REJECTED_UNSTABLE';
  if (c.correlation_penalty > 0.16) return 'REJECTED_HIGH_CORRELATION';
  if (c.sharpe > 0.7) return 'APPROVED';
  return 'NEEDS_MORE_DATA';
}

function decisionReason(decision: string): string {
  const reasons: Record<string, string> = {
    APPROVED: bi('通过初筛', 'Screen passed'),
    NEEDS_MORE_DATA: bi('需要更多样本', 'Needs more samples'),
    REJECTED_LOW_CAPACITY: bi('IC 低于研究门槛', 'IC below research gate'),
    REJECTED_UNSTABLE: bi('换手率过高', 'Turnover too high'),
    REJECTED_HIGH_CORRELATION: bi('相关性惩罚过高', 'Correlation penalty too high'),
  };
  return reasons[decision] ?? decision;
}

function operatorName(expression: string): string {
  const match = expression.match(/\(([^ :]+)/);
  return match?.[1] ?? 'unknown';
}

const bucketTone: Record<string, Tone> = {
  APPROVED: 'pos',
  NEEDS_MORE_DATA: 'warn',
  REJECTED_LOW_CAPACITY: 'neg',
  REJECTED_UNSTABLE: 'neg',
  REJECTED_HIGH_CORRELATION: 'special',
};

/* Frontend research-proxy correlation (used only when backend omits matrix). */
function proxyMatrix(candidates: AlphaCandidate[]): { ids: string[]; matrix: number[][]; method: string } {
  const sub = candidates.slice(0, 8);
  const proxy = (l: AlphaCandidate, r: AlphaCandidate, i: number, j: number) => {
    if (l.candidate_id === r.candidate_id) return 1;
    const sameOp = operatorName(l.expression) === operatorName(r.expression) ? 0.28 : 0;
    const penalty = (l.correlation_penalty + r.correlation_penalty) / 2;
    const scoreDist = Math.abs(l.composite_score - r.composite_score);
    const proximity = Math.max(0, 0.16 - Math.abs(i - j) * 0.015);
    return Math.max(0.05, Math.min(0.95, 0.18 + sameOp + penalty + proximity - scoreDist * 0.35));
  };
  return {
    ids: sub.map(c => c.candidate_id),
    matrix: sub.map((row, i) => sub.map((col, j) => proxy(row, col, i, j))),
    method: 'FRONTEND_RESEARCH_PROXY_EXPRESSION_SCORE_PENALTY',
  };
}

export default function AlphaFactory() {
  const { payload, apiState, error, usingBackend } =
    useConsoleSnapshot<AlphaFactoryPayload>(s => fetchAlphaFactorySnapshot(s));

  const candidates = usingBackend ? payload!.candidates : mockAlphaCandidates;
  const summary = usingBackend ? payload!.summary : {
    candidates: mockAlphaCandidates.length,
    avg_ic: mockAlphaCandidates.reduce((s, c) => s + c.ic, 0) / Math.max(mockAlphaCandidates.length, 1),
    avg_sharpe: mockAlphaCandidates.reduce((s, c) => s + c.sharpe, 0) / Math.max(mockAlphaCandidates.length, 1),
    active: mockAlphaCandidates.filter(c => c.lifecycle_state === 'ACTIVE').length,
  };
  const screening = usingBackend ? payload!.screening : undefined;
  const correlationMatrix = usingBackend ? payload!.correlation_matrix : undefined;
  const lineage = usingBackend ? payload!.lineage : [];

  /* Screening buckets: prefer backend, else derive from research-proxy. */
  const buckets = useMemo<Record<string, number>>(() => {
    if (screening?.buckets) return screening.buckets;
    return candidates.reduce<Record<string, number>>((acc, c) => {
      const d = screenDecision(c);
      acc[d] = (acc[d] ?? 0) + 1;
      return acc;
    }, {
      APPROVED: 0, NEEDS_MORE_DATA: 0,
      REJECTED_LOW_CAPACITY: 0, REJECTED_UNSTABLE: 0, REJECTED_HIGH_CORRELATION: 0,
    });
  }, [screening, candidates]);

  const approvedCount = buckets.APPROVED ?? 0;
  const avgComposite = useMemo(
    () => candidates.reduce((s, c) => s + c.composite_score, 0) / Math.max(candidates.length, 1),
    [candidates],
  );
  const avgIcir = useMemo(
    () => candidates.reduce((s, c) => s + c.icir, 0) / Math.max(candidates.length, 1),
    [candidates],
  );
  const totalCapacity = useMemo(
    () => candidates.reduce((s, c) => s + c.capacity_estimate, 0),
    [candidates],
  );

  /* Lifecycle composition (Donut). */
  const lifecycleSegments = useMemo(() => {
    const counts = candidates.reduce<Record<string, number>>((acc, c) => {
      acc[c.lifecycle_state] = (acc[c.lifecycle_state] ?? 0) + 1;
      return acc;
    }, {});
    return Object.entries(counts).map(([label, value]) => ({
      label, value, tone: statusTone(label),
    }));
  }, [candidates]);

  /* Screening buckets → Donut segments. */
  const bucketSegments = useMemo(
    () => Object.entries(buckets)
      .filter(([, v]) => v > 0)
      .map(([label, value]) => ({ label, value, tone: bucketTone[label] ?? 'muted' as Tone })),
    [buckets],
  );

  /* Composite-score distribution (Histogram). */
  const compositeBins = useMemo(() => {
    const edges = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0];
    const labels = ['0–.2', '.2–.4', '.4–.6', '.6–.8', '.8–1'];
    const counts = new Array(labels.length).fill(0) as number[];
    for (const c of candidates) {
      let idx = edges.findIndex((e, i) => c.composite_score < edges[i + 1] && i < labels.length);
      if (idx < 0) idx = labels.length - 1;
      counts[idx] += 1;
    }
    return labels.map((label, i) => ({ label, count: counts[i] }));
  }, [candidates]);

  /* Correlation matrix: backend payload, else research proxy. */
  const corr = useMemo(() => correlationMatrix ?? proxyMatrix(candidates), [correlationMatrix, candidates]);
  const corrIds = corr.ids.map(id => id.replace('alpha-', 'a'));

  /* Top candidates (backend ranking when present, else by composite). */
  const ranked = useMemo(() => {
    if (screening?.top_candidates?.length) return screening.top_candidates;
    return [...candidates]
      .sort((l, r) => r.composite_score - l.composite_score)
      .slice(0, 5)
      .map(c => ({
        candidate_id: c.candidate_id,
        decision: screenDecision(c) as string,
        reason: decisionReason(screenDecision(c)),
        composite_score: c.composite_score,
        expression: c.expression,
      }));
  }, [screening, candidates]);

  const candidateCols: Column<AlphaCandidate>[] = [
    { key: 'id', header: 'ID', render: c => <span className="mono text-accent">{c.candidate_id}</span> },
    { key: 'expr', header: bi('表达式', 'Expression'), render: c => (
      <span className="mono text-muted" title={c.expression}>{c.expression.length > 46 ? `${c.expression.slice(0, 46)}…` : c.expression}</span>
    ) },
    { key: 'ic', header: 'IC', align: 'right', mono: true, render: c => <span className="text-pos">{fixed(c.ic, 4)}</span> },
    { key: 'rank_ic', header: bi('排序IC', 'Rank IC'), align: 'right', mono: true, render: c => fixed(c.rank_ic, 4) },
    { key: 'icir', header: 'ICIR', align: 'right', mono: true, render: c => fixed(c.icir, 2) },
    { key: 'sharpe', header: bi('夏普', 'Sharpe'), align: 'right', mono: true, render: c => fixed(c.sharpe, 2) },
    { key: 'turnover', header: bi('换手率', 'Turnover'), align: 'right', mono: true, render: c => <span className="text-warn">{pct(c.turnover, 0)}</span> },
    { key: 'decay', header: bi('衰减半衰', 'Half-life'), align: 'right', mono: true, render: c => `${num(c.decay_half_life)}d` },
    { key: 'corr', header: bi('相关惩罚', 'Corr Pen.'), align: 'right', mono: true, render: c => <span className="text-special">{fixed(c.correlation_penalty, 2)}</span> },
    { key: 'cap', header: bi('容量', 'Capacity'), align: 'right', mono: true, render: c => money(c.capacity_estimate) },
    { key: 'score', header: bi('综合分', 'Score'), width: '150px', render: c => (
      <div className="flex items-center gap-2">
        <ProgressBar value={c.composite_score} tone="accent" />
        <span className="tnum w-9 text-right text-muted">{fixed(c.composite_score, 2)}</span>
      </div>
    ) },
    { key: 'state', header: bi('生命周期', 'Lifecycle'), render: c => <StatusChip value={c.lifecycle_state} /> },
    { key: 'screen', header: bi('初筛', 'Screen'), render: c => <StatusChip value={screenDecision(c)} /> },
  ];

  const lineageCols: Column<AlphaFactoryPayload['lineage'][number]>[] = [
    { key: 'id', header: 'ID', render: l => <span className="mono text-accent">{l.id}</span> },
    { key: 'layer', header: bi('层', 'Layer'), render: l => <Chip label={l.layer} tone="info" /> },
    { key: 'formula', header: bi('公式', 'Formula'), render: l => <span className="mono text-muted" title={l.formula}>{l.formula}</span> },
    { key: 'backend', header: bi('后端', 'Backend'), render: l => <span className="mono text-faint">{l.backend}</span> },
    { key: 'inputs', header: bi('输入', 'Inputs'), render: l => <span className="mono text-faint">{l.inputs.join(', ')}</span> },
    { key: 'output', header: bi('输出', 'Output'), render: l => <span className="mono text-ink">{l.output}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Alpha Factory" titleZh="Alpha 因子工厂"
        subtitle={bi('候选因子搜索、初筛与生命周期 — 只读合成快照', 'Candidate alpha search, screening and lifecycle — read-only synthetic snapshot')}
        actions={<Chip label={bi(`${candidates.length} 候选`, `${candidates.length} candidates`)} tone="info" />} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} realData={(payload as { data_provenance?: string } | null | undefined)?.data_provenance === 'REAL_CRYPTO_SERIES'} source={payload?.source} error={error} />

      <KpiStrip cols={7}>
        <StatTile label={bi('候选因子', 'Candidates')} value={num(summary.candidates)} accent />
        <StatTile label={bi('平均 IC', 'Avg IC')} value={fixed(summary.avg_ic, 4)} tone="pos" />
        <StatTile label={bi('平均夏普', 'Avg Sharpe')} value={fixed(summary.avg_sharpe, 2)} />
        <StatTile label={bi('平均 ICIR', 'Avg ICIR')} value={fixed(avgIcir, 2)} tone="info" />
        <StatTile label={bi('初筛通过', 'Screen Passed')} value={num(approvedCount)} tone="pos" sub={bi(`${candidates.length} 中`, `of ${candidates.length}`)} />
        <StatTile label={bi('平均综合分', 'Avg Score')} value={fixed(avgComposite, 2)} />
        <StatTile label={bi('合计容量', 'Total Cap.')} value={money(totalCapacity)} tone="special" sub={bi('合成估计', 'synthetic est.')} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('初筛', 'Screening')} title={bi('筛选结果桶', 'Screening Buckets')}
          right={<Chip label={bi('研究代理', 'research proxy')} tone="warn" />}>
          <div className="flex items-center gap-4">
            <Donut size={132} segments={bucketSegments} />
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              {Object.entries(buckets).map(([decision, count]) => (
                <div key={decision} className="flex items-center justify-between gap-2">
                  <StatusChip value={decision} />
                  <span className="tnum text-xs text-muted">{num(count)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('生命周期', 'Lifecycle')} title={bi('因子生命周期构成', 'Candidate Lifecycle Mix')}>
          <div className="flex items-center gap-4">
            <Donut size={132} segments={lifecycleSegments} />
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              {lifecycleSegments.map(s => (
                <div key={s.label} className="flex items-center justify-between gap-2">
                  <StatusChip value={s.label} />
                  <span className="tnum text-xs text-muted">{num(s.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('分布', 'Distribution')} title={bi('综合分分布', 'Composite Score Distribution')}>
          <Histogram bins={compositeBins} height={172} tone="accent" />
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('排查', 'Triage')} title={bi('相关性代理矩阵', 'Correlation Proxy Matrix')}
          right={<span className="mono text-2xs text-faint">{corr.method}</span>}>
          <Heatmap ids={corrIds} matrix={corr.matrix} size={420} />
          <p className="mt-2 text-2xs text-faint">
            {bi('由表达式族、综合分与相关性惩罚派生;研究排查用,不代表实盘持仓相关。',
              'Derived from expression family, score and correlation penalty; research triage only, not live exposure correlation.')}
          </p>
        </Panel>

        <Panel eyebrow={bi('排名', 'Ranked')} title={bi('Top Alpha 候选', 'Top Alpha Candidates')}>
          <div className="flex flex-col gap-3">
            {ranked.map(c => (
              <div key={c.candidate_id} className="flex flex-col gap-1.5 border-b border-line/60 pb-3 last:border-0 last:pb-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="mono text-xs font-semibold text-accent">{c.candidate_id}</span>
                  <StatusChip value={c.decision} />
                </div>
                <div className="mono truncate text-2xs text-muted" title={c.expression}>{c.expression}</div>
                <div className="flex items-center gap-2">
                  <ProgressBar value={c.composite_score} tone="accent" />
                  <span className="tnum w-9 text-right text-2xs text-muted">{fixed(c.composite_score, 2)}</span>
                </div>
                <div className="text-2xs text-faint">{c.reason || decisionReason(c.decision)}</div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('因子库', 'Catalog')} title={bi('Alpha 候选清单', 'Alpha Candidate Catalog')}
        right={<GateDot ok label={bi('只读', 'read-only')} />}>
        <DataTable columns={candidateCols} rows={candidates} getKey={c => c.candidate_id} dense />
      </Panel>

      {lineage.length > 0 && (
        <Panel eyebrow={bi('可复现', 'Reproducible')} title={bi('计算血缘', 'Calculation Lineage')}
          right={<Chip label={bi(`${lineage.length} 步`, `${lineage.length} steps`)} tone="info" />}>
          <DataTable columns={lineageCols} rows={lineage} getKey={l => l.id} dense />
        </Panel>
      )}
    </PageShell>
  );
}
