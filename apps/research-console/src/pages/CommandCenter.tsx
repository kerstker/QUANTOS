import { useEffect, useMemo, useState } from 'react';
import { Link } from '@/lib/router';
import { fetchDryRunSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { DryRunPipelinePayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  Chip,
  LoadingVeil,
  PageHeader,
  PageShell,
  Panel,
} from '@/ui/primitives';

type Tone = 'pos' | 'neg' | 'warn' | 'info' | 'accent';

function fmtUsd(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '—';
  if (Math.abs(value) >= 1_000_000) {
    const sign = value < 0 ? '-' : '';
    const compact = (Math.abs(value) / 1_000_000)
      .toFixed(2)
      .replace(/\.?0+$/, '');
    return `${sign}$${compact}M`;
  }
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);
}

function fmtAge(ageMs: number | null): string {
  if (ageMs == null) return '—';
  if (ageMs < 60_000) return `${Math.floor(ageMs / 1_000)}s`;
  return `${Math.floor(ageMs / 60_000)}m`;
}

function Metric({
  label,
  value,
  detail,
  tone = 'info',
}: {
  label: string;
  value: string | number;
  detail: string;
  tone?: Tone;
}) {
  const toneClass: Record<Tone, string> = {
    pos: 'text-pos',
    neg: 'text-neg',
    warn: 'text-warn',
    info: 'text-info',
    accent: 'text-accent',
  };
  return (
    <div className="min-w-0">
      <div className="text-3xs font-semibold uppercase tracking-[0.12em] text-faint">{label}</div>
      <div className={`ticker-num mt-1 truncate text-xl font-semibold ${toneClass[tone]}`}>{value}</div>
      <div className="mt-1 truncate text-3xs text-faint">{detail}</div>
    </div>
  );
}

function QuickLink({ to, zh, en }: { to: string; zh: string; en: string }) {
  return (
    <Link to={to}
      className="flex items-center justify-between rounded-md border border-line bg-inset/50 px-3 py-2 text-xs text-muted transition-colors hover:border-accent/40 hover:text-ink">
      <span>{bi(zh, en)}</span>
      <span aria-hidden className="text-accent">→</span>
    </Link>
  );
}

