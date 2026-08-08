import React, { useEffect, useMemo, useRef, useState } from 'react';

import { NavLink } from '@/lib/router';
import {
  fetchRuntimeContract,
  getConsoleRuntimeMode,
} from '@/lib/api/research-console-api';
import type { RuntimeContractPayload } from '@/lib/types/research';
import { WORKBENCH_DATA_SOURCE_MANIFEST } from '@/workbench/data-source-manifest';
import { GlobalDrawerHost } from '@/workbench/GlobalDrawerHost';
import { GlobalStatusBar } from '@/workbench/GlobalStatusBar';
import { ConfirmDialogHost } from './dialog';
import { ToastProvider } from './toast';
import { useConsoleSnapshot, type SnapshotResult } from './useConsoleSnapshot';

export interface NavItem { to: string; zh: string; en: string; icon: string; end?: boolean }
export interface NavGroup { zh: string; en: string; items: NavItem[] }

const runtimeMode = getConsoleRuntimeMode();
const clientApiVersion = import.meta.env.VITE_CONSOLE_API_VERSION || '0.1.0';
type RuntimeHandshakeState = Pick<SnapshotResult<RuntimeContractPayload>, 'payload' | 'apiState' | 'error'>;

function hasRuntimeContractShape(payload: RuntimeContractPayload | null): payload is RuntimeContractPayload {
  if (!payload || typeof payload !== 'object') return false;
  const boundary = payload.safety_boundary;
  const schema = payload.event_schema_catalog;
  return Boolean(
    boundary && typeof boundary === 'object'
      && typeof boundary.read_only_console === 'boolean'
      && typeof boundary.authorized_level === 'string'
      && schema && typeof schema === 'object'
      && typeof schema.sha256 === 'string'
      && typeof payload.instance_id === 'string'
      && typeof payload.build_sha_known === 'boolean',
  );
}

function runtimeContractIsSafe(payload: RuntimeContractPayload): boolean {
  return payload.api_version === clientApiVersion
    && payload.safety_boundary.read_only_console
    && payload.safety_boundary.authorized_level === 'L2_DRY_RUN_ONLY'
    && payload.safety_boundary.live_trading_authorized === false
    && payload.safety_boundary.order_submission_allowed === false
    && payload.safety_boundary.broker_connectivity === false;
}

function RuntimeHandshakeBadge({ state }: { state: RuntimeHandshakeState }) {
  let label = 'API UNAVAILABLE';
  let tone = 'border-warn/35 bg-warn/10 text-warn';
  if (runtimeMode === 'REAL_API' && state.apiState === 'loading') {
    label = 'API CHECKING';
  } else if (hasRuntimeContractShape(state.payload) && runtimeContractIsSafe(state.payload)) {
    label = state.payload.build_sha_known ? 'CONTRACT VERIFIED' : 'CONTRACT · SHA UNKNOWN';
    tone = state.payload.build_sha_known
      ? 'border-pos/35 bg-pos/10 text-pos'
      : 'border-warn/35 bg-warn/10 text-warn';
  } else if (runtimeMode === 'MOCK') {
    label = 'DEMO MODE';
  }
  return <span className={`rounded border px-2 py-1 text-[9px] font-semibold ${tone}`}>{label}</span>;
}

function RuntimeHandshakeNotice({ state }: { state: RuntimeHandshakeState }) {
  if (runtimeMode !== 'REAL_API' || state.apiState === 'loading' || state.apiState === 'disabled') return null;
  if (state.apiState === 'error' || !hasRuntimeContractShape(state.payload)) {
    return (
      <div role="alert" className="border-t border-neg/30 bg-neg/10 px-3 py-2 text-[11px] text-neg sm:px-6">
        控制面握手失败：{state.error ?? '无响应'}。错误响应不会被当作可信快照。
      </div>
    );
  }
  if (runtimeContractIsSafe(state.payload) && state.payload.build_sha_known) return null;
  return (
    <div role="alert" className="border-t border-warn/30 bg-warn/10 px-3 py-2 text-[11px] text-warn sm:px-6">
      运行时契约未完全验证；工作台保持只读和不可用优先。
    </div>
  );
}

function SessionClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(id);
  }, []);
  const pad = (value: number) => String(value).padStart(2, '0');
  const label = now
    ? `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())} UTC`
    : '--:--:-- UTC';
  return <span data-testid="session-clock" className="font-mono text-[10px] text-faint">{label}</span>;
}

