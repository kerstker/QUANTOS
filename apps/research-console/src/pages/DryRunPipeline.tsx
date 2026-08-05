import React, { useMemo } from 'react';
import {
  Callout,
  Chip,
  LoadingVeil,
  PageShell,
  Panel,
  StatusChip,
} from '@/ui/primitives';
import { EquityCurveChart, type EquitySeries } from '@/ui/workbench';
import { stamp, type Tone } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchDryRunSnapshot } from '@/lib/api/research-console-api';
import { statusLabel } from '@/lib/i18n/bilingual';
import type {
  AlphaPipelineCandidate,
  DryRunPipelinePayload,
  PaperOrderPreviewAlpha,
  PaperRealtimeAlpha,
  PaperRealtimeBacktest,
  PaperRealtimeHistoryPoint,
  RealtimeAlphaCandidatePayload,
  RealtimeAlphaAutonomyPayload,
  RealtimeAlphaLifecyclePayload,
} from '@/lib/types/research';

const mockPayload: DryRunPipelinePayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT',
  read_only: true,
  api_version: '0.1.0',
  data_provenance: 'FRONTEND_FALLBACK_NOT_DECISION_DATA',
  snapshot_present: false,
  verdict: 'NO_RUN',
  n_runs: 0,
  latest: {},
  runs: [],
  safety_note: 'Real console API unavailable.',
};

const PAPER_TONES: Tone[] = ['accent', 'info', 'warn', 'special', 'pos'];

function fmtUsd(value?: number | null): string {
  return value == null
    ? '—'
    : `$${value.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function fmtPct(value?: number | null): string {
  return value == null ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`;
}

function fmtAge(ageMs?: number | null): string {
  if (ageMs == null) return '—';
  if (ageMs < 60_000) return `${Math.max(0, Math.floor(ageMs / 1000))}秒`;
  return `${Math.floor(ageMs / 60_000)}分钟`;
}

