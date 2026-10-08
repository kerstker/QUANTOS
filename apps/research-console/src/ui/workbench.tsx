import React, { useState } from 'react';
import { bi, statusLabel } from '@/lib/i18n/bilingual';
import { stamp, TONE_VAR, type Tone } from './format';
import { Chip, Button } from './primitives';
import { useConfirm, type ConfirmSeverity } from './dialog';
import { useConsoleSnapshot } from './useConsoleSnapshot';
import { TickerTape } from './charts';
import {
  fetchMexcMarketSnapshot,
  fetchSafetyPrimitivesSnapshot,
} from '@/lib/api/research-console-api';
import type { MexcMarketPayload, SafetyPrimitivesPayload } from '@/lib/types/research';

/* ============================================================================
   Workbench kit — the shared components mandated by
   docs/FRONTEND_WORKBENCH_REDESIGN.md §4 that were missing from src/ui/.
   Same constraints as the rest of the console: zero new deps, CSS-token themed,
   deterministic, honest (missing/stale/unknown states are first-class).
   ========================================================================== */

/* ---------- EquityCurveChart: multi-series equity curves (RAW/VT/benchmark) --
   Extracted from the three hand-rolled inline copies (DryRunPipeline ×2,
   LiveDesk ×1). Pure SVG, no Date/random; x is epoch time when every point
   carries `t`, else per-series index. -------------------------------------- */

export interface EquitySeriesPoint { t?: number; equity: number }
export interface EquitySeries {
  label: React.ReactNode;
  points: ReadonlyArray<EquitySeriesPoint>;
  tone?: Tone;
  dashed?: boolean;
}

const SERIES_TONES: Tone[] = ['accent', 'info', 'muted', 'warn', 'pos', 'special'];

