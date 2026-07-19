import React from 'react';
import { bi, statusLabel } from '@/lib/i18n/bilingual';
import { statusTone, TONE_VAR, type Tone } from './format';
import type { ConsoleApiState } from './useConsoleSnapshot';
import { RESEARCH_CONSOLE_API_BASE } from '@/lib/api/research-console-api';

const toneText: Record<Tone, string> = {
  pos: 'text-pos', neg: 'text-neg', warn: 'text-warn', info: 'text-info',
  accent: 'text-accent', special: 'text-special', muted: 'text-muted',
};

/* ---------- Chip / Badge — soft pill ------------------------------------- */
export function Chip({ label, tone = 'muted', soft = true, className = '' }:
  { label: React.ReactNode; tone?: Tone; soft?: boolean; className?: string }) {
  const c = TONE_VAR[tone];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-2xs font-medium leading-tight ${className}`}
      style={{
        color: c,
        background: soft ? `color-mix(in srgb, ${c} 14%, transparent)` : 'transparent',
        boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${c} 30%, transparent)`,
      }}>
      {label}
    </span>
  );
}

export function StatusChip({ value, className = '' }: { value: string; className?: string }) {
  return <Chip label={statusLabel(value)} tone={statusTone(value)} className={className} />;
}

export function GateDot({ ok, label }: { ok: boolean; label?: string }) {
  const c = ok ? 'var(--pos)' : 'var(--neg)';
  return (
    <span className="inline-flex items-center gap-1.5 text-2xs">
      <span className="h-2 w-2 rounded-full" style={{ background: c, boxShadow: `0 0 10px ${c}` }} />
      {label && <span className="text-muted">{label}</span>}
    </span>
  );
}

