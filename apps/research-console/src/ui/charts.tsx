import React from 'react';
import { TONE_VAR, type Tone } from './format';

/* ============================================================================
   Bespoke SVG chart kit — pure, dependency-free, deterministic, themeable via
   CSS tokens. Every chart is responsive (viewBox + width:100%) and degrades
   gracefully on empty data. Numbers, not chartjunk.
   ========================================================================== */

type XY = { x: number; y: number };
const toXY = (d: ReadonlyArray<number | XY>): XY[] =>
  d.map((p, i) => (typeof p === 'number' ? { x: i, y: p } : p));

function bounds(pts: XY[]) {
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
}

function Empty({ h, label }: { h: number; label?: string }) {
  return (
    <div className="flex items-center justify-center text-2xs text-faint" style={{ height: h }}>
      {label ?? '无数据 / no data'}
    </div>
  );
}

/* ---------- Sparkline: tiny inline trend ---------------------------------- */
export function Sparkline({ data, tone = 'accent', height = 28, width = 96 }:
  { data: ReadonlyArray<number | XY>; tone?: Tone; height?: number; width?: number }) {
  const pts = toXY(data);
  if (pts.length < 2) return <Empty h={height} />;
  const { minX, maxX, minY, maxY } = bounds(pts);
  const sx = (x: number) => ((x - minX) / (maxX - minX || 1)) * width;
  const sy = (y: number) => height - ((y - minY) / (maxY - minY || 1)) * (height - 4) - 2;
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} preserveAspectRatio="none">
      <path d={d} fill="none" stroke={TONE_VAR[tone]} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/* ---------- AreaChart: line + gradient fill (equity / generic series) ------ */
