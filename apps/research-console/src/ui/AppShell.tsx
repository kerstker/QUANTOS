import React, { useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, useLocation } from '@/lib/router';
import { bi } from '@/lib/i18n/bilingual';
import { Chip } from './primitives';
import { ConfirmDialogHost } from './dialog';
import { ToastProvider } from './toast';
import { PipelineFlow } from './PipelineFlow';
import { GROUPS, type GroupId } from '@/app/routes';
import {
  fetchRuntimeContract,
  getConsoleRuntimeMode,
  type ConsoleRuntimeMode,
} from '@/lib/api/research-console-api';
import type { RuntimeContractPayload } from '@/lib/types/research';
import { useConsoleSnapshot, type SnapshotResult } from './useConsoleSnapshot';

export interface NavItem { to: string; zh: string; en: string; icon: string; end?: boolean }
export interface NavGroup { zh: string; en: string; items: NavItem[] }

const mode: ConsoleRuntimeMode = getConsoleRuntimeMode();
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

function RuntimeHandshake({ state }: { state: RuntimeHandshakeState }) {
  const { payload, apiState, error } = state;

  if (mode === 'MOCK') {
    return <Chip label="DEMO" tone="warn" />;
  }
  if (mode === 'REAL_API_UNAVAILABLE') {
    return <Chip label="API UNAVAILABLE" tone="neg" />;
  }
  if (apiState === 'loading') {
    return <Chip label="HANDSHAKE…" tone="info" />;
  }
  if (apiState === 'error' || !hasRuntimeContractShape(payload)) {
    return (
      <span title={error ?? 'Runtime contract unavailable'}>
        <Chip label="CONTRACT ERROR" tone="neg" />
      </span>
    );
  }

  const versionMatches = payload.api_version === clientApiVersion;
  const safetyMatches = payload.safety_boundary.read_only_console
    && payload.safety_boundary.authorized_level === 'L2_DRY_RUN_ONLY'
    && payload.safety_boundary.live_trading_authorized === false
    && payload.safety_boundary.order_submission_allowed === false
    && payload.safety_boundary.broker_connectivity === false;
  const contractOk = versionMatches && safetyMatches;
  const label = !versionMatches
    ? 'VERSION MISMATCH'
    : !safetyMatches
      ? 'SAFETY MISMATCH'
      : payload.build_sha_known ? 'CONTRACT OK' : 'CONTRACT OK · SHA UNPINNED';
  const detail = [
    `api ${payload.api_version} / client ${clientApiVersion}`,
    `instance ${payload.instance_id}`,
    `schema ${payload.event_schema_catalog.sha256.slice(0, 12)}`,
    payload.build_sha_known ? `build ${payload.build_sha}` : 'build SHA unknown',
  ].join(' · ');
  return (
    <span title={detail}>
      <Chip label={label} tone={contractOk ? (payload.build_sha_known ? 'pos' : 'warn') : 'neg'} />
    </span>
  );
}

function RuntimeHandshakeNotice({ state }: { state: RuntimeHandshakeState }) {
  const { payload, apiState, error } = state;
  if (mode !== 'REAL_API' || apiState === 'loading' || apiState === 'disabled') return null;
  if (apiState === 'error' || !hasRuntimeContractShape(payload)) {
    return (
      <div role="alert" className="border-b border-neg/30 bg-neg/10 px-5 py-2 text-2xs text-neg sm:px-8 lg:px-10">
        控制面握手失败 / Console runtime handshake failed：{error ?? 'no response'}。页面不会把错误响应当作可信快照。
      </div>
    );
  }
  const versionMatches = payload.api_version === clientApiVersion;
  const safetyMatches = payload.safety_boundary.read_only_console
    && payload.safety_boundary.authorized_level === 'L2_DRY_RUN_ONLY'
    && payload.safety_boundary.live_trading_authorized === false
    && payload.safety_boundary.order_submission_allowed === false
    && payload.safety_boundary.broker_connectivity === false;
  if (versionMatches && safetyMatches && payload.build_sha_known) return null;
  const reason = !versionMatches
    ? `API ${payload.api_version} ≠ client ${clientApiVersion}`
    : !safetyMatches
      ? '安全边界声明不一致 / safety boundary mismatch'
      : '构建 SHA 未固定 / build SHA is not pinned';
  return (
    <div role="status" className="border-b border-warn/30 bg-warn/10 px-5 py-2 text-2xs text-warn sm:px-8 lg:px-10">
      运行时握手提醒 / Runtime handshake notice：{reason}。实例 {payload.instance_id}，Schema {payload.event_schema_catalog.sha256.slice(0, 12)}。
    </div>
  );
}

