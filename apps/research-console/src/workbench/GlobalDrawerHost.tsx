import type { ComponentType } from 'react';
import { useEffect, useRef } from 'react';

import { useSearchParams } from '@/lib/router';
import { AuditDrawer } from './drawers/AuditDrawer';
import { GovernanceDrawer } from './drawers/GovernanceDrawer';
import { HealthDrawer } from './drawers/HealthDrawer';
import { TasksDrawer } from './drawers/TasksDrawer';

export type GlobalDrawerId = 'governance' | 'audit' | 'health' | 'tasks';

export interface GlobalDrawerDefinition {
  id: GlobalDrawerId;
  label: string;
  en: string;
  purpose: string;
  authoritativeEndpoint: string;
  Component: ComponentType;
}

export const GLOBAL_DRAWER_DEFINITIONS: readonly GlobalDrawerDefinition[] = [
  {
    id: 'governance', label: '治理', en: 'Governance',
    purpose: '运行时安全契约、L2 边界和控制面资格摘要',
    authoritativeEndpoint: '/api/v1/console/runtime-contract', Component: GovernanceDrawer,
  },
  {
    id: 'audit', label: '审计', en: 'Audit',
    purpose: '事件链完整性、积压、死信和最近权威事件摘要',
    authoritativeEndpoint: '/api/v1/console/events', Component: AuditDrawer,
  },
  {
    id: 'health', label: '健康', en: 'Health',
    purpose: '服务探活、降级和安全检查摘要',
    authoritativeEndpoint: '/api/v1/console/system-health', Component: HealthDrawer,
  },
  {
    id: 'tasks', label: '任务', en: 'Tasks',
    purpose: 'MFT 调度、租约时序和恢复状态摘要',
    authoritativeEndpoint: '/api/v1/console/mft-execution', Component: TasksDrawer,
  },
] as const;

function drawerFrom(value: string | null): GlobalDrawerDefinition | null {
  return GLOBAL_DRAWER_DEFINITIONS.find(item => item.id === value) ?? null;
}

export function GlobalDrawerHost() {
  const [searchParams, setSearchParams] = useSearchParams();
  const active = drawerFrom(searchParams.get('drawer'));
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const select = (id: GlobalDrawerId | null) => {
    const next = new URLSearchParams(searchParams);
    if (id === null) next.delete('drawer');
    else next.set('drawer', id);
    setSearchParams(next);
  };

  const close = () => {
    select(null);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  };

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
    // searchParams is intentionally part of close semantics so unrelated query keys are preserved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, searchParams]);

  return (
    <>
      <div className="flex shrink-0 items-center gap-1" aria-label="global drawers">
        {GLOBAL_DRAWER_DEFINITIONS.map(drawer => (
          <button
            key={drawer.id}
            type="button"
            aria-haspopup="dialog"
            aria-expanded={active?.id === drawer.id}
            onClick={event => {
              triggerRef.current = event.currentTarget;
              select(drawer.id);
            }}
            className="rounded border border-line bg-inset/65 px-2 py-1.5 text-[11px] font-medium text-muted hover:border-line-strong hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            {drawer.label}
          </button>
        ))}
      </div>
      {active && (
        <div
          className="fixed inset-0 z-[90] flex justify-end bg-black/45"
          onMouseDown={event => { if (event.target === event.currentTarget) close(); }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-label={`${active.label}抽屉`}
            className="h-full w-full max-w-md overflow-y-auto border-l border-line bg-panel p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-3xs font-semibold uppercase tracking-[0.16em] text-faint">GLOBAL DRAWER · {active.en}</p>
                <h2 className="mt-1 text-xl font-semibold text-ink">{active.label}</h2>
              </div>
              <button
                type="button"
                autoFocus
                aria-label={`关闭${active.label}抽屉`}
                onClick={close}
                className="rounded border border-line px-2.5 py-1.5 text-xs text-muted hover:bg-inset hover:text-ink"
              >
                关闭
              </button>
            </div>
            <p className="mt-4 text-xs leading-5 text-muted">{active.purpose}</p>
            <p className="mt-2 break-all font-mono text-[9px] text-faint">authority {active.authoritativeEndpoint}</p>
            <div className="mt-5"><active.Component /></div>
          </section>
        </div>
      )}
    </>
  );
}
