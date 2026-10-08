import type { ReactNode } from 'react';

import { fetchDataCatalogSnapshot } from '@/lib/api/research-console-api';
import type { BinanceRealtimePayload, DataCatalogPayload } from '@/lib/types/research';
import { PageShell } from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import type { JsonObject, MftSnapshot } from '@/workbench/contracts';
import { useMftReadModel } from '@/workbench/useMftReadModel';

function text(section: JsonObject, key: string): string | null {
  const value = section[key];
  return typeof value === 'string' && value.trim() ? value : null;
}

function number(section: JsonObject, key: string): number | null {
  const value = section[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function boolean(section: JsonObject, key: string): boolean | null {
  const value = section[key];
  return typeof value === 'boolean' ? value : null;
}

function timestamp(value: number | null): string {
  if (value === null) return 'UNKNOWN';
  return new Date(value).toISOString().replace('.000Z', 'Z');
}

function currency(value: number | null): string {
  if (value === null) return 'UNKNOWN';
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function percent(value: number | null): string {
  if (value === null) return 'UNKNOWN';
  return `${(value * 100).toFixed(2)}%`;
}

function snapshotId(snapshot: MftSnapshot): string {
  return snapshot.snapshot_id?.slice(0, 12) ?? 'UNKNOWN';
}

function SourceLine({ snapshot }: { snapshot: MftSnapshot }) {
  return (
    <div className="mt-4 flex flex-wrap gap-x-3 gap-y-1 border-t border-line/70 pt-3 text-[9px] uppercase tracking-[0.1em] text-faint">
      <span>{snapshot.source}</span>
      <span>snapshot {snapshotId(snapshot)}</span>
      <span>as-of {timestamp(snapshot.as_of_ms)}</span>
      <span>{snapshot.freshness.status} · age {snapshot.freshness.age_ms ?? 'UNKNOWN'}ms</span>
    </div>
  );
}

function Metric({ label, value, accent = false }: { label: string; value: ReactNode; accent?: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-inset/50 px-3 py-3">
      <div className="text-[9px] font-semibold uppercase tracking-[0.13em] text-faint">{label}</div>
      <div className={`mt-1 break-words font-mono text-sm font-semibold ${accent ? 'text-accent' : 'text-ink'}`}>
        {value}
      </div>
    </div>
  );
}

function MonitorPanel({ title, eyebrow, snapshot, children }: {
  title: string;
  eyebrow: string;
  snapshot: MftSnapshot;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-panel p-4 shadow-panel sm:p-5">
      <p className="text-[9px] font-semibold uppercase tracking-[0.16em] text-accent">{eyebrow}</p>
      <h2 className="mt-1 text-lg font-semibold text-ink">{title}</h2>
      <div className="mt-4">{children}</div>
      <SourceLine snapshot={snapshot} />
    </section>
  );
}

function diagnosticCode(phase: ReturnType<typeof useMftReadModel>['phase']): string {
  if (phase === 'STALE') return 'MFT_STALE';
  if (phase === 'RESNAPSHOT_REQUIRED') return 'MFT_RESNAPSHOT_REQUIRED';
  if (phase === 'RECONNECTING') return 'MFT_RECONNECTING';
  if (phase === 'LOADING') return 'MFT_LOADING';
  return 'MFT_UNAVAILABLE';
}

function DiagnosticOnly({ state, reason, publicMarket }: {
  state: ReturnType<typeof useMftReadModel>;
  reason?: string | null;
  publicMarket?: BinanceRealtimePayload | null;
}) {
  const notProvisioned = state.snapshot?.availability_mode === 'NOT_PROVISIONED'
    || state.reason === 'STORE_MISSING';
  const eventCount = publicMarket
    ? Object.values(publicMarket.stream.event_counts).reduce(
      (total: number, value: number) => total + value,
      0,
    )
    : 0;
  const complete = publicMarket?.rest_snapshot.coverage.complete ?? 0;
  const requested = publicMarket?.rest_snapshot.coverage.requested ?? publicMarket?.universe_size ?? 0;
  return (
    <PageShell>
      <header>
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Market Monitor</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">市场监控</h1>
        <p className="mt-2 max-w-3xl text-sm text-muted">
          MFT 执行权威不可验证；执行、仓位、收益与订单数字保持隐藏，公开市场监控独立展示。
        </p>
      </header>
      <section className={`rounded-xl border p-5 ${notProvisioned ? 'border-warn/35 bg-warn/10' : 'border-neg/35 bg-neg/10'}`} role="alert">
        <div className="flex flex-wrap items-center gap-2">
          <strong className={`font-mono text-sm ${notProvisioned ? 'text-warn' : 'text-neg'}`}>
            {notProvisioned ? 'MFT_VIEW_NOT_PROVISIONED' : diagnosticCode(state.phase)}
          </strong>
          <span className={`rounded border px-2 py-0.5 text-[10px] font-semibold ${notProvisioned ? 'border-warn/30 text-warn' : 'border-neg/30 text-neg'}`}>
            {notProvisioned ? 'EXPECTED_L2_CONFIGURATION' : 'DIAGNOSTIC_ONLY'}
          </span>
        </div>
        <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div><dt className="text-faint">phase</dt><dd className="mt-1 font-mono text-ink">{state.phase}</dd></div>
          <div><dt className="text-faint">reason</dt><dd className="mt-1 font-mono text-ink">{reason ?? state.reason ?? 'UNKNOWN'}</dd></div>
          <div><dt className="text-faint">transport</dt><dd className="mt-1 font-mono text-ink">{state.transport}</dd></div>
          <div><dt className="text-faint">retained snapshot</dt><dd className="mt-1 font-mono text-ink">{state.snapshot ? snapshotId(state.snapshot) : 'NONE'}</dd></div>
        </dl>
        <p className="mt-4 text-xs leading-5 text-muted">
          {notProvisioned
            ? '本发布没有 MFT 读模型生产者；公开行情继续独立运行。执行、收益和仓位字段保持隐藏，直到新的生产者完成接入。'
            : '不使用合成 MFT 回退，不展示保留快照中的执行、收益或风险值，也不生成交易建议。'}
        </p>
      </section>
      {publicMarket ? (
        <section className="rounded-xl border border-pos/35 bg-pos/10 p-5" aria-label="公开市场实时监控">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="font-mono text-sm text-pos">REAL_PUBLIC_MARKET_MONITORING</strong>
            <span className="rounded border border-warn/35 px-2 py-0.5 text-[10px] font-semibold text-warn">
              {notProvisioned ? 'TESTNET EXECUTION NOT PROVISIONED' : 'EXECUTION AUTHORITY UNAVAILABLE'}
            </span>
          </div>
          <p className="mt-2 text-xs leading-5 text-muted">
            仅展示公开 Binance 行情与本地只读特征投影；无凭证、无私有流、无券商连接、无下单能力。
          </p>
          <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
            <Metric label="stream" value={publicMarket.stream.effective_streaming ? 'STREAMING' : 'NOT_STREAMING'} accent />
            <Metric label="universe" value={publicMarket.universe_size} />
            <Metric label="stream age" value={`${publicMarket.stream.age_ms ?? 'UNKNOWN'}ms`} />
            <Metric label="stream lag" value={`${publicMarket.stream.lag_ms ?? 'UNKNOWN'}ms`} />
            <Metric label="public / market" value={`${publicMarket.stream.connections.public === true ? 'UP' : 'DOWN'} / ${publicMarket.stream.connections.market === true ? 'UP' : 'DOWN'}`} />
            <Metric label="events" value={eventCount.toLocaleString('en-US')} />
            <Metric label="REST coverage" value={`${complete}/${requested}`} />
            <Metric label="stream chain" value={publicMarket.stream.chain_valid ? 'VALID' : 'UNKNOWN'} />
            <Metric label="features" value={publicMarket.feature_projection.feature_count} />
            <Metric label="instruments" value={publicMarket.feature_projection.instrument_count} />
            <Metric label="drift" value={publicMarket.feature_projection.drift_status ?? 'UNKNOWN'} />
            <Metric label="checked at" value={timestamp(publicMarket.checked_at_ms)} />
          </div>
        </section>
      ) : (
        <section className="rounded-xl border border-warn/35 bg-warn/10 p-4 text-xs text-warn" role="status">
          公开市场数据权威同样不可用；本页不显示任何运营数字。
        </section>
      )}
    </PageShell>
  );
}

function admittedPublicMarket(result: ReturnType<typeof useConsoleSnapshot<DataCatalogPayload>>): BinanceRealtimePayload | null {
  const payload = result.payload;
  const market = payload?.binance_realtime;
  const provenance = payload?.data_provenance;
  if (!result.usingBackend || typeof provenance !== 'string' || !provenance.startsWith('REAL_') || !market) return null;
  const safety = market.safety;
  const safetyExact = safety.public_market_data_only === true
    && safety.credentials === false
    && safety.private_stream === false
    && safety.broker_connectivity === false
    && safety.order_submission === false
    && safety.live_trading === false;
  if (!safetyExact || market.verdict !== 'PASS' || !market.stream.fresh || !market.stream.effective_streaming) return null;
  return market;
}

function activeAlerts(snapshot: MftSnapshot): string[] {
  const alerts: string[] = [];
  const market = text(snapshot.market_gap, 'status');
  if (market !== 'CONTIGUOUS') alerts.push('MARKET_GAP_NOT_CONTIGUOUS');
  const scheduler = text(snapshot.scheduler, 'status');
  if (!['LEADER', 'READY', 'COMPLETED'].includes(scheduler ?? '')) alerts.push('SCHEDULER_NOT_READY');
  const risk = text(snapshot.risk, 'status');
  if (!['ALLOWED_TESTNET', 'PASS', 'READY'].includes(risk ?? '')) alerts.push('RISK_NOT_ALLOWED');
  if (boolean(snapshot.risk, 'kill_switch_latched') !== false) alerts.push('KILL_SWITCH_NOT_CLEAR');
  const reconciliation = text(snapshot.reconciliation, 'status');
  if (!['IN_SYNC', 'RECONCILED'].includes(reconciliation ?? '')
    || boolean(snapshot.reconciliation, 'requires_manual_review') === true) {
    alerts.push('RECONCILIATION_REVIEW');
  }
  if (!(text(snapshot.mandate_expiry, 'status') ?? '').startsWith('VALID')) alerts.push('MANDATE_NOT_VALID');
  return alerts;
}

export default function MarketMonitor() {
  const state = useMftReadModel();
  const catalog = useConsoleSnapshot<DataCatalogPayload>(fetchDataCatalogSnapshot);
  const publicMarket = admittedPublicMarket(catalog);
  const snapshot = state.snapshot;
  const requiredSectionUnavailable = snapshot !== null && [
    snapshot.market_gap, snapshot.scheduler, snapshot.risk, snapshot.reconciliation,
  ].some(section => {
    const status = text(section, 'status');
    return status === null || status === 'UNAVAILABLE' || status === 'MISSING';
  });
  const trusted = state.phase === 'LIVE'
    && snapshot?.status === 'AVAILABLE'
    && snapshot.freshness.status === 'CURRENT'
    && snapshot.missing_sections.length === 0
    && !requiredSectionUnavailable;

  if (!trusted || !snapshot) {
    const reason = requiredSectionUnavailable
      ? 'REQUIRED_SECTION_UNAVAILABLE'
      : snapshot?.missing_sections.length
        ? `MISSING_SECTIONS:${snapshot.missing_sections.join(',')}`
        : snapshot && snapshot.status !== 'AVAILABLE'
          ? snapshot.snapshot_status
          : null;
    return <DiagnosticOnly state={state} reason={reason} publicMarket={publicMarket} />;
  }

  const alerts = activeAlerts(snapshot);
  const missingIntervals = Array.isArray(snapshot.market_gap.missing_intervals)
    ? snapshot.market_gap.missing_intervals.length
    : null;
  const nextRebalance = number(snapshot.scheduler, 'next_rebalance_at_ms')
    ?? number(snapshot.scheduler, 'next_due_ms');

  return (
    <PageShell>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Market Monitor</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">市场监控</h1>
          <p className="mt-2 max-w-3xl text-sm text-muted">
            MFT 权威只读视图：市场连续性、调度、收益风险、告警和最近事件水位线。
          </p>
        </div>
        <div className="rounded-lg border border-pos/35 bg-pos/10 px-3 py-2 font-mono text-xs font-semibold text-pos">
          REAL_MFT_READ_MODEL · READ ONLY
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-2">
        <MonitorPanel title="市场连续性" eyebrow="Market continuity" snapshot={snapshot}>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="status" value={text(snapshot.market_gap, 'status') ?? 'UNKNOWN'} accent />
            <Metric label="venue / account" value={`${text(snapshot.market_gap, 'venue') ?? 'UNKNOWN'} · ${text(snapshot.market_gap, 'account') ?? 'UNKNOWN'}`} />
            <Metric label="latest closed bar" value={timestamp(number(snapshot.market_gap, 'latest_closed_bar_ms'))} />
            <Metric label="missing intervals" value={missingIntervals ?? 'UNKNOWN'} />
          </div>
        </MonitorPanel>

        <MonitorPanel title="调度与下次再平衡" eyebrow="Scheduler" snapshot={snapshot}>
          <div className="grid gap-2 sm:grid-cols-3">
            <Metric label="scheduler status" value={text(snapshot.scheduler, 'status') ?? 'UNKNOWN'} accent />
            <Metric label="last success" value={timestamp(number(snapshot.scheduler, 'last_success_ms'))} />
            <Metric label="Next rebalance" value={timestamp(nextRebalance)} />
          </div>
        </MonitorPanel>

        <MonitorPanel title="全局收益与风险" eyebrow="Global PnL & risk" snapshot={snapshot}>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="risk status" value={text(snapshot.risk, 'status') ?? 'UNKNOWN'} accent />
            <Metric label="day PnL" value={currency(number(snapshot.risk, 'day_pnl'))} />
            <Metric label="equity" value={currency(number(snapshot.risk, 'equity'))} />
            <Metric label="gross exposure" value={currency(number(snapshot.risk, 'gross_exposure'))} />
            <Metric label="drawdown" value={percent(number(snapshot.risk, 'drawdown'))} />
            <Metric label="positions" value={number(snapshot.positions, 'position_count') ?? 'UNKNOWN'} />
            <Metric label="kill switch" value={boolean(snapshot.risk, 'kill_switch_latched') === false ? 'CLEAR' : 'LATCHED / UNKNOWN'} />
            <Metric label="reconciliation" value={text(snapshot.reconciliation, 'status') ?? 'UNKNOWN'} />
          </div>
        </MonitorPanel>

        <MonitorPanel title="告警" eyebrow="Alerts" snapshot={snapshot}>
          <div className="rounded-lg border border-line bg-inset/50 p-3">
            <div className={`font-mono text-sm font-semibold ${alerts.length ? 'text-neg' : 'text-pos'}`}>
              {alerts.length} ACTIVE ALERTS
            </div>
            {alerts.length > 0 ? (
              <ul className="mt-3 space-y-1 font-mono text-xs text-neg">
                {alerts.map(alert => <li key={alert}>{alert}</li>)}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-muted">当前 snapshot 未派生出活动告警。</p>
            )}
          </div>
        </MonitorPanel>

        <MonitorPanel title="事件带" eyebrow="Authority event tape" snapshot={snapshot}>
          <div className="grid gap-2 sm:grid-cols-3">
            <Metric label="last event" value={`authority sequence ${snapshot.watermark?.sequence ?? 'UNKNOWN'}`} accent />
            <Metric label="event time" value={timestamp(snapshot.watermark?.event_at_ms ?? null)} />
            <Metric label="event hash" value={snapshot.watermark?.event_hash.slice(0, 16) ?? 'UNKNOWN'} />
          </div>
          <p className="mt-3 text-[10px] leading-4 text-faint">
            v1 snapshot 仅声明最近权威水位线；不把 section 状态伪装成历史事件。
          </p>
        </MonitorPanel>

        <MonitorPanel title="数据新鲜度" eyebrow="Freshness & provenance" snapshot={snapshot}>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="freshness" value={snapshot.freshness.status} accent />
            <Metric label="age" value={`${snapshot.freshness.age_ms ?? 'UNKNOWN'}ms`} />
            <Metric label="policy" value={`${snapshot.freshness.policy_id ?? 'UNKNOWN'} · ${snapshot.freshness.policy_version ?? 'UNKNOWN'}`} />
            <Metric label="as-of" value={timestamp(snapshot.as_of_ms)} />
          </div>
        </MonitorPanel>
      </div>
    </PageShell>
  );
}
