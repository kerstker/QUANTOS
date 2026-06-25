/** Deterministic display formatters for the cockpit. Numbers are the product —
 *  these keep them tight, tabular, and honest (no fabricated precision). */

export function num(v: number | undefined | null, digits = 0): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function pct(v: number | undefined | null, digits = 1): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

/** A figure already expressed in percent points (e.g. 12.5 -> "12.5%"). */
export function pctPoints(v: number | undefined | null, digits = 1): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return `${v.toFixed(digits)}%`;
}

export function fixed(v: number | undefined | null, digits = 2): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return v.toFixed(digits);
}

export function signed(v: number | undefined | null, digits = 2): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}`;
}

/** Compact currency for capacity / budget (CNY by default). */
export function money(v: number | undefined | null, ccy = 'CNY'): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿 ${ccy}`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(1)}万 ${ccy}`;
  return `${sign}${abs.toLocaleString('en-US')} ${ccy}`;
}

export function compact(v: number | undefined | null): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(v);
}

/** Truncate a content hash / id to a terminal-style short ref. */
export function shortHash(h: string | undefined | null, head = 8): string {
  if (!h) return '—';
  return h.length > head ? `${h.slice(0, head)}…` : h;
}

/** Epoch (seconds or ms) -> compact UTC stamp. Deterministic; no locale tz drift. */
export function stamp(ts: number | undefined | null): string {
  if (ts === undefined || ts === null) return '—';
  const ms = ts < 1e12 ? ts * 1000 : ts;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return String(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/** Map a value to a semantic tone for chips / bars / sparklines. */
export type Tone = 'pos' | 'neg' | 'warn' | 'info' | 'accent' | 'special' | 'muted';

export function deltaTone(v: number): Tone {
  if (v > 0) return 'pos';
  if (v < 0) return 'neg';
  return 'muted';
}

export const TONE_VAR: Record<Tone, string> = {
  pos: 'var(--pos)',
  neg: 'var(--neg)',
  warn: 'var(--warn)',
  info: 'var(--info)',
  accent: 'var(--accent)',
  special: 'var(--special)',
  muted: 'var(--muted)',
};

/** Classify common status / decision strings into a semantic tone. */
export function statusTone(s: string | undefined | null): Tone {
  if (!s) return 'muted';
  const u = s.toUpperCase();
  if (/(APPROVED|PASS|HEALTHY|OK|READY|COMPLETED|GO|PRODUCTION|ACTIVE|SEALED|VALID)/.test(u) && !/NOT|NO_GO|REJECT|FAIL/.test(u)) return 'pos';
  if (/(REJECT|FAIL|BREACH|DOWN|CRITICAL|BLOCK|NO_GO|DISALLOW|OVERDUE|FORBIDDEN)/.test(u)) return 'neg';
  if (/(WATCH|WARN|PENDING|DEGRADED|NEEDS|PARTIAL|PAUSED|HIGH|SLA)/.test(u)) return 'warn';
  if (/(RUNNING|LOADING|VALIDATING|PLANNING|SIMULAT|IN_PROGRESS|EVALUAT|READ_ONLY|L2)/.test(u)) return 'info';
  return 'muted';
}