export function EquityCurveChart({ series, height = 180, yFormat, xFormat, className = '' }: {
  series: ReadonlyArray<EquitySeries>;
  height?: number;
  yFormat?: (v: number) => string;
  xFormat?: (v: number) => string;
  className?: string;
}) {
  const usable = series
    .map((s, i) => ({
      ...s,
      tone: s.tone ?? SERIES_TONES[i % SERIES_TONES.length],
      pts: s.points.filter(p => Number.isFinite(p.equity)),
    }))
    .filter(s => s.pts.length >= 2);
  if (!usable.length) {
    return (
      <div className={`flex items-center justify-center text-2xs text-faint ${className}`} style={{ height }}>
        {bi('曲线点不足', 'not enough curve points')}
      </div>
    );
  }
  const W = 1000, padL = 8, padR = 12, padT = 8, padB = 16;
  const hasT = usable.every(s => s.pts.every(p => typeof p.t === 'number' && Number.isFinite(p.t)));
  const xs = usable.flatMap(s => s.pts.map((p, i) => (hasT ? (p.t as number) : i)));
  const ys = usable.flatMap(s => s.pts.map(p => p.equity));
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const sx = (v: number) => padL + ((v - minX) / (maxX - minX || 1)) * (W - padL - padR);
  const sy = (v: number) => padT + (1 - (v - minY) / (maxY - minY || 1)) * (height - padT - padB);
  const path = (s: (typeof usable)[number]) => s.pts.map((p, i) => {
    const xv = hasT ? (p.t as number) : i;
    return `${i ? 'L' : 'M'}${sx(xv).toFixed(1)} ${sy(p.equity).toFixed(1)}`;
  }).join(' ');
  return (
    <div className={className}>
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none"
        role="img" aria-label="权益曲线">
        <line x1={padL} x2={W - padR} y1={sy(minY)} y2={sy(minY)} stroke="var(--grid)" strokeWidth={1} />
        <line x1={padL} x2={W - padR} y1={sy(maxY)} y2={sy(maxY)} stroke="var(--grid)" strokeWidth={1} />
        {usable.map(s => (
          <path key={String(s.label)} d={path(s)} fill="none"
            stroke={TONE_VAR[s.tone]} strokeWidth={s.dashed ? 1.2 : 1.6}
            strokeDasharray={s.dashed ? '3 2' : undefined} vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-2xs">
        {usable.map(s => (
          <span key={String(s.label)} className="inline-flex items-center gap-1 text-muted">
            <span className="inline-block h-[2px] w-3" style={{ background: TONE_VAR[s.tone] }} />
            {s.label}
          </span>
        ))}
        <span className="ml-auto ticker-num text-faint">
          {yFormat ? yFormat(minY) : minY.toFixed(2)} – {yFormat ? yFormat(maxY) : maxY.toFixed(2)}
        </span>
      </div>
      {xFormat && hasT && (
        <div className="flex justify-between text-2xs text-faint ticker-num">
          <span>{xFormat(minX)}</span>
          <span>{xFormat(maxX)}</span>
        </div>
      )}
    </div>
  );
}

/* ---------- StatusBanner: persistent safety strip (never a toast) ----------
   Fed by /api/v1/console/safety-primitives. Kill-switch TRIPPED renders as a
   persistent red bar (P4: safety states are持久可见). When the backend is not
   connected the strip degrades honestly to an UNKNOWN/fail-closed posture —
   it never claims CLEAR without a real snapshot. --------------------------- */

export function StatusBanner({ className = '' }: { className?: string }) {
  const { payload, apiState, usingBackend } = useConsoleSnapshot<SafetyPrimitivesPayload>(
    s => fetchSafetyPrimitivesSnapshot(s));
  const tripped: boolean | null = usingBackend ? payload?.kill_switch?.is_active === true : null;
  const tint = tripped === true ? 'var(--neg)' : tripped === false ? 'var(--pos)' : 'var(--warn)';
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg px-3 py-1.5 text-2xs ${className}`}
      role="status" aria-label="安全状态"
      style={{ background: `color-mix(in srgb, ${tint} 7%, transparent)`, boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${tint} 30%, transparent)` }}>
      <span className="font-semibold" style={{ color: tint }}>
        {tripped === true ? bi('⛔ 熔断器已跳闸 —— 全部拒绝', '⛔ KILL-SWITCH TRIPPED — all rejected')
          : tripped === false ? bi('✓ 熔断器正常', '✓ KILL-SWITCH CLEAR')
            : bi('⚠ 安全态未知(未连后端,fail-closed 姿态)', '⚠ safety state UNKNOWN (backend offline, fail-closed posture)')}
      </span>
      <Chip label={tripped === true ? '熔断器已跳闸' : tripped === false ? '熔断器正常' : '熔断器未知'}
        tone={tripped === true ? 'neg' : tripped === false ? 'pos' : 'warn'} />
      {usingBackend && payload?.pre_trade_gate && (
        <Chip label={bi(`盘前门 ${payload.pre_trade_gate.checks_registered} 检查`, `gate ${payload.pre_trade_gate.checks_registered} checks`)} tone="info" />
      )}
      {usingBackend && payload?.safety_config && (
        <Chip label={payload.safety_config.require_human_approval ? '需要人工批准' : '无需批准'}
          tone={payload.safety_config.require_human_approval ? 'pos' : 'neg'} />
      )}
      <Chip label="实盘禁用" tone="neg" />
      <span className="ml-auto text-faint">
        {usingBackend ? bi('真实安全原语(只读)', 'real safety primitives (read-only)')
          : apiState === 'loading' ? bi('加载中…', 'loading…')
            : bi('离线/mock —— 不代表真实安全态', 'offline/mock — not the real safety state')}
      </span>
    </div>
  );
}

/* ---------- AsOfStamp: as-of time + staleness coloring --------------------- */

export function AsOfStamp({ ts, ageHours, freshWithinHours = 72, className = '' }: {
  ts?: number | null;
  ageHours?: number | null;
  freshWithinHours?: number;
  className?: string;
}) {
  const age = ageHours ?? (ts != null
    ? Math.max(0, (Date.now() - (ts < 1e12 ? ts * 1000 : ts)) / 3.6e6)
    : null);
  const fresh = age != null && age <= freshWithinHours;
  const tone: Tone = age == null ? 'muted' : fresh ? 'pos' : 'warn';
  return (
    <span className={`inline-flex items-center gap-1.5 text-2xs ${className}`} role="status">
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: TONE_VAR[tone] }} />
      <span className="ticker-num text-muted">{ts != null ? stamp(ts) : bi('未知时间', 'unknown time')}</span>
      {age != null && (
        <span className="text-faint">· {age < 1 ? `${Math.max(1, Math.round(age * 60))}分钟` : `${age.toFixed(1)}小时`}</span>
      )}
      <span className="font-medium" style={{ color: TONE_VAR[tone] }}>
        {age == null ? '时点未知' : fresh ? '新鲜' : '陈旧'}
      </span>
    </span>
  );
}

