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
  RealtimeAlphaAutonomyPayload,
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

function AutonomousAlphaPanel({ snapshot }: { snapshot?: RealtimeAlphaAutonomyPayload }) {
  const running = Boolean(
    snapshot?.boundary_valid && snapshot.autonomy.autonomous_search_running,
  );
  const spanDays = (snapshot?.data_admission.common_span_ms ?? 0) / 86_400_000;
  const candidates = snapshot?.top_candidates ?? [];
  const capacity = snapshot?.executable_depth_capacity;
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
            label={snapshot?.forward_shadow.status ?? 'SHADOW NO SNAPSHOT'}
            tone={snapshot?.forward_shadow.verdict.forward_shadow_running ? 'pos' : 'warn'}
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
          detail={`${snapshot?.funnel.evaluated ?? 0} evaluated`}
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
            {' '}· forward ledger {snapshot?.forward_shadow.ledger.chain_valid ? 'valid' : 'waiting'}
          </div>
          <div className="mt-2 text-3xs text-faint">
            Capacity max {fmtUsd(capacity?.max_supported_aum_usd)} · shadow sizing{' '}
            {fmtUsd(capacity?.shadow_aum_usd)} · {(capacity?.history_span_ms ?? 0) / 86_400_000 >= 30
              ? '30d evidence ready'
              : `${((capacity?.history_span_ms ?? 0) / 86_400_000).toFixed(2)}d / 30d`}
          </div>
        </div>
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
        <div className={`ticker-num mt-1 text-sm ${(pnl ?? 0) >= 0 ? 'text-pos' : 'text-neg'}`}>{fmtUsd(pnl)}</div>
      </div>
      <div>
        <div className="text-3xs uppercase tracking-wider text-faint">Return</div>
        <div className={`ticker-num mt-1 text-sm ${(value.return_on_aum ?? 0) >= 0 ? 'text-pos' : 'text-neg'}`}>
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
  const health = data.paper_health;
  const scheduler = data.scheduler_watchdog;
  const realtimeAlpha = data.realtime_alpha_autonomy;
  const alphaPipeline = data.alpha_pipeline;
  const pipelineCandidates = alphaPipeline?.candidates ?? [];
  const alphaRows = Object.entries(shadow?.alphas ?? {});
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
            全自动影子纸面驾驶舱
          </h1>
          <p className="mt-1 text-xs text-muted">
            实时行情、自动再平衡、估值与对账；独立、不可晋级的自动观察账户。
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
            label="Total equity"
            value={fmtUsd(shadow?.totals.equity)}
            detail={`${alphaRows.length} isolated books`}
            tone="accent"
          />
          <Metric
            label="Net PnL"
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
                {' / '}{alphaPipeline?.workflow?.event_count ?? 0} evidence events
                {' / '}{alphaPipeline?.workflow?.execution_paper_eligible_count ?? 0} paper-eligible now
              </span>
              {alphaPipeline?.workflow?.release_commit ? (
                <span className="font-mono text-faint">
                  release {alphaPipeline.workflow.release_commit.slice(0, 12)}
                </span>
              ) : null}
            </div>

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
                          <div className="mt-1 text-3xs text-faint">
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
            title="实时权益与策略账簿"
            eyebrow="Live paper books"
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
            right={<StatusChip value={scheduler?.verdict ?? 'NO_SNAPSHOT'} />}
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
            <div className="grid grid-cols-3 gap-3">
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
            <div className="grid grid-cols-2 gap-3">
              <Metric label="Ledger rows" value={shadow?.paper_ledger.n_rows ?? 0} detail={`${shadow?.paper_ledger.n_decisions ?? 0} decisions`} tone="info" />
              <Metric label="Snapshots" value={shadow?.persistence.new_snapshots ?? 0} detail="append-only cycle" tone="info" />
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