export function AreaChart({ data, height = 200, tone = 'accent', baselineAtZero = false, yFormat, xCount = 4 }:
  { data: ReadonlyArray<number | XY>; height?: number; tone?: Tone; baselineAtZero?: boolean;
    yFormat?: (v: number) => string; xCount?: number }) {
  const pts = toXY(data);
  if (pts.length < 2) return <Empty h={height} />;
  const W = 1000, padL = 52, padR = 12, padT = 10, padB = 20;
  const b = bounds(pts);
  const minY = baselineAtZero ? Math.min(0, b.minY) : b.minY;
  const maxY = b.maxY;
  const sx = (x: number) => padL + ((x - b.minX) / (b.maxX - b.minX || 1)) * (W - padL - padR);
  const sy = (y: number) => padT + (1 - (y - minY) / (maxY - minY || 1)) * (height - padT - padB);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`).join(' ');
  const area = `${line} L${sx(b.maxX).toFixed(1)} ${height - padB} L${sx(b.minX).toFixed(1)} ${height - padB} Z`;
  const uid = `${tone}-${Math.round(minY)}-${Math.round(maxY)}-${pts.length}`;
  const useGrad = tone === 'accent';
  const stroke = useGrad ? `url(#stroke-${uid})` : TONE_VAR[tone];
  const ticks = Array.from({ length: 3 }, (_, i) => minY + (i / 2) * (maxY - minY));
  const last = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img">
      <defs>
        <linearGradient id={`fill-${uid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={TONE_VAR[tone]} stopOpacity={0.32} />
          <stop offset="100%" stopColor={TONE_VAR[tone]} stopOpacity={0.0} />
        </linearGradient>
        <linearGradient id={`stroke-${uid}`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#5eead4" />
          <stop offset="55%" stopColor="#818cf8" />
          <stop offset="100%" stopColor="#c084fc" />
        </linearGradient>
        <filter id={`glow-${uid}`} x="-10%" y="-40%" width="120%" height="180%">
          <feGaussianBlur stdDeviation="3.2" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={sy(t)} y2={sy(t)} stroke="var(--grid)" strokeWidth={1} />
          <text x={padL - 8} y={sy(t) + 3} textAnchor="end" fontSize={11} fill="var(--faint)" className="tnum">
            {yFormat ? yFormat(t) : t.toFixed(0)}
          </text>
        </g>
      ))}
      <path d={area} fill={`url(#fill-${uid})`} />
      <path d={line} fill="none" stroke={stroke} strokeWidth={2.25} strokeLinecap="round" strokeLinejoin="round"
        vectorEffect="non-scaling-stroke" filter={`url(#glow-${uid})`} />
      <circle cx={sx(last.x)} cy={sy(last.y)} r={3.2} fill={useGrad ? '#c084fc' : TONE_VAR[tone]}
        stroke="var(--panel)" strokeWidth={1.5} />
      {Array.from({ length: xCount }, (_, i) => {
        const idx = Math.round((i / (xCount - 1)) * (pts.length - 1));
        return <text key={i} x={sx(pts[idx].x)} y={height - 6} textAnchor="middle" fontSize={10} fill="var(--faint)" className="tnum">{pts[idx].x}</text>;
      })}
    </svg>
  );
}

/* ---------- UnderwaterChart: drawdown (negative area below zero) ----------- */
export function UnderwaterChart({ data, height = 120, yFormat }:
  { data: ReadonlyArray<number | XY>; height?: number; yFormat?: (v: number) => string }) {
  const pts = toXY(data);
  if (pts.length < 2) return <Empty h={height} />;
  const W = 1000, padL = 48, padR = 12, padT = 8, padB = 16;
  const b = bounds(pts);
  const minY = Math.min(0, b.minY), maxY = Math.max(0, b.maxY);
  const sx = (x: number) => padL + ((x - b.minX) / (b.maxX - b.minX || 1)) * (W - padL - padR);
  const sy = (y: number) => padT + (1 - (y - minY) / (maxY - minY || 1)) * (height - padT - padB);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`).join(' ');
  const zero = sy(0);
  const area = `${line} L${sx(b.maxX).toFixed(1)} ${zero} L${sx(b.minX).toFixed(1)} ${zero} Z`;
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img">
      <defs>
        <linearGradient id="uw" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--neg)" stopOpacity={0.04} />
          <stop offset="100%" stopColor="var(--neg)" stopOpacity={0.32} />
        </linearGradient>
      </defs>
      <line x1={padL} x2={W - padR} y1={zero} y2={zero} stroke="var(--line-strong)" strokeWidth={1} />
      <text x={padL - 6} y={sy(b.minY) + 3} textAnchor="end" fontSize={11} fill="var(--faint)" className="tnum">
        {yFormat ? yFormat(b.minY) : b.minY.toFixed(0)}
      </text>
      <path d={area} fill="url(#uw)" />
      <path d={line} fill="none" stroke="var(--neg)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/* ---------- BarSeries: vertical bars, diverging pos/neg -------------------- */
export function BarSeries({ data, height = 160, labels, posTone = 'pos', negTone = 'neg', valueFormat }:
  { data: ReadonlyArray<number>; height?: number; labels?: ReadonlyArray<string>;
    posTone?: Tone; negTone?: Tone; valueFormat?: (v: number) => string }) {
  if (!data.length) return <Empty h={height} />;
  const W = 1000, padT = 14, padB = labels ? 26 : 10, padL = 8, padR = 8;
  const maxAbs = Math.max(...data.map(v => Math.abs(v)), 1e-9);
  const hasNeg = data.some(v => v < 0);
  const zeroY = hasNeg ? padT + (height - padT - padB) / 2 : height - padB;
  const fullH = hasNeg ? (height - padT - padB) / 2 : height - padT - padB;
  const slot = (W - padL - padR) / data.length;
  const bw = Math.min(slot * 0.62, 60);
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img">
      <line x1={padL} x2={W - padR} y1={zeroY} y2={zeroY} stroke="var(--line)" strokeWidth={1} />
      {data.map((v, i) => {
        const cx = padL + slot * i + slot / 2;
        const h = (Math.abs(v) / maxAbs) * fullH;
        const y = v >= 0 ? zeroY - h : zeroY;
        return (
          <g key={i}>
            <rect x={cx - bw / 2} y={y} width={bw} height={Math.max(h, 0.5)} rx={2}
              fill={TONE_VAR[v >= 0 ? posTone : negTone]} opacity={0.85} />
            {valueFormat && <text x={cx} y={v >= 0 ? y - 4 : y + h + 11} textAnchor="middle" fontSize={10} fill="var(--muted)" className="tnum">{valueFormat(v)}</text>}
            {labels && <text x={cx} y={height - 8} textAnchor="middle" fontSize={10} fill="var(--faint)">{labels[i]}</text>}
          </g>
        );
      })}
    </svg>
  );
}

/* ---------- Histogram: labelled distribution ------------------------------ */
export function Histogram({ bins, height = 150, tone = 'info' }:
  { bins: ReadonlyArray<{ label: string; count: number }>; height?: number; tone?: Tone }) {
  if (!bins.length) return <Empty h={height} />;
  const W = 1000, padT = 10, padB = 26, padL = 8, padR = 8;
  const max = Math.max(...bins.map(b => b.count), 1);
  const slot = (W - padL - padR) / bins.length;
  const bw = slot * 0.7;
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img">
      {bins.map((b, i) => {
        const h = (b.count / max) * (height - padT - padB);
        const x = padL + slot * i + (slot - bw) / 2;
        return (
          <g key={i}>
            <rect x={x} y={height - padB - h} width={bw} height={Math.max(h, 0.5)} rx={2} fill={TONE_VAR[tone]} opacity={0.8} />
            <text x={x + bw / 2} y={height - padB - h - 4} textAnchor="middle" fontSize={10} fill="var(--muted)" className="tnum">{b.count}</text>
            <text x={x + bw / 2} y={height - 8} textAnchor="middle" fontSize={9} fill="var(--faint)">{b.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ---------- Funnel: conversion stages ------------------------------------- */
export function Funnel({ stages, height = 200 }:
  { stages: ReadonlyArray<{ label: string; value: number; tone?: Tone }>; height?: number }) {
  if (!stages.length) return <Empty h={height} />;
  const W = 1000, padT = 6, padB = 6, gap = 10;
  const max = Math.max(...stages.map(s => s.value), 1);
  const slot = (W - gap * (stages.length - 1)) / stages.length;
  const tones: Tone[] = ['muted', 'info', 'accent', 'warn', 'special', 'pos'];
  const h = height - padT - padB;
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} role="img">
      {stages.map((s, i) => {
        const x = i * (slot + gap);
        const bh = (s.value / max) * (h - 40);
        const tone = s.tone ?? tones[i % tones.length];
        return (
          <g key={i}>
            <rect x={x} y={padT + (h - 40 - bh)} width={slot} height={bh + 1} rx={4} fill={TONE_VAR[tone]} opacity={0.22} />
            <rect x={x} y={padT + (h - 40 - bh)} width={slot} height={3} rx={2} fill={TONE_VAR[tone]} />
            <text x={x + slot / 2} y={height - 22} textAnchor="middle" fontSize={11} fill="var(--ink)" className="tnum" fontWeight={700}>{s.value.toLocaleString()}</text>
            <text x={x + slot / 2} y={height - 7} textAnchor="middle" fontSize={10} fill="var(--faint)">{s.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ---------- RadialGauge: score arc (0..max) ------------------------------- */
export function RadialGauge({ value, max = 1, size = 132, label, tone }:
  { value: number; max?: number; size?: number; label?: string; tone?: Tone }) {
  const frac = Math.max(0, Math.min(1, value / (max || 1)));
  const t: Tone = tone ?? (frac >= 0.75 ? 'pos' : frac >= 0.5 ? 'accent' : frac >= 0.3 ? 'warn' : 'neg');
  const r = size / 2 - 12, cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r, start = 0.5 * Math.PI;
  const a0 = start + 0.2 * Math.PI, sweep = 1.6 * Math.PI;
  const polar = (ang: number) => `${cx + r * Math.cos(ang)} ${cy + r * Math.sin(ang)}`;
  const arc = (frac0: number) => {
    const a1 = a0 + frac0 * sweep;
    const large = frac0 * sweep > Math.PI ? 1 : 0;
    return `M${polar(a0)} A${r} ${r} 0 ${large} 1 ${polar(a1)}`;
  };
  return (
    <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img">
      <path d={arc(1)} fill="none" stroke="var(--line-strong)" strokeWidth={9} strokeLinecap="round" />
      <path d={arc(frac)} fill="none" stroke={TONE_VAR[t]} strokeWidth={9} strokeLinecap="round" />
      <text x={cx} y={cy} textAnchor="middle" fontSize={size * 0.2} fill="var(--ink)" className="tnum" fontWeight={800}>
        {max === 1 ? `${Math.round(frac * 100)}` : value.toFixed(value < 10 ? 1 : 0)}
      </text>
      <text x={cx} y={cy + size * 0.16} textAnchor="middle" fontSize={11} fill="var(--faint)">{label ?? (max === 1 ? '%' : '')}</text>
    </svg>
  );
}

/* ---------- Heatmap: correlation matrix ----------------------------------- */
export function Heatmap({ ids, matrix, size = 360 }:
  { ids: ReadonlyArray<string>; matrix: ReadonlyArray<ReadonlyArray<number>>; size?: number }) {
  const n = ids.length;
  if (!n || !matrix.length) return <Empty h={size} />;
  const pad = 64, cell = (size - pad) / n;
  const color = (v: number) => {
    // diverging: -1 neg (red) .. 0 (panel) .. +1 special/teal
    const a = Math.min(1, Math.abs(v));
    return v >= 0 ? `color-mix(in srgb, var(--accent) ${a * 70}%, var(--panel))`
                  : `color-mix(in srgb, var(--neg) ${a * 70}%, var(--panel))`;
  };
  return (
    <svg viewBox={`0 0 ${size} ${size}`} width="100%" height={size} role="img">
      {matrix.map((row, i) => row.map((v, j) => (
        <g key={`${i}-${j}`}>
          <rect x={pad + j * cell} y={pad + i * cell} width={cell - 1.5} height={cell - 1.5} rx={2} fill={color(v)} />
          {cell > 26 && <text x={pad + j * cell + cell / 2} y={pad + i * cell + cell / 2 + 3} textAnchor="middle" fontSize={Math.min(11, cell / 3)} fill="var(--ink)" className="tnum" opacity={0.85}>{v.toFixed(2)}</text>}
        </g>
      )))}
      {ids.map((id, i) => (
        <text key={`r${i}`} x={pad - 6} y={pad + i * cell + cell / 2 + 3} textAnchor="end" fontSize={10} fill="var(--faint)">{id}</text>
      ))}
      {ids.map((id, j) => (
        <text key={`c${j}`} x={pad + j * cell + cell / 2} y={pad - 6} textAnchor="middle" fontSize={10} fill="var(--faint)">{id}</text>
      ))}
    </svg>
  );
}

/* ---------- Donut: composition -------------------------------------------- */
export function Donut({ segments, size = 132 }:
  { segments: ReadonlyArray<{ label: string; value: number; tone?: Tone }>; size?: number }) {
  const total = segments.reduce((s, x) => s + Math.max(0, x.value), 0);
  if (total <= 0) return <Empty h={size} />;
  const r = size / 2 - 10, cx = size / 2, cy = size / 2, sw = 14;
  const C = 2 * Math.PI * r;
  const tones: Tone[] = ['accent', 'info', 'warn', 'special', 'pos', 'neg', 'muted'];
  let off = 0;
  return (
    <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img">
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--inset)" strokeWidth={sw} />
      {segments.map((s, i) => {
        const frac = Math.max(0, s.value) / total;
        const dash = `${frac * C} ${C}`;
        const el = (
          <circle key={i} cx={cx} cy={cy} r={r} fill="none" strokeWidth={sw}
            stroke={TONE_VAR[s.tone ?? tones[i % tones.length]]}
            strokeDasharray={dash} strokeDashoffset={-off * C}
            transform={`rotate(-90 ${cx} ${cy})`} />
        );
        off += frac;
        return el;
      })}
      <text x={cx} y={cy + 4} textAnchor="middle" fontSize={size * 0.18} fill="var(--ink)" className="tnum" fontWeight={800}>{segments.length}</text>
    </svg>
  );
}

/* ---------- DivergingBars: horizontal attribution ------------------------- */
export function DivergingBars({ items, height, valueFormat }:
  { items: ReadonlyArray<{ label: string; value: number }>; height?: number; valueFormat?: (v: number) => string }) {
  if (!items.length) return <Empty h={height ?? 120} />;
  const rowH = 26, padT = 6, W = 1000, labelW = 220, valW = 84;
  const H = height ?? padT * 2 + items.length * rowH;
  const maxAbs = Math.max(...items.map(i => Math.abs(i.value)), 1e-9);
  const axis = labelW + (W - labelW - valW) / 2;
  const half = (W - labelW - valW) / 2;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img">
      <line x1={axis} x2={axis} y1={padT} y2={H - padT} stroke="var(--line)" strokeWidth={1} />
      {items.map((it, i) => {
        const y = padT + i * rowH;
        const w = (Math.abs(it.value) / maxAbs) * half;
        const pos = it.value >= 0;
        return (
          <g key={i}>
            <text x={8} y={y + rowH / 2 + 4} fontSize={11} fill="var(--muted)">{it.label}</text>
            <rect x={pos ? axis : axis - w} y={y + 5} width={Math.max(w, 0.5)} height={rowH - 12} rx={2}
              fill={TONE_VAR[pos ? 'pos' : 'neg']} opacity={0.85} />
            <text x={W - 8} y={y + rowH / 2 + 4} textAnchor="end" fontSize={11} fill="var(--ink)" className="tnum">
              {valueFormat ? valueFormat(it.value) : it.value.toFixed(2)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ============================================================================
   Institutional workbench kit — exchange-grade market microstructure visuals.
   Same constraints as above: hand-rolled SVG/DOM, zero deps, deterministic,
   themed via CSS tokens, graceful on empty data.
   ========================================================================== */

export interface Candle { t?: string | number; open: number; high: number; low: number; close: number; volume?: number }

/* ---------- CandleChart: OHLC candles + volume subpane + MA overlays ------ */
export function CandleChart({ bars, height = 280, maPeriods = [], yFormat }:
  { bars: ReadonlyArray<Candle>; height?: number; maPeriods?: ReadonlyArray<number>;
    yFormat?: (v: number) => string }) {
  if (bars.length < 2) return <Empty h={height} />;
  const W = 1000, padL = 56, padR = 12, padT = 10, padB = 18;
  const volFrac = bars.some(b => (b.volume ?? 0) > 0) ? 0.18 : 0;
  const volH = (height - padT - padB) * volFrac;
  const priceH = height - padT - padB - volH - (volFrac ? 6 : 0);
  const minY = Math.min(...bars.map(b => b.low));
  const maxY = Math.max(...bars.map(b => b.high));
  const maxVol = Math.max(...bars.map(b => b.volume ?? 0), 1e-9);
  const slot = (W - padL - padR) / bars.length;
  const bw = Math.max(1, Math.min(slot * 0.62, 14));
  const sx = (i: number) => padL + slot * i + slot / 2;
  const sy = (y: number) => padT + (1 - (y - minY) / (maxY - minY || 1)) * priceH;
  const ticks = Array.from({ length: 4 }, (_, i) => minY + (i / 3) * (maxY - minY));
  const maTones = ['var(--warn)', 'var(--info)', 'var(--special)'];
  const maLine = (period: number) => {
    if (period < 2 || bars.length < period) return null;
    let d = '';
    for (let i = period - 1; i < bars.length; i++) {
      let s = 0;
      for (let j = i - period + 1; j <= i; j++) s += bars[j].close;
      const v = s / period;
      d += `${d ? 'L' : 'M'}${sx(i).toFixed(1)} ${sy(v).toFixed(1)}`;
    }
    return d;
  };
  const last = bars[bars.length - 1];
  const lastUp = last.close >= last.open;
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img">
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={sy(t)} y2={sy(t)} stroke="var(--grid)" strokeWidth={1} />
          <text x={padL - 8} y={sy(t) + 3} textAnchor="end" fontSize={10} fill="var(--faint)" className="tnum">
            {yFormat ? yFormat(t) : t.toFixed(0)}
          </text>
        </g>
      ))}
      {bars.map((b, i) => {
        const up = b.close >= b.open;
        const c = up ? 'var(--pos)' : 'var(--neg)';
        const yO = sy(b.open), yC = sy(b.close);
        const y = Math.min(yO, yC), h = Math.max(Math.abs(yO - yC), 0.6);
        return (
          <g key={i}>
            <line x1={sx(i)} x2={sx(i)} y1={sy(b.high)} y2={sy(b.low)} stroke={c} strokeWidth={1} vectorEffect="non-scaling-stroke" />
            <rect x={sx(i) - bw / 2} y={y} width={bw} height={h} fill={c} opacity={up ? 0.85 : 0.9} />
          </g>
        );
      })}
      {maPeriods.map((p, k) => {
        const d = maLine(p);
        return d ? <path key={p} d={d} fill="none" stroke={maTones[k % maTones.length]} strokeWidth={1.2}
          vectorEffect="non-scaling-stroke" opacity={0.9} /> : null;
      })}
      {volFrac > 0 && bars.map((b, i) => {
        const up = b.close >= b.open;
        const vh = ((b.volume ?? 0) / maxVol) * volH;
        return <rect key={`v${i}`} x={sx(i) - bw / 2} y={height - padB - vh} width={bw} height={Math.max(vh, 0.4)}
          fill={up ? 'var(--pos)' : 'var(--neg)'} opacity={0.4} />;
      })}
      <line x1={padL} x2={W - padR} y1={sy(last.close)} y2={sy(last.close)}
        stroke={lastUp ? 'var(--pos)' : 'var(--neg)'} strokeWidth={1} strokeDasharray="4 3" opacity={0.7} />
      <text x={W - padR + 2} y={sy(last.close) + 3} fontSize={10} fill={lastUp ? 'var(--pos)' : 'var(--neg)'} className="tnum">
        {yFormat ? yFormat(last.close) : last.close.toFixed(0)}
      </text>
    </svg>
  );
}

/* ---------- DepthChart: bilateral cumulative book depth ------------------- */
export function DepthChart({ bids, asks, height = 160, xFormat }:
  { bids: ReadonlyArray<readonly [number, number]>; asks: ReadonlyArray<readonly [number, number]>;
    height?: number; xFormat?: (v: number) => string }) {
  if (!bids.length && !asks.length) return <Empty h={height} />;
  const W = 1000, padL = 8, padR = 8, padT = 8, padB = 18;
  const cum = (rows: ReadonlyArray<readonly [number, number]>) => {
    let s = 0;
    return rows.map(([p, q]) => ({ p, c: (s += q) }));
  };
  const bc = cum([...bids].sort((a, b) => b[0] - a[0]));
  const ac = cum([...asks].sort((a, b) => a[0] - b[0]));
  const maxC = Math.max(bc.length ? bc[bc.length - 1].c : 0, ac.length ? ac[ac.length - 1].c : 0, 1e-9);
  const prices = [...bc.map(x => x.p), ...ac.map(x => x.p)];
  const minP = Math.min(...prices), maxP = Math.max(...prices);
  const sx = (p: number) => padL + ((p - minP) / (maxP - minP || 1)) * (W - padL - padR);
  const sy = (c: number) => padT + (1 - c / maxC) * (height - padT - padB);
  const path = (rows: { p: number; c: number }[], side: 'bid' | 'ask') => {
    if (!rows.length) return null;
    const sorted = side === 'bid' ? [...rows].reverse() : rows;
    let d = '';
    sorted.forEach((r, i) => { d += `${i ? 'L' : 'M'}${sx(r.p).toFixed(1)} ${sy(r.c).toFixed(1)}`; });
    const base = height - padB;
    if (side === 'bid') d = `M${sx(minP)} ${base} ` + d + ` L${sx(sorted[sorted.length - 1].p)} ${base} Z`;
    else d = `M${sx(sorted[0].p)} ${base} ` + d + ` L${sx(maxP)} ${base} Z`;
    return d;
  };
  const xt = [minP, (minP + maxP) / 2, maxP];
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img">
      <path d={path(bc, 'bid') ?? ''} fill="var(--pos)" fillOpacity={0.18} stroke="var(--pos)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
      <path d={path(ac, 'ask') ?? ''} fill="var(--neg)" fillOpacity={0.18} stroke="var(--neg)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
      <line x1={padL} x2={W - padR} y1={height - padB} y2={height - padB} stroke="var(--line)" strokeWidth={1} />
      {xt.map((p, i) => (
        <text key={i} x={sx(p)} y={height - 5} textAnchor="middle" fontSize={10} fill="var(--faint)" className="tnum">
          {xFormat ? xFormat(p) : p.toFixed(0)}
        </text>
      ))}
    </svg>
  );
}

/* ---------- OrderBookLadder: bid/ask ladder with cumulative depth bars ---- */
export function OrderBookLadder({ bids, asks, levels = 8, priceDigits = 1, qtyDigits = 4 }:
  { bids: ReadonlyArray<readonly [number, number]>; asks: ReadonlyArray<readonly [number, number]>;
    levels?: number; priceDigits?: number; qtyDigits?: number }) {
  const b = [...bids].sort((x, y) => y[0] - x[0]).slice(0, levels);
  const a = [...asks].sort((x, y) => x[0] - y[0]).slice(0, levels);
  if (!b.length && !a.length) return <Empty h={levels * 22} />;
  const tot = (rows: ReadonlyArray<readonly [number, number]>) => {
    let s = 0;
    return rows.map(([p, q]) => [p, q, (s += q)] as const);
  };
  const bt = tot(b), at = tot(a);
  const maxT = Math.max(bt.length ? bt[bt.length - 1][2] : 0, at.length ? at[at.length - 1][2] : 0, 1e-9);
  const spread = b.length && a.length ? a[0][0] - b[0][0] : null;
  const row = (p: number, q: number, t: number, side: 'bid' | 'ask', i: number) => (
    <div key={`${side}${i}`} className="relative grid grid-cols-3 px-2 py-[3px] text-2xs ticker-num">
      <span className="absolute inset-y-0 right-0"
        style={{ width: `${(t / maxT) * 100}%`, background: side === 'bid' ? 'var(--pos)' : 'var(--neg)', opacity: 0.10 }} />
      <span className="relative" style={{ color: side === 'bid' ? 'var(--pos)' : 'var(--neg)' }}>{p.toFixed(priceDigits)}</span>
      <span className="relative text-right text-muted">{q.toFixed(qtyDigits)}</span>
      <span className="relative text-right text-faint">{t.toFixed(qtyDigits)}</span>
    </div>
  );
  return (
    <div className="density-compact overflow-hidden rounded-tile border border-line bg-inset/40">
      <div className="grid grid-cols-3 border-b border-line px-2 py-1 text-3xs uppercase tracking-[0.1em] text-faint">
        <span>Price</span><span className="text-right">Size</span><span className="text-right">Total</span>
      </div>
      <div className="flex flex-col-reverse">{at.map(([p, q, t], i) => row(p, q, t, 'ask', i))}</div>
      <div className="border-y border-line-strong px-2 py-1 text-center text-2xs ticker-num text-muted">
        {spread !== null ? `Spread ${spread.toFixed(priceDigits)}` : '—'}
      </div>
      <div>{bt.map(([p, q, t], i) => row(p, q, t, 'bid', i))}</div>
    </div>
  );
}

/* ---------- TickerTape: scrolling market tape (topbar slot) --------------- */
export function TickerTape({ items, durationSec = 40, className = '' }:
  { items: ReadonlyArray<{ symbol: string; price: number | string; changePct?: number }>;
    durationSec?: number; className?: string }) {
  if (!items.length) return null;
  const seq = (key: string) => (
    <div key={key} className="flex shrink-0 items-center">
      {items.map((it, i) => {
        const up = (it.changePct ?? 0) >= 0;
        return (
          <span key={i} className="mx-4 flex items-center gap-2 text-2xs ticker-num">
            <span className="font-semibold text-muted">{it.symbol}</span>
            <span className="text-ink">{typeof it.price === 'number' ? it.price.toLocaleString('en-US') : it.price}</span>
            {it.changePct !== undefined && (
              <span style={{ color: up ? 'var(--pos)' : 'var(--neg)' }}>
                {up ? '▲' : '▼'} {Math.abs(it.changePct).toFixed(2)}%
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
  return (
    <div className={`relative min-w-0 flex-1 overflow-hidden ${className}`} role="marquee" aria-label="market tape">
      <div className="tape-scroll flex w-max"
        style={{ animation: `q-tape ${durationSec}s linear infinite` }}>
        {seq('a')}{seq('b')}
      </div>
      <span className="pointer-events-none absolute inset-y-0 left-0 w-8 bg-gradient-to-r from-surface to-transparent" />
      <span className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-surface to-transparent" />
    </div>
  );
}

/* ---------- MetricGrid: dense institutional metric grid ------------------- */
export function MetricGrid({ items, minCol = 128, className = '' }:
  { items: ReadonlyArray<{ label: React.ReactNode; value: React.ReactNode; sub?: React.ReactNode; tone?: Tone }>;
    minCol?: number; className?: string }) {
  const toneColor = (t?: Tone) => (t ? TONE_VAR[t] : 'var(--ink)');
  return (
    <div className={`grid gap-px overflow-hidden rounded-tile border border-line bg-line ${className}`}
      style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${minCol}px, 1fr))` }}>
      {items.map((it, i) => (
        <div key={i} className="flex min-w-0 flex-col gap-0.5 bg-panel px-2.5 py-2">
          <span className="truncate text-3xs font-medium uppercase tracking-[0.1em] text-faint">{it.label}</span>
          <span className="truncate text-sm font-semibold ticker-num" style={{ color: toneColor(it.tone) }}>{it.value}</span>
          {it.sub !== undefined && <span className="truncate text-3xs text-faint">{it.sub}</span>}
        </div>
      ))}
    </div>
  );
}