/* ---------- EndpointFootnote: audit-traceable source path footnote --------- */

export function EndpointFootnote({ path, note, className = '' }: {
  path: string; note?: React.ReactNode; className?: string;
}) {
  return (
    <div className={`text-3xs text-faint ${className}`}>
      {bi('数据源', 'source')}: <span className="mono">{path}</span>{note ? <> · {note}</> : null}
    </div>
  );
}

/* ---------- StateView: loading / error / empty / ready wrapper ------------- */

export type StateViewStatus = 'loading' | 'error' | 'empty' | 'ready';

export function StateView({ state, error, empty, loading, children }: {
  state: StateViewStatus;
  error?: React.ReactNode;
  empty?: React.ReactNode;
  loading?: React.ReactNode;
  children: React.ReactNode;
}) {
  if (state === 'ready') return <>{children}</>;
  const body = state === 'loading'
    ? (loading ?? bi('加载中…', 'loading…'))
    : state === 'error'
      ? <span className="text-neg">{bi('加载失败', 'load failed')}{error ? <>: {error}</> : null}</span>
      : (empty ?? bi('暂无数据', 'no data'));
  return (
    <div className="flex items-center justify-center rounded-lg border border-dashed border-line py-8 text-2xs text-faint" role="status">
      {body}
    </div>
  );
}

/* ---------- ConfirmActionButton: controlled-change button -------------------
   Wraps the shared confirm dialog (P5): mutating actions always carry an
   explicit confirmation; nothing here touches any order/credential path. --- */

export function ConfirmActionButton({ label, title, message, severity = 'warn', confirmLabel,
  typeToConfirm, disabled = false, busyLabel, onConfirm, variant = 'accent', className = '' }: {
  label: React.ReactNode;
  title: React.ReactNode;
  message?: React.ReactNode;
  severity?: ConfirmSeverity;
  confirmLabel?: React.ReactNode;
  typeToConfirm?: string;
  disabled?: boolean;
  busyLabel?: React.ReactNode;
  onConfirm: () => void | Promise<void>;
  variant?: 'accent' | 'neg' | 'pos' | 'muted';
  className?: string;
}) {
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);
  const handle = async () => {
    const ok = await confirm({ title, message, severity, confirmLabel, typeToConfirm });
    if (!ok) return;
    setBusy(true);
    try { await onConfirm(); } finally { setBusy(false); }
  };
  return (
    <Button onClick={() => { void handle(); }} disabled={disabled || busy} variant={variant} className={className}>
      {busy ? (busyLabel ?? label) : label}
    </Button>
  );
}

/* ---------- MandateGateChip: read-only mandate/attestation badge ----------- */

export type MandateState = 'SIGNED' | 'UNSIGNED' | 'EXPIRED' | 'PENDING' | 'UNKNOWN';

export function MandateGateChip({ state, expiresAt, now, className = '' }: {
  state: MandateState;
  expiresAt?: number | null;
  now?: number;
  className?: string;
}) {
  const derived: MandateState =
    state === 'SIGNED' && expiresAt != null && now != null && expiresAt <= now ? 'EXPIRED' : state;
  const tone: Tone = derived === 'SIGNED' ? 'pos'
    : derived === 'EXPIRED' ? 'neg'
      : derived === 'PENDING' ? 'info'
        : derived === 'UNSIGNED' ? 'warn' : 'muted';
  return <Chip label={`授权书：${statusLabel(derived)}`} tone={tone} className={className} />;
}

/* ---------- MarketTape: topbar tape fed by the MEXC market snapshot --------
   Honest offline posture: without a real snapshot the tape says so instead of
   fabricating prices. ------------------------------------------------------ */

export function MarketTape() {
  const { payload, usingBackend } = useConsoleSnapshot<MexcMarketPayload>(
    s => fetchMexcMarketSnapshot(s));
  const items = usingBackend && payload?.snapshot_present && payload.latest_price != null
    ? [{
        symbol: `${payload.venue}·${payload.symbol}`,
        price: payload.latest_price,
        changePct: payload.change_24h_pct ?? undefined,
      }]
    : [{ symbol: 'MEXC·BTCUSDT', price: bi('未连接', 'offline') }];
  return <TickerTape items={items} />;
}
