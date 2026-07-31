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

const PRIMARY_OPERATIONS = [
  { to: '/research/command', zh: '指挥', en: 'Command' },
  { to: '/research/dry-run', zh: '纸面', en: 'Paper' },
  { to: '/research/risk', zh: '风险', en: 'Risk' },
  { to: '/research/data-catalog', zh: '数据', en: 'Data' },
  { to: '/research/system-health', zh: '健康', en: 'Health' },
] as const;

function isPrimaryOperationsPath(pathname: string): boolean {
  return pathname === '/'
    || pathname === '/research'
    || PRIMARY_OPERATIONS.some(item => item.to === pathname);
}

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

function SectionMenuItem({ item, onSelect }: { item: NavItem; onSelect: () => void }) {
  return (
    <NavLink to={item.to} end={item.end} onClick={onSelect}
      className={({ isActive }) =>
        `flex min-w-0 items-center gap-2 rounded-md px-2.5 py-2 text-[12px] transition-colors duration-100 ${
          isActive ? 'bg-elevated text-ink' : 'text-muted hover:bg-elevated/50 hover:text-ink'}`}>
      {({ isActive }) => (
        <>
          <span className={`grid h-6 w-6 shrink-0 place-items-center rounded text-[8px] font-bold ${
            isActive ? 'text-accent-ink' : 'text-faint'}`}
            style={isActive
              ? { background: 'var(--grad)' }
              : { background: 'var(--inset)', boxShadow: 'inset 0 0 0 1px var(--line)' }}>
            {item.icon}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium">{item.zh}</span>
            <span className="block truncate text-3xs text-faint">{item.en}</span>
          </span>
          {isActive && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />}
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
  const [sectionOpen, setSectionOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(() => !isPrimaryOperationsPath(pathname));
  const primaryItems = useMemo(() => {
    const allItems = nav.flatMap(group => group.items);
    return PRIMARY_OPERATIONS.map(primary => ({
      ...primary,
      icon: allItems.find(item => item.to === primary.to)?.icon ?? '--',
    }));
  }, [nav]);
  const isPrimaryRoute = isPrimaryOperationsPath(pathname);
  const activeGroup = useMemo(
    () => nav.find(g => g.items.some(it => pathname === it.to || pathname.startsWith(`${it.to}/`)))
       ?? nav.find(g => g.items.length > 0),
    [nav, pathname]);
  const activeGroupId: GroupId | null = useMemo(
    () => (activeGroup ? GROUPS.find(g => g.zh === activeGroup.zh)?.id ?? null : null),
    [activeGroup]);
  const activeItemIndex = activeGroup?.items.findIndex(
    item => pathname === item.to || pathname.startsWith(`${item.to}/`),
  ) ?? -1;
  const activeItem = activeGroup?.items[activeItemIndex >= 0 ? activeItemIndex : 0] ?? null;
  const previousItem = activeGroup && activeItemIndex > 0
    ? activeGroup.items[activeItemIndex - 1]
    : null;
  const nextItem = activeGroup && activeItemIndex >= 0 && activeItemIndex < activeGroup.items.length - 1
    ? activeGroup.items[activeItemIndex + 1]
    : null;

  useEffect(() => {
    setSectionOpen(false);
    if (!isPrimaryRoute) setAdvancedOpen(true);
  }, [isPrimaryRoute, pathname]);

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

            <div className="flex min-h-12 items-stretch border-t border-line/60 bg-surface/70 px-2 sm:px-4">
              <nav className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto py-1.5"
                aria-label="primary operations">
                {primaryItems.map(item => (
                  <NavLink key={item.to} to={item.to} end
                    className={({ isActive }) =>
                      `flex min-w-[92px] items-center justify-center gap-2 rounded-md px-3 py-1.5 transition-colors ${
                        isActive
                          ? 'bg-elevated text-ink shadow-sm'
                          : 'text-muted hover:bg-elevated/50 hover:text-ink'
                      }`}>
                    {({ isActive }) => (
                      <>
                        <span className={`grid h-6 w-6 place-items-center rounded text-[8px] font-bold ${
                          isActive ? 'bg-accent/15 text-accent' : 'bg-inset text-faint'
                        }`}>
                          {item.icon}
                        </span>
                        <span className="text-left leading-tight">
                          <span className="block text-[12px] font-semibold">{item.zh}</span>
                          <span className="block text-3xs text-faint">{item.en}</span>
                        </span>
                      </>
                    )}
                  </NavLink>
                ))}
              </nav>
              <div className="my-2 ml-2 w-px shrink-0 bg-line-strong" />
              <button type="button"
                aria-expanded={advancedOpen}
                aria-controls="advanced-workbench-navigation"
                onClick={() => setAdvancedOpen(open => !open)}
                className="my-1.5 ml-2 flex shrink-0 items-center gap-2 rounded-md border border-line bg-inset/70 px-3 text-[11px] font-medium text-muted hover:border-line-strong hover:text-ink">
                <span className="hidden sm:inline">{bi('高级工作台', 'Advanced')}</span>
                <span className="sm:hidden">{bi('高级', 'More')}</span>
                <span aria-hidden className="text-[9px]">{advancedOpen ? '▲' : '▼'}</span>
              </button>
            </div>

            {advancedOpen && (
              <div id="advanced-workbench-navigation">
                {/* Full lifecycle and page navigation remains available for researchers,
                    reviewers, and operators who explicitly enter the advanced workbench. */}
                <nav className="border-t border-line/60 px-1 sm:px-3" aria-label="lifecycle pipeline">
                  <PipelineFlow compact activeGroup={activeGroupId} />
                </nav>

                {activeGroup && (
              <nav className="relative flex h-11 items-center gap-2 border-t border-line/60 bg-surface/60 px-3 sm:px-5"
                aria-label="section pages">
                <span className="hidden shrink-0 text-3xs font-semibold uppercase tracking-[0.14em] text-faint md:inline">
                  {activeGroup.zh} <span className="opacity-50">{activeGroup.en}</span>
                </span>
                <span className="hidden h-3.5 w-px shrink-0 bg-line-strong md:inline-block" />
                {activeItem && (
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="grid h-6 w-6 shrink-0 place-items-center rounded bg-elevated text-[8px] font-bold text-accent">
                      {activeItem.icon}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-semibold text-ink">{activeItem.zh}</span>
                      <span className="hidden truncate text-3xs text-faint sm:block">{activeItem.en}</span>
                    </span>
                  </span>
                )}
                <span className="ml-auto flex shrink-0 items-center gap-1">
                  {previousItem && (
                    <NavLink to={previousItem.to}
                      aria-label={bi(`上一页：${previousItem.zh}`, `Previous: ${previousItem.en}`)}
                      className="grid h-7 w-7 place-items-center rounded border border-line bg-inset text-xs text-muted hover:border-line-strong hover:text-ink">
                      ←
                    </NavLink>
                  )}
                  {nextItem && (
                    <NavLink to={nextItem.to}
                      aria-label={bi(`下一页：${nextItem.zh}`, `Next: ${nextItem.en}`)}
                      className="grid h-7 w-7 place-items-center rounded border border-line bg-inset text-xs text-muted hover:border-line-strong hover:text-ink">
                      →
                    </NavLink>
                  )}
                  <button type="button"
                    aria-expanded={sectionOpen}
                    aria-controls="section-page-menu"
                    aria-label={bi('全部页面', 'All pages')}
                    onClick={() => setSectionOpen(open => !open)}
                    className="ml-1 flex h-7 items-center gap-1.5 rounded border border-line bg-inset px-2.5 text-[11px] font-medium text-muted hover:border-line-strong hover:text-ink">
                    <span>{bi('全部页面', 'All pages')}</span>
                    <span className="ticker-num text-faint">{activeGroup.items.length}</span>
                    <span aria-hidden className="text-[9px]">{sectionOpen ? '▲' : '▼'}</span>
                  </button>
                </span>
                {sectionOpen && (
                  <div id="section-page-menu" aria-label={bi('分区页面菜单', 'Section page menu')}
                    className="absolute right-3 top-[calc(100%+6px)] z-50 grid max-h-[65vh] w-[min(560px,calc(100vw-24px))] grid-cols-1 gap-1 overflow-y-auto rounded-lg border border-line bg-panel p-2 shadow-2xl sm:right-5 sm:grid-cols-2">
                    {activeGroup.items.map(item => (
                      <SectionMenuItem key={item.to} item={item} onSelect={() => setSectionOpen(false)} />
                    ))}
                  </div>
                )}
              </nav>
                )}
              </div>
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
