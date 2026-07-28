import React, { useEffect, useMemo, useState } from 'react';
import { Link } from '@/lib/router';
import {
  fetchResearchRuns, fetchOverviewSnapshot, fetchCryptoLiveSnapshot,
  fetchSafetyPrimitivesSnapshot, fetchPopulationOptimizerSnapshot, fetchEventsSnapshot,
} from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import type {
  ResearchRunsPayload, OverviewPayload, CryptoLivePayload,
  SafetyPrimitivesPayload, PopulationOptimizerPayload, EventsPayload,
} from '@/lib/types/research';
import { PageShell, PageHeader, Panel, StatusChip, Chip, LoadingVeil, Button, SectionHeader } from '@/ui/primitives';
import { PipelineFlow } from '@/ui/PipelineFlow';

/**
 * Command Center — the single-pane research cockpit. Draggable (reorder) + resizable (width/height) tiles
 * on a 12-column grid, each tile a live summary of a REAL endpoint (research runs / overview funnel /
 * crypto-live factors / safety primitives / de-redundant set). Layout (order + spans) persists to
 * localStorage so the operator keeps their arrangement. Read-only; no run is triggered from here.
 */

type Span = { w: number; h: number };
const SIZES: Span[] = [{ w: 3, h: 1 }, { w: 4, h: 1 }, { w: 6, h: 1 }, { w: 6, h: 2 }, { w: 12, h: 1 }];
const LS_KEY = 'quantos.commandcenter.layout.v1';

interface TileDef { id: string; title: string; titleZh: string; to: string; render: () => React.ReactNode; }

const TONE: Record<string, string> = {
  ink: 'text-ink', accent: 'text-accent', pos: 'text-pos', info: 'text-info', cyan: 'text-cyan', warn: 'text-warn',
};
function Metric({ label, value, tone = 'ink' }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-2xs text-faint">{label}</span>
      <span className={`mono text-lg font-semibold ${TONE[tone] ?? 'text-ink'}`}>{value}</span>
    </div>
  );
}

function RunsTile() {
  const { payload, usingBackend, apiState, error } = useConsoleSnapshot<ResearchRunsPayload>(s => fetchResearchRuns(s));
  const runs = usingBackend ? (payload?.runs ?? []) : [];
  const done = runs.filter(r => r.status === 'done').length;
  const active = runs.filter(r => r.status === 'queued' || r.status === 'running').length;
  if (apiState === 'error') return <span className="text-2xs text-warn">{error ?? bi('加载失败', 'load error')}</span>;
  return (
    <LoadingVeil active={apiState === 'loading'}>
      <div className="flex flex-wrap items-end gap-5">
        <Metric label={bi('运行总数', 'Total runs')} value={runs.length || '—'} tone="accent" />
        <Metric label={bi('完成', 'Done')} value={done} tone="pos" />
        <Metric label={bi('进行中', 'Active')} value={active} tone="info" />
        {runs[0] && <div className="text-2xs text-muted">{bi('最新', 'latest')}: {runs[0].title}</div>}
      </div>
    </LoadingVeil>
  );
}

function OverviewTile() {
  const { payload, usingBackend, apiState, error } = useConsoleSnapshot<OverviewPayload>(s => fetchOverviewSnapshot(s));
  const f = usingBackend ? (payload?.funnel ?? []) : [];
  if (apiState === 'error') return <span className="text-2xs text-warn">{error ?? bi('加载失败', 'load error')}</span>;
  return (
    <LoadingVeil active={apiState === 'loading'}>
      <div className="flex flex-wrap items-end gap-4">
        {f.length ? f.map(x => <Metric key={x.id} label={x.label} value={x.value} />)
          : <span className="text-2xs text-faint">{bi('未连后端', 'backend offline')}</span>}
      </div>
    </LoadingVeil>
  );
}

function CryptoTile() {
  const { payload, usingBackend, apiState, error } = useConsoleSnapshot<CryptoLivePayload>(s => fetchCryptoLiveSnapshot(s));
  const factors = usingBackend ? (payload?.factors ?? []) : [];
  const top = [...factors].sort((a, b) => (b.net_sharpe ?? -9) - (a.net_sharpe ?? -9))[0];
  if (apiState === 'error') return <span className="text-2xs text-warn">{error ?? bi('加载失败', 'load error')}</span>;
  return (
    <LoadingVeil active={apiState === 'loading'}>
      <div className="flex flex-wrap items-end gap-5">
        <Metric label={bi('真实因子', 'Real factors')} value={factors.length || '—'} tone="cyan" />
        {top && <Metric label={bi('最佳净夏普', 'Top net SR')} value={top.net_sharpe?.toFixed(2) ?? '—'} tone="pos" />}
        {top && <div className="text-2xs text-muted">{top.name ?? top.alpha_id}</div>}
      </div>
    </LoadingVeil>
  );
}

