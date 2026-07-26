import React, { useEffect, useMemo, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { bi } from '@/lib/i18n/bilingual';
import { Chip } from './primitives';
import { ConfirmDialogHost } from './dialog';
import { ToastProvider } from './toast';
import { PipelineFlow } from './PipelineFlow';
import { GROUPS, type GroupId } from '@/app/routes';

export interface NavItem { to: string; zh: string; en: string; icon: string; end?: boolean }
export interface NavGroup { zh: string; en: string; items: NavItem[] }

const mode = import.meta.env.VITE_MOCK_MODE !== 'false' ? 'MOCK' : 'LIVE_API';

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

export function AppShell({ nav, tape, children }:
  { nav: ReadonlyArray<NavGroup>; tape?: React.ReactNode; children: React.ReactNode }) {
  const { pathname } = useLocation();
  const activeGroup = useMemo(
    () => nav.find(g => g.items.some(it => pathname === it.to || pathname.startsWith(`${it.to}/`)))
       ?? nav.find(g => g.items.length > 0),
    [nav, pathname]);
  const activeGroupId: GroupId | null = useMemo(
    () => (activeGroup ? GROUPS.find(g => g.zh === activeGroup.zh)?.id ?? null : null),
    [activeGroup]);

  return (
    <ToastProvider>
      <ConfirmDialogHost>
        <div className="flex min-h-screen flex-col text-ink">

          {/* ══ ZONE 1 · command bar: brand · search · tape · session/safety ══ */}
          <header className="glass sticky top-0 z-40 border-b border-line">
            <div className="flex h-14 items-center gap-4 px-4 sm:px-6">
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

              {/* global search (placeholder affordance — wiring is a page-level concern) */}
              <div className="hidden shrink-0 items-center gap-1.5 rounded-md border border-line bg-inset/60 px-3 py-1.5 text-xs text-faint md:flex">
                <span aria-hidden>⌕</span>
                <span className="ticker-num">{bi('搜索…', 'Search…')}</span>
                <kbd className="rounded border border-line px-1 text-3xs text-faint">/</kbd>
              </div>

              {/* centre ticker-tape slot (provided by the host page; empty = reserved) */}
              <div className="hidden min-w-0 flex-1 items-center lg:flex">
                {tape ?? <span className="text-3xs uppercase tracking-[0.14em] text-faint/50">TAPE</span>}
              </div>
              <div className="min-w-0 flex-1 lg:hidden" />

              <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
                <span className="hidden xl:inline-flex"><SessionClock /></span>
                <span className="hidden xl:inline-flex h-3.5 w-px bg-line-strong" />
                <span className="sm:hidden"><Chip label="L2" tone="info" /></span>
                <span className="hidden sm:inline-flex"><Chip label="MEXC_SPOT_ARMED_GATED" tone="warn" /></span>
                <span className="hidden md:inline-flex"><Chip label={bi('只读', 'READ_ONLY')} tone="info" /></span>
                <span className="sm:hidden"><Chip label="LIVE OFF" tone="neg" /></span>
                <span className="hidden sm:inline-flex"><Chip label={bi('实盘门禁 · 人工批准', 'LIVE_GATED_HUMAN')} tone="neg" /></span>
                <Chip label={mode} tone={mode === 'MOCK' ? 'warn' : 'pos'} />
              </div>
            </div>

            {/* ══ ZONE 2 · lifecycle pipeline stepper: the 7 stages as a connected,
                  live-instrumented flow (each node pulls one real metric) ══ */}
            <nav className="border-t border-line/60 px-2 sm:px-4" aria-label="lifecycle pipeline">
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

          {/* ══ ZONE 4 · main canvas — full width, generous margins ══ */}
          <main className="min-w-0 flex-1 overflow-x-hidden px-5 py-6 sm:px-8 lg:px-10 lg:py-8">
            {children}
          </main>
        </div>
      </ConfirmDialogHost>
    </ToastProvider>
  );
}
