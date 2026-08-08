import { fetchEventsSnapshot, getConsoleRuntimeMode } from '@/lib/api/research-console-api';
import type { EventsPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';

function stamp(value: number): string {
  const milliseconds = value < 10_000_000_000 ? value * 1_000 : value;
  return new Date(milliseconds).toISOString().replace('.000Z', 'Z');
}

export function AuditDrawer() {
  const state = useConsoleSnapshot<EventsPayload>(fetchEventsSnapshot);
  if (!state.usingBackend || state.payload === null) {
    const code = getConsoleRuntimeMode() === 'MOCK' ? 'DEMO_MODE_NO_AUDIT_AUTHORITY' : 'AUDIT_UNAVAILABLE';
    return (
      <div className="rounded-lg border border-warn/35 bg-warn/10 p-4" role="status">
        <p className="font-mono text-xs font-semibold text-warn">{code}</p>
        <p className="mt-2 break-all font-mono text-[10px] text-muted">{state.error ?? state.apiState}</p>
        <p className="mt-3 text-xs leading-5 text-muted">缺少权威事件快照时不生成示例事件，也不推断审计链完整。</p>
      </div>
    );
  }

  const payload = state.payload;
  const health = payload.event_bus_health;
  const events = [...payload.events].sort((a, b) => b.timestamp - a.timestamp).slice(0, 12);
  const integrity = Object.entries(health.integrity);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded border border-line bg-inset/45 p-3">
          <p className="text-[10px] uppercase text-faint">Bus verdict</p>
          <p className="mt-1 font-mono text-xs font-semibold text-ink">{health.verdict}</p>
        </div>
        <div className="rounded border border-line bg-inset/45 p-3">
          <p className="text-[10px] uppercase text-faint">Pending / DLQ</p>
          <p className="mt-1 font-mono text-xs font-semibold text-ink">
            {health.pending_events ?? 'UNKNOWN'} / {health.dead_letter_count ?? 'UNKNOWN'}
          </p>
        </div>
      </div>
      <div className="space-y-2">
        {integrity.map(([name, value]) => (
          <div key={name} className="flex justify-between rounded border border-line bg-inset/45 px-3 py-2 font-mono text-[10px]">
            <span className="text-muted">{name}</span>
            <span className={value === true ? 'text-pos' : 'text-warn'}>{value === true ? 'VERIFIED' : 'UNVERIFIED'}</span>
          </div>
        ))}
      </div>
      <div>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-faint">Latest authority events · bounded 12</p>
        {events.length === 0 ? <p className="font-mono text-xs text-faint">EMPTY · NO_EVENTS_REPORTED</p> : (
          <ol className="space-y-2">
            {events.map(event => (
              <li key={event.event_id} className="rounded border border-line bg-inset/45 p-3">
                <p className="break-all font-mono text-xs text-accent">{event.event_type}</p>
                <p className="mt-1 break-all font-mono text-[10px] text-muted">{event.source} · {stamp(event.timestamp)}</p>
                <p className="mt-1 break-all font-mono text-[10px] text-faint">{event.event_id}</p>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