/* UTC session clock — deterministic first paint (placeholder until mounted). */
function SessionClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const p = (n: number) => String(n).padStart(2, '0');
  const label = now
    ? `${p(now.getUTCHours())}:${p(now.getUTCMinutes())}:${p(now.getUTCSeconds())} UTC`
    : '--:--:-- UTC';
  return <span className="ticker-num text-xs text-muted">{label}</span>;
}

/* ---------------------------------------------------------------------------
   Institutional zone layout (2026-07-24 full refactor):
     ZONE 1  command bar   — brand · search · market tape · session/safety
     ZONE 2  primary nav   — the 7 lifecycle zones as large horizontal tabs
     ZONE 3  section nav   — the active zone's pages as a pill strip
     ZONE 4  main canvas   — full width, no left rail
   The old collapsed left rail is gone: navigation hierarchy lives at the top,
   the full viewport width belongs to content.
   --------------------------------------------------------------------------- */

function SectionPill({ item }: { item: NavItem }) {
  return (
    <NavLink to={item.to} end={item.end}
      className={({ isActive }) =>
        `flex shrink-0 items-center gap-2 rounded-md px-3 py-1.5 text-[13px] transition-colors duration-100 ${
          isActive ? 'text-ink' : 'text-muted hover:bg-elevated/50 hover:text-ink'}`}>
      {({ isActive }) => (
        <>
          <span className={`grid h-5 w-5 shrink-0 place-items-center rounded text-[8px] font-bold ${
            isActive ? 'text-accent-ink' : 'text-faint'}`}
            style={isActive
              ? { background: 'var(--grad)' }
              : { background: 'var(--inset)', boxShadow: 'inset 0 0 0 1px var(--line)' }}>
            {item.icon}
          </span>
          <span className="whitespace-nowrap font-medium">{item.zh}</span>
          <span className="hidden whitespace-nowrap text-2xs text-faint xl:inline">{item.en}</span>
          {isActive && <span className="ml-0.5 h-1.5 w-1.5 rounded-full" style={{ background: 'var(--accent)' }} />}
        </>
      )}
    </NavLink>
  );
}

function CommandPalette({ nav, open, onClose }: {
  nav: ReadonlyArray<NavGroup>;
  open: boolean;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const items = useMemo(() => nav.flatMap(group => group.items.map(item => ({ ...item, group: group.zh }))), [nav]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(item => `${item.zh} ${item.en} ${item.group} ${item.to}`.toLowerCase().includes(q));
  }, [items, query]);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    const focus = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(focus);
  }, [open]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[80] flex items-start justify-center bg-black/40 px-4 pt-[12vh]"
      role="dialog" aria-modal="true" aria-label="global search"
      onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="w-full max-w-xl overflow-hidden rounded-xl border border-line bg-panel shadow-2xl">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <span className="text-muted" aria-hidden>⌕</span>
          <input ref={inputRef} value={query} onChange={event => setQuery(event.target.value)}
            onKeyDown={event => { if (event.key === 'Escape') onClose(); }}
            placeholder={bi('搜索页面、流程或路径…', 'Search pages, workflows, or paths…')}
            className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-faint" />
          <kbd className="rounded border border-line px-1.5 text-3xs text-faint">ESC</kbd>
        </div>
        <div className="max-h-[55vh] overflow-y-auto p-2">
          {!filtered.length ? (
            <div className="px-3 py-8 text-center text-xs text-faint">{bi('没有匹配页面', 'No matching page')}</div>
          ) : filtered.map(item => (
            <NavLink key={item.to} to={item.to} onClick={onClose}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-elevated">
              <span className="grid h-7 w-7 place-items-center rounded bg-inset text-2xs font-bold text-accent">{item.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-ink">{item.zh} <span className="text-faint">{item.en}</span></span>
                <span className="block truncate text-3xs text-faint">{item.group} · {item.to}</span>
              </span>
              <span className="text-faint">↵</span>
            </NavLink>
          ))}
        </div>
      </div>
    </div>
  );
}

