import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, Panel, KpiStrip, StatTile, DataTable, StatusChip, Chip, type Column,
} from '@/ui/primitives';
import { fixed, signed, pct } from '@/ui/format';

/**
 * Signal -> Portfolio (Phase 5 surface). Shows the adapter turning a cross-sectional signal into a
 * dollar-neutral target book: per-name target weights, gross/net, turnover, cost (incl. spread), and
 * capacity flags + rejected signals. RESEARCH / DRY-RUN ONLY — a target book, never an order.
 */

interface Pos { inst: string; weight: number; participation: number | null; capacity_ok: boolean | null; }
const book: Pos[] = [
  { inst: 'BTCUSDT', weight: 0.21, participation: 0.004, capacity_ok: true },
  { inst: 'ETHUSDT', weight: 0.18, participation: 0.006, capacity_ok: true },
  { inst: 'SOLUSDT', weight: 0.12, participation: 0.03, capacity_ok: true },
  { inst: 'ARBUSDT', weight: -0.15, participation: 0.04, capacity_ok: true },
  { inst: 'PEPEUSDT', weight: -0.19, participation: 0.18, capacity_ok: false },
  { inst: 'WIFUSDT', weight: -0.15, participation: null, capacity_ok: null },
];
const rejected = [
  { id: 'LUNAUSDT', reason: 'STALE' },
  { id: 'XYZUSDT', reason: 'MISSING' },
];

export default function SignalToPortfolio() {
  const gross = book.reduce((s, p) => s + Math.abs(p.weight), 0);
  const net = book.reduce((s, p) => s + p.weight, 0);

  const cols: Column<Pos>[] = [
    { key: 'inst', header: bi('标的', 'Instrument'), render: p => <span className="mono text-ink">{p.inst}</span> },
    { key: 'w', header: bi('目标权重', 'Target Weight'), align: 'right', mono: true, render: p => (
      <span className={p.weight > 0 ? 'text-pos' : 'text-neg'}>{signed(p.weight, 3)}</span>) },
    { key: 'side', header: bi('方向', 'Side'), render: p => (
      <Chip label={p.weight > 0 ? bi('多', 'long') : bi('空', 'short')} tone={p.weight > 0 ? 'pos' : 'neg'} />) },
    { key: 'part', header: bi('参与率', 'Participation'), align: 'right', mono: true, render: p => (
      p.participation === null ? <span className="text-faint">—</span> : pct(p.participation, 1)) },
    { key: 'cap', header: bi('容量', 'Capacity'), render: p => (
      p.capacity_ok === null ? <Chip label={bi('未知', 'unknown')} tone="muted" />
        : <StatusChip value={p.capacity_ok ? 'OK' : 'BREACH'} />) },
  ];

  return (
    <PageShell>
      <PageHeader title="Signal → Portfolio" titleZh="信号 → 组合"
        subtitle={bi('截面信号 → 美元中性目标 book(归一/中性化/约束/成本/容量);研究·只算 book,绝不下单',
          'Cross-sectional signal → dollar-neutral target book (normalize / neutralize / constrain / cost / capacity). Research only — a target book, never an order')} />

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究/干跑(L2)· 仅产出目标权重,无任何下单/券商/执行路径',
          'Read-only · research / dry-run (L2) · produces target weights only — no order / broker / execution path')}
      </div>

      <KpiStrip cols={5}>
        <StatTile label={bi('持仓数', 'Positions')} value={String(book.length)} />
        <StatTile label={bi('毛敞口', 'Gross')} value={fixed(gross, 2)} accent />
        <StatTile label={bi('净敞口', 'Net')} value={fixed(net, 3)} tone={Math.abs(net) < 0.02 ? 'pos' : 'warn'} sub={bi('美元中性', 'dollar-neutral')} />
        <StatTile label={bi('换手', 'Turnover')} value="0.41" tone="info" />
        <StatTile label={bi('成本(含价差)', 'Cost incl. spread')} value="6.2 bps" tone="warn" />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('目标 book', 'Target Book')} title={bi('目标权重与容量', 'Target Weights & Capacity')}>
          <DataTable columns={cols} rows={book} getKey={p => p.inst} />
        </Panel>
        <Panel eyebrow={bi('被拒信号', 'Rejected')} title={bi('清洗丢弃', 'Cleaned Out')}>
          <div className="flex flex-col gap-2">
            {rejected.map(r => (
              <div key={r.id} className="flex items-center justify-between rounded-lg border border-line bg-inset/40 px-3 py-2">
                <span className="mono text-xs text-ink">{r.id}</span>
                <Chip label={r.reason} tone="warn" />
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </PageShell>
  );
}