function CommandPalette({ nav, open, onClose }: {
  nav: ReadonlyArray<NavGroup>;
  open: boolean;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const items = useMemo(
    () => nav.flatMap(group => group.items.map(item => ({ ...item, group: group.zh }))),
    [nav],
  );
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return items;
    return items.filter(item => (
      `${item.zh} ${item.en} ${item.group} ${item.to}`.toLowerCase().includes(normalized)
    ));
  }, [items, query]);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    const focus = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(focus);
  }, [open]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center bg-black/45 px-4 pt-[12vh]"
      role="dialog"
      aria-modal="true"
      aria-label="全局搜索"
      onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-xl overflow-hidden rounded-xl border border-line bg-panel shadow-2xl">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <span aria-hidden className="text-muted">⌕</span>
          <input
            ref={inputRef}
            value={query}
            onChange={event => setQuery(event.target.value)}
            onKeyDown={event => { if (event.key === 'Escape') onClose(); }}
            placeholder="搜索页面、流程或路径… / Search pages, workflows, or paths…"
            className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-faint"
          />
          <kbd className="rounded border border-line px-1.5 text-3xs text-faint">ESC</kbd>
        </div>
        <div className="max-h-[55vh] overflow-y-auto p-2">
          {!filtered.length ? (
            <div className="px-3 py-8 text-center text-xs text-faint">没有匹配页面 / No matching page</div>
          ) : filtered.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={onClose}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-elevated"
            >
              <span className="grid h-7 w-7 place-items-center rounded bg-inset text-2xs font-bold text-accent">{item.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-ink">{item.zh}</span>
                <span className="block truncate text-3xs text-faint">{item.group} · {item.to}</span>
              </span>
              <span aria-hidden className="text-faint">→</span>
            </NavLink>
          ))}
        </div>
      </div>
    </div>
  );
}

export function AppShell({ nav, children }: {
  nav: ReadonlyArray<NavGroup>;
  tape?: React.ReactNode;
  children: React.ReactNode;
}) {
  const runtimeHandshake = useConsoleSnapshot<RuntimeContractPayload>(fetchRuntimeContract);
  const [searchOpen, setSearchOpen] = useState(false);
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (
    document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
  ));

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && searchOpen) {
        setSearchOpen(false);
        return;
      }
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) return;
      event.preventDefault();
      setSearchOpen(true);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [searchOpen]);

  return (
    <ToastProvider>
      <ConfirmDialogHost>
        <div className="flex min-h-screen min-w-0 flex-col overflow-x-hidden text-ink">
          <header className="glass sticky top-0 z-40 border-b border-line">
            <div className="flex min-h-14 items-center gap-3 px-3 py-2 sm:px-6">
              <div className="flex shrink-0 items-center gap-2.5">
                <span
                  className="grid h-8 w-8 place-items-center rounded text-[15px] font-black text-accent-ink"
                  style={{ background: 'var(--grad)' }}
                >
                  Q
                </span>
                <div className="leading-none">
                  <div className="text-[15px] font-bold tracking-tight text-ink">QUANTOS</div>
                  <div className="mt-1 hidden text-[8px] uppercase tracking-[0.14em] text-faint sm:block">Professional Workbench</div>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setSearchOpen(true)}
                aria-label="全局搜索 / Global search"
                className="flex shrink-0 items-center gap-1.5 rounded-md border border-line bg-inset/60 px-2.5 py-1.5 text-xs text-faint hover:border-accent/50 hover:text-ink"
              >
                <span aria-hidden>⌕</span>
                <span className="hidden sm:inline">搜索</span>
                <kbd className="rounded border border-line px-1 text-3xs text-faint">/</kbd>
              </button>
              <div className="min-w-0 flex-1" />
              <span className="hidden md:inline"><SessionClock /></span>
              <button
                type="button"
                aria-label={theme === 'dark' ? '切换浅色模式' : '切换深色模式'}
                onClick={() => setTheme(current => current === 'dark' ? 'light' : 'dark')}
                className="grid h-7 w-7 shrink-0 place-items-center rounded border border-line bg-inset text-xs text-muted hover:border-line-strong hover:text-ink"
              >
                <span aria-hidden>{theme === 'dark' ? '☀' : '☾'}</span>
              </button>
              <RuntimeHandshakeBadge state={runtimeHandshake} />
            </div>

            <div className="flex flex-col gap-1 border-t border-line/60 bg-surface/70 px-2 py-1.5 xl:flex-row xl:items-center sm:px-4">
              <nav
                className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto"
                aria-label="object workspaces"
              >
                {WORKBENCH_DATA_SOURCE_MANIFEST.map(item => (
                  <NavLink
                    key={item.id}
                    to={item.route}
                    end
                    className={({ isActive }) => (
                      `flex min-w-[132px] items-center gap-2 rounded-md px-3 py-2 transition-colors ${
                        isActive
                          ? 'bg-elevated text-ink shadow-sm'
                          : 'text-muted hover:bg-elevated/50 hover:text-ink'
                      }`
                    )}
                  >
                    {({ isActive }) => (
                      <>
                        <span className={`grid h-6 w-7 place-items-center rounded text-[8px] font-bold ${
                          isActive ? 'bg-accent/15 text-accent' : 'bg-inset text-faint'
                        }`}>
                          {item.icon}
                        </span>
                        <span className="min-w-0 leading-tight">
                          <span className="block truncate text-[12px] font-semibold">{item.zh}</span>
                          <span className="block truncate text-[8px] uppercase tracking-wide text-faint">{item.en}</span>
                        </span>
                      </>
                    )}
                  </NavLink>
                ))}
              </nav>
              <GlobalDrawerHost />
            </div>

            <GlobalStatusBar />
            <RuntimeHandshakeNotice state={runtimeHandshake} />
          </header>

          <main className="min-w-0 flex-1 overflow-x-hidden px-3 py-4 sm:px-6 sm:py-6 lg:px-10 lg:py-8">
            {children}
          </main>
        </div>
        <CommandPalette nav={nav} open={searchOpen} onClose={() => setSearchOpen(false)} />
      </ConfirmDialogHost>
    </ToastProvider>
  );
}
