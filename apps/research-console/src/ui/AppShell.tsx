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
        `group relative flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition-all duration-150 ${
          isActive ? 'bg-elevated/70 text-ink shadow-panel' : 'text-muted hover:bg-elevated/40 hover:text-ink'}`}>
      {({ isActive }) => (
        <>
          {isActive && <span className="absolute left-0 top-2 h-[calc(100%-16px)] w-[3px] rounded-full bg-grad" />}
          <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg text-[9px] font-bold transition-all ${
            isActive ? 'text-ink' : 'text-faint group-hover:text-muted'}`}
            style={isActive ? { background: 'var(--grad)', boxShadow: 'var(--glow-accent)' }
              : { background: 'var(--inset)', boxShadow: 'inset 0 0 0 1px var(--line)' }}>
            {item.icon}
          </span>
          <span className="min-w-0 leading-tight">
            <span className="block truncate font-medium">{item.zh}</span>
            <span className="block truncate text-2xs text-faint">{item.en}</span>
          </span>
        </>
      )}
    </NavLink>
  );
}

export function AppShell({ nav, children }: { nav: ReadonlyArray<NavGroup>; children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col text-ink">
      {/* ---- top command bar: brand + persistent safety cluster (glass) ---- */}
      <header className="glass sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line px-5">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-xl text-[15px] font-black text-ink"
            style={{ background: 'var(--grad)', boxShadow: 'var(--glow-accent)' }}>Q</span>
          <div className="leading-none">
            <div className="text-[15px] font-bold tracking-tight text-ink">QUANTOS</div>
            <div className="mt-0.5 text-2xs text-faint">{bi('量化研究指挥中心', 'Research Command Center')}</div>
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
        <nav className="hidden w-60 shrink-0 flex-col gap-5 overflow-y-auto border-r border-line bg-surface/40 p-4 lg:flex lg:sticky lg:top-14 lg:h-[calc(100vh-3.5rem)]">
          {nav.map(group => (
            <div key={group.en} className="flex flex-col gap-1">
              <div className="px-3 pb-1.5 text-2xs font-semibold uppercase tracking-[0.16em] text-faint">
                {group.zh} <span className="opacity-50">{group.en}</span>
              </div>
              {group.items.map(item => <RailLink key={item.to} item={item} />)}
            </div>
          ))}
        </nav>

        {/* ---- compact horizontal nav (< lg) ---- */}
        <nav className="glass flex gap-1.5 overflow-x-auto border-b border-line p-2.5 lg:hidden">
          {nav.flatMap(g => g.items).map(item => (
            <NavLink key={item.to} to={item.to} end={item.end}
              className={({ isActive }) => `flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs ${
                isActive ? 'bg-elevated text-ink' : 'text-muted'}`}>
              <span className="text-2xs font-bold text-accent">{item.icon}</span>{item.zh}
            </NavLink>
          ))}
        </nav>

        {/* ---- main canvas ---- */}
        <main className="min-w-0 flex-1 overflow-x-hidden p-5 lg:p-8">{children}</main>
      </div>
    </div>
  );
}
