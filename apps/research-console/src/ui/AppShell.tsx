import React, { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { bi } from '@/lib/i18n/bilingual';
import { Chip } from './primitives';
import { ConfirmDialogHost } from './dialog';
import { ToastProvider } from './toast';

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
  return <span className="ticker-num text-2xs text-muted">{label}</span>;
}

function RailLink({ item }: { item: NavItem }) {
  return (
    <NavLink to={item.to} end={item.end}
      className={({ isActive }) =>
        `group relative flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[12.5px] transition-colors duration-100 ${
          isActive ? 'bg-elevated/70 text-ink shadow-panel' : 'text-muted hover:bg-elevated/40 hover:text-ink'}`}>
      {({ isActive }) => (
        <>
          {isActive && <span className="absolute left-0 top-1.5 h-[calc(100%-12px)] w-[2px] rounded-full bg-grad" />}
          <span className={`grid h-6 w-6 shrink-0 place-items-center rounded text-[8.5px] font-bold transition-colors ${
            isActive ? 'text-accent-ink' : 'text-faint group-hover:text-muted'}`}
            style={isActive ? { background: 'var(--grad)' }
              : { background: 'var(--inset)', boxShadow: 'inset 0 0 0 1px var(--line)' }}>
            {item.icon}
          </span>
          <span className="min-w-0 leading-tight">
            <span className="block truncate font-medium">{item.zh}</span>
            <span className="block truncate text-3xs text-faint">{item.en}</span>
          </span>
        </>
      )}
    </NavLink>
  );
}

export function AppShell({ nav, tape, children }:
  { nav: ReadonlyArray<NavGroup>; tape?: React.ReactNode; children: React.ReactNode }) {
  return (
    <ToastProvider>
      <ConfirmDialogHost>
        <div className="flex min-h-screen flex-col text-ink">
          {/* ---- top command bar: brand · search · tape slot · session/safety cluster ---- */}
          <header className="glass sticky top-0 z-30 flex h-12 items-center gap-3 border-b border-line px-3 sm:px-4">
            <div className="flex shrink-0 items-center gap-2">
              <span className="grid h-7 w-7 place-items-center rounded text-[13px] font-black text-accent-ink"
                style={{ background: 'var(--grad)' }}>Q</span>
              <div className="leading-none">
                <div className="text-[13.5px] font-bold tracking-tight text-ink">QUANTOS</div>
                <div className="mt-0.5 hidden text-3xs uppercase tracking-[0.12em] text-faint sm:block">
                  {bi('机构研究工作台', 'Institutional Workbench')}
                </div>
              </div>
            </div>

            {/* global search (placeholder affordance — wiring is a page-level concern) */}
            <div className="hidden shrink-0 items-center gap-1.5 rounded-md border border-line bg-inset/60 px-2.5 py-1 text-2xs text-faint md:flex">
              <span aria-hidden>⌕</span>
              <span className="ticker-num">{bi('搜索…', 'Search…')}</span>
              <kbd className="rounded border border-line px-1 text-3xs text-faint">/</kbd>
            </div>

            {/* centre ticker-tape slot (provided by the host page; empty = reserved) */}
            <div className="hidden min-w-0 flex-1 items-center lg:flex">
              {tape ?? <span className="text-3xs uppercase tracking-[0.14em] text-faint/50">TAPE</span>}
            </div>
            <div className="min-w-0 flex-1 lg:hidden" />

            <div className="flex shrink-0 items-center gap-1 sm:gap-1.5">
              <span className="hidden xl:inline-flex"><SessionClock /></span>
              <span className="hidden xl:inline-flex h-3 w-px bg-line-strong" />
              <span className="sm:hidden"><Chip label="L2" tone="info" /></span>
              <span className="hidden sm:inline-flex"><Chip label="MEXC_SPOT_ARMED_GATED" tone="warn" /></span>
              <span className="hidden md:inline-flex"><Chip label={bi('只读', 'READ_ONLY')} tone="info" /></span>
              <span className="sm:hidden"><Chip label="LIVE OFF" tone="neg" /></span>
              <span className="hidden sm:inline-flex"><Chip label={bi('实盘门禁 · 人工批准', 'LIVE_GATED_HUMAN')} tone="neg" /></span>
              <Chip label={mode} tone={mode === 'MOCK' ? 'warn' : 'pos'} />
            </div>
          </header>

          <div className="flex flex-1 flex-col lg:flex-row">
            {/* ---- left rail (lg+), narrowed high-density ---- */}
            <nav className="hidden w-52 shrink-0 flex-col gap-4 overflow-y-auto border-r border-line bg-surface/40 p-3 lg:flex lg:sticky lg:top-12 lg:h-[calc(100vh-3rem)]">
              {nav.map(group => (
                <div key={group.en} className="flex flex-col gap-0.5">
                  <div className="px-2.5 pb-1 text-3xs font-semibold uppercase tracking-[0.16em] text-faint">
                    {group.zh} <span className="opacity-50">{group.en}</span>
                  </div>
                  {group.items.map(item => <RailLink key={item.to} item={item} />)}
                </div>
              ))}
            </nav>

            {/* ---- compact horizontal nav (< lg) ---- */}
            <nav className="glass flex gap-1 overflow-x-auto border-b border-line p-2 lg:hidden">
              {nav.flatMap(g => g.items).map(item => (
                <NavLink key={item.to} to={item.to} end={item.end}
                  className={({ isActive }) => `flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-xs ${
                    isActive ? 'bg-elevated text-ink' : 'text-muted'}`}>
                  <span className="text-3xs font-bold text-accent">{item.icon}</span>{item.zh}
                </NavLink>
              ))}
            </nav>

            {/* ---- main canvas ---- */}
            <main className="min-w-0 flex-1 overflow-x-hidden p-4 lg:p-6">{children}</main>
          </div>
        </div>
      </ConfirmDialogHost>
    </ToastProvider>
  );
}
