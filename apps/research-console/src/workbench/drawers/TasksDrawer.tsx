import type { JsonObject, MftSnapshot } from '@/workbench/contracts';
import { useMftReadModel, type MftReadModelState } from '@/workbench/useMftReadModel';

function text(value: JsonObject, key: string): string | null {
  const item = value[key];
  return typeof item === 'string' && item.trim().length > 0 ? item : null;
}

function number(value: JsonObject, key: string): number | null {
  const item = value[key];
  return typeof item === 'number' && Number.isFinite(item) ? item : null;
}

function stamp(value: number | null): string {
  return value === null ? 'UNAVAILABLE' : new Date(value).toISOString().replace('.000Z', 'Z');
}

function trusted(state: MftReadModelState): MftSnapshot | null {
  const snapshot = state.snapshot;
  if (state.phase !== 'LIVE' || snapshot === null || snapshot.status !== 'AVAILABLE'
    || snapshot.freshness.status !== 'CURRENT' || snapshot.missing_sections.length !== 0) return null;
  return snapshot;
}

export function TasksDrawer() {
  const state = useMftReadModel();
  const snapshot = trusted(state);
  if (snapshot === null) {
    return (
      <div className="rounded-lg border border-warn/35 bg-warn/10 p-4" role="status">
        <p className="font-mono text-xs font-semibold text-warn">TASKS_UNAVAILABLE</p>
        <p className="mt-2 break-all font-mono text-[10px] text-muted">{state.reason ?? state.phase}</p>
        <p className="mt-3 text-xs leading-5 text-muted">仅展示 current、complete 的 MFT scheduler 权威状态；陈旧值不会保留为正常任务。</p>
      </div>
    );
  }

  const scheduler = snapshot.scheduler;
  const taskName = text(scheduler, 'task_name') ?? text(scheduler, 'name') ?? 'MFT_SCHEDULER';
  const status = text(scheduler, 'status') ?? 'UNKNOWN';
  const recovery = text(scheduler, 'recovery_status') ?? text(scheduler, 'recovery') ?? 'UNKNOWN';
  const lastSuccess = number(scheduler, 'last_success_ms');
  const nextDue = number(scheduler, 'next_due_ms') ?? number(scheduler, 'next_rebalance_at_ms');

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-inset/45 p-4" role="status">
        <p className="break-all font-mono text-sm font-semibold text-accent">{taskName}</p>
        <p className="mt-2 font-mono text-xs text-ink">{status}</p>
        <p className="mt-1 font-mono text-[10px] text-muted">recovery {recovery}</p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded border border-line bg-inset/45 p-3">
          <p className="text-[9px] uppercase text-faint">Last success</p>
          <p className="mt-1 break-all font-mono text-[10px] text-ink">{stamp(lastSuccess)}</p>
        </div>
        <div className="rounded border border-line bg-inset/45 p-3">
          <p className="text-[9px] uppercase text-faint">Next due</p>
          <p className="mt-1 break-all font-mono text-[10px] text-ink">{stamp(nextDue)}</p>
        </div>
      </div>
      <p className="font-mono text-[10px] text-faint">
        REAL_MFT_READ_MODEL · authority sequence {snapshot.watermark?.sequence ?? 'UNKNOWN'} · READ ONLY
      </p>
    </div>
  );
}
