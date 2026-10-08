import { Component, useState, type ReactNode } from 'react';
import { Link } from '@/lib/router';
import { fmt, statusLabel } from './data';
export function Icon({ name }: {
  name: string;
}) {
  const paths: Record<string, ReactNode> = {
    home: <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>, runs: <>
      <path d="M8 5h13M8 12h13M8 19h13" />
      <path d="m3 5 1 1 2-2m-3 8 1 1 2-2m-3 8 1 1 2-2" />
    </>, forward: <>
      <path d="M2 12h4l3-8 5 16 3-8h5" />
    </>, data: <>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0" />
    </>, system: <>
      <path d="M3 4h18v13H3zM8 21h8m-4-4v4" />
      <path d="m7 11 3-3 3 4 4-3" />
    </>, arrow: <path d="m5 12h14m-5-5 5 5-5 5" />, plus: <path d="M12 5v14M5 12h14" />, search: <>
      <circle cx="10" cy="10" r="6" />
      <path d="m15 15 5 5" />
    </>, refresh: <>
      <path d="M20 7V3l-4 4M4 17v4l4-4" />
      <path d="M20 7a9 9 0 0 0-15 0M4 17a9 9 0 0 0 15 0" />
    </>, moon: <path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z" />, menu: <path d="M4 6h16M4 12h16M4 18h16" />, close: <path d="m6 6 12 12M6 18 18 6" />
  };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.runs}</svg>;
}
export function Badge({ value }: {
  value: string;
}) {
  return <span className={`s-badge ${['healthy', 'done', 'PASSED', 'REAL', 'READY'].includes(value) ? 'good' : ['down', 'error', 'FAILED', 'CORRUPT_OR_UNREADABLE'].includes(value) ? 'bad' : ['running', 'queued', 'OBSERVING'].includes(value) ? 'active' : ''}`}>
    <i />{statusLabel(value)}</span>;
}
export function Empty({ title, children, action }: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return <div className="s-empty">
    <span className="s-empty-icon">
      <Icon name="data" />
    </span>
    <h3>{title}</h3>{children && <p>{children}</p>}{action}</div>;
}
export function ErrorNote({ error, retry }: {
  error: string | null;
  retry: () => void;
}) {
  return error ? <div className="s-error" role="alert">
    <div>
      <strong>数据暂时无法读取</strong>
      <p>{error}</p>
    </div>
    <button className="s-button" onClick={retry}>重试</button>
  </div> : null;
}
export function Header({ eyebrow, title, children, action }: {
  eyebrow: string;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return <header className="s-page-header">
    <div>
      <p className="s-eyebrow">{eyebrow}</p>
      <h1>{title}</h1>{children && <p className="s-lead">{children}</p>}</div>{action}</header>;
}
export function Panel({ title, note, action, children, className = '' }: {
  title: string;
  note?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return <section className={`s-panel ${className}`}>
    <header className="s-panel-head">
      <div>
        <h2>{title}</h2>{note && <p>{note}</p>}</div>{action}</header>{children}</section>;
}
export function Metric({ label, value, note }: {
  label: string;
  value: ReactNode;
  note?: string;
}) {
  return <div className="s-metric">
    <span>{label}</span>
    <strong>{value}</strong>{note && <small>{note}</small>}</div>;
}
export function Loading() {
  return <div className="s-loading" role="status">正在读取真实数据<span />
  </div>;
}
export function SparkChart({ points, title = '权益变化' }: {
  points: Array<{
    time: number;
    value: number;
  }>;
  title?: string;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const rows = points.filter(p => Number.isFinite(p.value) && Number.isFinite(p.time));
  if (rows.length < 2)
    return <Empty title="尚无可绘制的曲线">至少需要两个有效数据点。</Empty>;
  const min = Math.min(...rows.map(p => p.value)), max = Math.max(...rows.map(p => p.value));
  const pad = Math.max((max - min) * .16, 1);
  const lo = min - pad, hi = max + pad;
  const x = (i: number) => 70 + i / (rows.length - 1) * 870, y = (v: number) => 225 - (v - lo) / (hi - lo) * 190;
  const line = rows.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(p.value)}`).join(' ');
  const active = rows[Math.min(selected ?? rows.length - 1, rows.length - 1)];
  return <div className="s-chart">
    <div className="s-chart-value">
      <span>{new Date(active.time).toLocaleDateString('zh-CN')} · USDC</span>
      <strong>{fmt(active.value)}</strong>
    </div>
    <svg viewBox="0 0 980 280" role="img" aria-label={title} onPointerMove={e => { const box = e.currentTarget.getBoundingClientRect(); setSelected(Math.max(0, Math.min(rows.length - 1, Math.round(((e.clientX - box.left) / box.width * 980 - 70) / 870 * (rows.length - 1))))); }} onPointerLeave={() => setSelected(null)}>
      {[0, .25, .5, .75, 1].map(t => <g key={t}>
        <path d={`M70 ${35 + 190 * t}H940`} stroke="var(--s-line)" strokeDasharray="3 5" />
        <text x="55" y={39 + 190 * t} textAnchor="end" fill="var(--s-muted)" fontSize="11">{fmt(hi - (hi - lo) * t, 0)}</text>
      </g>)}
      <path d={`${line}L940 225H70Z`} fill="var(--s-chart-fill)" />
      <path d={line} fill="none" stroke="var(--s-accent)" strokeWidth="2.5" />
      {selected !== null && <g>
        <path d={`M${x(selected)} 35V225`} stroke="var(--s-muted)" strokeDasharray="4 4" />
        <circle cx={x(selected)} cy={y(active.value)} r="4" fill="var(--s-accent)" />
      </g>}
      {[0, .5, 1].map(t => <text key={t} x={70 + 870 * t} y="260" textAnchor={t === 0 ? 'start' : t === 1 ? 'end' : 'middle'} fill="var(--s-muted)" fontSize="11">{new Date(rows[Math.round((rows.length - 1) * t)].time).toLocaleDateString('zh-CN')}</text>)}
    </svg>
    <label className="s-sr-only">查看曲线数据点<input type="range" min="0" max={rows.length - 1} value={selected ?? rows.length - 1} onChange={e => setSelected(Number(e.target.value))} />
    </label>
  </div>;
}
export class StudioBoundary extends Component<{
  children: ReactNode;
}, {
  error: boolean;
}> {
  state = { error: false };
  static getDerivedStateFromError() { return { error: true }; }
  render() { return this.state.error ? <Empty title="这个页面遇到了问题" action={<Link className="s-button" to="/">返回工作台</Link>}>请刷新重试，已保存的运行不会受影响。</Empty> : this.props.children; }
}
