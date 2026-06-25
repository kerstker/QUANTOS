import React from 'react';
import { NavLink } from 'react-router-dom';
import { bi } from '@/lib/i18n/bilingual';
import { Chip } from './primitives';

export interface NavItem { to: string; zh: string; en: string; icon: string; end?: boolean }
export interface NavGroup { zh: string; en: string; items: NavItem[] }

const mode = import.meta.env.VITE_MOCK_MODE !== 'false' ? 'MOCK' : 'LIVE_API';

function RailLink({ item }: { item: NavItem }) {
  return (
    <NavLink to={item.to} end={item.end}
      className={({ isActive }) =>
        `group relative flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
          isActive ? 'bg-elevated text-ink' : 'text-muted hover:bg-elevated/50 hover:text-ink'}`}>
      {({ isActive }) => (
        <>
          {isActive && <span className="absolute left-0 top-1.5 h-[calc(100%-12px)] w-0.5 rounded-full bg-accent" />}
          <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-md border text-[9px] font-bold ${
            isActive ? 'border-accent-dim bg-accent-ink text-accent' : 'border-line bg-inset text-faint group-hover:text-muted'}`}>
            {item.icon}
          </span>
          <span className="min-w-0 leading-tight">
            <span className="block truncate">{item.zh}</span>
            <span className="block truncate text-2xs text-faint">{item.en}</span>
          </span>
        </>
      )}
    </NavLink>
  );
}

export function AppShell({ nav, children }: { nav: ReadonlyArray<NavGroup>; children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-ink">
      {/* ---- top command bar: brand + persistent safety cluster ---- */}
      <header className="sticky top-0 z-30 flex h-12 items-center gap-3 border-b border-line bg-surface/90 px-4 backdrop-blur">
        <div className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-accent-ink text-accent font-bold ring-1 ring-accent-dim">Q</span>
          <div className="leading-none">
            <div className="text-sm font-bold tracking-wide text-ink">QUANTOS</div>
            <div className="text-2xs text-faint">{bi('量化研究指挥中心', 'Research Command Center')}</div>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <Chip label="L2_DRY_RUN_ONLY" tone="info" />
          <Chip label={bi('只读', 'READ_ONLY')} tone="info" />
          <Chip label={bi('实盘禁用', 'LIVE_DISALLOWED')} tone="neg" />
          <Chip label={mode} tone={mode === 'MOCK' ? 'warn' : 'pos'} />
        </div>
      </header>

      <div className="flex flex-1 flex-col lg:flex-row">
        {/* ---- left rail (lg+) ---- */}
        <nav className="hidden w-56 shrink-0 flex-col gap-4 overflow-y-auto border-r border-line bg-surface/60 p-3 lg:flex lg:sticky lg:top-12 lg:h-[calc(100vh-3rem)]">
          {nav.map(group => (
            <div key={group.en} className="flex flex-col gap-0.5">
              <div className="px-2.5 pb-1 text-2xs font-semibold uppercase tracking-wider text-faint">
                {group.zh} <span className="opacity-50">{group.en}</span>
              </div>
              {group.items.map(item => <RailLink key={item.to} item={item} />)}
            </div>
          ))}
        </nav>

        {/* ---- compact horizontal nav (< lg) ---- */}
        <nav className="flex gap-1 overflow-x-auto border-b border-line bg-surface/60 p-2 lg:hidden">
          {nav.flatMap(g => g.items).map(item => (
            <NavLink key={item.to} to={item.to} end={item.end}
              className={({ isActive }) => `flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs ${
                isActive ? 'bg-elevated text-ink' : 'text-muted'}`}>
              <span className="text-2xs font-bold text-accent">{item.icon}</span>{item.zh}
            </NavLink>
          ))}
        </nav>

        {/* ---- main canvas ---- */}
        <main className="min-w-0 flex-1 overflow-x-hidden p-4 lg:p-6">{children}</main>
      </div>
    </div>
  );
}
