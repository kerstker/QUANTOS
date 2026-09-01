import { useEffect, useState } from 'react';

import {
  fetchRunnerHealth,
  runnerEnabled,
  type RunnerHealth,
} from '@/lib/api/research-runner-api';
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

type RunnerState =
  | { status: 'CHECKING'; health: null; reason: null }
  | { status: 'AVAILABLE'; health: RunnerHealth; reason: null }
  | { status: 'UNAVAILABLE'; health: null; reason: string };

function useRunnerState(): RunnerState {
  const [state, setState] = useState<RunnerState>(() => (
    runnerEnabled()
      ? { status: 'CHECKING', health: null, reason: null }
      : { status: 'UNAVAILABLE', health: null, reason: 'VITE_RUNNER_URL_NOT_CONFIGURED' }
  ));
  useEffect(() => {
    if (!runnerEnabled()) return;
    const controller = new AbortController();
    fetchRunnerHealth(controller.signal)
      .then(health => setState({ status: 'AVAILABLE', health, reason: null }))
      .catch(error => {
        if (!controller.signal.aborted) {
          setState({
            status: 'UNAVAILABLE', health: null,
            reason: error instanceof Error ? error.message : String(error),
          });
        }
      });
    return () => controller.abort();
  }, []);
  return state;
}

export function TasksDrawer() {
  const mftState = useMftReadModel();
  const runner = useRunnerState();
  const snapshot = trusted(mftState);

  return (
    <div className="space-y-4">
      <section className={`rounded-lg border p-4 ${runner.status === 'AVAILABLE'
        ? 'border-pos/35 bg-pos/10' : 'border-warn/35 bg-warn/10'}`} role="status">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-[0.14em] text-faint">Research runner · 8420</p>
            <p className={`mt-1 font-mono text-xs font-semibold ${runner.status === 'AVAILABLE' ? 'text-pos' : 'text-warn'}`}>
              {runner.status === 'AVAILABLE' ? runner.health.mode : `RUNNER_${runner.status}`}
            </p>
          </div>
          <a href="/research/runs" className="rounded border border-line px-2 py-1 text-[10px] text-accent hover:bg-inset">
            打开研究运行
          </a>
        </div>
        {runner.status === 'AVAILABLE' ? (
          <>
            <p className="mt-2 font-mono text-[10px] text-muted">
              {runner.health.kinds.length} runnable · live_disallowed={String(runner.health.live_disallowed)}
            </p>
            <div className="mt-2 flex flex-wrap gap-1">
              {runner.health.kinds.map(kind => (
                <span key={kind} className="rounded border border-pos/25 px-1.5 py-0.5 font-mono text-[9px] text-pos">{kind}</span>
              ))}
            </div>
          </>
        ) : (
          <p className="mt-2 break-all font-mono text-[10px] text-muted">{runner.reason ?? 'HEALTH_CHECK_IN_PROGRESS'}</p>
        )}
      </section>

      {snapshot === null ? (
        <section className="rounded-lg border border-warn/35 bg-warn/10 p-4" role="status">
          <p className="font-mono text-xs font-semibold text-warn">MFT_TASKS_UNAVAILABLE</p>
          <p className="mt-2 break-all font-mono text-[10px] text-muted">{mftState.reason ?? mftState.phase}</p>
          <p className="mt-3 text-xs leading-5 text-muted">
            MFT scheduler authority 与研究 runner 是两个独立模块；一个未配置不会再遮蔽另一个的状态。
          </p>
        </section>
      ) : (
        <MftTask snapshot={snapshot} />
      )}
    </div>
  );
}

function MftTask({ snapshot }: { snapshot: MftSnapshot }) {
  const scheduler = snapshot.scheduler;
  const taskName = text(scheduler, 'task_name') ?? text(scheduler, 'name') ?? 'MFT_SCHEDULER';
  const status = text(scheduler, 'status') ?? 'UNKNOWN';
  const recovery = text(scheduler, 'recovery_status') ?? text(scheduler, 'recovery') ?? 'UNKNOWN';
  const lastSuccess = number(scheduler, 'last_success_ms');
  const nextDue = number(scheduler, 'next_due_ms') ?? number(scheduler, 'next_rebalance_at_ms');
  return (
    <section className="space-y-3 rounded-lg border border-line bg-inset/30 p-3">
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
    </section>
  );
}
