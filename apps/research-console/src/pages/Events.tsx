import React from 'react';
import { fetchEventsSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { EventBusHealth, EventsPayload, EventRecord } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  Chip, GateDot, ProgressBar, type Column,
} from '@/ui/primitives';
import { Histogram, Donut } from '@/ui/charts';
import { num, stamp, shortHash, statusTone, type Tone } from '@/ui/format';

const fallbackEvents: EventRecord[] = Array.from({ length: 10 }, (_, index) => ({
  event_id: `evt-${1000 + index}`,
  event_type: `research.${['agent.created', 'loop.completed', 'alpha.approved', 'paper.passed', 'risk.approved'][index % 5]}`,
  timestamp: 1_781_222_400 - index * 300,
  source: ['research-agent-loop', 'paper-trading', 'research-risk'][index % 3],
  correlation_id: `corr-${2000 + index}`,
  payload: { read_only: true, fallback: true },
}));

const fallbackBusHealth: EventBusHealth = {
  schema_version: 1,
  verdict: 'UNAVAILABLE',
  checked_at_ms: 0,
  group_id: 'domain-event-projection-v1',
  consumer_mode: 'SINGLE_NODE_LEASED_PARTITIONS',
  integrity: {
    event_chain_valid: null,
    consumer_chain_valid: null,
    coordination_chain_valid: null,
  },
  active_workers: [],
  partition_count: 0,
  pending_events: null,
  oldest_pending_age_ms: null,
  dead_letter_count: null,
  event_records: null,
  consumer_journal_records: null,
  coordination_journal_records: null,
  allocated_bytes: null,
  retention_mode: 'APPEND_ONLY_ALERT_ONLY',
  alerts: [],
};

/** Last token of a dotted event type → terminal-style short label (deterministic). */
const leaf = (t: string): string => {
  const parts = t.split('.');
  return parts[parts.length - 1] || t;
};

/** Compact one-line preview of an event payload, ordering keys for stability. */
function previewPayload(p: Record<string, unknown>): string {
  const keys = Object.keys(p).sort();
  if (!keys.length) return '{}';
  return keys
    .map(k => {
      const v = p[k];
      const s = typeof v === 'string' ? v
        : typeof v === 'number' || typeof v === 'boolean' ? String(v)
          : Array.isArray(v) ? `[${v.length}]`
            : v === null ? 'null' : '{…}';
      return `${k}=${s}`;
    })
    .join('  ');
}