export function AppShell({ nav, tape, children }:
  { nav: ReadonlyArray<NavGroup>; tape?: React.ReactNode; children: React.ReactNode }) {
  const { pathname } = useLocation();
  const runtimeHandshake = useConsoleSnapshot<RuntimeContractPayload>(fetchRuntimeContract);
  const [searchOpen, setSearchOpen] = useState(false);
  const activeGroup = useMemo(
    () => nav.find(g => g.items.some(it => pathname === it.to || pathname.startsWith(`${it.to}/`)))
       ?? nav.find(g => g.items.length > 0),
    [nav, pathname]);
  const activeGroupId: GroupId | null = useMemo(
    () => (activeGroup ? GROUPS.find(g => g.zh === activeGroup.zh)?.id ?? null : null),
    [activeGroup]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) return;
      event.preventDefault();
      setSearchOpen(true);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  return (
    <ToastProvider>
      <ConfirmDialogHost>
        <div className="flex min-h-screen flex-col text-ink">

          {/* ══ ZONE 1 · command bar: brand · search · tape · session/safety ══ */}
          <header className="glass sticky top-0 z-40 border-b border-line">
            <div className="flex h-14 items-center gap-2 px-3 sm:gap-4 sm:px-6">
              <div className="flex shrink-0 items-center gap-2.5">
                <span className="grid h-8 w-8 place-items-center rounded text-[15px] font-black text-accent-ink"
                  style={{ background: 'var(--grad)' }}>Q</span>
                <div className="leading-none">
                  <div className="text-[15px] font-bold tracking-tight text-ink">QUANTOS</div>
                  <div className="mt-0.5 hidden text-3xs uppercase tracking-[0.12em] text-faint sm:block">
                    {bi('机构研究工作台', 'Institutional Workbench')}
                  </div>
                </div>
              </div>

              {/* route search command palette — navigation stays in the shell */}
              <button type="button" onClick={() => setSearchOpen(true)}
                aria-label={bi('全局搜索', 'Global search')}
                className="hidden shrink-0 items-center gap-1.5 rounded-md border border-line bg-inset/60 px-3 py-1.5 text-xs text-faint hover:border-accent/50 hover:text-ink md:flex">
                <span aria-hidden>⌕</span>
                <span className="ticker-num">{bi('搜索…', 'Search…')}</span>
                <kbd className="rounded border border-line px-1 text-3xs text-faint">/</kbd>
              </button>

              {/* centre ticker-tape slot (provided by the host page; empty = reserved) */}
              <div className="hidden min-w-0 flex-1 items-center lg:flex">
                {tape ?? <span className="text-3xs uppercase tracking-[0.14em] text-faint/50">TAPE</span>}
              </div>
              <div className="min-w-0 flex-1 lg:hidden" />

              <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
                <span className="hidden xl:inline-flex"><SessionClock /></span>
                <span className="hidden xl:inline-flex h-3.5 w-px bg-line-strong" />
                <span className="sm:hidden"><Chip label="L2" tone="info" /></span>
                <span className="hidden sm:inline-flex"><Chip label="L2_DRY_RUN_ONLY" tone="warn" /></span>
                <span className="hidden md:inline-flex"><Chip label={bi('只读', 'READ_ONLY')} tone="info" /></span>
                <span className="hidden sm:inline-flex"><Chip label={bi('实盘关闭', 'LIVE OFF')} tone="neg" /></span>
                <span className="hidden sm:inline-flex"><Chip label={bi('实盘门禁 · 人工批准', 'LIVE_GATED_HUMAN')} tone="neg" /></span>
                <span className="hidden xs:inline-flex"><Chip label={mode} tone={mode === 'REAL_API' ? 'pos' : 'warn'} /></span>
                <span className="hidden lg:inline-flex">
                  <RuntimeHandshake state={runtimeHandshake} />
                </span>
              </div>
            </div>

            {/* ══ ZONE 2 · lifecycle pipeline stepper: the 7 stages as a connected,
                  live-instrumented flow (each node pulls one real metric) ══ */}
            <nav className="hidden border-t border-line/60 px-2 md:block sm:px-4" aria-label="lifecycle pipeline">
              <PipelineFlow compact activeGroup={activeGroupId} />
            </nav>

            {/* ══ ZONE 3 · section navigation: pages inside the active zone ══ */}
            {activeGroup && (
              <nav className="flex items-center gap-1 overflow-x-auto border-t border-line/60 bg-surface/60 px-3 py-1.5 sm:px-5"
                aria-label="section pages">
                <span className="mr-1 hidden shrink-0 text-3xs font-semibold uppercase tracking-[0.16em] text-faint lg:inline">
                  {activeGroup.zh} <span className="opacity-50">{activeGroup.en}</span>
                </span>
                <span className="mr-1 hidden h-3.5 w-px shrink-0 bg-line-strong lg:inline-block" />
                {activeGroup.items.map(item => <SectionPill key={item.to} item={item} />)}
              </nav>
            )}
          </header>

          <RuntimeHandshakeNotice state={runtimeHandshake} />

          {/* ══ ZONE 4 · main canvas — full width, generous margins ══ */}
          <main className="min-w-0 flex-1 overflow-x-hidden px-3 py-4 sm:px-6 sm:py-6 lg:px-10 lg:py-8">
            {children}
          </main>
        </div>
        <CommandPalette nav={nav} open={searchOpen} onClose={() => setSearchOpen(false)} />
      </ConfirmDialogHost>
    </ToastProvider>
  );
}
