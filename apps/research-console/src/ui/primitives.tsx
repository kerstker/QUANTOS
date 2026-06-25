import React from 'react';
import { bi, statusLabel } from '@/lib/i18n/bilingual';
import { statusTone, TONE_VAR, type Tone } from './format';
import type { ConsoleApiState } from './useConsoleSnapshot';
import { RESEARCH_CONSOLE_API_BASE } from '@/lib/api/research-console-api';

const toneText: Record<Tone, string> = {
  pos: 'text-pos', neg: 'text-neg', warn: 'text-warn', info: 'text-info',
  accent: 'text-accent', special: 'text-special', muted: 'text-muted',
};

/* ---------- Chip / Badge -------------------------------------------------- */
export function Chip({ label, tone = 'muted', soft = true, className = '' }:
  { label: React.ReactNode; tone?: Tone; soft?: boolean; className?: string }) {
  const c = TONE_VAR[tone];
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-2xs font-semibold leading-tight ${className}`}
      style={{
        color: c,
        background: soft ? `color-mix(in srgb, ${c} 12%, transparent)` : 'transparent',
        border: `1px solid color-mix(in srgb, ${c} 35%, transparent)`,
      }}>
      {label}
    </span>
  );
}

/** Status/decision chip that auto-tones + bilingual-labels common enums. */
export function StatusChip({ value, className = '' }: { value: string; className?: string }) {
  return <Chip label={statusLabel(value)} tone={statusTone(value)} className={className} />;
}

export function GateDot({ ok, label }: { ok: boolean; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-2xs">
      <span className="h-2 w-2 rounded-full" style={{ background: ok ? 'var(--pos)' : 'var(--neg)', boxShadow: `0 0 8px ${ok ? 'var(--pos)' : 'var(--neg)'}` }} />
      {label && <span className="text-muted">{label}</span>}
    </span>
  );
}

/* ---------- Panel: the canvas card ---------------------------------------- */
export function Panel({ title, eyebrow, right, tone, children, className = '', bodyClass = '' }:
  { title?: React.ReactNode; eyebrow?: React.ReactNode; right?: React.ReactNode; tone?: Tone;
    children: React.ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={`q-rise flex min-w-0 flex-col rounded-panel border border-line bg-panel ${className}`}
      style={tone ? { borderColor: `color-mix(in srgb, ${TONE_VAR[tone]} 40%, var(--line))` } : undefined}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <div className="min-w-0">
            {eyebrow && <div className="text-2xs uppercase tracking-wider text-faint">{eyebrow}</div>}
            {title && <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>}
          </div>
          {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
        </header>
      )}
      <div className={`min-w-0 flex-1 p-4 ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function SectionHeader({ title, sub }: { title: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="mb-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
      {sub && <p className="mt-0.5 text-2xs text-faint">{sub}</p>}
    </div>
  );
}

/* ---------- Stat tile + KPI strip ----------------------------------------- */
export function StatTile({ label, value, unit, sub, tone, accent }:
  { label: React.ReactNode; value: React.ReactNode; unit?: string; sub?: React.ReactNode; tone?: Tone; accent?: boolean }) {
  return (
    <div className="flex min-w-0 flex-col justify-between rounded-xl border border-line bg-gradient-to-b from-elevated/40 to-panel p-3.5">
      <div className="truncate text-2xs uppercase tracking-wide text-faint">{label}</div>
      <div className={`mt-1.5 flex items-baseline gap-1 ${accent ? 'text-accent' : tone ? toneText[tone] : 'text-ink'}`}>
        <span className="tnum text-2xl font-bold leading-none">{value}</span>
        {unit && <span className="text-xs text-muted">{unit}</span>}
      </div>
      {sub !== undefined && <div className="mt-1.5 truncate text-2xs text-faint">{sub}</div>}
    </div>
  );
}

export function KpiStrip({ children, cols = 4 }: { children: React.ReactNode; cols?: number }) {
  return <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${Math.max(150, 1000 / cols)}px, 1fr))` }}>{children}</div>;
}

/* ---------- Progress / score bar ------------------------------------------ */
export function ProgressBar({ value, max = 1, tone = 'accent', height = 6 }:
  { value: number; max?: number; tone?: Tone; height?: number }) {
  const frac = Math.max(0, Math.min(1, value / (max || 1)));
  return (
    <div className="w-full overflow-hidden rounded-full bg-inset" style={{ height }}>
      <div className="h-full rounded-full" style={{ width: `${frac * 100}%`, background: TONE_VAR[tone] }} />
    </div>
  );
}

/* ---------- DataTable ------------------------------------------------------ */
export interface Column<T> {
  key: string;
  header: React.ReactNode;
  align?: 'left' | 'right' | 'center';
  mono?: boolean;
  width?: string;
  render: (row: T, i: number) => React.ReactNode;
}
export function DataTable<T>({ columns, rows, dense, empty, getKey }:
  { columns: ReadonlyArray<Column<T>>; rows: ReadonlyArray<T>; dense?: boolean; empty?: React.ReactNode;
    getKey?: (row: T, i: number) => React.Key }) {
  if (!rows.length) return <EmptyState>{empty ?? bi('无记录', 'No records')}</EmptyState>;
  const pad = dense ? 'px-3 py-1.5' : 'px-3 py-2.5';
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-line-strong">
            {columns.map(c => (
              <th key={c.key} style={{ width: c.width, textAlign: c.align ?? 'left' }}
                className={`${pad} text-2xs font-semibold uppercase tracking-wide text-faint`}>{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={getKey ? getKey(row, i) : i} className="border-b border-line/60 transition-colors hover:bg-elevated/40">
              {columns.map(c => (
                <td key={c.key} style={{ textAlign: c.align ?? 'left' }}
                  className={`${pad} align-middle text-ink ${c.mono ? 'mono tnum' : ''} ${c.align === 'right' ? 'tnum' : ''}`}>
                  {c.render(row, i)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------- KeyValue / Field ---------------------------------------------- */
export function KeyValue({ items, cols = 2 }: { items: ReadonlyArray<{ k: React.ReactNode; v: React.ReactNode }>; cols?: number }) {
  return (
    <dl className="grid gap-x-6 gap-y-2.5" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0,1fr))` }}>
      {items.map((it, i) => (
        <div key={i} className="flex min-w-0 flex-col gap-0.5">
          <dt className="text-2xs uppercase tracking-wide text-faint">{it.k}</dt>
          <dd className="truncate text-xs text-ink mono">{it.v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center justify-center rounded-lg border border-dashed border-line py-8 text-xs text-faint">{children}</div>;
}

/* ---------- Page header + provenance -------------------------------------- */
export function PageHeader({ title, titleZh, subtitle, actions }:
  { title: string; titleZh: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-ink">
          {titleZh} <span className="text-muted font-medium">/ {title}</span>
        </h1>
        {subtitle && <p className="mt-1 max-w-3xl text-xs text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/**
 * Honesty + connection ribbon. Neither data source is real research output —
 * mock mode = frontend fixtures, backend mode = the console API's deterministic
 * SYNTHETIC snapshots. Labelled explicitly so a synthetic Sharpe / PnL / quality
 * figure is never mistaken for measured alpha (audit P1). Read-only always.
 */
export function ProvenanceRibbon({ usingBackend, apiState, source, error }:
  { usingBackend: boolean; apiState: ConsoleApiState; source?: string; error?: string | null }) {
  const apiTone: Tone = apiState === 'connected' ? 'pos' : apiState === 'error' ? 'neg' : apiState === 'loading' ? 'info' : 'muted';
  const apiLabel = apiState === 'connected' ? bi('已连接', 'CONNECTED') : apiState === 'error' ? bi('错误', 'ERROR')
    : apiState === 'loading' ? bi('加载中', 'LOADING') : bi('未连接', 'NOT_CONNECTED');
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-warn/30 bg-warn/5 px-3 py-1.5">
      <span className="text-2xs font-semibold text-warn">
        ⚠ {bi('合成演示数据 · 非真实研究结果或盈亏', 'Synthetic demo data · not real research results or PnL')}
        {usingBackend ? bi('（后端确定性合成快照）', ' (backend synthetic snapshot)') : bi('（前端 mock）', ' (frontend mock)')}
      </span>
      <span className="mx-1 h-3 w-px bg-line-strong" />
      <Chip label={usingBackend ? 'BACKEND_COMPUTED' : 'MOCK_MODE'} tone={usingBackend ? 'pos' : 'warn'} />
      <Chip label={apiLabel} tone={apiTone} />
      <Chip label={bi('只读', 'READ_ONLY')} tone="info" />
      <span className="ml-auto text-2xs text-faint">
        {error ? <span className="text-neg">{bi('错误', 'Error')}: {error}</span>
          : <>{bi('数据源', 'Source')}: <span className="mono">{source ?? (usingBackend ? 'BACKEND_COMPUTED_SNAPSHOT' : 'FRONTEND_MOCK_FALLBACK')}</span>
            {'  ·  API: '}<span className="mono">{RESEARCH_CONSOLE_API_BASE || bi('未启用', 'disabled')}</span></>}
      </span>
    </div>
  );
}

/* ---------- Layout helpers ------------------------------------------------- */
export function Grid({ children, min = 320, className = '' }: { children: React.ReactNode; min?: number; className?: string }) {
  return <div className={`grid gap-4 ${className}`} style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))` }}>{children}</div>;
}

export function PageShell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto flex w-full max-w-[1760px] flex-col gap-4">{children}</div>;
}