export default function Events() {
  const { payload, apiState, error, usingBackend } =
    useConsoleSnapshot<EventsPayload>(s => fetchEventsSnapshot(s));

  const events = usingBackend && payload ? payload.events : fallbackEvents;
  const busHealth = usingBackend && payload?.event_bus_health
    ? payload.event_bus_health
    : fallbackBusHealth;
  const busIntegrityOk = Object.values(busHealth.integrity).every(value => value === true);
  const busVerdictOk = busHealth.verdict === 'PASS' || busHealth.verdict === 'WARN';

  // --- deterministic aggregates (no Math.random / Date.now) -----------------
  const typeCounts = new Map<string, number>();
  const sourceCounts = new Map<string, number>();
  const familyCounts = new Map<string, number>();
  let withCorrelation = 0;
  let minTs = Number.POSITIVE_INFINITY;
  let maxTs = Number.NEGATIVE_INFINITY;

  for (const e of events) {
    typeCounts.set(leaf(e.event_type), (typeCounts.get(leaf(e.event_type)) ?? 0) + 1);
    sourceCounts.set(e.source, (sourceCounts.get(e.source) ?? 0) + 1);
    const family = e.event_type.split('.')[0] || e.event_type;
    familyCounts.set(family, (familyCounts.get(family) ?? 0) + 1);
    if (e.correlation_id) withCorrelation += 1;
    if (e.timestamp < minTs) minTs = e.timestamp;
    if (e.timestamp > maxTs) maxTs = e.timestamp;
  }

  const typeBins = [...typeCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 9)
    .map(([label, count]) => ({ label, count }));

  const sourceTones: Tone[] = ['accent', 'info', 'warn', 'special', 'pos', 'muted'];
  const sourceSegments = [...sourceCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([label, value], i) => ({ label, value, tone: sourceTones[i % sourceTones.length] }));

  const familyRows = [...familyCounts.entries()].sort((a, b) => b[1] - a[1]);
  const maxFamily = Math.max(...familyRows.map(([, c]) => c), 1);

  const spanMin = Number.isFinite(minTs) && Number.isFinite(maxTs)
    ? Math.round((maxTs - minTs) / 60) : 0;
  const corrCoverage = events.length ? withCorrelation / events.length : 0;

  const eventCols: Column<EventRecord>[] = [
    {
      key: 'type', header: bi('事件类型', 'Event Type'), render: e => (
        <div className="flex items-center gap-2">
          <Chip label={leaf(e.event_type)} tone={statusTone(e.event_type)} />
          <span className="mono text-2xs text-faint">{e.event_type}</span>
        </div>
      ),
    },
    { key: 'source', header: bi('来源', 'Source'), render: e => <span className="text-muted">{e.source}</span> },
    {
      key: 'corr', header: bi('关联 ID', 'Correlation'), mono: true,
      render: e => e.correlation_id
        ? <span className="text-info">{e.correlation_id}</span>
        : <span className="text-faint">—</span>,
    },
    {
      key: 'payload', header: bi('载荷预览', 'Payload'), render: e => (
        <span className="mono text-2xs text-faint" title={previewPayload(e.payload)}>
          {previewPayload(e.payload)}
        </span>
      ),
    },
    { key: 'id', header: bi('事件 ID', 'Event ID'), align: 'right', mono: true, render: e => shortHash(e.event_id, 12) },
    { key: 'ts', header: bi('时间戳', 'Timestamp'), align: 'right', mono: true, render: e => stamp(e.timestamp) },
  ];

  const sortedEvents = [...events].sort((a, b) => b.timestamp - a.timestamp);

  return (
    <PageShell>
      <PageHeader title="Event Stream" titleZh="事件流"
        subtitle={bi('只读审计事件快照 · 跨智能体、纸面与风险服务的事件总线', 'Read-only audit event snapshot across the agent, paper, and risk bus')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={payload?.source} error={error} />

      <KpiStrip cols={6}>
        <StatTile label={bi('事件总数', 'Total Events')} value={num(events.length)} accent />
        <StatTile label={bi('事件类型', 'Distinct Types')} value={num(typeCounts.size)} tone="info" />
        <StatTile label={bi('来源服务', 'Sources')} value={num(sourceCounts.size)} />
        <StatTile label={bi('关联覆盖', 'Correlated')} value={num(withCorrelation)}
          sub={bi(`占 ${(corrCoverage * 100).toFixed(0)}%`, `${(corrCoverage * 100).toFixed(0)}% of stream`)}
          tone={corrCoverage >= 1 ? 'pos' : 'warn'} />
        <StatTile label={bi('时间跨度', 'Time Span')} value={num(spanMin)} unit={bi('分', 'min')}
          sub={spanMin ? stamp(minTs) : undefined} />
        <StatTile label={bi('总线健康', 'Bus Health')} value={busHealth.verdict}
          sub={bi(
            `积压 ${busHealth.pending_events ?? '-'} · 死信 ${busHealth.dead_letter_count ?? '-'}`,
            `lag ${busHealth.pending_events ?? '-'} · DLQ ${busHealth.dead_letter_count ?? '-'}`,
          )}
          tone={statusTone(busHealth.verdict)} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('分布', 'Distribution')}
          title={bi('事件类型频次', 'Event Type Frequency')}
          right={<Chip label={bi(`Top ${typeBins.length}`, `Top ${typeBins.length}`)} tone="info" />}>
          <Histogram bins={typeBins} height={236} tone="accent" />
        </Panel>

        <Panel eyebrow={bi('来源', 'Sources')} title={bi('来源服务构成', 'Source Service Mix')}>
          <div className="flex items-center gap-5">
            <Donut size={150} segments={sourceSegments} />
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              {sourceSegments.map(s => (
                <div key={s.label} className="flex flex-col gap-1">
                  <div className="flex items-center justify-between gap-2">
                    <Chip label={s.label} tone={s.tone} />
                    <span className="tnum text-2xs text-muted">{num(s.value)}</span>
                  </div>
                  <ProgressBar value={s.value} max={events.length || 1} tone={s.tone} height={4} />
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('命名空间', 'Namespace')} title={bi('事件域分布', 'Event Family Breakdown')}>
          <div className="flex flex-col gap-3">
            {familyRows.map(([family, count]) => (
              <div key={family} className="flex flex-col gap-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="mono text-2xs text-ink">{family}</span>
                  <span className="tnum text-2xs text-muted">{num(count)}</span>
                </div>
                <ProgressBar value={count} max={maxFamily} tone={statusTone(family)} />
              </div>
            ))}
          </div>
        </Panel>

        <Panel className="xl:col-span-2" eyebrow={bi('合约', 'Invariants')}
          title={bi('事件总线只读保证', 'Event Bus Read-Only Guarantees')}>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex items-center justify-between rounded-lg border border-line bg-inset/40 px-3 py-2.5">
              <span className="text-xs text-muted">{bi('仅审计 · 不可变事件', 'Audit-only · immutable events')}</span>
              <GateDot ok label={bi('强制', 'ENFORCED')} />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-line bg-inset/40 px-3 py-2.5">
              <span className="text-xs text-muted">{bi('无下单 / 无经纪事件', 'No order / broker events')}</span>
              <GateDot ok label={bi('已封锁', 'BLOCKED')} />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-line bg-inset/40 px-3 py-2.5">
              <span className="text-xs text-muted">{bi('关联链完整', 'Correlation chain intact')}</span>
              <GateDot ok={corrCoverage >= 1} label={corrCoverage >= 1 ? bi('完整', 'FULL') : bi('部分', 'PARTIAL')} />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-line bg-inset/40 px-3 py-2.5">
              <span className="text-xs text-muted">{bi('合成演示数据标记', 'Synthetic-data marker')}</span>
              <GateDot ok label={bi('诚实', 'HONEST')} />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-line bg-inset/40 px-3 py-2.5">
              <span className="text-xs text-muted">{bi('事件链完整性', 'Event-chain integrity')}</span>
              <GateDot ok={busIntegrityOk} label={busIntegrityOk ? 'VERIFIED' : 'UNVERIFIED'} />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-line bg-inset/40 px-3 py-2.5">
              <span className="text-xs text-muted">{bi('保留策略', 'Retention policy')}</span>
              <GateDot ok={busVerdictOk}
                label={busHealth.retention_mode === 'APPEND_ONLY_ALERT_ONLY' ? 'ALERT ONLY' : busHealth.verdict} />
            </div>
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('总线', 'Bus')} title={bi('事件明细(按时间倒序)', 'Event Log (newest first)')}
        right={<Chip label={bi(`${events.length} 条`, `${events.length} events`)} tone="info" />}>
        <DataTable columns={eventCols} rows={sortedEvents} dense getKey={e => e.event_id} />
      </Panel>
    </PageShell>
  );
}
