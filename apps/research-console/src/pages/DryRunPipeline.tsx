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
import type {
  DryRunPipelinePayload,
  PaperOrderPreviewAlpha,
  PaperRealtimeAlpha,
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
  if (ageMs < 60_000) return `${Math.max(0, Math.floor(ageMs / 1000))}s`;
  return `${Math.floor(ageMs / 60_000)}m`;
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
    : 'no retained range';

  return (
    <Panel
      title="纸面执行质量"
      eyebrow="Route-level TCA · simulation only"
      right={<Chip label={history?.status ?? 'NO_ROUTE_TCA'} tone={ready ? 'pos' : 'warn'} />}
      bodyClass="p-4"
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="Weighted shortfall" value={fmtBps(history?.weighted_shortfall_bps)} detail="notional weighted" tone={ready ? 'pos' : 'warn'} />
        <Metric label="Routed runs" value={(history?.run_count ?? 0).toLocaleString('en-US')} detail="append-only evidence" tone="info" />
        <Metric label="Route records" value={(history?.record_count ?? 0).toLocaleString('en-US')} detail="verified records" tone="info" />
        <Metric label="Venues" value={history?.venue_count ?? 0} detail={range} tone="special" />
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border border-line bg-inset/35">
        <table className="w-full table-fixed text-left text-xs" aria-label="paper route TCA by venue">
          <thead className="border-b border-line text-3xs uppercase tracking-[0.12em] text-faint">
            <tr>
              <th className="w-[34%] px-3 py-2 font-medium">Venue</th>
              <th className="px-3 py-2 text-right font-medium">Records</th>
              <th className="px-3 py-2 text-right font-medium">Notional</th>
              <th className="px-3 py-2 text-right font-medium">Shortfall</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/70">
            {venues.length ? venues.map(venue => (
              <tr key={venue.venue_id}>
                <td className="truncate px-3 py-2 font-semibold text-ink">{venue.venue_id}</td>
                <td className="px-3 py-2 text-right ticker-num text-muted">{venue.record_count.toLocaleString('en-US')}</td>
                <td className="px-3 py-2 text-right ticker-num text-muted">{fmtCompactUsd(venue.reference_notional)}</td>
                <td className="px-3 py-2 text-right ticker-num text-ink">{fmtBps(venue.weighted_shortfall_bps)}</td>
              </tr>
            )) : (
              <tr>
                <td colSpan={4} className="px-3 py-5 text-center text-faint">No verified route-level TCA retained.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-3xs text-faint">
        <span>{range}</span>
        <span>SIMULATION_ONLY · verified on read</span>
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
      <Chip label={ok ? 'PASS' : blocked ? 'GATED' : 'CHECK'} tone={tone} />
    </div>
  );
}

function PaperLoadingState() {
  return (
    <div
      className="mt-4 rounded-lg border border-line bg-panel p-5"
      aria-live="polite"
      aria-label="paper snapshot loading"
    >
      <div className="flex items-center gap-3">
        <span className="q-live h-2.5 w-2.5 rounded-full bg-info" />
        <div>
          <div className="text-sm font-semibold text-ink">正在建立只读纸面快照</div>
          <div className="mt-1 text-3xs text-faint">
            Connecting to the paper ledger, scheduler, market marks and risk evidence.
          </div>
        </div>
      </div>
      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-5">
        {['Equity', 'PnL', 'Positions', 'Reconciliation', 'Official gate'].map(label => (
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
  if (horizonMs < 60 * 60_000) return `${Math.round(horizonMs / 60_000)}m`;
  return `${Math.round(horizonMs / (60 * 60_000))}h`;
}

type LifecycleFilter = 'ALL' | 'LOSS' | 'PROFIT' | 'NO_PNL';

function lifecycleSeries(row: RealtimeAlphaLifecyclePayload): EquitySeries[] {
  const points = (row.paper.performance_history ?? [])
    .filter(point => Number.isFinite(point.cum_return))
    .map(point => ({ t: point.as_of_ms, equity: 100 * (1 + point.cum_return) }));
  return points.length
    ? [{ label: `${row.candidate_id} paper index`, points, tone: row.performance_state === 'LOSS' ? 'warn' : 'pos' }]
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
  const series = useMemo(() => lifecycleSeries(row), [row]);
  const stages = [
    { label: 'REGISTERED', reached: true },
    { label: 'EVALUATED', reached: row.research.evaluation_status === 'EVALUATED' },
    { label: 'SIGNAL SHADOW', reached: row.paper.n_decisions > 0 },
    { label: 'EXEC PAPER', reached: row.execution.n_executions > 0 },
    { label: 'FORMAL PASS', reached: row.research.gate_pass },
  ];
  const blockers = [...(row.research.gate_reasons ?? []), ...row.errors];

  return (
    <div className="border-t border-line bg-panel/70 p-4" data-testid="factor-lifecycle-report">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-3xs font-semibold uppercase tracking-[0.14em] text-faint">Factor lifecycle report · SSE live</div>
          <div className="mt-1 break-all text-sm font-semibold text-ink">{row.candidate_id}</div>
          <div className="mt-1 text-3xs text-muted">
            {row.family_id ?? '--'} / {row.mechanism ?? '--'} · {fmtHorizon(row.horizon_ms)} · last event {fmtTime(row.last_event_ms)}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Chip label={row.pipeline_stage} tone="info" />
          <Chip
            label={row.performance_state}
            tone={row.performance_state === 'LOSS' ? 'neg' : row.performance_state === 'PROFIT' ? 'pos' : 'muted'}
          />
          <Chip label="NON-PROMOTABLE" tone="warn" />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5" aria-label="factor lifecycle stages">
        {stages.map((stage, index) => (
          <React.Fragment key={stage.label}>
            {index ? <span className="text-line-strong">→</span> : null}
            <Chip label={stage.label} tone={stage.reached ? (stage.label === 'FORMAL PASS' ? 'pos' : 'info') : 'muted'} />
          </React.Fragment>
        ))}
      </div>

      <div className="mt-4 grid grid-cols-2 gap-y-4 rounded-lg border border-line bg-inset/35 px-4 py-4 lg:grid-cols-6">
        <Metric label="Research return" value={fmtPct(row.research.net_return)} detail={`${row.research.n_periods ?? 0} observations`} tone={(row.research.net_return ?? 0) >= 0 ? 'pos' : 'neg'} />
        <Metric label="Holdout SR" value={row.research.holdout_net_sharpe?.toFixed(2) ?? '—'} detail={row.research.gate_pass ? 'formal pass' : 'not passed'} tone={row.research.gate_pass ? 'pos' : 'warn'} />
        <Metric label="Paper return" value={fmtPct(row.paper.cum_return)} detail={`${row.paper.n_realizations}/${row.paper.n_decisions} realized`} tone={row.performance_state === 'LOSS' ? 'neg' : row.performance_state === 'PROFIT' ? 'pos' : 'muted'} />
        <Metric label="Paper drawdown" value={fmtPct(row.paper.max_drawdown == null ? null : -row.paper.max_drawdown)} detail={`${row.paper.n_pending} pending`} tone={(row.paper.max_drawdown ?? 0) > 0 ? 'neg' : 'muted'} />
        <Metric label="Execution" value={row.execution.n_executions} detail={`${row.execution.n_orders} orders / ${row.execution.n_fill_slices} fills`} tone="info" />
        <Metric label="Execution cost" value={fmtUsd(row.execution.execution_cost)} detail={`${row.execution.n_missed_execution_windows} missed windows`} tone="warn" />
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,1fr)]">
        <div className="rounded-lg border border-line bg-inset/35 p-3">
          <div className="mb-2 flex items-center justify-between gap-2 text-3xs text-faint">
            <span className="font-semibold uppercase tracking-[0.12em]">Realized paper performance index</span>
            <span>100 = first decision · net of modeled costs</span>
          </div>
          {series.length ? (
            <EquityCurveChart series={series} height={190} />
          ) : (
            <div className="grid h-[190px] place-items-center text-xs text-faint">No realized paper history yet.</div>
          )}
        </div>

        <div className="overflow-hidden rounded-lg border border-line bg-inset/35">
          <div className="border-b border-line px-3 py-2 text-3xs font-semibold uppercase tracking-[0.12em] text-faint">
            Latest realized periods
          </div>
          <table className="w-full text-left text-3xs" aria-label="selected factor realized performance history">
            <thead className="border-b border-line/70 text-faint">
              <tr><th className="px-3 py-2 font-medium">Time</th><th className="px-3 py-2 text-right font-medium">Period</th><th className="px-3 py-2 text-right font-medium">Cumulative</th><th className="px-3 py-2 text-right font-medium">DD</th></tr>
            </thead>
            <tbody className="divide-y divide-line/60">
              {recent.length ? recent.map(point => (
                <tr key={point.as_of_ms}>
                  <td className="px-3 py-2 text-muted">{fmtTime(point.as_of_ms)}</td>
                  <td className={`px-3 py-2 text-right ticker-num ${signedClass(point.period_return)}`}>{fmtPct(point.period_return)}</td>
                  <td className={`px-3 py-2 text-right ticker-num ${signedClass(point.cum_return)}`}>{fmtPct(point.cum_return)}</td>
                  <td className="px-3 py-2 text-right ticker-num text-neg">{fmtPct(-point.drawdown)}</td>
                </tr>
              )) : <tr><td colSpan={4} className="px-3 py-5 text-center text-faint">Collecting first realization.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div className="rounded-md border border-line bg-inset/35 px-3 py-2 text-3xs text-muted">
          <div className="font-semibold uppercase tracking-[0.1em] text-faint">Identity / lineage</div>
          <div className="mt-1">parent {candidate?.parent_candidate_id ?? 'ROOT'} · generation {candidate?.adaptive_generation ?? 0}</div>
          <div className="mt-1">mutation {candidate?.mutation ?? 'NONE'} · fresh OOS {candidate?.fresh_oos_required ? 'REQUIRED' : 'BASELINE'}</div>
        </div>
        <div className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-3xs text-muted">
          <div className="font-semibold uppercase tracking-[0.1em] text-faint">Gate report</div>
          <div className="mt-1 break-words">{blockers.length ? blockers.join(' · ') : 'No reported blocker; formal authority still remains human-gated.'}</div>
        </div>
      </div>
      <div className="mt-2 text-3xs text-faint">
        Source {row.paper.pnl_source} · engineering evidence only · promotion {row.promotion_eligible ? 'allowed' : 'blocked'} · live {row.live_allowed ? 'allowed' : 'off'}
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
  const profitable = lifecycle.filter(row => row.performance_state === 'PROFIT').length;
  const losing = lifecycle.filter(row => row.performance_state === 'LOSS').length;
  const noPnl = lifecycle.filter(row => row.performance_state !== 'LOSS' && row.performance_state !== 'PROFIT').length;
  const performanceRank: Record<string, number> = { LOSS: 0, PROFIT: 1, FLAT: 2, NO_REALIZED_PNL: 3 };
  const filteredLifecycle = lifecycle
    .filter(row => filter === 'ALL'
      || (filter === 'NO_PNL'
        ? row.performance_state !== 'LOSS' && row.performance_state !== 'PROFIT'
        : row.performance_state === filter))
    .slice()
    .sort((a, b) => (performanceRank[a.performance_state] ?? 9) - (performanceRank[b.performance_state] ?? 9)
      || (a.paper.cum_return ?? 0) - (b.paper.cum_return ?? 0));
  const effectiveSelectedId = selectedCandidateId
    ?? lifecycle.find(row => row.performance_state === 'LOSS')?.candidate_id
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
          <Chip label={snapshot?.autonomy.requires_developer_ai ? 'IDE COUPLED' : 'SYSTEM NATIVE'} tone="info" />
          <Chip
            label={executionPaperRunning
              ? `EXEC PAPER ${executionTrackCount}`
              : engineeringPaperRunning
                ? `SIGNAL SHADOW ${engineeringTrackCount}`
              : snapshot?.forward_shadow.status ?? 'PAPER NO SNAPSHOT'}
            tone={executionPaperRunning ? 'pos' : engineeringPaperRunning ? 'info' : 'warn'}
          />
          <Chip label={snapshot?.status ?? 'NO SNAPSHOT'} tone={statusTone} />
        </div>
      }
      bodyClass="p-4"
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Metric
          label="Autonomy"
          value={running ? 'RUNNING' : 'GATED'}
          detail="hourly system worker"
          tone={statusTone}
        />
        <Metric
          label="Realtime history"
          value={`${spanDays.toFixed(2)}d`}
          detail={`${snapshot?.data_admission.common_instruments?.length ?? 0} instruments`}
          tone={spanDays >= 30 ? 'pos' : 'warn'}
        />
        <Metric
          label="Pre-registered"
          value={snapshot?.funnel.pre_registered ?? 0}
          detail={`${snapshot?.families.length ?? 0} families`}
          tone="special"
        />
        <Metric
          label="Diagnostic leads"
          value={snapshot?.funnel.diagnostic_leads ?? 0}
          detail={`${snapshot?.funnel.evaluated ?? 0} evaluated · ${engineeringTrackCount} signal tracks`}
          tone={(snapshot?.funnel.diagnostic_leads ?? 0) > 0 ? 'info' : 'muted'}
        />
        <Metric
          label="Formal passes"
          value={snapshot?.funnel.formal_passes ?? 0}
          detail={`global trials ${snapshot?.global_trial_count ?? '--'}`}
          tone={(snapshot?.funnel.formal_passes ?? 0) > 0 ? 'pos' : 'warn'}
        />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1.8fr)_minmax(240px,1fr)]">
        <div className="overflow-hidden rounded-lg border border-line bg-inset/35">
          <div className="border-b border-line px-3 py-2 text-3xs font-semibold uppercase tracking-[0.12em] text-faint">
            Leading diagnostics — not approved Alpha
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
            <div className="px-3 py-6 text-center text-xs text-faint">No evaluated candidate snapshot yet.</div>
          )}
        </div>

        <div className="rounded-lg border border-line bg-inset/35 p-3">
          <div className="text-3xs font-semibold uppercase tracking-[0.12em] text-faint">Current hard gates</div>
          <div className="mt-2 space-y-1.5">
            {gates.slice(0, 4).map(gate => (
              <div key={gate} className="break-words text-3xs text-muted">• {gate}</div>
            ))}
          </div>
          <div className="mt-3 border-t border-line pt-2 text-3xs text-faint">
            Data admission {snapshot?.data_admission.status ?? 'NO_SNAPSHOT'} · registry chain{' '}
            {snapshot?.registry.chain_valid ? 'valid' : 'blocked'}
            {' '}· signal ledger {snapshot?.forward_shadow.ledger.chain_valid ? 'valid' : 'waiting'}
          </div>
          <div className="mt-2 text-3xs text-faint">
            Signal Shadow {engineeringPaperRunning ? 'RUNNING' : 'WAITING'} · formal graduation{' '}
            {graduationGates.length ? graduationGates.join(' · ') : 'gates cleared'}
          </div>
          <div className="mt-2 text-3xs text-faint">
            Execution Paper {executionPaperRunning ? 'RUNNING' : snapshot?.execution_paper?.status ?? 'WAITING'} ·{' '}
              {executionTrackCount} accounts · {executionCount} routed · {noActionDecisions} no-action ·{' '}
              {missedExecutionWindows} audited misses · lot authority{' '}
            {snapshot?.execution_paper?.lot_authority.instrument_count ?? 0} instruments · ledger{' '}
            {snapshot?.execution_paper?.ledger.chain_valid ? 'valid' : 'waiting'}
          </div>
          <div className="mt-2 text-3xs text-faint">
            Capacity max {fmtUsd(capacity?.max_supported_aum_usd)} · shadow sizing{' '}
            {fmtUsd(capacity?.shadow_aum_usd)} · {(capacity?.history_span_ms ?? 0) / 86_400_000 >= 30
              ? '30d evidence ready'
              : `${((capacity?.history_span_ms ?? 0) / 86_400_000).toFixed(2)}d / 30d`}
          </div>
        </div>
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border border-line bg-inset/35">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2 text-3xs text-muted">
          <Chip
            label={feedback?.status ?? 'FEEDBACK WAITING'}
            tone={(feedback?.active_adaptive_candidates ?? 0) > 0 ? 'special' : 'info'}
          />
          <span>
            Learning feedback · {feedback?.consumed_realizations ?? 0} realized outcomes ·{' '}
            {feedback?.active_adaptive_candidates ?? 0} adaptive challengers
          </span>
          <span className="ml-auto">
            {(feedback?.families ?? []).map(row => `${row.family_id}:${row.action}`).join(' · ') || 'family budgets collecting'}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <div className="text-3xs font-semibold uppercase tracking-[0.12em] text-faint">
            全量 Alpha 生命周期与实时业绩
          </div>
          <Chip label={`${lifecycle.length} FACTORS`} tone="info" />
          <Chip label={`${profitable} PROFIT`} tone={profitable ? 'pos' : 'muted'} />
          <Chip label={`${losing} LOSS`} tone={losing ? 'neg' : 'muted'} />
          <Chip label={`${noPnl} NO PNL`} tone="muted" />
          <span className="ml-auto text-3xs text-faint">SSE · realized engineering-shadow net return</span>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2" aria-label="factor performance filters">
          {([
            ['ALL', `ALL ${lifecycle.length}`],
            ['LOSS', `LOSS ${losing}`],
            ['PROFIT', `PROFIT ${profitable}`],
            ['NO_PNL', `NO PNL ${noPnl}`],
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
          <table className="w-full min-w-[940px] text-left text-xs" aria-label="realtime alpha lifecycle performance">
            <thead className="sticky top-0 border-b border-line bg-panel text-3xs uppercase tracking-[0.1em] text-faint">
              <tr>
                <th className="px-3 py-2 font-medium">Factor</th>
                <th className="px-3 py-2 font-medium">Family / horizon</th>
                <th className="px-3 py-2 font-medium">Lifecycle</th>
                <th className="px-3 py-2 text-right font-medium">Research</th>
                <th className="px-3 py-2 text-right font-medium">Paper PnL</th>
                <th className="px-3 py-2 text-right font-medium">Realized</th>
                <th className="px-3 py-2 text-right font-medium">Execution</th>
                <th className="px-3 py-2 font-medium">State</th>
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
                  <td className="px-3 py-2"><Chip label={row.pipeline_stage} tone="info" /></td>
                  <td className={`px-3 py-2 text-right ticker-num ${signedClass(row.research.net_return)}`}>
                    {fmtPct(row.research.net_return)}
                  </td>
                  <td className={`px-3 py-2 text-right ticker-num ${signedClass(row.paper.cum_return)}`}>
                    <div>{fmtPct(row.paper.cum_return)}</div>
                    <div className="mt-0.5 text-3xs text-faint">
                      last {fmtPct(row.paper.performance_history?.[Math.max(0, (row.paper.performance_history?.length ?? 1) - 1)]?.period_return)}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right ticker-num text-muted">
                    {row.paper.n_realizations}/{row.paper.n_decisions}
                  </td>
                  <td className="px-3 py-2 text-right ticker-num text-muted">
                    {row.execution.n_executions} runs · {row.execution.n_orders} orders
                  </td>
                  <td className="px-3 py-2">
                    <Chip
                      label={row.performance_state}
                      tone={row.performance_state === 'LOSS' ? 'neg' : row.performance_state === 'PROFIT' ? 'pos' : 'muted'}
                    />
                  </td>
                </tr>
              )) : (
                <tr><td colSpan={8} className="px-3 py-5 text-center text-faint">No factors match this performance filter.</td></tr>
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
          <Chip label={value.status ?? 'PAPER'} tone="info" />
        </div>
        <div className="mt-1 text-3xs text-faint">{positions.length} active positions · public marks</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Equity</div>
        <div className="ticker-num mt-1 text-sm text-ink">{fmtUsd(value.equity)}</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Net PnL</div>
        <div className={`ticker-num mt-1 text-sm ${signedClass(pnl)}`}>{fmtUsd(pnl)}</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Return</div>
        <div className={`ticker-num mt-1 text-sm ${signedClass(value.return_on_aum)}`}>
          {fmtPct(value.return_on_aum)}
        </div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Forward gate</div>
        <div className="ticker-num mt-1 text-sm text-warn">{forwardPeriods ?? 0} / {(forwardPeriods ?? 0) + (periodsToGate ?? 0)}</div>
        <div className="mt-1 truncate text-3xs text-faint">
          {riskReasons.length ? riskReasons.join(' · ') : reconciled ? 'reconciled' : 'reconciliation check'}
        </div>
      </div>
    </div>
  );
}

function OfficialPaperRow({ alpha, value }: { alpha: string; value: PaperRealtimeAlpha }) {
  const activePositions = Object.values(value.positions ?? {}).filter(quantity => quantity !== 0).length;
  const pending = value.status === 'PAPER_PENDING';
  return (
    <div className="grid gap-3 border-b border-line/70 py-3 last:border-b-0 md:grid-cols-[minmax(170px,1.4fr)_repeat(4,minmax(92px,1fr))] md:items-center">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-ink">{alpha}</span>
          <Chip label={value.status ?? 'NO_STATUS'} tone={pending ? 'warn' : 'info'} />
        </div>
        <div className="mt-1 text-3xs text-faint">main paper account · not shadow challenger</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Equity</div>
        <div className="ticker-num mt-1 text-sm text-ink">{fmtUsd(value.equity)}</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Net PnL</div>
        <div className={`ticker-num mt-1 text-sm ${signedClass(value.net_pnl)}`}>{fmtUsd(value.net_pnl)}</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Return</div>
        <div className={`ticker-num mt-1 text-sm ${signedClass(value.return_on_aum)}`}>{fmtPct(value.return_on_aum)}</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Activity</div>
        <div className="ticker-num mt-1 text-sm text-muted">{value.n_fills ?? 0} fills · {activePositions} positions</div>
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
          {failedEngines.length ? `blocked by ${failedEngines.join(', ')}` : risk?.status ?? 'NO RISK SNAPSHOT'}
        </div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Capacity</div>
        <div className={`ticker-num mt-1 text-sm ${capacity?.passed ? 'text-pos' : 'text-warn'}`}>
          {capacity?.capacity_usage == null ? '--' : `${(capacity.capacity_usage * 100).toFixed(1)}%`}
        </div>
        <div className="mt-1 truncate text-3xs text-faint">{capacity?.bottleneck_alpha ?? 'no bottleneck'}</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">DSR confidence</div>
        <div className={`ticker-num mt-1 text-sm ${(validation ?? 0) >= 0.3 ? 'text-pos' : 'text-warn'}`}>
          {validation == null ? '--' : validation.toFixed(3)}
        </div>
        <div className="mt-1 text-3xs text-faint">minimum 0.300</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Forward confidence</div>
        <div className={`ticker-num mt-1 text-sm ${(paper ?? 0) >= 0.3 ? 'text-pos' : 'text-warn'}`}>
          {paper == null ? '--' : paper.toFixed(3)}
        </div>
        <div className="mt-1 text-3xs text-faint">
          {model?.alphas_with_decay ? `${model.alphas_with_decay} decay flag` : 'minimum 0.300'}
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
  const alphaRows = Object.entries(shadow?.alphas ?? {});
  const officialRows = Object.entries(officialPaper?.alphas ?? {});
  const officialProfit = officialRows.filter(([, alpha]) => (alpha.net_pnl ?? 0) > 0 && (alpha.n_fills ?? 0) > 0).length;
  const officialLoss = officialRows.filter(([, alpha]) => (alpha.net_pnl ?? 0) < 0 && (alpha.n_fills ?? 0) > 0).length;
  const officialPending = officialRows.filter(([, alpha]) => alpha.status === 'PAPER_PENDING' || (alpha.n_fills ?? 0) === 0).length;
  const sortedOfficialRows = officialRows.slice().sort(([, a], [, b]) => {
    const rank = (value: PaperRealtimeAlpha) => value.status === 'PAPER_PENDING' || (value.n_fills ?? 0) === 0
      ? 2
      : (value.net_pnl ?? 0) < 0 ? 0 : 1;
    return rank(a) - rank(b) || (a.net_pnl ?? 0) - (b.net_pnl ?? 0);
  });
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
      detail: `${shadow?.provider ?? 'NO_PROVIDER'} · age ${fmtAge(observedAgeMs)}`,
      ok: Boolean(shadow?.verdict.paper_mark_ready && markFresh),
    },
    {
      label: '自动调度与再平衡',
      detail: `${schedulerChecks.length}/2 required tasks healthy`,
      ok: schedulerHealthy && Boolean(shadow?.verdict.auto_rebalance_ready),
    },
    {
      label: '影子纸面执行',
      detail: `${alphaRows.length} isolated books · ${totalPositions} positions`,
      ok: Boolean(shadow?.verdict.shadow_paper_running),
    },
    {
      label: '逐笔估值与对账',
      detail: reconciled ? 'all residuals ≈ 0' : 'residual requires attention',
      ok: reconciled,
    },
    {
      label: '连续运行与故障恢复',
      detail: `${operational?.window.accepted_cycle_count ?? 0} cycles · ${passedDrills}/${drillEntries.length} drills`,
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
    ? 'SSE CONNECTING'
    : apiState === 'loading' && usingBackend
      ? 'SSE RECONNECTING'
    : apiState === 'connected' && usingBackend
      ? 'SSE LIVE'
      : 'API UNAVAILABLE';
  const transportTone: Tone = initialLoading || apiState === 'loading'
    ? 'info'
    : apiState === 'connected' && usingBackend
      ? 'pos'
      : 'neg';
  const automationLabel = initialLoading
    ? 'CONNECTING'
    : shadowRunning
      ? 'SCHEDULED_ACTIVE'
      : 'SAFE_BLOCKED';
  const automationTone: Tone = initialLoading ? 'info' : shadowRunning ? 'pos' : 'neg';

  return (
    <PageShell>
      <div className="flex flex-col gap-3 border-b border-line pb-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${
              initialLoading ? 'q-live bg-info' : shadowRunning ? 'q-live bg-pos' : 'bg-neg'
            }`} />
            <span className="text-3xs font-semibold uppercase tracking-[0.16em] text-faint">Paper Operations</span>
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
          <Chip label="L2 PAPER ONLY" tone="info" />
          <Chip label="LIVE OFF" tone="neg" />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-inset/50 px-3 py-2 text-3xs text-faint">
        <Chip label={transportLabel} tone={transportTone} />
        <span>{initialLoading ? 'AWAITING READ-ONLY SNAPSHOT' : shadow?.provider ?? 'NO REAL PROVIDER'}</span>
        <span className="hidden text-line-strong sm:inline">|</span>
        <span>
          {initialLoading
            ? 'transport handshake in progress'
            : <>更新 {fmtTime(observedAt)} · {fmtAge(observedAgeMs)} ago</>}
        </span>
        <span className="ml-auto">
          {error
            ? <span className="text-neg">{error}</span>
            : initialLoading ? 'NO DECISION DATA YET' : data.data_provenance}
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
            label="Shadow total equity"
            value={fmtUsd(shadow?.totals.equity)}
            detail={`${alphaRows.length} isolated books`}
            tone="accent"
          />
          <Metric
            label="Shadow net PnL"
            value={fmtUsd(shadow?.totals.net_pnl)}
            detail={`funding ${fmtUsd(shadow?.totals.funding_pnl)}`}
            tone={(shadow?.totals.net_pnl ?? 0) >= 0 ? 'pos' : 'neg'}
          />
          <Metric label="Positions" value={totalPositions} detail="marked instruments" tone="info" />
          <Metric
            label="Reconciliation"
            value={reconciled ? 'CLEAN' : 'CHECK'}
            detail={shadow?.persistence.chain_valid ? 'hash chain valid' : 'chain invalid'}
            tone={reconciled && shadow?.persistence.chain_valid ? 'pos' : 'neg'}
          />
          <Metric
            label="Official paper"
            value={health?.verdict.paper_ready ? 'READY' : 'GATED'}
            detail={health?.readiness_class ?? 'no health snapshot'}
            tone={health?.verdict.paper_ready ? 'pos' : 'warn'}
          />
        </div>

        <div className="mt-4">
          <Panel
            title="主纸面账户实时业绩"
            eyebrow="paper_realtime · authoritative main paper account"
            right={(
              <div className="flex flex-wrap items-center gap-2">
                <Chip label={`${officialLoss} LOSS`} tone={officialLoss ? 'neg' : 'muted'} />
                <Chip label={`${officialProfit} PROFIT`} tone={officialProfit ? 'pos' : 'muted'} />
                <Chip label={`${officialPending} PENDING`} tone="warn" />
                <Chip label={`${officialRows.length} ACCOUNTS`} tone="info" />
              </div>
            )}
            bodyClass="px-4 py-2"
          >
            <div className="mb-2 rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-3xs text-muted">
              该区域与下方影子 Challenger 账户完全分开。亏损优先排序；PAPER_PENDING 的初始权益和 0 PnL 不算盈利，也不以影子收益替代。
            </div>
            {sortedOfficialRows.length ? sortedOfficialRows.map(([alpha, value]) => (
              <OfficialPaperRow key={alpha} alpha={alpha} value={value} />
            )) : (
              <div className="py-6 text-center text-xs text-faint">Main paper account snapshot unavailable.</div>
            )}
          </Panel>
        </div>

        <div className="mt-4">
          <Panel
            title="Alpha pipeline"
            eyebrow="Discovery -> forward challenger -> candidate paper"
            right={
              <Chip
                label={alphaPipeline?.handoff.status ?? 'NO PIPELINE'}
                tone={alphaPipeline?.handoff.status === 'CONNECTED' ? 'pos' : 'warn'}
              />
            }
            bodyClass="p-4"
          >
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Metric
                label="Factory"
                value={alphaPipeline?.factory.survivors ?? 0}
                detail={`${alphaPipeline?.factory.data_dimensions ?? 0} data dimensions / ${alphaPipeline?.factory.rounds ?? 0} rounds`}
                tone="special"
              />
              <Metric
                label="Forward paper"
                value={alphaPipeline?.counts.forward_paper ?? 0}
                detail={`${alphaPipeline?.counts.forward_only ?? 0} still at forward gate`}
                tone="info"
              />
              <Metric
                label="Engineering paper"
                value={shadow?.candidate_universe?.running_count ?? alphaRows.length}
                detail={`${alphaPipeline?.counts.candidate_execution_paper ?? 0} official candidates; isolated shadow books`}
                tone="accent"
              />
              <Metric
                label="Legacy paper"
                value={alphaPipeline?.counts.legacy_diagnostic_paper ?? 0}
                detail="diagnostic only; quarantined"
                tone={(alphaPipeline?.counts.legacy_diagnostic_paper ?? 0) > 0 ? 'warn' : 'pos'}
              />
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-md border border-line bg-inset/35 px-3 py-2 text-xs text-muted">
              <Chip
                label={alphaPipeline?.workflow?.verified ? 'ONE WORKFLOW' : 'WORKFLOW GATED'}
                tone={alphaPipeline?.workflow?.verified ? 'pos' : 'warn'}
              />
              <span>
                {alphaPipeline?.workflow?.population_count ?? alphaPipeline?.counts.forward_paper ?? 0} frozen identities
                {' / '}{alphaPipeline?.workflow?.realtime_handoff_count ?? 0} realtime handoff
                {' / '}{alphaPipeline?.workflow?.event_count ?? 0} evidence events
                {' / '}{alphaPipeline?.workflow?.execution_paper_eligible_count ?? 0} paper-eligible now
              </span>
              {alphaPipeline?.workflow?.release_commit ? (
                <span className="font-mono text-faint">
                  release {alphaPipeline.workflow.release_commit.slice(0, 12)}
                </span>
              ) : null}
            </div>

            {alphaPipeline?.exception_queue?.length ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-3xs text-muted" aria-label="alpha exception queue">
                <span className="uppercase tracking-[0.12em] text-faint">Exception queue</span>
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
                  <span className="text-faint">+{alphaPipeline.exception_queue.length - 4} more</span>
                ) : null}
              </div>
            ) : null}

            <div className="mt-4 overflow-hidden rounded-lg border border-line bg-inset/35">
              <table className="w-full table-fixed text-left text-xs" aria-label="alpha paper pipeline">
                <thead className="border-b border-line text-3xs uppercase tracking-[0.12em] text-faint">
              <tr>
                    <th className="w-[30%] px-3 py-2 font-medium">Alpha</th>
                    <th className="w-[18%] px-3 py-2 font-medium">Stage</th>
                    <th className="px-3 py-2 text-right font-medium">OOS</th>
                    <th className="px-3 py-2 text-right font-medium">Pending</th>
                    <th className="px-3 py-2 text-right font-medium">Return</th>
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
                            ? 'LEGACY PAPER'
                            : candidate.execution_paper
                              ? 'CANDIDATE PAPER'
                            : candidate.execution_paper_eligible
                              ? 'PAPER ELIGIBLE'
                            : candidate.stage === 'PARALLEL_VALIDATION'
                              ? 'VALIDATING'
                            : candidate.independent_evaluation?.status === 'VERIFIED'
                              ? 'EVALUATED'
                              : 'FORWARD'}
                          tone={candidate.paper_class === 'LEGACY_DIAGNOSTIC_PAPER'
                            ? 'warn'
                            : candidate.execution_paper ? 'pos' : 'info'}
                        />
                        {candidate.paper_class === 'LEGACY_DIAGNOSTIC_PAPER' ? (
                          <div className="mt-1 text-3xs text-warn">
                            diagnostic only · never deployment eligible
                          </div>
                        ) : !candidate.execution_paper ? (
                          <div
                            className="mt-1 text-3xs text-faint"
                            title={[
                              candidate.risk_packet?.decision_basis,
                              ...(candidate.risk_packet?.admission_blockers ?? []),
                            ].filter(Boolean).join(' · ') || undefined}
                          >
                            Eval {candidate.independent_evaluation?.verdict ?? 'PENDING'} · Inputs {candidate.risk_packet?.input_evidence_status ?? 'PENDING'} · Admission {candidate.risk_packet?.status ?? 'PENDING'}
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
                      <td colSpan={5} className="px-3 py-5 text-center text-faint">No attested forward-paper candidates.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {alphaPipeline?.handoff.gap_codes.length ? (
              <Callout summary="Pipeline handoff is not fully connected" tone="warn">
                {alphaPipeline.handoff.gap_codes.map(code => <div key={code}>- {code}</div>)}
                {alphaPipeline.factory.verdict ? <div>- Factory: {alphaPipeline.factory.verdict}</div> : null}
              </Callout>
            ) : null}
            {alphaPipeline?.handoff.boundary_codes?.length ? (
              <Callout summary="Pipeline safety boundaries are active" tone="info">
                {alphaPipeline.handoff.boundary_codes.map(code => <div key={code}>- {code}</div>)}
              </Callout>
            ) : null}
          </Panel>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(300px,.7fr)]">
          <Panel
            title="影子 Challenger 实时权益与策略账簿"
            eyebrow="paper_shadow · isolated non-promotable books"
            right={<Chip label="SSE STREAM" tone="info" />}
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
                <div className="py-8 text-center text-xs text-faint">No real shadow-paper snapshot.</div>
              )}
            </div>
          </Panel>

          <Panel
            title="自动流水线"
            eyebrow="Automation control"
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
            eyebrow="Evidence gate"
            right={<Chip label={health?.verdict.paper_ready ? 'PASS' : 'BLOCKED'} tone={health?.verdict.paper_ready ? 'pos' : 'warn'} />}
            bodyClass="p-4"
          >
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Metric
                label="Workflow authority"
                value={data.paper_order_preview?.verdict.workflow_admission_valid ? 'VERIFIED' : 'BLOCKED'}
                detail={`${data.paper_order_preview?.verdict.workflow_authorized_ids?.length ?? 0} candidates authorized`}
                tone={data.paper_order_preview?.verdict.workflow_admission_valid ? 'pos' : 'neg'}
              />
              <Metric
                label="Risk inputs"
                value={`${precomputedRiskInputs}/${pipelineCandidates.length || 0}`}
                detail="precomputed · admission separate"
                tone={precomputedRiskInputs === pipelineCandidates.length && precomputedRiskInputs > 0 ? 'pos' : 'warn'}
              />
              <Metric
                label="Forward"
                value={Object.values(forwardByAlpha)[0]?.n_forward_periods ?? 0}
                detail={health?.forward_evidence?.evidence_maturing
                  ? `next ${stamp(health.forward_evidence.next_realization_eligible_at)} UTC`
                  : `target ${health?.forward_evidence?.evidence_gate_periods ?? 8}`}
                tone="warn"
              />
              <Metric
                label="Promotion"
                value="OFF"
                detail="human-only boundary"
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
                    No formal pre-fill risk snapshot.
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
                : 'No active blocker reported.'}
            </Callout>
          </Panel>

          <Panel
            title="运行完整性"
            eyebrow="Operational integrity"
            right={<Chip label={shadow?.persistence.chain_valid ? 'CHAIN VALID' : 'CHAIN CHECK'} tone={shadow?.persistence.chain_valid ? 'pos' : 'neg'} />}
            bodyClass="p-4"
          >
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              <Metric label="Ledger rows" value={shadow?.paper_ledger.n_rows ?? 0} detail={`${shadow?.paper_ledger.n_decisions ?? 0} decisions`} tone="info" />
              <Metric label="Snapshots" value={shadow?.persistence.new_snapshots ?? 0} detail="append-only cycle" tone="info" />
              <Metric
                label="Operating window"
                value={fmtAge(operational?.window.history_span_ms ?? null)}
                detail={`${operational?.window.accepted_cycle_count ?? 0}/${operational?.policy?.production_minimum_successes ?? 274} accepted`}
                tone={operational?.window.production_window_ready ? 'pos' : 'warn'}
              />
              <Metric
                label="Fault drills"
                value={`${passedDrills}/${drillEntries.length}`}
                detail="isolated temporary stores"
                tone={drillEntries.length > 0 && passedDrills === drillEntries.length ? 'pos' : 'warn'}
              />
              <Metric
                label="Restart recovery"
                value={restartProven ? 'PROVEN' : 'MATURING'}
                detail="release transition ≤15m"
                tone={restartProven ? 'pos' : 'warn'}
              />
              <Metric label="Data refresh" value={data.data_refresh?.outcome ?? '—'} detail={`${data.data_refresh?.failed_total ?? 0} failures`} tone={(data.data_refresh?.failed_total ?? 0) === 0 ? 'pos' : 'neg'} />
              <Metric label="Broker path" value="OFF" detail="public data only" tone="neg" />
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