function SafetyTile() {
  const { payload, apiState, error } = useConsoleSnapshot<SafetyPrimitivesPayload>(s => fetchSafetyPrimitivesSnapshot(s));
  if (apiState === 'error') return <span className="text-2xs text-warn">{error ?? bi('加载失败', 'load error')}</span>;
  if (!payload) return <span className="text-2xs text-faint">{bi('载入中…', 'loading…')}</span>;
  return (
    <LoadingVeil active={apiState === 'loading'}>
      <div className="flex flex-wrap items-center gap-3">
        <StatusChip value={payload.kill_switch?.is_active ? 'KILL_TRIPPED' : 'KILL_CLEAR'} />
        <Metric label={bi('盘前检查', 'Pre-trade checks')} value={payload.pre_trade_gate?.checks_registered ?? '—'} tone="info" />
        <Chip label="LIVE_DISALLOWED" tone="neg" />
        <Chip label={payload.safety_config?.require_human_approval ? 'HUMAN_APPROVAL' : 'NO_APPROVAL'}
          tone={payload.safety_config?.require_human_approval ? 'pos' : 'neg'} />
      </div>
    </LoadingVeil>
  );
}

function OptimizerTile() {
  const { payload, usingBackend, apiState, error } = useConsoleSnapshot<PopulationOptimizerPayload>(s => fetchPopulationOptimizerSnapshot(s));
  const d = usingBackend ? payload : null;
  if (apiState === 'error') return <span className="text-2xs text-warn">{error ?? bi('加载失败', 'load error')}</span>;
  return (
    <LoadingVeil active={apiState === 'loading'}>
      <div className="flex flex-wrap items-end gap-5">
        <Metric label={bi('已选', 'Selected')} value={d?.selected?.length ?? '—'} tone="pos" />
        <Metric label={bi('剔除', 'Excluded')} value={d?.excluded?.length ?? '—'} tone="warn" />
        <Metric label={bi('多样性', 'Diversity')} value={d?.diversity ?? '—'} tone="info" />
      </div>
    </LoadingVeil>
  );
}

function ActivityTile() {
  const { payload, usingBackend, apiState, error } = useConsoleSnapshot<EventsPayload>(s => fetchEventsSnapshot(s));
  const events = usingBackend ? (payload?.events ?? []) : [];
  const health = usingBackend ? payload?.event_bus_health : null;
  if (apiState === 'error') return <span className="text-2xs text-warn">{error ?? bi('加载失败', 'load error')}</span>;
  return (
    <LoadingVeil active={apiState === 'loading'}>
      <div className="flex flex-wrap items-start gap-5">
        <Metric label={bi('事件总数', 'Events')} value={(health?.event_records ?? events.length) || '—'} tone="accent" />
        <Metric label={bi('待处理', 'Pending')} value={health?.pending_events ?? '—'} tone="info" />
        <Metric label={bi('死信', 'Dead letters')} value={health?.dead_letter_count ?? '—'} tone={health?.dead_letter_count ? 'warn' : 'pos'} />
        <div className="min-w-[14rem] flex-1 text-2xs text-muted">
          <div className="mb-1 flex items-center gap-2"><StatusChip value={health?.verdict ?? 'UNKNOWN'} /> <span>{bi('后台活动 / 最近事件', 'background activity / latest events')}</span></div>
          <div className="space-y-0.5 text-faint">
            {events.slice(0, 3).map(event => <div key={event.event_id} className="truncate">{event.event_type} · {event.source}</div>)}
            {!events.length && <span>{bi('暂无事件记录', 'no event records')}</span>}
          </div>
        </div>
      </div>
    </LoadingVeil>
  );
}

const TILES: TileDef[] = [
  { id: 'activity', title: 'Background Activity', titleZh: '后台活动', to: '/research/events', render: () => <ActivityTile /> },
  { id: 'runs', title: 'Research Runs', titleZh: '研究运行', to: '/research/runs', render: () => <RunsTile /> },
  { id: 'overview', title: 'Research Funnel', titleZh: '研究漏斗', to: '/research/alpha-zoo', render: () => <OverviewTile /> },
  { id: 'crypto', title: 'Crypto Live', titleZh: '加密实盘', to: '/research/crypto-live', render: () => <CryptoTile /> },
  { id: 'safety', title: 'Safety Primitives', titleZh: '安全原语', to: '/research/risk', render: () => <SafetyTile /> },
  { id: 'optimizer', title: 'De-redundant Set', titleZh: '去冗余选集', to: '/research/population-optimizer', render: () => <OptimizerTile /> },
];