/* ---------- Panel: glassy elevated card ----------------------------------- */
export function Panel({ title, eyebrow, right, tone, children, className = '', bodyClass = '' }:
  { title?: React.ReactNode; eyebrow?: React.ReactNode; right?: React.ReactNode; tone?: Tone;
    children: React.ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={`q-rise flex min-w-0 flex-col rounded-panel border border-line bg-gradient-to-b from-panel-2/60 to-panel shadow-panel ${className}`}
      style={tone ? { boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${TONE_VAR[tone]} 22%, transparent), var(--shadow-panel)` } : undefined}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            {eyebrow && <div className="mb-0.5 text-2xs font-medium uppercase tracking-[0.14em] text-faint">{eyebrow}</div>}
            {title && <h2 className="truncate text-[15px] font-semibold tracking-tight text-ink">{title}</h2>}
          </div>
          {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
        </header>
      )}
      <div className={`min-w-0 flex-1 p-5 ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function SectionHeader({ title, sub }: { title: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="mb-3">
      <h3 className="text-2xs font-semibold uppercase tracking-[0.14em] text-muted">{title}</h3>
      {sub && <p className="mt-0.5 text-2xs text-faint">{sub}</p>}
    </div>
  );
}

/* ---------- Stat tile + KPI strip — big confident numbers ----------------- */
export function StatTile({ label, value, unit, sub, tone, accent }:
  { label: React.ReactNode; value: React.ReactNode; unit?: string; sub?: React.ReactNode; tone?: Tone; accent?: boolean }) {
  return (
    <div className="lift flex min-w-0 flex-col justify-between gap-3 rounded-tile border border-line bg-gradient-to-b from-panel-2/70 to-panel p-4 shadow-panel">
      <div className="truncate text-2xs font-medium uppercase tracking-[0.12em] text-faint">{label}</div>
      <div className="flex items-baseline gap-1.5">
        <span className={`tnum text-[28px] font-bold leading-none tracking-tight ${accent ? 'grad-text' : tone ? toneText[tone] : 'text-ink'}`}>{value}</span>
        {unit && <span className="text-xs font-medium text-muted">{unit}</span>}
      </div>
      {sub !== undefined && <div className="truncate text-2xs text-faint">{sub}</div>}
    </div>
  );
}

export function KpiStrip({ children, cols = 4 }: { children: React.ReactNode; cols?: number }) {
  return <div className="grid gap-3.5" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${Math.max(168, 1100 / cols)}px, 1fr))` }}>{children}</div>;
}

/* ---------- Progress / score bar — gradient fill -------------------------- */
export function ProgressBar({ value, max = 1, tone = 'accent', height = 7 }:
  { value: number; max?: number; tone?: Tone; height?: number }) {
  const frac = Math.max(0, Math.min(1, value / (max || 1)));
  return (
    <div className="w-full overflow-hidden rounded-full bg-inset" style={{ height }}>
      <div className="h-full rounded-full transition-[width] duration-500"
        style={{ width: `${frac * 100}%`, background: tone === 'accent' ? 'var(--grad)' : TONE_VAR[tone],
          boxShadow: `0 0 12px -2px ${tone === 'accent' ? 'rgba(139,156,255,.6)' : TONE_VAR[tone]}` }} />
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
  const pad = dense ? 'px-3 py-2' : 'px-3.5 py-3';
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr className="border-b border-line">
            {columns.map(c => (
              <th key={c.key} style={{ width: c.width, textAlign: c.align ?? 'left' }}
                className={`${pad} text-2xs font-semibold uppercase tracking-[0.1em] text-faint`}>{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={getKey ? getKey(row, i) : i} className="border-b border-line/50 transition-colors hover:bg-elevated/40">
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

/* ---------- KeyValue ------------------------------------------------------- */
export function KeyValue({ items, cols = 2 }: { items: ReadonlyArray<{ k: React.ReactNode; v: React.ReactNode }>; cols?: number }) {
  return (
    <dl className="grid gap-x-6 gap-y-3" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0,1fr))` }}>
      {items.map((it, i) => (
        <div key={i} className="flex min-w-0 flex-col gap-1 rounded-lg border border-line bg-inset/50 px-3 py-2">
          <dt className="text-2xs uppercase tracking-[0.1em] text-faint">{it.k}</dt>
          <dd className="truncate text-[13px] text-ink mono tnum">{it.v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center justify-center rounded-xl border border-dashed border-line py-10 text-xs text-faint">{children}</div>;
}

/* ---------- Page header + provenance -------------------------------------- */
export function PageHeader({ title, titleZh, subtitle, actions }:
  { title: string; titleZh: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <span className="h-6 w-1 rounded-full bg-grad" />
          <h1 className="text-[26px] font-bold leading-none tracking-tight text-ink">{titleZh}</h1>
          <span className="text-[15px] font-medium tracking-tight text-faint">{title}</span>
        </div>
        {subtitle && <p className="mt-2 max-w-3xl pl-3.5 text-[13px] leading-relaxed text-muted">{subtitle}</p>}
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
export function ProvenanceRibbon({ usingBackend, apiState, source, error, realData = false, evidenceData = false }:
  { usingBackend: boolean; apiState: ConsoleApiState; source?: string; error?: string | null;
    realData?: boolean; evidenceData?: boolean }) {
  const apiTone: Tone = apiState === 'connected' ? 'pos' : apiState === 'error' ? 'neg' : apiState === 'loading' ? 'info' : 'muted';
  const apiLabel = apiState === 'connected' ? bi('已连接', 'CONNECTED') : apiState === 'error' ? bi('错误', 'ERROR')
    : apiState === 'loading' ? bi('加载中', 'LOADING') : bi('未连接', 'NOT_CONNECTED');
  // realData=true: these are REAL observed research results (the run_registry), not synthetic fixtures —
  // honesty demands an accurate label (still read-only; historical backtest != future returns).
  const evidence = evidenceData && usingBackend;
  const real = realData && usingBackend;
  const tint = real ? 'var(--pos)' : 'var(--warn)';
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl px-3.5 py-2"
      style={{ background: `color-mix(in srgb, ${tint} 7%, transparent)`, boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${tint} 28%, transparent)` }}>
      {evidence ? (
        <span className="text-2xs font-medium text-pos">
          ✓ {bi('机器可验证的仓库证据 · 非收益或实盘就绪声明',
            'Machine-verifiable repository evidence · not a PnL or live-readiness claim')}
        </span>
      ) : real ? (
        <span className="text-2xs font-medium text-pos">
          ✓ {bi('真实研究结果(登记表观察值) · 历史回测,非未来收益承诺',
            'Real research results (observed, from the registry) · historical backtest, NOT a promise of future returns')}
        </span>
      ) : (
        <span className="text-2xs font-medium text-warn">
          ⚠ {bi('合成演示数据 · 非真实研究结果或盈亏', 'Synthetic demo data · not real research results or PnL')}
          {usingBackend ? bi('（后端确定性合成快照）', ' (backend synthetic snapshot)') : bi('（前端 mock）', ' (frontend mock)')}
        </span>
      )}
      <span className="mx-1 h-3 w-px bg-line-strong" />
      <Chip label={evidence ? 'VERIFIED_EVIDENCE' : real ? 'REAL_RESULTS' : usingBackend ? 'BACKEND_COMPUTED' : 'MOCK_MODE'} tone={usingBackend ? 'pos' : 'warn'} />
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

/* ---------- Button --------------------------------------------------------- */
const _BTN_ACTIVE: Record<string, string> = {
  accent: 'border-accent/50 text-accent hover:bg-accent/10',
  neg: 'border-neg/50 text-neg hover:bg-neg/10',
  pos: 'border-pos/50 text-pos hover:bg-pos/10',
  muted: 'border-line text-muted hover:bg-inset',
};
export function Button({ onClick, disabled = false, variant = 'accent', children, className = '' }:
  { onClick?: () => void; disabled?: boolean; variant?: 'accent' | 'neg' | 'pos' | 'muted';
    children: React.ReactNode; className?: string }) {
  const cls = disabled ? 'cursor-not-allowed border-line text-faint' : (_BTN_ACTIVE[variant] ?? _BTN_ACTIVE.accent);
  return (
    <button onClick={onClick} disabled={disabled}
      className={`rounded-md border px-2.5 py-1 text-2xs transition ${cls} ${className}`}>
      {children}
    </button>
  );
}

/* ---------- Layout helpers ------------------------------------------------- */
export function Grid({ children, min = 320, className = '' }: { children: React.ReactNode; min?: number; className?: string }) {
  return <div className={`grid gap-4 ${className}`} style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))` }}>{children}</div>;
}

export function PageShell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto flex w-full max-w-[1720px] flex-col gap-5">{children}</div>;
}
