import React, { useMemo, useState, Component } from 'react';
import { bi, statusLabel } from '@/lib/i18n/bilingual';
import { statusTone, TONE_VAR, type Tone } from './format';
import type { ConsoleApiState } from './useConsoleSnapshot';
import { deriveSnapshotDisplayState, type SnapshotDisplayState } from './snapshot-state';
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
        <header className="flex flex-col items-start gap-3 border-b border-line px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div className="min-w-0 max-w-full">
            {eyebrow && <div className="mb-0.5 break-words text-2xs font-medium uppercase tracking-[0.14em] text-faint">{eyebrow}</div>}
            {title && <h2 className="break-words text-base font-semibold tracking-tight text-ink sm:truncate">{title}</h2>}
          </div>
          {right && <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0 sm:justify-end">{right}</div>}
        </header>
      )}
      <div className={`min-w-0 flex-1 p-6 ${bodyClass}`}>{children}</div>
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
        <span className={`tnum text-[34px] font-bold leading-none tracking-tight ${accent ? 'grad-text' : tone ? toneText[tone] : 'text-ink'}`}>{value}</span>
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
  /** Enables click-to-sort on this column's header (arrow indicator shown when active). */
  sortable?: boolean;
  /** Value to sort by for this column. Required when `sortable` is true. */
  sortAccessor?: (row: T) => number | string | null | undefined;
}
export function DataTable<T>({ columns, rows, dense, empty, getKey, searchable, searchPlaceholder, stickyHeader = false, maxHeight, density = 'default' }:
  { columns: ReadonlyArray<Column<T>>; rows: ReadonlyArray<T>; dense?: boolean; empty?: React.ReactNode;
    getKey?: (row: T, i: number) => React.Key;
    /** Adds a text-filter input above the table; returns the searchable text for a row. */
    searchable?: (row: T) => string;
    searchPlaceholder?: string;
    /** Sticky thead inside a vertically scrollable container (pair with maxHeight for the scroll window). */
    stickyHeader?: boolean;
    /** Vertical scroll window in px, used together with stickyHeader. */
    maxHeight?: number;
    /** 'compact' = institutional terminal density (overrides `dense` padding). */
    density?: 'default' | 'compact' }) {
  const [query, setQuery] = useState('');
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const filtered = useMemo(() => {
    if (!searchable || !query.trim()) return rows;
    const q = query.trim().toLowerCase();
    return rows.filter(r => searchable(r).toLowerCase().includes(q));
  }, [rows, searchable, query]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const col = columns.find(c => c.key === sortKey);
    if (!col?.sortAccessor) return filtered;
    const sign = sortDir === 'desc' ? -1 : 1;
    return [...filtered].sort((a, b) => {
      const av = col.sortAccessor!(a), bv = col.sortAccessor!(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;   // nulls sort last regardless of direction
      if (bv == null) return -1;
      if (typeof av === 'string' || typeof bv === 'string') return sign * String(av).localeCompare(String(bv));
      return sign * ((bv as number) - (av as number));
    });
  }, [filtered, sortKey, sortDir, columns]);

  function sortOn(key: string) {
    if (sortKey === key) setSortDir(d => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(key); setSortDir('desc'); }
  }

  const pad = density === 'compact' ? 'px-2 py-1' : dense ? 'px-3 py-2' : 'px-3.5 py-3';
  return (
    <div>
      {searchable && (
        <input value={query} onChange={e => setQuery(e.target.value)}
          placeholder={searchPlaceholder ?? bi('搜索…', 'search…')}
          className="mb-2.5 w-full max-w-xs rounded-md border border-line bg-inset/40 px-2.5 py-1.5 text-xs text-ink placeholder:text-faint sm:w-64" />
      )}
      {!sorted.length ? <EmptyState>{empty ?? bi('无记录', 'No records')}</EmptyState> : (
        <div className={`-mx-1 ${stickyHeader ? 'overflow-auto' : 'overflow-x-auto'} ${density === 'compact' ? 'density-compact' : ''}`}
          style={stickyHeader && maxHeight ? { maxHeight } : undefined}>
          <table className="w-full border-collapse text-[14px]">
            <thead className={stickyHeader ? 'sticky top-0 z-10' : undefined}>
              <tr className="border-b border-line">
                {columns.map(c => (
                  <th key={c.key} style={{ width: c.width, textAlign: c.align ?? 'left' }}
                    onClick={c.sortable ? () => sortOn(c.key) : undefined}
                    className={`${pad} text-2xs font-semibold uppercase tracking-[0.1em] text-faint ${stickyHeader ? 'bg-panel-2' : ''} ${c.sortable ? 'cursor-pointer select-none hover:text-ink' : ''}`}>
                    {c.header}{c.sortable && sortKey === c.key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => (
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
      )}
      {searchable && query.trim() && (
        <div className="mt-1.5 text-2xs text-faint">{bi(`筛出 ${sorted.length} / ${rows.length}`, `${sorted.length} / ${rows.length} shown`)}</div>
      )}
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

/**
 * LoadingVeil — wraps a panel/page body; while `active` (apiState === 'loading') dims the
 * content and overlays a small spinner, so a slow/real fetch is never visually identical to
 * "connected" or "showing mock data". Purely a visual overlay — never changes what's rendered
 * underneath (the page's usingBackend ? payload : fallback selection is untouched).
 */
export function LoadingVeil({ active, children }: { active: boolean; children: React.ReactNode }) {
  return (
    <div className="relative">
      <div className={active ? 'pointer-events-none opacity-40 transition-opacity duration-200' : 'transition-opacity duration-200'}>
        {children}
      </div>
      {active && (
        <div className="absolute inset-0 flex items-center justify-center" aria-live="polite">
          <span className="flex items-center gap-2 rounded-full border border-line bg-panel-2/90 px-3 py-1.5 text-2xs text-muted shadow-panel">
            <span className="q-live h-2 w-2 rounded-full" style={{ background: 'var(--info)' }} />
            {bi('加载中…', 'loading…')}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * PillToggle — the recurring "row of selectable filter/toggle buttons" pattern (active = accent
 * border+bg, inactive = muted border), previously hand-rolled per page. One shared look everywhere
 * a chip-style filter/selector appears (kind filters, generator filters, alpha selectors, screens).
 */
export function PillToggle({ label, active, onClick, tone = 'accent', disabled = false, title }:
  { label: React.ReactNode; active: boolean; onClick: () => void; tone?: Tone; disabled?: boolean; title?: string }) {
  const c = TONE_VAR[tone];
  return (
    <button onClick={onClick} disabled={disabled} title={title}
      className="rounded-md border px-2.5 py-1 text-xs transition disabled:cursor-not-allowed disabled:opacity-40"
      style={active
        ? { borderColor: `color-mix(in srgb, ${c} 55%, transparent)`, color: c, background: `color-mix(in srgb, ${c} 12%, transparent)` }
        : { borderColor: 'var(--line)', color: 'var(--muted)' }}>
      {label}
    </button>
  );
}

/**
 * Callout — a one-line-summary-always-visible, expandable-for-detail disclosure. Replaces the
 * repeated multi-sentence bilingual "honesty disclaimer" paragraphs that push real content below
 * a wall of caveat text on first paint.
 */
export function Callout({ summary, tone = 'info', children }:
  { summary: React.ReactNode; tone?: Tone; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const c = TONE_VAR[tone];
  return (
    <div className="rounded-lg px-3 py-2" style={{ background: `color-mix(in srgb, ${c} 6%, transparent)`, boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${c} 24%, transparent)` }}>
      <button onClick={() => setOpen(o => !o)} className="flex w-full items-center gap-2 text-left text-2xs text-muted">
        <span style={{ color: c }}>{open ? '▾' : '▸'}</span>
        <span className="min-w-0 flex-1 truncate">{summary}</span>
        <span className="shrink-0 text-faint">{open ? bi('收起', 'less') : bi('详情', 'details')}</span>
      </button>
      {open && <div className="mt-2 pl-4 text-2xs leading-relaxed text-faint">{children}</div>}
    </div>
  );
}

/* ---------- Page header + provenance -------------------------------------- */
export function PageHeader({ title, titleZh, subtitle, actions }:
  { title: string; titleZh: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <span className="h-7 w-1 rounded-full bg-grad" />
          <h1 title={title} className="text-[30px] font-bold leading-none tracking-tight text-ink">{titleZh}</h1>
        </div>
        {subtitle && <p className="mt-2.5 max-w-3xl pl-4 text-sm leading-relaxed text-muted">{subtitle}</p>}
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
export function ProvenanceRibbon({
  usingBackend, apiState, source, error, realData = false, evidenceData = false,
  variant = 'default', noDataYet = false, dataStatus, diagnosticOnly = false, extraChips,
}: {
  usingBackend: boolean; apiState: ConsoleApiState; source?: string; error?: string | null;
  realData?: boolean; evidenceData?: boolean;
  /** 'crypto' swaps in CryptoLive's copy (was a bespoke RealDataRibbon); 'default' covers everyone else. */
  variant?: 'default' | 'crypto';
  /** Backend connected but the real snapshot hasn't been generated yet (distinct from mock/synthetic). */
  noDataYet?: boolean;
  /** Domain admission state. When set, it overrides the transport-only inference. */
  dataStatus?: SnapshotDisplayState;
  /** Retained invalidated/forensic evidence: readable, amber, never actionable. */
  diagnosticOnly?: boolean;
  /** Extra page-specific chips appended after the standard set (e.g. LIVE_DISALLOWED, NOT ADVICE). */
  extraChips?: React.ReactNode;
}) {
  const apiTone: Tone = apiState === 'connected' ? 'pos' : apiState === 'error' ? 'neg' : apiState === 'loading' ? 'info' : 'muted';
  const apiLabel = apiState === 'connected' ? bi('已连接', 'CONNECTED') : apiState === 'error' ? bi('错误', 'ERROR')
    : apiState === 'loading' ? bi('加载中', 'LOADING') : bi('未连接', 'NOT_CONNECTED');
  const displayState = deriveSnapshotDisplayState({
    apiState, usingBackend, realData, evidenceData, dataStatus, noDataYet, diagnosticOnly,
  });
  // realData=true: these are REAL observed research results (the run_registry), not synthetic fixtures —
  // honesty demands an accurate label (still read-only; historical backtest != future returns).
  const evidence = displayState === 'EVIDENCE';
  const real = displayState === 'REAL';
  const tint: string = displayState === 'UNAVAILABLE' || displayState === 'BLOCKED'
    ? 'var(--neg)'
    : displayState === 'STALE' || displayState === 'MOCK' || displayState === 'DIAGNOSTIC'
      ? 'var(--warn)'
      : 'var(--info)';

  let icon: string;
  let headline: React.ReactNode;
  let modeChip: string;
  if (displayState === 'UNAVAILABLE') {
    icon = '✕'; modeChip = 'UNAVAILABLE';
    headline = bi('后端不可用 · 下方回退内容不可作为决策数据',
      'Backend unavailable · fallback content is not decision data');
  } else if (displayState === 'BLOCKED') {
    icon = '⛔'; modeChip = 'DATA_BLOCKED';
    headline = bi('数据/门禁阻塞 · 禁止继续操作', 'Data/admission blocked · actions are forbidden');
  } else if (displayState === 'DIAGNOSTIC') {
    icon = '◇'; modeChip = 'DIAGNOSTIC_ONLY';
    headline = bi(
      '只读取证快照 · 可检查身份与原因，但不构成当前权威或操作许可',
      'Read-only forensic snapshot · inspectable identity/reasons, not current authority or permission',
    );
  } else if (displayState === 'STALE') {
    icon = '⚠'; modeChip = 'STALE_DATA';
    headline = bi('数据过期 · 禁止把当前快照当作可用证据',
      'Stale data · this snapshot is not usable evidence');
  } else if (displayState === 'LOADING') {
    icon = '○'; modeChip = 'LOADING';
    headline = bi('正在加载快照 · 尚未形成决策状态', 'Loading snapshot · no decision state yet');
  } else if (displayState === 'NO_SNAPSHOT') {
    icon = '○'; modeChip = 'NO_SNAPSHOT';
    headline = bi('尚无真实快照 · 不代表通过或可交易',
      'No real snapshot · this is neither approval nor tradability');
  } else if (evidence) {
    icon = '✓'; modeChip = 'VERIFIED_EVIDENCE';
    headline = bi('机器可验证的仓库证据 · 非收益或实盘就绪声明',
      'Machine-verifiable repository evidence · not a PnL or live-readiness claim');
  } else if (real) {
    modeChip = 'REAL_RESULTS';
    if (variant === 'crypto') {
      icon = '◆';
      headline = bi('真实只读行情(CoinGecko)· 链下抓取、研究核心离线 · 非实盘盈亏',
        'Real read-only market data (CoinGecko) · off-gate fetch, offline research core · not live PnL');
    } else {
      icon = '✓';
      headline = bi('真实研究结果(登记表观察值) · 历史回测,非未来收益承诺',
        'Real research results (observed, from the registry) · historical backtest, NOT a promise of future returns');
    }
  } else if (variant === 'crypto') {
    icon = '○'; modeChip = 'MOCK_MODE';
    headline = bi('未连接只读后端 · 真实加密研究仅在连接后端时渲染',
      'Not connected · real crypto research renders only against the read-only backend');
  } else {
    icon = '⚠'; modeChip = usingBackend ? 'BACKEND_COMPUTED' : 'MOCK_MODE';
    headline = (
      <>
        {bi('合成演示数据 · 非真实研究结果或盈亏', 'Synthetic demo data · not real research results or PnL')}
        {usingBackend ? bi('（后端确定性合成快照）', ' (backend synthetic snapshot)') : bi('（前端 mock）', ' (frontend mock)')}
      </>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl px-3.5 py-2"
      style={{ background: `color-mix(in srgb, ${tint} 7%, transparent)`, boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${tint} 28%, transparent)` }}>
      <span className="text-2xs font-medium" style={{ color: tint }}>{icon} {headline}</span>
      <span className="mx-1 h-3 w-px bg-line-strong" />
      <Chip label={modeChip} tone={evidence || real ? 'pos' : displayState === 'UNAVAILABLE' || displayState === 'BLOCKED' ? 'neg' : displayState === 'NO_SNAPSHOT' || displayState === 'LOADING' ? 'info' : 'warn'} />
      <Chip label={apiLabel} tone={apiTone} />
      <Chip label={bi('只读', 'READ_ONLY')} tone="info" />
      {extraChips}
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

/* ---------- Error Boundary -------------------------------------------------- */
interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}
/** 捕获子组件渲染异常，防止整个 App 白屏崩溃。 */
export class ErrorBoundary extends Component<
  { children: React.ReactNode; fallback?: React.ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: React.ReactNode; fallback?: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] 未捕获的渲染异常:', error.message, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="mx-auto mt-16 max-w-lg rounded-lg border border-neg/30 bg-surface p-6 text-center">
          <h2 className="text-lg font-semibold text-neg mb-3">
            {bi('页面发生错误', 'Something went wrong')}
          </h2>
          <p className="text-sm text-faint mb-4">
            {this.state.error?.message ?? bi('未知异常', 'Unknown error')}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="rounded-md border border-line px-3 py-1 text-2xs text-muted hover:bg-inset">
            {bi('重试', 'Retry')}
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
