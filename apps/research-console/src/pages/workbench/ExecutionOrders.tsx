import type { ReactNode } from 'react';

import { PageShell } from '@/ui/primitives';
import type { JsonObject, MftDeltaEvent, MftSnapshot } from '@/workbench/contracts';
import { useMftReadModel, type MftReadModelState } from '@/workbench/useMftReadModel';

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function string(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null;
}

function text(section: JsonObject, key: string): string | null {
  return string(section[key]);
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function number(section: JsonObject, key: string): number | null {
  return numberValue(section[key]);
}

function bool(section: JsonObject, key: string): boolean | null {
  return typeof section[key] === 'boolean' ? section[key] : null;
}

function timestamp(value: number | null): string {
  return value === null ? 'UNAVAILABLE' : new Date(value).toISOString().replace('.000Z', 'Z');
}

function percent(value: number | null): string {
  return value === null ? 'UNAVAILABLE' : `${(value * 100).toFixed(2)}%`;
}

function fixed(value: number | null, digits = 2): string {
  return value === null ? 'UNAVAILABLE' : value.toFixed(digits);
}

function Panel({ eyebrow, title, right, testId, children }: {
  eyebrow: string;
  title: string;
  right?: ReactNode;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-panel shadow-sm" data-testid={testId}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-faint">{eyebrow}</p>
          <h2 className="mt-1 text-base font-semibold text-ink">{title}</h2>
        </div>
        {right}
      </header>
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

function Metric({ label, value, tone = 'text-ink' }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div className="rounded-lg border border-line bg-inset/45 p-3">
      <p className="text-[10px] uppercase tracking-[0.12em] text-faint">{label}</p>
      <div className={`mt-1 break-all font-mono text-xs font-semibold ${tone}`}>{value}</div>
    </div>
  );
}

function trustedSnapshot(state: MftReadModelState): MftSnapshot | null {
  const snapshot = state.snapshot;
  if (state.phase !== 'LIVE' || snapshot === null || snapshot.status !== 'AVAILABLE'
    || snapshot.freshness.status !== 'CURRENT' || snapshot.missing_sections.length !== 0) return null;
  return snapshot;
}

function diagnosticCode(state: MftReadModelState): string {
  if (state.reason === 'STORE_MISSING') return 'TESTNET_EXECUTION_NOT_PROVISIONED';
  if (state.phase === 'STALE') return 'MFT_STALE';
  if (state.phase === 'RESNAPSHOT_REQUIRED') return 'MFT_RESNAPSHOT_REQUIRED';
  if (state.phase === 'RECONNECTING') return 'MFT_RECONNECTING';
  if (state.phase === 'LOADING') return 'MFT_LOADING';
  return 'MFT_UNAVAILABLE';
}

function recoveryGuidance(state: MftReadModelState): string {
  if (state.phase === 'STALE' || state.phase === 'RESNAPSHOT_REQUIRED') {
    return `${state.reason ?? state.phase} · REFETCH_FULL_SNAPSHOT · DO_NOT_USE_RETAINED_STATE`;
  }
  if (state.phase === 'RECONNECTING') return `${state.reason ?? state.phase} · WAIT_FOR_DELTA_CATCHUP`;
  if (state.reason === 'HTTP_429') return 'HTTP_429 · BACKOFF_THEN_RETRY_READ_ONLY';
  if (state.reason === 'TIMEOUT') return 'TIMEOUT · RETRY_READ_ONLY_WITH_BACKOFF';
  if (state.reason === 'STORE_MISSING') {
    return 'STORE_MISSING · PROVISION_CURRENT_TESTNET_PRODUCER · PUBLIC_RESEARCH_REMAINS_AVAILABLE';
  }
  return `${state.reason ?? state.phase} · VERIFY_READ_MODEL_AND_RETRY`;
}

function DiagnosticOnly({ state }: { state: MftReadModelState }) {
  const notProvisioned = state.reason === 'STORE_MISSING';
  return (
    <PageShell>
      <header>
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Execution &amp; Orders</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">执行与订单</h1>
      </header>
      <section className={`rounded-xl border p-5 ${notProvisioned ? 'border-warn/35 bg-warn/10' : 'border-neg/35 bg-neg/10'}`} role="status">
        <p className={`font-mono text-sm font-semibold ${notProvisioned ? 'text-warn' : 'text-neg'}`}>{diagnosticCode(state)}</p>
        <p className="mt-2 break-all font-mono text-xs text-muted">{recoveryGuidance(state)}</p>
        <span className={`mt-4 inline-flex rounded border px-2 py-1 font-mono text-[10px] ${notProvisioned ? 'border-warn/35 text-warn' : 'border-neg/35 text-neg'}`}>
          {notProvisioned ? 'EXPECTED_GATED_CONFIGURATION' : 'DIAGNOSTIC_ONLY'}
        </span>
        <p className="mt-3 text-xs leading-5 text-muted">
          {notProvisioned
            ? '当前没有部署 MFT Testnet 生产者，因此不生成订单、成交、仓位和 TCA。控制面与公开市场研究仍可独立运行。'
            : '陈旧、断流或游标缺口时隐藏保留的订单、成交、仓位和 TCA；恢复必须从权威 full snapshot 开始。'}
        </p>
      </section>
    </PageShell>
  );
}

interface OrderRow {
  clientId: string;
  instrument: string;
  side: string;
  state: string;
  quantity: number | null;
  filled: number | null;
  recoveryReason: string;
}

function orderRows(section: JsonObject): OrderRow[] {
  const raw = section.orders;
  const entries: Array<[string, Record<string, unknown>]> = [];
  if (Array.isArray(raw)) {
    raw.forEach((value, index) => {
      if (record(value)) entries.push([String(index), value]);
    });
  } else if (record(raw)) {
    Object.entries(raw).forEach(([key, value]) => {
      if (record(value)) entries.push([key, value]);
    });
  }
  return entries.map(([key, value]) => ({
    clientId: string(value.client_order_id) ?? key,
    instrument: string(value.instrument_id) ?? string(value.instrument) ?? 'UNKNOWN',
    side: string(value.side) ?? 'UNKNOWN',
    state: string(value.canonical_state) ?? string(value.state) ?? string(value.status) ?? 'UNKNOWN',
    quantity: numberValue(value.order_quantity) ?? numberValue(value.quantity),
    filled: numberValue(value.cumulative_filled_qty) ?? numberValue(value.filled_quantity),
    recoveryReason: string(value.recovery_reason) ?? string(value.error_code) ?? '',
  })).sort((a, b) => a.clientId.localeCompare(b.clientId));
}

interface FillRow {
  fillId: string;
  clientId: string;
  instrument: string;
  quantity: number | null;
  price: number | null;
}

function fillRows(section: JsonObject): FillRow[] {
  if (!Array.isArray(section.items)) return [];
  return section.items.flatMap((value, index) => {
    if (!record(value)) return [];
    return [{
      fillId: string(value.fill_id) ?? `fill-${index + 1}`,
      clientId: string(value.client_order_id) ?? 'UNKNOWN',
      instrument: string(value.instrument_id) ?? string(value.instrument) ?? 'UNKNOWN',
      quantity: numberValue(value.quantity),
      price: numberValue(value.price) ?? numberValue(value.fill_price) ?? numberValue(value.avg_price),
    }];
  });
}

function positionRows(section: JsonObject): Array<{ instrument: string; quantity: number }> {
  const raw = section.items;
  if (record(raw)) {
    return Object.entries(raw).flatMap(([instrument, value]) => {
      const quantity = numberValue(value);
      return quantity === null ? [] : [{ instrument, quantity }];
    }).sort((a, b) => a.instrument.localeCompare(b.instrument));
  }
  if (!Array.isArray(raw)) return [];
  return raw.flatMap(value => {
    if (!record(value)) return [];
    const instrument = string(value.instrument_id) ?? string(value.instrument);
    const quantity = numberValue(value.quantity);
    return instrument === null || quantity === null ? [] : [{ instrument, quantity }];
  }).sort((a, b) => a.instrument.localeCompare(b.instrument));
}

function partialCancel(order: OrderRow): boolean {
  const cancelled = ['CANCELED', 'CANCELLED', 'PARTIALLY_FILLED'].includes(order.state);
  return cancelled && order.filled !== null && order.filled > 0
    && (order.quantity === null || order.filled < order.quantity);
}

function reviewReasons(order: OrderRow): string[] {
  const reasons: string[] = [];
  if (order.state === 'UNKNOWN' || order.recoveryReason.includes('ACK_UNKNOWN')) reasons.push('ACK_UNKNOWN');
  if (partialCancel(order)) reasons.push('PARTIAL_CANCEL_RACE');
  if (order.recoveryReason.includes('LEDGER')) reasons.push('LEDGER_FAILURE');
  return reasons;
}

function relevantEvents(events: readonly MftDeltaEvent[]): MftDeltaEvent[] {
  return events
    .filter(event => event.section === 'oms' || event.section === 'fills')
    .sort((a, b) => a.sequence - b.sequence);
}

const RECOVERY_MATRIX = [
  ['ACK_UNKNOWN', 'QUERY_VENUE_BY_CLIENT_ORDER_ID · NEVER_RESUBMIT_BLIND'],
  ['PARTIAL_CANCEL_RACE', 'RECONCILE_CUMULATIVE_FILL_THEN_FINALIZE'],
  ['LEDGER_FAILURE', 'QUERY_VENUE_AND_REBUILD_LEDGER · NEVER_RESUBMIT_BLIND'],
  ['STALE', 'REFETCH_FULL_SNAPSHOT · HIDE_RETAINED_VALUES'],
  ['CURSOR_GAP', 'REFETCH_FULL_SNAPSHOT · RESTART_DELTA_FROM_NEW_CURSOR'],
] as const;

export default function ExecutionOrders() {
  const state = useMftReadModel();
  const snapshot = trustedSnapshot(state);
  if (snapshot === null) return <DiagnosticOnly state={state} />;

  const orders = orderRows(snapshot.oms);
  const fills = fillRows(snapshot.fills);
  const positions = positionRows(snapshot.positions);
  const pending = orders.flatMap(order => {
    const reasons = reviewReasons(order);
    return reasons.length ? [{ order, reasons }] : [];
  });
  const events = relevantEvents(state.events ?? []);
  const funnel = snapshot.intent_funnel;
  const venue = text(snapshot.market_gap, 'venue') ?? 'UNKNOWN';
  const account = text(snapshot.market_gap, 'account') ?? 'UNKNOWN';

  return (
    <PageShell>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Execution &amp; Orders</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">执行与订单</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">
            MFT v1 权威只读执行视图：意图、OMS、成交、仓位、TCA、恢复队列与本会话 delta 事件带。
          </p>
        </div>
        <div className="rounded-lg border border-warn/35 bg-warn/10 px-3 py-2 font-mono text-xs font-semibold text-warn">
          TESTNET MICROSTRUCTURE · READ ONLY · NO DIRECT COMMANDS
        </div>
      </header>

      <Panel eyebrow="Execution environment" title="环境与意图漏斗"
        right={<span className="font-mono text-[10px] text-pos">REAL_MFT_READ_MODEL</span>}>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric label="readiness" value={snapshot.safety.readiness} tone="text-warn" />
          <Metric label="venue / account" value={`${venue} · ${account}`} />
          <Metric label="microstructure" value={snapshot.safety.microstructure_scope} />
          <Metric label="authority" value={`sequence ${snapshot.watermark?.sequence ?? 'UNKNOWN'}`} tone="text-accent" />
        </div>
        <p className="mt-3 rounded-lg border border-line bg-inset/45 px-3 py-2 font-mono text-xs text-muted">
          received {number(funnel, 'received') ?? 'UNKNOWN'} · validated {number(funnel, 'validated') ?? 'UNKNOWN'} · risk_allowed {number(funnel, 'risk_allowed') ?? 'UNKNOWN'} · blocked {number(funnel, 'blocked') ?? 'UNKNOWN'} · completed {number(funnel, 'completed') ?? 'UNKNOWN'}
        </p>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <Panel eyebrow="Order management system" title="OMS 当前权威状态"
          right={<span className="font-mono text-[10px] text-faint">{text(snapshot.oms, 'status') ?? 'UNKNOWN'}</span>}>
          {orders.length === 0 ? <p className="font-mono text-xs text-faint">EMPTY · NO_OMS_ORDERS</p> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px] text-left text-xs">
                <thead className="text-[10px] uppercase tracking-[0.1em] text-faint">
                  <tr><th className="pb-2">Client order</th><th className="pb-2">Instrument</th><th className="pb-2">Side</th><th className="pb-2">State</th><th className="pb-2 text-right">Filled / qty</th></tr>
                </thead>
                <tbody className="divide-y divide-line/70 font-mono">
                  {orders.map(order => (
                    <tr key={order.clientId}>
                      <td className="py-2.5 text-accent">{order.clientId}</td>
                      <td className="py-2.5 text-ink">{order.instrument}</td>
                      <td className="py-2.5 text-muted">{order.side}</td>
                      <td className={`py-2.5 ${reviewReasons(order).length ? 'text-warn' : 'text-ink'}`}>{order.state}</td>
                      <td className="py-2.5 text-right text-muted">{fixed(order.filled, 6)} / {fixed(order.quantity, 6)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel eyebrow="Recovery queue" title="待审与恢复队列" testId="pending-review-queue"
          right={<span className={`font-mono text-[10px] ${pending.length ? 'text-warn' : 'text-pos'}`}>{pending.length} pending</span>}>
          {pending.length === 0 ? <p className="font-mono text-xs text-pos">EMPTY · NO_RECOVERY_REVIEW</p> : (
            <div className="space-y-2">
              {pending.map(({ order, reasons }) => (
                <div key={order.clientId} className="rounded-lg border border-warn/35 bg-warn/10 p-3">
                  <p className="font-mono text-xs font-semibold text-warn">{order.clientId} · {order.state}</p>
                  <p className="mt-1 font-mono text-[10px] text-muted">{reasons.join(' · ')}</p>
                </div>
              ))}
            </div>
          )}
          {bool(snapshot.reconciliation, 'requires_manual_review') === true && (
            <p className="mt-3 border-t border-line pt-3 font-mono text-[10px] text-neg">PORTFOLIO_RECONCILIATION_REQUIRES_MANUAL_REVIEW</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel eyebrow="Economic effects" title="成交与仓位">
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">Fills · count {number(snapshot.fills, 'count') ?? fills.length}</p>
              <div className="space-y-2">
                {fills.length === 0 ? <p className="font-mono text-xs text-faint">EMPTY</p> : fills.map(fill => (
                  <div key={fill.fillId} className="rounded border border-line bg-inset/45 p-2.5 font-mono text-[10px]">
                    <p className="text-accent">{fill.fillId} · {fill.instrument}</p>
                    <p className="mt-1 text-muted">order {fill.clientId} · qty {fixed(fill.quantity, 6)} · price {fixed(fill.price, 4)}</p>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">Positions · {text(snapshot.positions, 'status') ?? 'UNKNOWN'}</p>
              <div className="space-y-2">
                {positions.length === 0 ? <p className="font-mono text-xs text-faint">EMPTY</p> : positions.map(position => (
                  <div key={position.instrument} className="flex justify-between rounded border border-line bg-inset/45 p-2.5 font-mono text-xs">
                    <span className="text-accent">{position.instrument}</span><span className="text-ink">{position.quantity.toFixed(6)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Panel>

        <Panel eyebrow="Transaction cost analysis" title="TCA 与控制状态">
          <div className="grid gap-3 sm:grid-cols-2">
            <Metric label="TCA status" value={text(snapshot.tca, 'status') ?? 'UNAVAILABLE'} />
            <Metric label="fill ratio" value={percent(number(snapshot.tca, 'fill_ratio'))} />
            <Metric label="slippage" value={`${fixed(number(snapshot.tca, 'slippage_bps'))} bps`} />
            <Metric label="report" value={text(snapshot.tca, 'report_id') ?? 'UNAVAILABLE'} />
            <Metric label="reconciliation" value={text(snapshot.reconciliation, 'status') ?? 'UNAVAILABLE'}
              tone={bool(snapshot.reconciliation, 'requires_manual_review') === true ? 'text-neg' : 'text-ink'} />
            <Metric label="mandate expiry" value={text(snapshot.mandate_expiry, 'status') ?? 'UNAVAILABLE'} />
          </div>
          <p className="mt-3 font-mono text-[10px] text-faint">
            mandate expires {timestamp(number(snapshot.mandate_expiry, 'expires_at_ms'))} · scope {text(snapshot.tca, 'microstructure_scope') ?? snapshot.safety.microstructure_scope}
          </p>
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel eyebrow="Authority sequence · session delta only" title="订单 / 成交事件带" testId="authority-event-tape"
          right={<span className="font-mono text-[10px] text-faint">current authority sequence {snapshot.watermark?.sequence ?? 'UNKNOWN'}</span>}>
          {events.length === 0 ? (
            <div className="rounded-lg border border-line bg-inset/45 p-3">
              <p className="font-mono text-xs text-faint">NO_SESSION_DELTA_EVENTS · {state.eventHistory}</p>
              <p className="mt-1 text-[10px] text-muted">Snapshot rows are current state, not historical events.</p>
            </div>
          ) : (
            <ol className="space-y-2">
              {events.map(event => (
                <li key={event.event_id} data-testid="authority-event"
                  className="grid gap-2 rounded-lg border border-line bg-inset/45 p-3 font-mono text-xs sm:grid-cols-[70px_1fr_auto]">
                  <span className="text-accent">seq {event.sequence}</span>
                  <span className="break-all text-ink">{event.event_id}</span>
                  <span className="text-muted">{event.section} · {timestamp(event.event_at_ms)}</span>
                </li>
              ))}
            </ol>
          )}
          <p className="mt-3 text-[10px] leading-4 text-faint">
            Event id 在同步层去重；相同 id 与相同 payload 不产生第二条经济效果，内容冲突会强制 resnapshot。
          </p>
        </Panel>

        <Panel eyebrow="Policy · not a current observation" title="显式恢复指引">
          <div className="space-y-2">
            {RECOVERY_MATRIX.map(([failure, guidance]) => (
              <div key={failure} className="rounded-lg border border-line bg-inset/45 p-3 font-mono text-[10px]">
                <p className="text-warn">{failure} · {guidance}</p>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[10px] text-faint">这是 fail-closed runbook 摘要，不是浏览器命令入口。</p>
        </Panel>
      </div>

      <p className="font-mono text-[10px] text-faint">
        {snapshot.source} · snapshot {snapshot.snapshot_id?.slice(0, 12) ?? 'UNKNOWN'} · {snapshot.freshness.status} · age {snapshot.freshness.age_ms ?? 'UNKNOWN'}ms
      </p>
    </PageShell>
  );
}