export default function CommandCenter() {
  const [refreshTick, setRefreshTick] = useState(0);
  useEffect(() => {
    if (!isResearchConsoleApiEnabled()) return undefined;
    const timer = window.setInterval(() => setRefreshTick(value => value + 1), 10_000);
    return () => window.clearInterval(timer);
  }, []);

  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<DryRunPipelinePayload>(
    signal => fetchDryRunSnapshot(signal),
    [refreshTick],
  );
  const data = usingBackend ? payload : null;
  const shadow = data?.paper_shadow;
  const health = data?.paper_health;
  const scheduler = data?.scheduler_watchdog;
  const observedAt = shadow?.observed_at ?? null;
  const observedAgeMs = observedAt == null ? null : Math.max(0, Date.now() - observedAt);
  const markFresh = observedAgeMs != null && observedAgeMs <= (health?.max_age_ms ?? 300_000);
  const paperRunning = Boolean(
    shadow?.verdict.shadow_paper_running
    && shadow.verdict.paper_mark_ready
    && markFresh
    && shadow.persistence.chain_valid,
  );
  const alphaRows = Object.entries(shadow?.alphas ?? {});
  const positionCount = alphaRows.reduce(
    (total, [, alpha]) => total + Object.values(alpha.positions ?? {}).filter(quantity => quantity !== 0).length,
    0,
  );
  const reconciled = alphaRows.length > 0 && alphaRows.every(
    ([, alpha]) => Math.abs(alpha.reconciliation_residual ?? Number.POSITIVE_INFINITY) < 1e-6,
  );
  const forwardRows = Object.values(health?.forward_evidence?.alphas ?? {});
  const forwardPeriods = forwardRows.length
    ? Math.min(...forwardRows.map(row => row.n_forward_periods ?? 0))
    : 0;
  const forwardTarget = health?.forward_evidence?.evidence_gate_periods ?? 8;
  const schedulerSummary = scheduler?.summary ?? {};
  const schedulerBreaches = schedulerSummary.BREACH ?? 0;
  const issueLines = useMemo(
    () => [
      ...(health?.blockers ?? []),
      ...Object.entries(shadow?.errors ?? {}).map(([alpha, issue]) => `${alpha}: ${issue}`),
    ],
    [health?.blockers, shadow?.errors],
  );
  const transportOk = apiState === 'connected' && usingBackend;

  return (
    <PageShell>
      <PageHeader
        title="Operations Command"
        titleZh="纸面交易指挥中心"
        subtitle={bi(
          '一屏只回答四件事：纸面是否在跑、证据是否成熟、数据与调度是否健康、安全边界是否完整。每 10 秒读取真实聚合 API。',
          'One screen answers four questions: is paper running, is evidence mature, are data and schedules healthy, and is the safety boundary intact. Real aggregate API, polled every 10 seconds.',
        )}
        actions={(
          <div className="flex flex-wrap items-center gap-2">
            <Chip label={transportOk ? 'REAL API' : apiState === 'loading' ? 'CONNECTING' : 'API UNAVAILABLE'}
              tone={transportOk ? 'pos' : apiState === 'loading' ? 'info' : 'neg'} />
            <Chip label={paperRunning ? 'PAPER ACTIVE' : 'PAPER GATED'} tone={paperRunning ? 'pos' : 'warn'} />
            <Chip label="LIVE OFF" tone="neg" />
          </div>
        )}
      />

      {error && (
        <div role="alert" className="mb-4 rounded-lg border border-neg/30 bg-neg/10 px-4 py-3 text-xs text-neg">
          {error}
        </div>
      )}

      <LoadingVeil active={apiState === 'loading'}>
        <div className="grid gap-4 xl:grid-cols-2">
          <Panel title={bi('实时纸面运行', 'Live paper operation')} eyebrow="01 · PAPER"
            right={<Chip label={paperRunning ? 'RUNNING' : 'CHECK'} tone={paperRunning ? 'pos' : 'warn'} />}
            bodyClass="p-4">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Metric label="Equity" value={fmtUsd(shadow?.totals.equity)}
                detail={`${alphaRows.length} isolated books`} tone="accent" />
              <Metric label="Net PnL" value={fmtUsd(shadow?.totals.net_pnl)}
                detail={`funding ${fmtUsd(shadow?.totals.funding_pnl)}`}
                tone={(shadow?.totals.net_pnl ?? 0) >= 0 ? 'pos' : 'neg'} />
              <Metric label="Positions" value={positionCount}
                detail={`${alphaRows.reduce((sum, [, alpha]) => sum + (alpha.n_fills ?? 0), 0)} paper fills`} />
              <Metric label="Reconciliation" value={reconciled ? 'CLEAN' : 'CHECK'}
                detail={shadow?.persistence.chain_valid ? 'hash chain valid' : 'chain unavailable'}
                tone={reconciled && shadow?.persistence.chain_valid ? 'pos' : 'neg'} />
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3 text-3xs text-faint">
              <span>{shadow?.provider ?? 'NO_PROVIDER'}</span>
              <span>·</span>
              <span>{bi('快照年龄', 'snapshot age')} {fmtAge(observedAgeMs)}</span>
              <span>·</span>
              <span>{shadow?.execution_class ?? 'NO_EXECUTION_CLASS'}</span>
            </div>
          </Panel>

          <Panel title={bi('证据与风险门', 'Evidence and risk gate')} eyebrow="02 · EVIDENCE"
            right={<Chip label={health?.verdict.paper_ready ? 'READY' : 'MATURING'}
              tone={health?.verdict.paper_ready ? 'pos' : 'warn'} />}
            bodyClass="p-4">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Metric label="Forward" value={`${forwardPeriods}/${forwardTarget}`}
                detail="independent periods" tone={forwardPeriods >= forwardTarget ? 'pos' : 'warn'} />
              <Metric label="Risk bundles" value={health?.research_risk_bundle_complete ? '3/3' : 'CHECK'}
                detail="nine-engine inputs" tone={health?.research_risk_bundle_complete ? 'pos' : 'warn'} />
              <Metric label="Official paper" value={health?.verdict.paper_ready ? 'READY' : 'GATED'}
                detail={health?.readiness_class ?? 'NO_HEALTH'} tone={health?.verdict.paper_ready ? 'pos' : 'warn'} />
              <Metric label="Promotion" value="HUMAN"
                detail="structurally non-automatic" tone="neg" />
            </div>
            <div className="mt-4 rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
              <div className="font-medium text-ink">{health?.next_action ?? 'Await fresh forward evidence.'}</div>
              <div className="mt-1 truncate text-faint">
                {issueLines.length ? `${issueLines.length} active gate reasons · ${issueLines[0]}` : 'No active blocker reported.'}
              </div>
            </div>
          </Panel>

          <Panel title={bi('数据与调度', 'Data and scheduling')} eyebrow="03 · OPERATIONS"
            right={<Chip label={schedulerBreaches === 0 ? 'NO BREACH' : `${schedulerBreaches} BREACH`}
              tone={schedulerBreaches === 0 ? 'pos' : 'neg'} />}
            bodyClass="p-4">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Metric label="Refresh" value={data?.data_refresh?.outcome ?? '—'}
                detail={`${data?.data_refresh?.failed_total ?? 0} failures`}
                tone={(data?.data_refresh?.failed_total ?? 0) === 0 ? 'pos' : 'neg'} />
              <Metric label="Scheduler" value={scheduler?.verdict ?? '—'}
                detail={`${schedulerSummary.PASS ?? 0} checks pass`}
                tone={schedulerBreaches === 0 ? 'pos' : 'neg'} />
              <Metric label="Dry runs" value={data?.n_runs ?? 0}
                detail="append-only records" />
              <Metric label="TCA" value={data?.route_tca_history?.record_count ?? 0}
                detail={data?.route_tca_history?.status ?? 'NO HISTORY'}
                tone={data?.route_tca_history?.status === 'LONGITUDINAL_READY' ? 'pos' : 'warn'} />
            </div>
          </Panel>

          <Panel title={bi('安全边界', 'Safety boundary')} eyebrow="04 · CONTROL"
            right={<Chip label="L2 PAPER ONLY" tone="info" />}
            bodyClass="p-4">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Metric label="Public data" value={shadow?.safety.public_market_data_only ? 'ONLY' : 'CHECK'}
                detail="no private account API" tone={shadow?.safety.public_market_data_only ? 'pos' : 'neg'} />
              <Metric label="Credentials" value={shadow?.safety.credentials_accessed ? 'TOUCHED' : 'NONE'}
                detail="paper worker boundary" tone={shadow?.safety.credentials_accessed ? 'neg' : 'pos'} />
              <Metric label="Broker" value={shadow?.safety.broker_connectivity ? 'CONNECTED' : 'OFF'}
                detail="no venue session" tone={shadow?.safety.broker_connectivity ? 'neg' : 'pos'} />
              <Metric label="Submission" value={shadow?.safety.broker_order_submission ? 'ENABLED' : 'BLOCKED'}
                detail="live structurally closed" tone={shadow?.safety.broker_order_submission ? 'neg' : 'pos'} />
            </div>
          </Panel>
        </div>

        <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <QuickLink to="/research/dry-run" zh="打开纸面驾驶舱" en="Open paper cockpit" />
          <QuickLink to="/research/risk" zh="查看风险门" en="Inspect risk gates" />
          <QuickLink to="/research/data-catalog" zh="检查数据状态" en="Inspect data state" />
          <QuickLink to="/research/system-health" zh="查看系统健康" en="Open system health" />
        </div>
      </LoadingVeil>
    </PageShell>
  );
}