interface Layout { order: string[]; spans: Record<string, number> }   // spans = index into SIZES

function loadLayout(): Layout {
  const defaults = { order: TILES.map(t => t.id), spans: Object.fromEntries(TILES.map(t => [t.id, 1])) };
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const saved = JSON.parse(raw) as Partial<Layout>;
      const savedOrder = Array.isArray(saved.order) ? saved.order.filter(id => TILES.some(tile => tile.id === id)) : [];
      const order = [...savedOrder, ...defaults.order.filter(id => !savedOrder.includes(id))];
      return { order, spans: { ...defaults.spans, ...(saved.spans ?? {}) } };
    }
  } catch { /* ignore */ }
  return defaults;
}

export default function CommandCenter() {
  const [layout, setLayout] = useState<Layout>(loadLayout);
  const [drag, setDrag] = useState<string | null>(null);
  useEffect(() => { try { localStorage.setItem(LS_KEY, JSON.stringify(layout)); } catch { /* ignore */ } }, [layout]);

  const ordered = useMemo(() => layout.order.map(id => TILES.find(t => t.id === id)).filter(Boolean) as TileDef[], [layout.order]);

  function reorder(targetId: string) {
    if (!drag || drag === targetId) return;
    setLayout(l => {
      const order = [...l.order];
      const from = order.indexOf(drag), to = order.indexOf(targetId);
      order.splice(to, 0, order.splice(from, 1)[0]);
      return { ...l, order };
    });
  }
  function cycleSize(id: string) {
    setLayout(l => ({ ...l, spans: { ...l.spans, [id]: ((l.spans[id] ?? 1) + 1) % SIZES.length } }));
  }
  function resetLayout() { setLayout({ order: TILES.map(t => t.id), spans: Object.fromEntries(TILES.map(t => [t.id, 1])) }); }

  return (
    <PageShell>
      <PageHeader title="Command Center" titleZh="指挥中心"
        subtitle={bi('研究全流程一张图:七个阶段连成一条可视化管道,每个节点实时显示该阶段的真实读数;点击进入任一阶段。下方磁贴可拖拽重排 / 点击缩放 · 布局自动保存 · 全部真实数据(只读)',
          'The whole research lifecycle on one canvas: seven stages wired into a visual pipeline, each node showing a live real reading; click to enter any stage. Tiles below: drag to reorder / click to resize · layout auto-saved · all real data (read-only)')}
        actions={<Button onClick={resetLayout} variant="muted">{bi('重置布局', 'reset layout')}</Button>} />

      {/* ── 全流程可视化管道:数据 → 研究 → 验证 → 组合 → 执行 → 治理 ── */}
      <PipelineFlow activeGroup="overview" />

      <SectionHeader title={bi('实时磁贴', 'LIVE TILES')}
        sub={bi('拖拽重排 · ⤢ 缩放 · 打开进入对应页面', 'drag to reorder · ⤢ resize · open jumps to the page')} />

      <div className="grid grid-cols-12 gap-4">
        {ordered.map(tile => {
          const span = SIZES[layout.spans[tile.id] ?? 1];
          const col = ['', 'col-span-3', '', 'col-span-3', 'col-span-4', '', 'col-span-6', '', '', '', '', '', 'col-span-12'][span.w] || 'col-span-6';
          return (
            <div key={tile.id} className={`${col} ${span.h > 1 ? 'row-span-2' : ''}`}
              draggable onDragStart={() => setDrag(tile.id)} onDragEnd={() => setDrag(null)}
              onDragOver={e => { e.preventDefault(); reorder(tile.id); }}>
              <Panel eyebrow={bi('磁贴', 'Tile')} title={bi(tile.titleZh, tile.title)}
                className="terminal-panel" bodyClass="density-compact"
                right={<div className="flex items-center gap-1.5">
                  <button onClick={() => cycleSize(tile.id)} title={bi('缩放', 'resize')}
                    aria-label={bi('缩放磁贴', 'resize tile')}
                    className="rounded border border-line px-1.5 py-0.5 text-2xs text-muted hover:text-ink">⤢</button>
                  <Link to={tile.to} className="text-2xs text-accent hover:underline">{bi('打开', 'open')}</Link>
                  <span className="cursor-grab text-faint" role="img" title={bi('拖动重排', 'drag to reorder')}
                    aria-label={bi('拖动重排', 'drag to reorder')}>⠿</span>
                </div>}>
                <div className={span.h > 1 ? 'min-h-[8rem]' : ''}>{tile.render()}</div>
              </Panel>
            </div>
          );
        })}
      </div>
    </PageShell>
  );
}
