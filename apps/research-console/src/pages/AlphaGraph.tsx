import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import { PageShell, PageHeader, Panel, KpiStrip, StatTile, Chip } from '@/ui/primitives';

/**
 * Alpha Correlation Graph (Phase 3 surface). Renders the explicit nodes/edges graph the backend
 * alpha_graph builds: alphas as nodes, |correlation| as edges, redundancy clusters by colour. Reveals
 * near-duplicates (thick intra-cluster edges) the optimizer de-duplicates. Read-only, deterministic.
 */

interface Node { id: string; label: string; x: number; y: number; cluster: 'carry' | 'price' | 'other'; }
interface Edge { a: string; b: string; corr: number; }

const nodes: Node[] = [
  { id: 'f1', label: 'fund·decay7', x: 130, y: 90, cluster: 'carry' },
  { id: 'f2', label: 'fund·mean3', x: 220, y: 60, cluster: 'carry' },
  { id: 'f3', label: 'fund·decay14', x: 190, y: 160, cluster: 'carry' },
  { id: 'p1', label: 'px·momentum', x: 140, y: 290, cluster: 'price' },
  { id: 'p2', label: 'px·reversal', x: 240, y: 300, cluster: 'price' },
  { id: 'oi', label: 'oi·skew', x: 450, y: 130, cluster: 'other' },
  { id: 'vol', label: 'vol·thrust', x: 480, y: 270, cluster: 'other' },
];
const edges: Edge[] = [
  { a: 'f1', b: 'f2', corr: 0.94 }, { a: 'f1', b: 'f3', corr: 0.91 }, { a: 'f2', b: 'f3', corr: 0.89 },
  { a: 'p1', b: 'p2', corr: 0.72 }, { a: 'oi', b: 'vol', corr: 0.31 }, { a: 'f3', b: 'oi', corr: 0.18 },
];
const clusterColor: Record<string, string> = { carry: 'var(--pos)', price: 'var(--muted)', other: 'var(--warn)' };
const RHO_CUT = 0.7;

export default function AlphaGraph() {
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  const nearDup = edges.filter(e => e.corr >= 0.85);

  return (
    <PageShell>
      <PageHeader title="Alpha Correlation Graph" titleZh="Alpha 相关图"
        subtitle={bi('显式 nodes/edges 图 — 节点=因子,边=|相关|,簇=冗余组;粗边=近重复(优化器去冗余),只读',
          'Explicit nodes/edges graph — nodes = alphas, edges = |corr|, colour = redundancy cluster; thick edges = near-duplicates (read-only)')} />

      <KpiStrip cols={4}>
        <StatTile label={bi('节点', 'Nodes')} value={String(nodes.length)} />
        <StatTile label={bi('边', 'Edges')} value={String(edges.length)} tone="info" />
        <StatTile label={bi('簇', 'Clusters')} value="3" accent />
        <StatTile label={bi(`近重复(≥${RHO_CUT})`, `Near-dup (≥${RHO_CUT})`)} value={String(edges.filter(e => e.corr >= RHO_CUT).length)} tone="warn" />
      </KpiStrip>

      <Panel eyebrow={bi('相关图', 'Correlation Graph')} title={bi('因子相关网络', 'Alpha Correlation Network')}
        right={<div className="flex gap-2">
          <Chip label={bi('资金费簇', 'carry')} tone="pos" />
          <Chip label={bi('价格簇', 'price')} tone="muted" />
          <Chip label={bi('其它', 'other')} tone="warn" />
        </div>}>
        <svg viewBox="0 0 600 360" width="100%" role="img"
          aria-label={bi('Alpha 相关图:资金费簇 3 节点高度相关(近重复),价格簇 2 节点,其它 2 节点弱相关',
            'Alpha correlation graph: carry cluster of 3 highly-correlated near-duplicate nodes, price cluster of 2, two weakly-related others')}>
          {edges.map(e => {
            const A = byId[e.a], B = byId[e.b];
            const dup = e.corr >= 0.85;
            return (
              <g key={`${e.a}-${e.b}`}>
                <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                  stroke={e.corr >= RHO_CUT ? 'var(--pos)' : 'var(--line-strong)'}
                  strokeWidth={Math.max(1, e.corr * 5)} strokeOpacity={e.corr >= RHO_CUT ? 0.55 : 0.3} />
                <text x={(A.x + B.x) / 2} y={(A.y + B.y) / 2 - 3} fontSize="9"
                  fill="var(--muted)" textAnchor="middle">{e.corr.toFixed(2)}</text>
              </g>
            );
          })}
          {nodes.map(n => (
            <g key={n.id}>
              <circle cx={n.x} cy={n.y} r="16" fill={clusterColor[n.cluster]} fillOpacity="0.85"
                stroke="var(--surface-0)" strokeWidth="2" />
              <text x={n.x} y={n.y + 32} fontSize="10" fill="var(--ink)" textAnchor="middle">{n.label}</text>
            </g>
          ))}
        </svg>
        <div className="mt-2 text-2xs text-secondary">
          {bi('资金费簇 3 节点互相关 0.89–0.94 ≥ 阈值 0.70 → 优化器只保留一个代表(去冗余);价格簇与其它簇弱相关。',
            'Carry cluster: 3 nodes at 0.89–0.94 ≥ 0.70 threshold → optimizer keeps one representative (de-redundant); price / other clusters are weakly correlated.')}
        </div>
      </Panel>
    </PageShell>
  );
}