function fmtBps(value?: string | null): string {
  if (value == null) return '—';
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${parsed.toFixed(2)} bps` : '—';
}

function fmtCompactUsd(value?: string | null): string {
  if (value == null) return '—';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(parsed);
}

function fmtTime(value?: number | null): string {
  if (value == null) return '—';
  return new Date(value).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

function signedClass(value?: number | null): string {
  if (value == null || !Number.isFinite(value) || value === 0) return 'text-muted';
  return value > 0 ? 'text-pos' : 'text-neg';
}

type PaperPerformanceValue = Pick<PaperRealtimeAlpha, 'status' | 'n_fills' | 'net_pnl'>;

function hasRealizedPaperPnl(value?: PaperPerformanceValue | null): boolean {
  return value?.status === 'PAPER_MARKED' && (value.n_fills ?? 0) > 0;
}

function paperPerformanceState(value?: PaperPerformanceValue | null): 'PROFIT' | 'LOSS' | 'FLAT' | 'NO_REALIZED_PNL' {
  if (!hasRealizedPaperPnl(value)) return 'NO_REALIZED_PNL';
  const pnl = value?.net_pnl ?? 0;
  return pnl > 0 ? 'PROFIT' : pnl < 0 ? 'LOSS' : 'FLAT';
}

function fmtRealizedPct(value: number | null | undefined, realized: boolean): string {
  return realized ? fmtPct(value) : '—';
}

function gateStatusTone(status?: string | null): Tone {
  const normalized = (status ?? 'NOT_AVAILABLE').toUpperCase();
  if (normalized === 'PASS' || normalized === 'READY' || normalized === 'PAPER_MARKED') return 'pos';
  if (normalized.includes('FAIL') || normalized.includes('BLOCK') || normalized.includes('REJECT')) return 'neg';
  if (normalized.includes('RUNNING') || normalized.includes('VALID')) return 'info';
  return 'warn';
}

function paperSeries(history: PaperRealtimeHistoryPoint[]): EquitySeries[] {
  const byAlpha = new Map<string, { t: number; equity: number }[]>();
  for (const point of history) {
    const equity = point.equity ?? point.net_pnl;
    if (typeof equity !== 'number' || !Number.isFinite(equity)) continue;
    const rows = byAlpha.get(point.alpha_id) ?? [];
    rows.push({ t: point.observed_at, equity });
    byAlpha.set(point.alpha_id, rows);
  }
  return Array.from(byAlpha.entries()).map(([alpha, points], index) => ({
    label: alpha,
    points: points.sort((a, b) => a.t - b.t),
    tone: PAPER_TONES[index % PAPER_TONES.length],
  }));
}

function Metric({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string;
  value: React.ReactNode;
  detail: React.ReactNode;
  tone?: Tone;
}) {
  const toneClass: Record<Tone, string> = {
    pos: 'text-pos',
    neg: 'text-neg',
    warn: 'text-warn',
    info: 'text-info',
    accent: 'text-accent',
    special: 'text-special',
    muted: 'text-ink',
  };
  return (
    <div className="min-w-0 border-l border-line px-3 first:border-l-0 first:pl-0 sm:px-4">
      <div className="text-3xs font-semibold uppercase tracking-[0.14em] text-faint">{label}</div>
      <div className={`ticker-num mt-1 truncate text-xl font-semibold ${toneClass[tone]}`}>{value}</div>
      <div className="mt-1 truncate text-3xs text-faint">{detail}</div>
    </div>
  );
}

type RouteTcaHistory = NonNullable<DryRunPipelinePayload['route_tca_history']>;

function TcaHistoryPanel({ history }: { history?: RouteTcaHistory }) {
  const ready = history?.status === 'LONGITUDINAL_READY';
  const venues = history?.venues ?? [];
  const range = history?.first_timestamp != null && history.last_timestamp != null
    ? `${new Date(history.first_timestamp).toLocaleDateString('zh-CN')} — ${new Date(history.last_timestamp).toLocaleDateString('zh-CN')}`
    : '暂无保留区间';

  return (
    <Panel
      title="纸面执行质量"
      eyebrow="路由级 TCA · 仅模拟"
      right={<Chip label={history?.status ? statusLabel(history.status) : '暂无路由 TCA'} tone={ready ? 'pos' : 'warn'} />}
      bodyClass="p-4"
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="加权执行缺口" value={fmtBps(history?.weighted_shortfall_bps)} detail="按名义金额加权" tone={ready ? 'pos' : 'warn'} />
        <Metric label="路由运行次数" value={(history?.run_count ?? 0).toLocaleString('zh-CN')} detail="仅追加证据" tone="info" />
        <Metric label="路由记录" value={(history?.record_count ?? 0).toLocaleString('zh-CN')} detail="已验证记录" tone="info" />
        <Metric label="交易场所" value={history?.venue_count ?? 0} detail={range} tone="special" />
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border border-line bg-inset/35">
        <table className="w-full table-fixed text-left text-xs" aria-label="按交易场所统计的纸面路由 TCA">
          <thead className="border-b border-line text-3xs uppercase tracking-[0.12em] text-faint">
            <tr>
              <th className="w-[34%] px-3 py-2 font-medium">交易场所</th>
              <th className="px-3 py-2 text-right font-medium">记录数</th>
              <th className="px-3 py-2 text-right font-medium">名义金额</th>
              <th className="px-3 py-2 text-right font-medium">执行缺口</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/70">
            {venues.length ? venues.map(venue => (
              <tr key={venue.venue_id}>
                <td className="truncate px-3 py-2 font-semibold text-ink">{venue.venue_id}</td>
                <td className="px-3 py-2 text-right ticker-num text-muted">{venue.record_count.toLocaleString('zh-CN')}</td>
                <td className="px-3 py-2 text-right ticker-num text-muted">{fmtCompactUsd(venue.reference_notional)}</td>
                <td className="px-3 py-2 text-right ticker-num text-ink">{fmtBps(venue.weighted_shortfall_bps)}</td>
              </tr>
            )) : (
              <tr>
                <td colSpan={4} className="px-3 py-5 text-center text-faint">暂无已验证并保留的路由级 TCA。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-3xs text-faint">
        <span>{range}</span>
        <span>仅模拟 · 读取时验证</span>
      </div>
    </Panel>
  );
}

function FlowStep({
  index,
  label,
  detail,
  ok,
  blocked = false,
}: {
  index: number;
  label: string;
  detail: string;
  ok: boolean;
  blocked?: boolean;
}) {
  const tone: Tone = ok ? 'pos' : blocked ? 'warn' : 'neg';
  return (
    <div className="flex min-w-0 items-center gap-3 py-2.5">
      <span
        className="grid h-7 w-7 shrink-0 place-items-center rounded-full border text-2xs font-bold"
        style={{
          color: `var(--${tone})`,
          borderColor: `color-mix(in srgb, var(--${tone}) 45%, transparent)`,
          background: `color-mix(in srgb, var(--${tone}) 10%, transparent)`,
        }}
      >
        {ok ? '✓' : index}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-xs font-medium text-ink">{label}</span>
        <span className="block truncate text-3xs text-faint">{detail}</span>
      </span>
      <Chip label={ok ? '通过' : blocked ? '门禁阻断' : '待检查'} tone={tone} />
    </div>
  );
}

function PaperLoadingState() {
  return (
    <div
      className="mt-4 rounded-lg border border-line bg-panel p-5"
      aria-live="polite"
      aria-label="纸面快照加载中"
    >
      <div className="flex items-center gap-3">
        <span className="q-live h-2.5 w-2.5 rounded-full bg-info" />
        <div>
          <div className="text-sm font-semibold text-ink">正在建立只读纸面快照</div>
          <div className="mt-1 text-3xs text-faint">
            正在连接纸面账本、调度器、市场估值和风险证据。
          </div>
        </div>
      </div>
      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-5">
        {['权益', '盈亏', '持仓', '对账', '正式门禁'].map(label => (
          <div key={label} className="rounded-md border border-line/70 bg-inset/40 p-3">
            <div className="text-3xs uppercase tracking-wider text-faint">{label}</div>
            <div className="mt-3 h-5 w-2/3 animate-pulse rounded bg-line/60" />
            <div className="mt-2 h-2.5 w-1/2 animate-pulse rounded bg-line/40" />
          </div>
        ))}
      </div>
      <div className="mt-4 h-28 animate-pulse rounded-lg border border-line/70 bg-inset/30" />
    </div>
  );
}

function fmtHorizon(horizonMs?: number | null): string {
  if (horizonMs == null) return '--';
  if (horizonMs < 60 * 60_000) return `${Math.round(horizonMs / 60_000)}分钟`;
  return `${Math.round(horizonMs / (60 * 60_000))}小时`;
}

type LifecycleFilter = 'ALL' | 'LOSS' | 'PROFIT' | 'NO_PNL';

function effectiveLifecycleState(row: RealtimeAlphaLifecyclePayload): string {
  return row.effective_performance_state ?? row.performance_state;
}

function executionLifecycleState(row: RealtimeAlphaLifecyclePayload): string {
  return row.execution_performance_state ?? 'NO_REALIZED_PNL';
}

function lifecycleSeries(row: RealtimeAlphaLifecyclePayload): EquitySeries[] {
  const points = (row.paper.performance_history ?? [])
    .filter(point => Number.isFinite(point.cum_return))
    .map(point => ({ t: point.as_of_ms, equity: 100 * (1 + point.cum_return) }));
  return points.length
    ? [{ label: `${row.candidate_id} 纸面指数`, points, tone: effectiveLifecycleState(row) === 'LOSS' ? 'warn' : 'pos' }]
    : [];
}

function FactorLifecycleReport({
  row,
  candidate,
}: {
  row: RealtimeAlphaLifecyclePayload;
  candidate?: RealtimeAlphaCandidatePayload;
}) {
  const history = row.paper.performance_history ?? [];
  const recent = history.slice(-8).reverse();
  const executionRecent = (row.execution.execution_history ?? []).slice(-8).reverse();
  const executionPnlAvailable = row.execution.execution_pnl_available === true;
  const executionPositions = Object.entries(row.execution.positions_lots ?? {})
    .filter(([, lots]) => lots !== 0);
  const series = useMemo(() => lifecycleSeries(row), [row]);
  const effectiveState = effectiveLifecycleState(row);
  const executionState = executionLifecycleState(row);
  const formalShadow = row.formal_shadow;
  const formalShadowState = formalShadow?.status ?? 'NOT_AVAILABLE';
  const realized = row.paper.n_realizations > 0;
  const stages = [
    { label: '已登记', reached: true },
    { label: '已评估', reached: row.research.evaluation_status === 'EVALUATED' },
    { label: '信号影子', reached: row.paper.n_decisions > 0 },
    { label: '工程纸面', reached: row.execution.n_executions > 0 },
    { label: '正式通过', reached: row.research.gate_pass },
  ];
  const blockers = [...(row.research.gate_reasons ?? []), ...row.errors];

  return (
    <div className="border-t border-line bg-panel/70 p-4" data-testid="factor-lifecycle-report">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-3xs font-semibold tracking-[0.14em] text-faint">因子生命周期报告 · SSE 实时</div>
          <div className="mt-1 break-all text-sm font-semibold text-ink">{row.candidate_id}</div>
          <div className="mt-1 text-3xs text-muted">
            {row.family_id ?? '--'} / {row.mechanism ?? '--'} · {fmtHorizon(row.horizon_ms)} · 最近事件 {fmtTime(row.last_event_ms)}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Chip label={statusLabel(row.pipeline_stage)} tone="info" />
          <Chip
            label={statusLabel(effectiveState)}
            tone={effectiveState === 'LOSS' ? 'neg' : effectiveState === 'PROFIT' ? 'pos' : 'muted'}
          />
          <Chip
            label={`影子 ${statusLabel(row.performance_state)}`}
            tone={row.performance_state === 'LOSS' ? 'neg' : row.performance_state === 'PROFIT' ? 'pos' : 'muted'}
          />
          <Chip
            label={`执行 ${statusLabel(executionState)}`}
            tone={executionState === 'LOSS' ? 'neg' : executionState === 'PROFIT' ? 'pos' : 'muted'}
          />
          <Chip
            label={`正式影子 ${statusLabel(formalShadowState)}`}
            tone={formalShadowState === 'MATURE' ? 'pos' : formalShadowState === 'RUNNING' ? 'info' : 'warn'}
          />
          <Chip label="不可晋级" tone="warn" />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="factor lifecycle stages">
        {stages.map((stage, index) => (
          <React.Fragment key={stage.label}>
            {index ? <span className="text-line-strong">→</span> : null}
            <Chip label={stage.label} tone={stage.reached ? (stage.label === '正式通过' ? 'pos' : 'info') : 'muted'} />
          </React.Fragment>
        ))}
      </div>

      <div className="mt-4 grid grid-cols-2 gap-y-4 rounded-lg border border-line bg-inset/35 px-4 py-4 lg:grid-cols-6">
        <Metric label="研究收益" value={fmtPct(row.research.net_return)} detail={`${row.research.n_periods ?? 0} 个观测`} tone={(row.research.net_return ?? 0) >= 0 ? 'pos' : 'neg'} />
        <Metric label="留出集夏普" value={row.research.holdout_net_sharpe?.toFixed(2) ?? '—'} detail={row.research.gate_pass ? '正式通过' : '未通过'} tone={row.research.gate_pass ? 'pos' : 'warn'} />
        <Metric label="影子收益" value={fmtRealizedPct(row.paper.cum_return, realized)} detail={`${row.paper.n_realizations}/${row.paper.n_decisions} 已兑现`} tone={row.performance_state === 'LOSS' ? 'neg' : row.performance_state === 'PROFIT' ? 'pos' : 'muted'} />
        <Metric label="纸面回撤" value={fmtPct(row.paper.max_drawdown == null ? null : -row.paper.max_drawdown)} detail={`${row.paper.n_pending} 个待兑现`} tone={(row.paper.max_drawdown ?? 0) > 0 ? 'neg' : 'muted'} />
        <Metric label="模拟执行" value={row.execution.n_executions} detail={`${row.execution.n_orders} 笔订单 / ${row.execution.n_fill_slices} 个成交片段`} tone="info" />
        <Metric label="执行成本" value={fmtUsd(row.execution.execution_cost)} detail={`${row.execution.n_missed_execution_windows} 个错过窗口`} tone="warn" />
        <Metric
          label="正式影子"
          value={formalShadow ? formalShadow.n_realizations : '--'}
          detail={formalShadow ? `${statusLabel(formalShadowState)} / 独立账本` : '权威快照不可用'}
          tone={formalShadowState === 'MATURE' ? 'pos' : formalShadowState === 'RUNNING' ? 'info' : 'warn'}
        />
        <Metric
          label="执行净盈亏"
          value={fmtUsd(executionPnlAvailable ? row.execution.cumulative_net_pnl : null)}
          detail={executionPnlAvailable ? `${executionRecent.length} 个近期批次 / 累计` : '核算尚未完成'}
          tone={!executionPnlAvailable ? 'warn' : (row.execution.cumulative_net_pnl ?? 0) < 0 ? 'neg' : 'pos'}
        />
        <Metric
          label="执行权益"
          value={fmtUsd(executionPnlAvailable ? row.execution.last_equity : null)}
          detail={executionPnlAvailable ? '最近有效模拟批次' : '等待有效盈亏'}
          tone="info"
        />
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,1fr)]">
        <div className="rounded-lg border border-line bg-inset/35 p-3">
          <div className="mb-2 flex items-center justify-between gap-2 text-3xs text-faint">
            <span className="font-semibold tracking-[0.12em]">已兑现纸面业绩指数</span>
            <span>100 = 首次决策 · 已扣模型成本</span>
          </div>
          {series.length ? (
            <EquityCurveChart series={series} height={190} />
          ) : (
            <div className="grid h-[190px] place-items-center text-xs text-faint">尚无已兑现纸面历史。</div>
          )}
        </div>

        <div className="overflow-hidden rounded-lg border border-line bg-inset/35">
          <div className="border-b border-line px-3 py-2 text-3xs font-semibold uppercase tracking-[0.12em] text-faint">
            最近已兑现周期
          </div>
          <table className="w-full text-left text-3xs" aria-label="所选因子的已兑现业绩历史">
            <thead className="border-b border-line/70 text-faint">
              <tr><th className="px-3 py-2 font-medium">时间</th><th className="px-3 py-2 text-right font-medium">本期</th><th className="px-3 py-2 text-right font-medium">累计</th><th className="px-3 py-2 text-right font-medium">回撤</th></tr>
            </thead>
            <tbody className="divide-y divide-line/60">
              {recent.length ? recent.map(point => (
                <tr key={point.as_of_ms}>
                  <td className="px-3 py-2 text-muted">{fmtTime(point.as_of_ms)}</td>
                  <td className={`px-3 py-2 text-right ticker-num ${signedClass(point.period_return)}`}>{fmtPct(point.period_return)}</td>
                  <td className={`px-3 py-2 text-right ticker-num ${signedClass(point.cum_return)}`}>{fmtPct(point.cum_return)}</td>
                  <td className="px-3 py-2 text-right ticker-num text-neg">{fmtPct(-point.drawdown)}</td>
                </tr>
              )) : <tr><td colSpan={4} className="px-3 py-5 text-center text-faint">正在收集首次兑现结果。</td></tr>}
            </tbody>
          </table>
        </div>

        <div className="overflow-hidden rounded-lg border border-line bg-inset/35 xl:col-span-2" data-testid="execution-paper-accounting">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2 text-3xs text-faint">
            <span className="font-semibold tracking-[0.12em]">工程纸面核算 · 模拟 SOR</span>
            <span>{executionPnlAvailable ? `${row.execution.n_executions} 个有效批次` : '正在收集有效核算'}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-3xs" aria-label="所选因子的工程纸面核算历史">
              <thead className="border-b border-line/70 text-faint">
                <tr><th className="px-3 py-2 font-medium">时间</th><th className="px-3 py-2 text-right font-medium">批次净盈亏</th><th className="px-3 py-2 text-right font-medium">累计</th><th className="px-3 py-2 text-right font-medium">TCA 成本</th><th className="px-3 py-2 text-right font-medium">换手金额</th></tr>
              </thead>
              <tbody className="divide-y divide-line/60">
                {executionRecent.length ? executionRecent.map(point => (
                  <tr key={point.as_of_ms}>
                    <td className="px-3 py-2 text-muted">{fmtTime(point.as_of_ms)}</td>
                    <td className={`px-3 py-2 text-right ticker-num ${signedClass(point.net_pnl)}`}>{fmtUsd(point.net_pnl)}</td>
                    <td className={`px-3 py-2 text-right ticker-num ${signedClass(point.cumulative_net_pnl)}`}>{fmtUsd(point.cumulative_net_pnl)}</td>
                    <td className="px-3 py-2 text-right ticker-num text-warn">{fmtUsd(point.execution_cost)}</td>
                    <td className="px-3 py-2 text-right ticker-num text-muted">{fmtUsd(point.turnover)}</td>
                  </tr>
                )) : <tr><td colSpan={5} className="px-3 py-5 text-center text-faint">尚无有效执行盈亏；执行次数不等于业绩。</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="border-t border-line/70 px-3 py-2 text-3xs text-muted">
            仓位手数 {executionPositions.length ? executionPositions.map(([instrument, lots]) => `${instrument} ${lots > 0 ? '+' : ''}${lots}`).join(' · ') : '空仓 / 无有效快照'} · 对账残差必须为零 · 非正式账户盈亏
          </div>
        </div>
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div className="rounded-md border border-line bg-inset/35 px-3 py-2 text-3xs text-muted">
          <div className="font-semibold tracking-[0.1em] text-faint">身份 / 血缘</div>
          <div className="mt-1">父代 {candidate?.parent_candidate_id ?? '根节点'} · 代数 {candidate?.adaptive_generation ?? 0}</div>
          <div className="mt-1">变异 {candidate?.mutation ?? '无'} · 新 OOS {candidate?.fresh_oos_required ? '必需' : '基线'}</div>
        </div>
        <div className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-3xs text-muted">
          <div className="font-semibold tracking-[0.1em] text-faint">门禁报告</div>
          <div className="mt-1 break-words">{blockers.length ? blockers.join(' · ') : '未报告阻断项；正式授权仍由人工门控制。'}</div>
        </div>
      </div>
      <div className="mt-2 text-3xs text-faint">
        来源 {row.paper.pnl_source} · 仅工程证据 · 晋级{row.promotion_eligible ? '允许' : '阻断'} · 实盘{row.live_allowed ? '允许' : '关闭'}
      </div>
    </div>
  );
}

function AutonomousAlphaPanel({ snapshot }: { snapshot?: RealtimeAlphaAutonomyPayload }) {
  const [filter, setFilter] = React.useState<LifecycleFilter>('ALL');
  const [selectedCandidateId, setSelectedCandidateId] = React.useState<string | null>(null);
  const running = Boolean(
    snapshot?.boundary_valid && snapshot.autonomy.autonomous_search_running,
  );
  const spanDays = (snapshot?.data_admission.common_span_ms ?? 0) / 86_400_000;
  const candidates = snapshot?.top_candidates ?? [];
  const lifecycle = snapshot?.lifecycle ?? [];
  const feedback = snapshot?.feedback;
  const profitable = lifecycle.filter(row => effectiveLifecycleState(row) === 'PROFIT').length;
  const losing = lifecycle.filter(row => effectiveLifecycleState(row) === 'LOSS').length;
  const noPnl = lifecycle.filter(row => !['LOSS', 'PROFIT'].includes(effectiveLifecycleState(row))).length;
  const executionProfitable = lifecycle.filter(row => executionLifecycleState(row) === 'PROFIT').length;
  const executionLosing = lifecycle.filter(row => executionLifecycleState(row) === 'LOSS').length;
  const performanceRank: Record<string, number> = { LOSS: 0, PROFIT: 1, FLAT: 2, NO_REALIZED_PNL: 3 };
  const filteredLifecycle = lifecycle
    .filter(row => filter === 'ALL'
      || (filter === 'NO_PNL'
        ? !['LOSS', 'PROFIT'].includes(effectiveLifecycleState(row))
        : effectiveLifecycleState(row) === filter))
    .slice()
    .sort((a, b) => (performanceRank[effectiveLifecycleState(a)] ?? 9) - (performanceRank[effectiveLifecycleState(b)] ?? 9)
      || (a.execution.cumulative_net_pnl ?? a.paper.cum_return ?? 0) - (b.execution.cumulative_net_pnl ?? b.paper.cum_return ?? 0));
  const effectiveSelectedId = selectedCandidateId
    ?? lifecycle.find(row => effectiveLifecycleState(row) === 'LOSS')?.candidate_id
    ?? lifecycle[0]?.candidate_id
    ?? null;
  const selectedLifecycle = lifecycle.find(row => row.candidate_id === effectiveSelectedId);
  const selectedCandidate = snapshot?.candidates?.find(row => row.candidate_id === effectiveSelectedId)
    ?? snapshot?.top_candidates.find(row => row.candidate_id === effectiveSelectedId);
  const capacity = snapshot?.executable_depth_capacity;
  const engineeringPaperRunning = Boolean(
    snapshot?.forward_shadow.verdict.engineering_shadow_running,
  );
  const engineeringTrackCount = snapshot?.forward_shadow.admitted_candidates?.length
    ?? Object.keys(snapshot?.forward_shadow.tracks ?? {}).length;
  const executionPaperRunning = Boolean(
    snapshot?.execution_paper?.verdict.engineering_execution_paper_running,
  );
  const executionTrackCount = snapshot?.execution_paper?.candidate_count ?? 0;
  const executionCount = Object.values(snapshot?.execution_paper?.tracks ?? {})
    .reduce((total, track) => total + (track.n_executions ?? 0), 0);
    const missedExecutionWindows = Object.values(snapshot?.execution_paper?.tracks ?? {})
      .reduce((total, track) => total + (track.n_missed_execution_windows ?? 0), 0);
    const noActionDecisions = Object.values(snapshot?.execution_paper?.tracks ?? {})
      .reduce((total, track) => total + (track.n_no_actions ?? 0), 0);
  const graduationGates = snapshot?.forward_shadow.handoff?.graduation_blockers ?? [];
  const gates = snapshot?.boundary_valid
    ? snapshot.remaining_gates
    : snapshot?.blockers ?? ['AUTONOMY_SNAPSHOT_MISSING'];
  const statusTone: Tone = running ? 'pos' : snapshot?.snapshot_present ? 'neg' : 'warn';

  return (
    <Panel
      title="自主 Alpha 发现"
      eyebrow="实时微观结构 → 预注册试验 → 锁定验证"
      right={
        <div className="flex flex-wrap items-center gap-2">
          <Chip label={snapshot?.autonomy.requires_developer_ai ? '依赖开发 IDE' : '系统原生'} tone="info" />
          <Chip
            label={executionPaperRunning
              ? `执行纸面 ${executionTrackCount}`
              : engineeringPaperRunning
                ? `信号影子 ${engineeringTrackCount}`
              : snapshot?.forward_shadow.status ? statusLabel(snapshot.forward_shadow.status) : '无纸面快照'}
            tone={executionPaperRunning ? 'pos' : engineeringPaperRunning ? 'info' : 'warn'}
          />
          <Chip label={snapshot?.status ? statusLabel(snapshot.status) : '无快照'} tone={statusTone} />
        </div>
      }
      bodyClass="p-4"
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Metric
          label="自治状态"
          value={running ? '运行中' : '门禁阻断'}
          detail="系统每小时自动运行"
          tone={statusTone}
        />
        <Metric
          label="实时历史"
          value={`${spanDays.toFixed(2)}天`}
          detail={`${snapshot?.data_admission.common_instruments?.length ?? 0} 个标的`}
          tone={spanDays >= 30 ? 'pos' : 'warn'}
        />
        <Metric
          label="预注册试验"
          value={snapshot?.funnel.pre_registered ?? 0}
          detail={`${snapshot?.families.length ?? 0} 个因子族`}
          tone="special"
        />
        <Metric
          label="诊断线索"
          value={snapshot?.funnel.diagnostic_leads ?? 0}
          detail={`${snapshot?.funnel.evaluated ?? 0} 个已评估 · ${engineeringTrackCount} 条信号轨道`}
          tone={(snapshot?.funnel.diagnostic_leads ?? 0) > 0 ? 'info' : 'muted'}
        />
        <Metric
          label="正式通过"
          value={snapshot?.funnel.formal_passes ?? 0}
          detail={`全局试验 ${snapshot?.global_trial_count ?? '--'}`}
          tone={(snapshot?.funnel.formal_passes ?? 0) > 0 ? 'pos' : 'warn'}
        />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1.8fr)_minmax(240px,1fr)]">
        <div className="overflow-hidden rounded-lg border border-line bg-inset/35">
          <div className="border-b border-line px-3 py-2 text-3xs font-semibold uppercase tracking-[0.12em] text-faint">
            领先诊断线索 — 尚未获批的 Alpha
          </div>
          {candidates.length ? candidates.slice(0, 4).map(candidate => (
            <div
              key={candidate.candidate_id}
              className="grid grid-cols-[minmax(0,1fr)_55px_72px_72px] items-center gap-2 border-b border-line/70 px-3 py-2 last:border-b-0"
            >
              <div className="min-w-0">
                <div className="truncate text-xs font-medium text-ink">{candidate.candidate_id}</div>
                <div className="truncate text-3xs text-faint">{candidate.family_id} · {candidate.status}</div>
              </div>
              <span className="ticker-num text-right text-3xs text-muted">{fmtHorizon(candidate.horizon_ms)}</span>
              <span className="ticker-num text-right text-3xs text-muted">
                SR {candidate.holdout_net_sharpe?.toFixed(2) ?? '--'}
              </span>
              <span className={`ticker-num text-right text-3xs ${(candidate.net_return ?? 0) >= 0 ? 'text-pos' : 'text-neg'}`}>
                {fmtPct(candidate.net_return)}
              </span>
            </div>
          )) : (
            <div className="px-3 py-6 text-center text-xs text-faint">尚无已评估候选快照。</div>
          )}
        </div>

        <div className="rounded-lg border border-line bg-inset/35 p-3">
          <div className="text-3xs font-semibold tracking-[0.12em] text-faint">当前硬门禁</div>
          <div className="mt-2 space-y-1.5">
            {gates.slice(0, 4).map(gate => (
              <div key={gate} className="break-words text-3xs text-muted">• {gate}</div>
            ))}
          </div>
          <div className="mt-3 border-t border-line pt-2 text-3xs text-faint">
            数据准入 {snapshot?.data_admission.status ? statusLabel(snapshot.data_admission.status) : '无快照'} · 注册链{' '}
            {snapshot?.registry.chain_valid ? '有效' : '受阻'}
            {' '}· 信号账本 {snapshot?.forward_shadow.ledger.chain_valid ? '有效' : '等待中'}
          </div>
          <div className="mt-2 text-3xs text-faint">
            信号影子 {engineeringPaperRunning ? '运行中' : '等待中'} · 正式毕业{' '}
            {graduationGates.length ? graduationGates.join(' · ') : '门禁已清除'}
          </div>
          <div className="mt-2 text-3xs text-faint">
            执行纸面 {executionPaperRunning ? '运行中' : snapshot?.execution_paper?.status ? statusLabel(snapshot.execution_paper.status) : '等待中'} ·{' '}
              {executionTrackCount} 个账户 · {executionCount} 次路由 · {noActionDecisions} 次无动作 ·{' '}
              {missedExecutionWindows} 个已审计错过窗口 · 手数权威{' '}
            {snapshot?.execution_paper?.lot_authority.instrument_count ?? 0} 个标的 · 账本{' '}
            {snapshot?.execution_paper?.ledger.chain_valid ? '有效' : '等待中'}
          </div>
          <div className="mt-2 text-3xs text-faint">
            容量上限 {fmtUsd(capacity?.max_supported_aum_usd)} · 影子规模{' '}
            {fmtUsd(capacity?.shadow_aum_usd)} · {(capacity?.history_span_ms ?? 0) / 86_400_000 >= 30
              ? '30 天证据就绪'
              : `${((capacity?.history_span_ms ?? 0) / 86_400_000).toFixed(2)}天 / 30天`}
          </div>
        </div>
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border border-line bg-inset/35">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2 text-3xs text-muted">
          <Chip
            label={feedback?.status ? statusLabel(feedback.status) : '反馈等待中'}
            tone={(feedback?.active_adaptive_candidates ?? 0) > 0 ? 'special' : 'info'}
          />
          <span>
            学习反馈 · {feedback?.consumed_realizations ?? 0} 个已兑现结果 ·{' '}
            {feedback?.active_adaptive_candidates ?? 0} 个自适应挑战者
          </span>
          <span className="ml-auto">
            {(feedback?.families ?? []).map(row => `${row.family_id}:${row.action}`).join(' · ') || '正在积累因子族预算'}
          </span>
        </div>
        {(feedback?.proposal_blockers?.length ?? 0) > 0 && (
          <div className="border-b border-line px-3 py-1.5 text-3xs text-faint">
            自适应分配器 {'·'} {feedback?.proposal_blockers?.join(' · ')} {'·'}{' '}
            剩余 {feedback?.candidate_budget_remaining ?? 0} 个提案名额
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <div className="text-3xs font-semibold uppercase tracking-[0.12em] text-faint">
            全量 Alpha 生命周期与实时业绩
          </div>
          <Chip label={`${lifecycle.length} 个因子`} tone="info" />
          <Chip label={`${profitable} 个盈利`} tone={profitable ? 'pos' : 'muted'} />
          <Chip label={`${losing} 个亏损`} tone={losing ? 'neg' : 'muted'} />
          <Chip label={`${noPnl} 个无盈亏`} tone="muted" />
          <span className="text-3xs text-faint">执行：{executionProfitable} 盈利 / {executionLosing} 亏损</span>
          <span className="ml-auto text-3xs text-faint">SSE · 优先使用执行净盈亏，不可用时回退影子业绩</span>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2" aria-label="factor performance filters">
          {([
            ['ALL', `全部 ${lifecycle.length}`],
            ['LOSS', `亏损 ${losing}`],
            ['PROFIT', `盈利 ${profitable}`],
            ['NO_PNL', `无盈亏 ${noPnl}`],
          ] as Array<[LifecycleFilter, string]>).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setFilter(value)}
              className={`rounded border px-2 py-1 text-3xs font-semibold tracking-[0.08em] ${
                filter === value ? 'border-info/60 bg-info/10 text-info' : 'border-line bg-inset/40 text-muted hover:text-ink'
              }`}
              aria-pressed={filter === value}
            >
              {label}
            </button>
          ))}
          <span className="ml-auto text-3xs text-faint">亏损优先排序 · 点击因子查看完整报告</span>
        </div>
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full min-w-[940px] text-left text-xs" aria-label="实时 Alpha 生命周期业绩">
            <thead className="sticky top-0 border-b border-line bg-panel text-3xs uppercase tracking-[0.1em] text-faint">
              <tr>
                <th className="px-3 py-2 font-medium">因子</th>
                <th className="px-3 py-2 font-medium">因子族 / 周期</th>
                <th className="px-3 py-2 font-medium">生命周期</th>
                <th className="px-3 py-2 text-right font-medium">研究收益</th>
                <th className="px-3 py-2 text-right font-medium">影子盈亏</th>
                <th className="px-3 py-2 text-right font-medium">已兑现</th>
                <th className="px-3 py-2 text-right font-medium">执行</th>
                <th className="px-3 py-2 font-medium">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/70">
              {filteredLifecycle.length ? filteredLifecycle.map(row => (
                <tr key={row.candidate_id} className={row.candidate_id === effectiveSelectedId ? 'bg-info/5' : undefined}>
                  <td className="max-w-[250px] px-3 py-2 font-medium text-ink" title={row.candidate_id}>
                    <button
                      type="button"
                      className="block max-w-full truncate text-left underline-offset-2 hover:text-info hover:underline"
                      onClick={() => setSelectedCandidateId(row.candidate_id)}
                      aria-label={`查看 ${row.candidate_id} 生命周期报告`}
                    >
                      {row.candidate_id}
                    </button>
                  </td>
                  <td className="px-3 py-2 text-muted">{row.family_id ?? '--'} · {fmtHorizon(row.horizon_ms)}</td>
                  <td className="px-3 py-2"><Chip label={statusLabel(row.pipeline_stage)} tone="info" /></td>
                  <td className={`px-3 py-2 text-right ticker-num ${signedClass(row.research.net_return)}`}>
                    {fmtPct(row.research.net_return)}
                  </td>
                  <td className={`px-3 py-2 text-right ticker-num ${signedClass(row.paper.cum_return)}`}>
                    <div>{fmtRealizedPct(row.paper.cum_return, row.paper.n_realizations > 0)}</div>
                    <div className="mt-0.5 text-3xs text-faint">
                      {row.paper.n_realizations > 0
                        ? `最近一期 ${fmtPct(row.paper.performance_history?.[Math.max(0, (row.paper.performance_history?.length ?? 1) - 1)]?.period_return)}`
                        : '无已兑现纸面盈亏'}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right ticker-num text-muted">
                    {row.paper.n_realizations}/{row.paper.n_decisions}
                  </td>
                  <td className="px-3 py-2 text-right ticker-num text-muted">
                    <div>{row.execution.n_executions} 次运行 · {row.execution.n_orders} 笔订单</div>
                    <div className="mt-0.5 text-3xs text-faint">
                      净盈亏 {row.execution.execution_pnl_available ? fmtUsd(row.execution.cumulative_net_pnl) : '等待核算'}
                    </div>
                    <div className={`mt-0.5 text-3xs ${executionLifecycleState(row) === 'LOSS' ? 'text-neg' : executionLifecycleState(row) === 'PROFIT' ? 'text-pos' : 'text-faint'}`}>
                      {statusLabel(executionLifecycleState(row))}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <Chip
                      label={statusLabel(effectiveLifecycleState(row))}
                      tone={effectiveLifecycleState(row) === 'LOSS' ? 'neg' : effectiveLifecycleState(row) === 'PROFIT' ? 'pos' : 'muted'}
                    />
                    <div className="mt-1 text-3xs text-faint">影子 {statusLabel(row.performance_state)}</div>
                  </td>
                </tr>
              )) : (
                <tr><td colSpan={8} className="px-3 py-5 text-center text-faint">没有符合当前业绩筛选条件的因子。</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {selectedLifecycle ? <FactorLifecycleReport row={selectedLifecycle} candidate={selectedCandidate} /> : null}
      </div>
    </Panel>
  );
}

function StrategyRow({
  alpha,
  value,
  forwardPeriods,
  periodsToGate,
  riskReasons,
}: {
  alpha: string;
  value: PaperRealtimeAlpha;
  forwardPeriods?: number | null;
  periodsToGate?: number | null;
  riskReasons: string[];
}) {
  const positions = Object.entries(value.positions ?? {}).filter(([, qty]) => qty !== 0);
  const pnl = value.net_pnl;
  const reconciled = Math.abs(value.reconciliation_residual ?? Number.POSITIVE_INFINITY) < 1e-6;
  return (
    <div className="grid gap-3 border-b border-line/70 py-3 last:border-b-0 md:grid-cols-[minmax(150px,1.25fr)_repeat(4,minmax(92px,1fr))] md:items-center">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-ink">{alpha}</span>
          <Chip label={value.status ? statusLabel(value.status) : '纸面'} tone="info" />
        </div>
        <div className="mt-1 text-3xs text-faint">{positions.length} 个活跃持仓 · 公共行情估值</div>
      </div>
      <div>
        <div className="text-3xs tracking-wider text-faint">权益</div>
        <div className="ticker-num mt-1 text-sm text-ink">{fmtUsd(value.equity)}</div>
      </div>
      <div>
        <div className="text-3xs tracking-wider text-faint">净盈亏</div>
        <div className={`ticker-num mt-1 text-sm ${signedClass(pnl)}`}>{fmtUsd(pnl)}</div>
      </div>
      <div>
        <div className="text-3xs tracking-wider text-faint">收益率</div>
        <div className={`ticker-num mt-1 text-sm ${signedClass(value.return_on_aum)}`}>
          {fmtPct(value.return_on_aum)}
        </div>
      </div>
      <div>
        <div className="text-3xs tracking-wider text-faint">前向门禁</div>
        <div className="ticker-num mt-1 text-sm text-warn">{forwardPeriods ?? 0} / {(forwardPeriods ?? 0) + (periodsToGate ?? 0)}</div>
        <div className="mt-1 truncate text-3xs text-faint">
          {riskReasons.length ? riskReasons.join(' · ') : reconciled ? '已对账' : '待检查对账'}
        </div>
      </div>
    </div>
  );
}

type OfficialPaperHistoryPoint = {
  observed_at: number;
  equity: number;
  net_pnl: number;
};

function officialPaperHistory(
  alpha: string,
  history: PaperRealtimeHistoryPoint[],
): OfficialPaperHistoryPoint[] {
  return history
    .filter(point => point.alpha_id === alpha
      && Number.isFinite(point.observed_at)
      && Number.isFinite(point.equity)
      && Number.isFinite(point.net_pnl))
    .map(point => ({
      observed_at: point.observed_at,
      equity: point.equity as number,
      net_pnl: point.net_pnl as number,
    }))
    .sort((a, b) => a.observed_at - b.observed_at);
}

function officialPaperDrawdown(points: OfficialPaperHistoryPoint[]): number | null {
  let peak: number | null = null;
  let maxDrawdown = 0;
  for (const point of points) {
    peak = peak == null ? point.equity : Math.max(peak, point.equity);
    if (peak > 0) maxDrawdown = Math.max(maxDrawdown, (peak - point.equity) / peak);
  }
  return points.length ? maxDrawdown : null;
}

function OfficialFactorLifecycleReport({
  alpha,
  value,
  backtest,
  history,
  candidate,
}: {
  alpha: string;
  value: PaperRealtimeAlpha;
  backtest?: PaperRealtimeBacktest;
  history: PaperRealtimeHistoryPoint[];
  candidate?: AlphaPipelineCandidate;
}) {
  const points = officialPaperHistory(alpha, history);
  const currentPaper = hasRealizedPaperPnl(value);
  const pending = !currentPaper;
  const paperDrawdown = officialPaperDrawdown(points);
  const series: EquitySeries[] = points.length
    ? [{
      label: `${alpha} 正式纸面权益`,
      points: points.map(point => ({ t: point.observed_at, equity: point.equity })),
      tone: (value.net_pnl ?? 0) < 0 ? 'warn' : 'pos',
    }]
    : [];
  const blockers = candidate?.blockers ?? [];
  const performanceState = pending
    ? 'NO_REALIZED_PNL'
    : (value.net_pnl ?? 0) > 0 ? 'PROFIT' : (value.net_pnl ?? 0) < 0 ? 'LOSS' : 'FLAT';
  const stages = [
    { label: '研究回测', reached: (backtest?.n_periods ?? 0) > 0 },
    { label: '独立验证', reached: candidate?.independent_evaluation?.status === 'VERIFIED' },
    { label: '纸面估值', reached: currentPaper },
    { label: '前向成熟', reached: Boolean(candidate?.execution_paper) },
    { label: '实盘', reached: false },
  ];
  const gateRows = [
    ['HISTORICAL_OOS', '历史样本外'],
    ['SIGNAL_SHADOW', '信号影子'],
    ['RESEARCH_RISK', '研究风险'],
    ['EXECUTION_PAPER', '工程纸面'],
    ['LIFECYCLE', '生命周期'],
  ] as const;

  return (
    <div className="border-t border-line bg-panel/70 p-4" data-testid="official-factor-lifecycle-report">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-3xs font-semibold tracking-[0.14em] text-faint">正式因子生命周期报告 · SSE 实时</div>
          <div className="mt-1 break-all text-sm font-semibold text-ink">{alpha}</div>
          <div className="mt-1 text-3xs text-muted">
            {candidate?.family_id ?? '未分类'} / {candidate?.definition_kind ?? '纸面实时估值'} · 最近估值 {fmtTime(value.observed_at)}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Chip label={value.status ? statusLabel(value.status) : '无状态'} tone={pending ? 'warn' : 'info'} />
          <Chip label={statusLabel(performanceState)} tone={performanceState === 'LOSS' ? 'neg' : performanceState === 'PROFIT' ? 'pos' : 'muted'} />
          <Chip label="实盘关闭" tone="neg" />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="official factor lifecycle stages">
        {stages.map((stage, index) => (
          <React.Fragment key={stage.label}>
            {index ? <span className="text-line-strong">→</span> : null}
            <Chip label={stage.label} tone={stage.reached ? 'info' : 'muted'} />
          </React.Fragment>
        ))}
      </div>

      <div className="mt-4 grid grid-cols-2 gap-y-4 rounded-lg border border-line bg-inset/35 px-4 py-4 lg:grid-cols-6">
        <Metric label="研究收益" value={fmtPct(backtest?.total_return)} detail={`${backtest?.n_periods ?? 0} 个回测周期`} tone={(backtest?.total_return ?? 0) >= 0 ? 'pos' : 'neg'} />
        <Metric label="研究夏普" value={backtest?.sharpe_vt?.toFixed(2) ?? '—'} detail={backtest?.research_risk_decision ? statusLabel(backtest.research_risk_decision) : '无风险裁决'} tone={backtest?.research_risk_decision === 'RISK_APPROVED' ? 'pos' : 'warn'} />
        <Metric label="纸面净盈亏" value={fmtUsd(currentPaper ? value.net_pnl : null)} detail={currentPaper ? `${value.n_fills ?? 0} 个成交 · ${points.length} 次估值` : '无已兑现纸面盈亏'} tone={performanceState === 'LOSS' ? 'neg' : performanceState === 'PROFIT' ? 'pos' : 'muted'} />
        <Metric label="纸面收益率" value={fmtPct(currentPaper ? value.return_on_aum : null)} detail={`${Object.keys(value.positions ?? {}).filter(symbol => value.positions?.[symbol] !== 0).length} 个活跃持仓`} tone={performanceState === 'LOSS' ? 'neg' : performanceState === 'PROFIT' ? 'pos' : 'muted'} />
        <Metric label="纸面回撤" value={fmtPct(paperDrawdown == null ? null : -paperDrawdown)} detail={points.length ? '基于保留估值' : '无历史'} tone={paperDrawdown ? 'neg' : 'muted'} />
        <Metric label="资金费 / 成本" value={fmtUsd(currentPaper ? (value.funding_pnl ?? 0) - (value.costs ?? 0) : null)} detail={currentPaper ? `${value.funding_events ?? 0} 个资金费事件` : '无已兑现纸面盈亏'} tone="info" />
      </div>

      <div className="mt-3 rounded-lg border border-line bg-inset/25 px-3 py-3" data-testid="official-factor-gate-matrix">
        <div className="mb-2 text-3xs font-semibold tracking-[0.12em] text-faint">门禁证据矩阵</div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {gateRows.map(([key, label]) => {
            const gate = candidate?.gates?.[key];
            const status = gate?.status ?? 'NOT_AVAILABLE';
            return (
              <div key={key} className="min-w-0 rounded-md border border-line bg-panel/60 px-2.5 py-2">
                <div className="truncate text-3xs uppercase tracking-[0.08em] text-faint">{label}</div>
                <Chip label={statusLabel(status)} tone={gateStatusTone(status)} />
                <div className="mt-1 truncate text-3xs text-muted" title={gate?.reference ?? undefined}>
                  {gate?.observed_at_ms != null ? fmtTime(gate.observed_at_ms) : '无证据时间戳'}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 rounded-lg border border-line bg-inset/25 px-4 py-3 md:grid-cols-5" data-testid="official-paper-pnl-breakdown">
        <Metric label="已实现盈亏" value={fmtUsd(currentPaper ? value.realized_pnl : null)} detail={currentPaper ? '已平仓成交' : '纸面待成交'} tone={currentPaper && (value.realized_pnl ?? 0) < 0 ? 'neg' : 'muted'} />
        <Metric label="未实现盈亏" value={fmtUsd(currentPaper ? value.unrealized_pnl : null)} detail={currentPaper ? '最新公共行情估值' : '纸面待成交'} tone={currentPaper && (value.unrealized_pnl ?? 0) < 0 ? 'neg' : currentPaper ? 'pos' : 'muted'} />
        <Metric label="资金费盈亏" value={fmtUsd(currentPaper ? value.funding_pnl : null)} detail={currentPaper ? `${value.funding_events ?? 0} 个事件` : '纸面待成交'} tone={currentPaper && (value.funding_pnl ?? 0) < 0 ? 'neg' : currentPaper ? 'pos' : 'muted'} />
        <Metric label="交易成本" value={fmtUsd(currentPaper ? value.costs : null)} detail={currentPaper ? '模型化执行成本' : '纸面待成交'} tone={currentPaper ? 'warn' : 'muted'} />
        <Metric label="对账残差" value={fmtUsd(currentPaper ? value.reconciliation_residual : null)} detail={currentPaper ? '必须等于 0' : '纸面待成交'} tone={currentPaper && Math.abs(value.reconciliation_residual ?? Number.POSITIVE_INFINITY) > 1e-6 ? 'neg' : currentPaper ? 'pos' : 'muted'} />
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,1fr)]">
        <div className="rounded-lg border border-line bg-inset/35 p-3">
          <div className="mb-2 flex items-center justify-between gap-2 text-3xs text-faint">
            <span className="font-semibold tracking-[0.12em]">保留的纸面权益</span>
            <span>公共行情估值 · 不提交订单</span>
          </div>
          {series.length ? (
            <EquityCurveChart series={series} height={190} yFormat={point => fmtUsd(point)} />
          ) : (
            <div className="grid h-[190px] place-items-center text-xs text-faint">尚无保留的纸面历史。</div>
          )}
        </div>

        <div className="overflow-hidden rounded-lg border border-line bg-inset/35">
          <div className="border-b border-line px-3 py-2 text-3xs font-semibold tracking-[0.12em] text-faint">最近纸面估值</div>
          <table className="w-full text-left text-3xs" aria-label="正式因子纸面历史">
            <thead className="border-b border-line/70 text-faint">
              <tr><th className="px-3 py-2 font-medium">时间</th><th className="px-3 py-2 text-right font-medium">净盈亏</th><th className="px-3 py-2 text-right font-medium">权益</th></tr>
            </thead>
            <tbody className="divide-y divide-line/60">
              {points.slice(-8).reverse().length ? points.slice(-8).reverse().map(point => (
                <tr key={point.observed_at}>
                  <td className="px-3 py-2 text-muted">{fmtTime(point.observed_at)}</td>
                  <td className={`px-3 py-2 text-right ticker-num ${signedClass(point.net_pnl)}`}>{fmtUsd(point.net_pnl)}</td>
                  <td className="px-3 py-2 text-right ticker-num text-muted">{fmtUsd(point.equity)}</td>
                </tr>
              )) : <tr><td colSpan={3} className="px-3 py-5 text-center text-faint">正在收集首次纸面估值。</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div className="rounded-md border border-line bg-inset/35 px-3 py-2 text-3xs text-muted">
          <div className="font-semibold tracking-[0.1em] text-faint">身份 / 成熟度</div>
          <div className="mt-1">因子族 {candidate?.family_id ?? '未分类'} · 定义 {candidate?.definition_kind ?? '未知'}</div>
          <div className="mt-1">成熟度 {candidate?.maturity?.observed_periods ?? 0}/{candidate?.maturity?.required_periods ?? '—'} · 状态 {value.status ? statusLabel(value.status) : '未知'}</div>
        </div>
        <div className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-3xs text-muted">
          <div className="font-semibold tracking-[0.1em] text-faint">门禁报告</div>
          <div className="mt-1 break-words">{blockers.length ? blockers.join(' · ') : '未报告阻断项；人工晋级门仍然有效，实盘在结构上关闭。'}</div>
        </div>
      </div>
      <div className="mt-2 text-3xs text-faint">
        来源 PAPER_REALTIME_MARK_ARTIFACT · 权威主纸面账户 · 晋级阻断 · 实盘关闭 · 订单提交关闭
      </div>
    </div>
  );
}

function OfficialPaperRow({
  alpha,
  value,
  candidate,
  selected,
  onSelect,
}: {
  alpha: string;
  value: PaperRealtimeAlpha;
  candidate?: AlphaPipelineCandidate;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const activePositions = Object.values(value.positions ?? {}).filter(quantity => quantity !== 0).length;
  const pending = !hasRealizedPaperPnl(value);
  const performanceState = paperPerformanceState(value);
  const blockers = candidate?.blockers ?? [];
  return (
    <div className="border-b border-line/70 py-3 last:border-b-0">
      <div className="grid gap-3 md:grid-cols-[minmax(190px,1.5fr)_repeat(4,minmax(92px,1fr))] md:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={`truncate text-left text-sm font-semibold text-ink underline-offset-2 hover:text-info hover:underline ${selected ? 'text-info' : ''}`}
              onClick={onSelect}
              aria-label={`查看 ${alpha} 主纸面生命周期报告`}
            >
              {alpha}
            </button>
            <Chip label={value.status ? statusLabel(value.status) : '无状态'} tone={pending ? 'warn' : 'info'} />
            <Chip label={candidate?.stage ? statusLabel(candidate.stage) : '无工作流阶段'} tone={candidate ? 'special' : 'muted'} />
            <Chip label={statusLabel(performanceState)} tone={performanceState === 'LOSS' ? 'neg' : performanceState === 'PROFIT' ? 'pos' : 'muted'} />
          </div>
          <div className="mt-1 truncate text-3xs text-faint">
            {candidate
              ? `${candidate.family_id ?? 'UNCLASSIFIED'} · ${candidate.definition_kind ?? 'UNKNOWN_DEFINITION'}`
              : '主纸面账户 · 工作流身份不可用'}
          </div>
        </div>
        <div>
          <div className="text-3xs tracking-wider text-faint">权益</div>
          <div className="ticker-num mt-1 text-sm text-ink">{fmtUsd(value.equity)}</div>
        </div>
        <div>
          <div className="text-3xs tracking-wider text-faint">净盈亏</div>
          <div className={`ticker-num mt-1 text-sm ${signedClass(pending ? null : value.net_pnl)}`}>{fmtUsd(pending ? null : value.net_pnl)}</div>
        </div>
        <div>
          <div className="text-3xs tracking-wider text-faint">收益率</div>
          <div className={`ticker-num mt-1 text-sm ${signedClass(pending ? null : value.return_on_aum)}`}>{fmtRealizedPct(value.return_on_aum, !pending)}</div>
        </div>
        <div>
          <div className="text-3xs tracking-wider text-faint">活动 / 成熟度</div>
          <div className="ticker-num mt-1 text-sm text-muted">{value.n_fills ?? 0} 个成交 · {activePositions} 个持仓</div>
          <div className="mt-1 text-3xs text-faint">
            成熟度 {candidate?.maturity?.observed_periods ?? 0}/{candidate?.maturity?.required_periods ?? '—'}
          </div>
        </div>
      </div>
      <div className="mt-2 truncate text-3xs text-faint" title={blockers.join(' · ')}>
        生命周期阻断项：{blockers.length ? blockers.join(' · ') : '未报告；人工晋级门仍然有效'}
      </div>
    </div>
  );
}

function FormalRiskRow({
  alpha,
  preview,
}: {
  alpha: string;
  preview?: PaperOrderPreviewAlpha;
}) {
  const risk = preview?.research_risk;
  const model = risk?.reports?.model_alpha;
  const capacity = risk?.reports?.capacity;
  const gatesPassed = risk?.gates_passed ?? 0;
  const gatesRun = risk?.gates_run ?? 9;
  const failedEngines = Object.entries(risk?.engines_run ?? {})
    .filter(([, passed]) => !passed)
    .map(([engine]) => engine);
  const validation = model?.min_validation_confidence;
  const paper = model?.min_paper_confidence;
  const approved = risk?.decision === 'RISK_APPROVED';

  return (
    <div className="grid gap-2 border-b border-line/70 py-3 last:border-b-0 sm:grid-cols-[minmax(150px,1.2fr)_repeat(3,minmax(90px,1fr))] sm:items-center">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-xs font-semibold text-ink">{alpha}</span>
          <Chip label={`${gatesPassed}/${gatesRun}`} tone={approved ? 'pos' : 'warn'} />
        </div>
        <div className="mt-1 truncate text-3xs text-faint">
          {failedEngines.length ? `被 ${failedEngines.join(', ')} 阻断` : risk?.status ? statusLabel(risk.status) : '无风险快照'}
        </div>
      </div>
      <div>
        <div className="text-3xs tracking-wider text-faint">容量</div>
        <div className={`ticker-num mt-1 text-sm ${capacity?.passed ? 'text-pos' : 'text-warn'}`}>
          {capacity?.capacity_usage == null ? '--' : `${(capacity.capacity_usage * 100).toFixed(1)}%`}
        </div>
        <div className="mt-1 truncate text-3xs text-faint">{capacity?.bottleneck_alpha ?? '无瓶颈'}</div>
      </div>
      <div>
        <div className="text-3xs tracking-wider text-faint">DSR 置信度</div>
        <div className={`ticker-num mt-1 text-sm ${(validation ?? 0) >= 0.3 ? 'text-pos' : 'text-warn'}`}>
          {validation == null ? '--' : validation.toFixed(3)}
        </div>
        <div className="mt-1 text-3xs text-faint">最低 0.300</div>
      </div>
      <div>
        <div className="text-3xs tracking-wider text-faint">前向置信度</div>
        <div className={`ticker-num mt-1 text-sm ${(paper ?? 0) >= 0.3 ? 'text-pos' : 'text-warn'}`}>
          {paper == null ? '--' : paper.toFixed(3)}
        </div>
        <div className="mt-1 text-3xs text-faint">
          {model?.alphas_with_decay ? `${model.alphas_with_decay} 个衰减标记` : '最低 0.300'}
        </div>
      </div>
    </div>
  );
}

export default function DryRunPipeline() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<DryRunPipelinePayload>(
    signal => fetchDryRunSnapshot(signal),
    [],
    { streamPath: '/api/v1/console/stream/dry-run' },
  );
  const initialLoading = apiState === 'loading' && !usingBackend;
  const data = usingBackend ? payload! : mockPayload;
  const shadow = data.paper_shadow;
  const officialPaper = data.paper_realtime;
  const operational = data.paper_operational;
  const health = data.paper_health;
  const scheduler = data.scheduler_watchdog;
  const realtimeAlpha = data.realtime_alpha_autonomy;
  const alphaPipeline = data.alpha_pipeline;
  const pipelineCandidates = alphaPipeline?.candidates ?? [];
  const pipelineByAlpha = new Map(pipelineCandidates.map(candidate => [candidate.alpha_id, candidate]));
  const alphaRows = Object.entries(shadow?.alphas ?? {});
  const officialRows = Object.entries(officialPaper?.alphas ?? {});
  const [selectedOfficialId, setSelectedOfficialId] = React.useState<string | null>(null);
  const officialProfit = officialRows.filter(([, alpha]) => (alpha.net_pnl ?? 0) > 0 && (alpha.n_fills ?? 0) > 0).length;
  const officialLoss = officialRows.filter(([, alpha]) => (alpha.net_pnl ?? 0) < 0 && (alpha.n_fills ?? 0) > 0).length;
  const officialPending = officialRows.filter(([, alpha]) => alpha.status === 'PAPER_PENDING' || (alpha.n_fills ?? 0) === 0).length;
  const sortedOfficialRows = officialRows.slice().sort(([, a], [, b]) => {
    const rank = (value: PaperRealtimeAlpha) => value.status === 'PAPER_PENDING' || (value.n_fills ?? 0) === 0
      ? 2
      : (value.net_pnl ?? 0) < 0 ? 0 : 1;
    return rank(a) - rank(b) || (a.net_pnl ?? 0) - (b.net_pnl ?? 0);
  });
  const effectiveOfficialId = selectedOfficialId
    ?? officialRows.find(([, alpha]) => (alpha.net_pnl ?? 0) < 0 && (alpha.n_fills ?? 0) > 0)?.[0]
    ?? officialRows[0]?.[0]
    ?? null;
  const selectedOfficial = effectiveOfficialId
    ? officialRows.find(([alpha]) => alpha === effectiveOfficialId)
    : undefined;
  const selectedOfficialCandidate = effectiveOfficialId
    ? pipelineByAlpha.get(effectiveOfficialId)
    : undefined;
  const observedAt = shadow?.observed_at ?? health?.observed_at;
  const observedAgeMs = observedAt == null ? null : Math.max(0, Date.now() - observedAt);
  const markFresh = observedAgeMs != null && observedAgeMs <= (health?.max_age_ms ?? 300_000);
  const schedulerChecks = (scheduler?.checks ?? []).filter(check =>
    check.task_name === 'QUANTOS_paper_shadow_auto'
    || check.task_name === 'QUANTOS_paper_shadow_realtime');
  const schedulerHealthy = schedulerChecks.length === 2
    && schedulerChecks.every(check => check.status === 'PASS');
  const shadowRunning = Boolean(shadow?.verdict.shadow_paper_running && schedulerHealthy);
  const reconciled = alphaRows.length > 0 && alphaRows.every(([, alpha]) =>
    Math.abs(alpha.reconciliation_residual ?? Number.POSITIVE_INFINITY) < 1e-6);
  const forwardByAlpha = health?.forward_evidence?.alphas ?? {};
  const bundleByAlpha = health?.research_risk_bundles ?? {};
  const formalPreviewByAlpha = data.paper_order_preview?.alphas ?? {};
  const precomputedRiskInputs = pipelineCandidates.filter(candidate =>
    candidate.risk_packet?.input_evidence_status === 'READY'
    || candidate.risk_packet?.status === 'READY').length;
  const drillEntries = Object.entries(operational?.window.drills ?? {});
  const passedDrills = drillEntries.filter(([, passed]) => passed).length;
  const restartProven = (operational?.window.planned_restart_recoveries ?? [])
    .some(restart => restart.within_policy);
  const totalPositions = alphaRows.reduce(
    (sum, [, alpha]) => sum + Object.values(alpha.positions ?? {}).filter(qty => qty !== 0).length,
    0,
  );
  const flow = [
    {
      label: '实时公共行情',
      detail: `${shadow?.provider ?? '无数据提供方'} · 距今 ${fmtAge(observedAgeMs)}`,
      ok: Boolean(shadow?.verdict.paper_mark_ready && markFresh),
    },
    {
      label: '自动调度与再平衡',
      detail: `${schedulerChecks.length}/2 个必需任务健康`,
      ok: schedulerHealthy && Boolean(shadow?.verdict.auto_rebalance_ready),
    },
    {
      label: '影子纸面执行',
      detail: `${alphaRows.length} 个隔离账簿 · ${totalPositions} 个持仓`,
      ok: Boolean(shadow?.verdict.shadow_paper_running),
    },
    {
      label: '逐笔估值与对账',
      detail: reconciled ? '全部残差约等于 0' : '对账残差需要处理',
      ok: reconciled,
    },
    {
      label: '连续运行与故障恢复',
      detail: `${operational?.window.accepted_cycle_count ?? 0} 个周期 · ${passedDrills}/${drillEntries.length} 个演练`,
      ok: Boolean(operational?.window.engineering_smoke_ready),
      blocked: !operational?.window.engineering_smoke_ready,
    },
    {
      label: '正式纸面与前向证据',
      detail: health?.readiness_class ?? 'PAPER_GATE_UNAVAILABLE',
      ok: Boolean(health?.verdict.paper_ready),
      blocked: !health?.verdict.paper_ready,
    },
  ];
  const issueLines = [
    ...(health?.blockers ?? []),
    ...Object.entries(shadow?.errors ?? {}).map(([alpha, issue]) => `${alpha}: ${issue}`),
    ...(data.data_refresh?.alerts ?? []),
  ];
  const chartSeries = useMemo(() => paperSeries(shadow?.history ?? []), [shadow?.history]);
  const transportLabel = initialLoading
    ? 'SSE 连接中'
    : apiState === 'loading' && usingBackend
      ? 'SSE 重连中'
    : apiState === 'connected' && usingBackend
      ? 'SSE 实时'
      : 'API 不可用';
  const transportTone: Tone = initialLoading || apiState === 'loading'
    ? 'info'
    : apiState === 'connected' && usingBackend
      ? 'pos'
      : 'neg';
  const automationLabel = initialLoading
    ? '连接中'
    : shadowRunning
      ? '自动调度中'
      : '安全阻断';
  const automationTone: Tone = initialLoading ? 'info' : shadowRunning ? 'pos' : 'neg';

  return (
    <PageShell>
      <div className="flex flex-col gap-3 border-b border-line pb-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${
              initialLoading ? 'q-live bg-info' : shadowRunning ? 'q-live bg-pos' : 'bg-neg'
            }`} />
            <span className="text-3xs font-semibold tracking-[0.16em] text-faint">纸面交易运行</span>
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink sm:text-[30px]">
            全自动纸面交易驾驶舱
          </h1>
          <p className="mt-1 text-xs text-muted">
            主纸面账户与独立 Challenger 分栏；实时行情、生命周期、业绩、模拟执行与对账。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Chip label={automationLabel} tone={automationTone} />
          <Chip label="L2 仅纸面" tone="info" />
          <Chip label="实盘关闭" tone="neg" />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-inset/50 px-3 py-2 text-3xs text-faint">
        <Chip label={transportLabel} tone={transportTone} />
        <span>{initialLoading ? '等待只读快照' : shadow?.provider ?? '无真实数据提供方'}</span>
        <span className="hidden text-line-strong sm:inline">|</span>
        <span>
          {initialLoading
            ? '正在进行传输握手'
            : <>更新于 {fmtTime(observedAt)} · 距今 {fmtAge(observedAgeMs)}</>}
        </span>
        <span className="ml-auto">
          {error
            ? <span className="text-neg">{error}</span>
            : initialLoading ? '暂无决策数据' : data.data_provenance}
        </span>
      </div>

      {initialLoading ? (
        <PaperLoadingState />
      ) : (
      <LoadingVeil active={apiState === 'loading'}>
        <div className="mb-4">
          <AutonomousAlphaPanel snapshot={realtimeAlpha} />
        </div>
        <div className="grid grid-cols-2 gap-y-4 rounded-lg border border-line bg-panel px-4 py-4 md:grid-cols-5">
          <Metric
            label="影子总权益"
            value={fmtUsd(shadow?.totals.equity)}
            detail={`${alphaRows.length} 个隔离账簿`}
            tone="accent"
          />
          <Metric
            label="影子净盈亏"
            value={fmtUsd(shadow?.totals.net_pnl)}
            detail={`资金费 ${fmtUsd(shadow?.totals.funding_pnl)}`}
            tone={(shadow?.totals.net_pnl ?? 0) >= 0 ? 'pos' : 'neg'}
          />
          <Metric label="持仓" value={totalPositions} detail="已估值标的" tone="info" />
          <Metric
            label="对账"
            value={reconciled ? '已对账' : '待检查'}
            detail={shadow?.persistence.chain_valid ? '哈希链有效' : '哈希链无效'}
            tone={reconciled && shadow?.persistence.chain_valid ? 'pos' : 'neg'}
          />
          <Metric
            label="正式纸面"
            value={health?.verdict.paper_ready ? '就绪' : '门禁阻断'}
            detail={health?.readiness_class ?? '无健康快照'}
            tone={health?.verdict.paper_ready ? 'pos' : 'warn'}
          />
        </div>

        <div className="mt-4">
          <Panel
            title="主纸面账户实时业绩"
            eyebrow="主纸面实时 · 因子生命周期详情 · 权威主账户 · SSE 实时"
            right={(
              <div className="flex flex-wrap items-center gap-2">
                <Chip label={`${officialLoss} 个亏损`} tone={officialLoss ? 'neg' : 'muted'} />
                <Chip label={`${officialProfit} 个盈利`} tone={officialProfit ? 'pos' : 'muted'} />
                <Chip label={`${officialPending} 个待成交`} tone="warn" />
                <Chip label={`${officialRows.length} 个账户`} tone="info" />
              </div>
            )}
            bodyClass="px-4 py-2"
          >
            <div className="mb-2 rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-3xs text-muted">
              该区域与下方影子 Challenger 账户完全分开。亏损优先排序；PAPER_PENDING 的初始权益和 0 PnL 不算盈利，也不以影子收益替代。
            </div>
            {sortedOfficialRows.length ? sortedOfficialRows.map(([alpha, value]) => (
              <OfficialPaperRow
                key={alpha}
                alpha={alpha}
                value={value}
                candidate={pipelineByAlpha.get(alpha)}
                selected={alpha === effectiveOfficialId}
                onSelect={() => setSelectedOfficialId(alpha)}
              />
            )) : (
              <div className="py-6 text-center text-xs text-faint">主纸面账户快照不可用。</div>
            )}
            {selectedOfficial ? (
              <OfficialFactorLifecycleReport
                alpha={selectedOfficial[0]}
                value={selectedOfficial[1]}
                backtest={officialPaper?.backtests?.[selectedOfficial[0]]}
                history={officialPaper?.history ?? []}
                candidate={selectedOfficialCandidate}
              />
            ) : null}
          </Panel>
        </div>

        <div className="mt-4">
          <Panel
            title="Alpha 全自动流水线"
            eyebrow="发现 → 前向挑战者 → 候选纸面"
            right={
              <Chip
                label={alphaPipeline?.handoff.status ? statusLabel(alphaPipeline.handoff.status) : '无流水线'}
                tone={alphaPipeline?.handoff.status === 'CONNECTED' ? 'pos' : 'warn'}
              />
            }
            bodyClass="p-4"
          >
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Metric
                label="因子工厂"
                value={alphaPipeline?.factory.survivors ?? 0}
                detail={`${alphaPipeline?.factory.data_dimensions ?? 0} 个数据维度 / ${alphaPipeline?.factory.rounds ?? 0} 轮`}
                tone="special"
              />
              <Metric
                label="前向纸面"
                value={alphaPipeline?.counts.forward_paper ?? 0}
                detail={`${alphaPipeline?.counts.forward_only ?? 0} 个仍在前向门禁`}
                tone="info"
              />
              <Metric
                label="工程纸面"
                value={shadow?.candidate_universe?.running_count ?? alphaRows.length}
                detail={`${alphaPipeline?.counts.candidate_execution_paper ?? 0} 个正式候选；隔离影子账簿`}
                tone="accent"
              />
              <Metric
                label="遗留诊断纸面"
                value={alphaPipeline?.counts.legacy_diagnostic_paper ?? 0}
                detail="仅诊断；已隔离"
                tone={(alphaPipeline?.counts.legacy_diagnostic_paper ?? 0) > 0 ? 'warn' : 'pos'}
              />
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-md border border-line bg-inset/35 px-3 py-2 text-xs text-muted">
              <Chip
                label={alphaPipeline?.workflow?.verified ? '统一工作流' : '工作流受阻'}
                tone={alphaPipeline?.workflow?.verified ? 'pos' : 'warn'}
              />
              <span>
                {alphaPipeline?.workflow?.population_count ?? alphaPipeline?.counts.forward_paper ?? 0} 个冻结身份
                {' / '}{alphaPipeline?.workflow?.realtime_handoff_count ?? 0} 个实时交接
                {' / '}{alphaPipeline?.workflow?.event_count ?? 0} 个证据事件
                {' / '}{alphaPipeline?.workflow?.execution_paper_eligible_count ?? 0} 个当前纸面合格
              </span>
              {alphaPipeline?.workflow?.release_commit ? (
                <span className="font-mono text-faint">
                  发布版本 {alphaPipeline.workflow.release_commit.slice(0, 12)}
                </span>
              ) : null}
            </div>

            {alphaPipeline?.exception_queue?.length ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-3xs text-muted" aria-label="Alpha 异常队列">
                <span className="tracking-[0.12em] text-faint">异常队列</span>
                {alphaPipeline.exception_queue.slice(0, 4).map(item => (
                  <span
                    key={item.code}
                    className="rounded border border-line bg-inset/50 px-2 py-1"
                    title={item.candidate_ids.join(', ')}
                  >
                    {item.candidate_count} · {item.code.replace(/_/g, ' ')}
                  </span>
                ))}
                {alphaPipeline.exception_queue.length > 4 ? (
                  <span className="text-faint">另有 {alphaPipeline.exception_queue.length - 4} 项</span>
                ) : null}
              </div>
            ) : null}

            <div className="mt-4 overflow-hidden rounded-lg border border-line bg-inset/35">
              <table className="w-full table-fixed text-left text-xs" aria-label="Alpha 纸面流水线">
                <thead className="border-b border-line text-3xs uppercase tracking-[0.12em] text-faint">
              <tr>
                    <th className="w-[30%] px-3 py-2 font-medium">Alpha</th>
                    <th className="w-[18%] px-3 py-2 font-medium">阶段</th>
                    <th className="px-3 py-2 text-right font-medium">OOS</th>
                    <th className="px-3 py-2 text-right font-medium">待兑现</th>
                    <th className="px-3 py-2 text-right font-medium">收益率</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/70">
                  {pipelineCandidates.length ? pipelineCandidates.map(candidate => (
                    <tr key={candidate.alpha_id}>
                      <td className="truncate px-3 py-2 font-semibold text-ink" title={candidate.blockers.join(', ')}>
                        {candidate.alpha_id}
                        {candidate.definition_kind === 'REALTIME_MECHANISM' ? (
                          <span className="ml-2 text-3xs font-normal text-accent">RT</span>
                        ) : null}
                      </td>
                      <td className="px-3 py-2">
                        <Chip
                          label={candidate.paper_class === 'LEGACY_DIAGNOSTIC_PAPER'
                            ? '遗留诊断纸面'
                            : candidate.execution_paper
                              ? '候选纸面'
                            : candidate.execution_paper_eligible
                              ? '纸面合格'
                            : candidate.stage === 'PARALLEL_VALIDATION'
                              ? '验证中'
                            : candidate.independent_evaluation?.status === 'VERIFIED'
                              ? '已评估'
                              : '前向验证'}
                          tone={candidate.paper_class === 'LEGACY_DIAGNOSTIC_PAPER'
                            ? 'warn'
                            : candidate.execution_paper ? 'pos' : 'info'}
                        />
                        {candidate.paper_class === 'LEGACY_DIAGNOSTIC_PAPER' ? (
                          <div className="mt-1 text-3xs text-warn">
                            仅诊断 · 永不具备部署资格
                          </div>
                        ) : !candidate.execution_paper ? (
                          <div
                            className="mt-1 text-3xs text-faint"
                            title={[
                              candidate.risk_packet?.decision_basis,
                              ...(candidate.risk_packet?.admission_blockers ?? []),
                            ].filter(Boolean).join(' · ') || undefined}
                          >
                            评估 {candidate.independent_evaluation?.verdict ? statusLabel(candidate.independent_evaluation.verdict) : '待处理'} · 输入 {candidate.risk_packet?.input_evidence_status ? statusLabel(candidate.risk_packet.input_evidence_status) : '待处理'} · 准入 {candidate.risk_packet?.status ? statusLabel(candidate.risk_packet.status) : '待处理'}
                          </div>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 text-right ticker-num text-muted">
                        {candidate.n_forward_periods ?? 0}/{alphaPipeline?.counts.forward_paper ? (data.forward_track?.evidence_gate_weeks ?? 8) : 8}
                      </td>
                      <td className="px-3 py-2 text-right ticker-num text-muted">
                        {candidate.attested_pending_decisions ?? candidate.pending_decisions ?? 0}
                      </td>
                      <td className={`px-3 py-2 text-right ticker-num ${(candidate.cum_return ?? 0) >= 0 ? 'text-pos' : 'text-neg'}`}>
                        {fmtPct(candidate.cum_return)}
                      </td>
                    </tr>
                  )) : (
                    <tr>
                      <td colSpan={5} className="px-3 py-5 text-center text-faint">暂无经证明的前向纸面候选。</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {alphaPipeline?.handoff.gap_codes.length ? (
              <Callout summary="流水线交接尚未完全连通" tone="warn">
                {alphaPipeline.handoff.gap_codes.map(code => <div key={code}>- {code}</div>)}
                {alphaPipeline.factory.verdict ? <div>- 因子工厂：{alphaPipeline.factory.verdict}</div> : null}
              </Callout>
            ) : null}
            {alphaPipeline?.handoff.boundary_codes?.length ? (
              <Callout summary="流水线安全边界已启用" tone="info">
                {alphaPipeline.handoff.boundary_codes.map(code => <div key={code}>- {code}</div>)}
              </Callout>
            ) : null}
          </Panel>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(300px,.7fr)]">
          <Panel
            title="影子 Challenger 实时权益与策略账簿"
            eyebrow="纸面影子 · 隔离且不可晋级的账簿"
            right={<Chip label="SSE 实时流" tone="info" />}
            bodyClass="p-4"
          >
            <div className="rounded-lg border border-line bg-inset/40 p-2">
              <EquityCurveChart
                height={210}
                series={chartSeries}
                yFormat={value => fmtUsd(value)}
              />
            </div>
            <div className="mt-3">
              {alphaRows.length ? alphaRows.map(([alpha, value]) => {
                const forward = forwardByAlpha[alpha];
                const bundle = bundleByAlpha[alpha];
                return (
                  <StrategyRow
                    key={alpha}
                    alpha={alpha}
                    value={value}
                    forwardPeriods={forward?.n_forward_periods}
                    periodsToGate={forward?.periods_to_gate}
                    riskReasons={
                      formalPreviewByAlpha[alpha]?.research_risk?.reasons
                      ?? bundle?.reasons
                      ?? bundle?.missing
                      ?? []
                    }
                  />
                );
              }) : (
                <div className="py-8 text-center text-xs text-faint">暂无真实影子纸面快照。</div>
              )}
            </div>
          </Panel>

          <Panel
            title="自动流水线"
            eyebrow="自动化控制"
            right={<StatusChip value={scheduler == null ? 'NO_SNAPSHOT' : schedulerHealthy ? 'PASS' : 'BREACH'} />}
            bodyClass="divide-y divide-line/70 px-4 py-2"
          >
            {flow.map((step, index) => (
              <FlowStep key={step.label} index={index + 1} {...step} />
            ))}
          </Panel>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Panel
            title="正式纸面门禁"
            eyebrow="证据门禁"
            right={<Chip label={health?.verdict.paper_ready ? '通过' : '受阻'} tone={health?.verdict.paper_ready ? 'pos' : 'warn'} />}
            bodyClass="p-4"
          >
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Metric
                label="工作流权威"
                value={data.paper_order_preview?.verdict.workflow_admission_valid ? '已验证' : '受阻'}
                detail={`${data.paper_order_preview?.verdict.workflow_authorized_ids?.length ?? 0} 个候选已授权`}
                tone={data.paper_order_preview?.verdict.workflow_admission_valid ? 'pos' : 'neg'}
              />
              <Metric
                label="风险输入"
                value={`${precomputedRiskInputs}/${pipelineCandidates.length || 0}`}
                detail="预计算 · 与准入分离"
                tone={precomputedRiskInputs === pipelineCandidates.length && precomputedRiskInputs > 0 ? 'pos' : 'warn'}
              />
              <Metric
                label="前向证据"
                value={Object.values(forwardByAlpha)[0]?.n_forward_periods ?? 0}
                detail={health?.forward_evidence?.evidence_maturing
                  ? `下次 ${stamp(health.forward_evidence.next_realization_eligible_at)} UTC`
                  : `目标 ${health?.forward_evidence?.evidence_gate_periods ?? 8}`}
                tone="warn"
              />
              <Metric
                label="晋级"
                value="关闭"
                detail="仅人工边界"
                tone="neg"
              />
            </div>
            <div className="mt-4 rounded-lg border border-line bg-inset/35 px-3">
              {Object.keys(formalPreviewByAlpha).length
                ? Object.entries(formalPreviewByAlpha).map(([alpha, preview]) => (
                  <FormalRiskRow key={alpha} alpha={alpha} preview={preview} />
                ))
                : (
                  <div className="py-4 text-xs text-faint">
                    暂无正式预填充风险快照。
                  </div>
                )}
            </div>
            <Callout
              summary={health?.next_action === 'continue_scheduled_forward_evidence_and_review_remaining_risk_gates'
                ? `前向证据按计划成熟中 · 下一次最早可兑现 ${stamp(health.forward_evidence?.next_realization_eligible_at)} UTC`
                : health?.next_action ?? '等待前向周期与研究风险证据满足正式纸面门禁'}
              tone={health?.verdict.paper_ready ? 'pos' : 'warn'}
            >
              {issueLines.length
                ? issueLines.map((issue, index) => <div key={`${issue}-${index}`}>• {issue}</div>)
                : '未报告活动阻断项。'}
            </Callout>
          </Panel>

          <Panel
            title="运行完整性"
            eyebrow="运行完整性"
            right={<Chip label={shadow?.persistence.chain_valid ? '链有效' : '链待检查'} tone={shadow?.persistence.chain_valid ? 'pos' : 'neg'} />}
            bodyClass="p-4"
          >
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              <Metric label="账本行数" value={shadow?.paper_ledger.n_rows ?? 0} detail={`${shadow?.paper_ledger.n_decisions ?? 0} 个决策`} tone="info" />
              <Metric label="快照" value={shadow?.persistence.new_snapshots ?? 0} detail="仅追加周期" tone="info" />
              <Metric
                label="运行窗口"
                value={fmtAge(operational?.window.history_span_ms ?? null)}
                detail={`${operational?.window.accepted_cycle_count ?? 0}/${operational?.policy?.production_minimum_successes ?? 274} 已接受 · 发布 ${(operational?.window.active_release_commit ?? '未知').slice(0, 8)} · 保留 ${operational?.window.excluded_pre_active_release_cycle_count ?? 0} 个遗留周期`}
                tone={operational?.window.production_window_ready ? 'pos' : 'warn'}
              />
              <Metric
                label="故障演练"
                value={`${passedDrills}/${drillEntries.length}`}
                detail="隔离临时存储"
                tone={drillEntries.length > 0 && passedDrills === drillEntries.length ? 'pos' : 'warn'}
              />
              <Metric
                label="重启恢复"
                value={restartProven ? '已证明' : '证据积累中'}
                detail="发布切换不超过 15 分钟"
                tone={restartProven ? 'pos' : 'warn'}
              />
              <Metric label="数据刷新" value={data.data_refresh?.outcome ? statusLabel(data.data_refresh.outcome) : '—'} detail={`${data.data_refresh?.failed_total ?? 0} 个失败`} tone={(data.data_refresh?.failed_total ?? 0) === 0 ? 'pos' : 'neg'} />
              <Metric label="经纪商路径" value="关闭" detail="仅公共数据" tone="neg" />
            </div>
          </Panel>
        </div>

        <div className="mt-4">
          <TcaHistoryPanel history={data.route_tca_history} />
        </div>
      </LoadingVeil>
      )}
    </PageShell>
  );
}
