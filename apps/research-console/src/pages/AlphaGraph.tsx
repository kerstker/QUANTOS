import React from 'react';
import { fetchAlphaGraphSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { AlphaGraphPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, Chip } from '@/ui/primitives';

/**
 * Alpha Correlation Graph. Renders the explicit nodes/edges graph the backend builds with the real
 * services/alpha correlation_graph engine over the real per-factor return series: alphas as nodes,
 * |correlation| as edges, redundancy clusters (connected components ≥ rho_cut) by colour. Thick edges =
 * near-duplicates the optimizer de-duplicates. Read-only, deterministic; synthetic fallback when no snapshot.
 */

// cluster colour palette (cl_0, cl_1, …) — cycles for many clusters
const PALETTE = ['var(--pos)', 'var(--accent)', 'var(--warn)', 'var(--info)', 'var(--special)', 'var(--cyan)', 'var(--neg)', 'var(--muted)'];
function clusterColor(cluster: string): string {
  const i = parseInt(cluster.replace(/\D/g, ''), 10) || 0;
  return PALETTE[i % PALETTE.length];
}

const fallback: AlphaGraphPayload = {
  source: 'FRONTEND_ALPHAGRAPH_FALLBACK', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', rho_cut: 0.7, n_near_dup: 3,
  clusters: [{ name: 'cl_0', count: 3 }, { name: 'cl_1', count: 2 }, { name: 'cl_2', count: 2 }],
  nodes: [
    { id: 'f1', label: 'fund·decay7', x: 130, y: 90, cluster: 'cl_0' },
    { id: 'f2', label: 'fund·mean3', x: 220, y: 60, cluster: 'cl_0' },
    { id: 'f3', label: 'fund·decay14', x: 190, y: 160, cluster: 'cl_0' },
    { id: 'p1', label: 'px·momentum', x: 140, y: 290, cluster: 'cl_1' },
    { id: 'p2', label: 'px·reversal', x: 240, y: 300, cluster: 'cl_1' },
    { id: 'oi', label: 'oi·skew', x: 450, y: 130, cluster: 'cl_2' },
    { id: 'vol', label: 'vol·thrust', x: 480, y: 270, cluster: 'cl_2' },
  ],
  edges: [
    { a: 'f1', b: 'f2', corr: 0.94 }, { a: 'f1', b: 'f3', corr: 0.91 }, { a: 'f2', b: 'f3', corr: 0.89 },
    { a: 'p1', b: 'p2', corr: 0.72 }, { a: 'oi', b: 'vol', corr: 0.31 },
  ],
};

export default function AlphaGraph() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<AlphaGraphPayload>(
    s => fetchAlphaGraphSnapshot(s));
  const data = usingBackend ? payload! : fallback;
  const { nodes, edges, clusters, rho_cut: rho, n_near_dup } = data;
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));

  return (
    <PageShell>
      <PageHeader title="Alpha Correlation Graph" titleZh="Alpha 相关图"
        subtitle={bi('显式 nodes/edges 图 — 节点=因子,边=|相关|,簇=冗余组(连通分量);粗边=近重复(优化器去冗余),只读',
          'Explicit nodes/edges graph — nodes = alphas, edges = |corr|, colour = redundancy cluster; thick edges = near-duplicates (read-only)')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={data.source} error={error}
        realData={data.data_provenance === 'REAL_CRYPTO_SERIES'} />

      <KpiStrip cols={4}>
        <StatTile label={bi('节点', 'Nodes')} value={String(nodes.length)} />
        <StatTile label={bi('边', 'Edges')} value={String(edges.length)} tone="info" />
        <StatTile label={bi('簇', 'Clusters')} value={String(clusters.length)} accent />
        <StatTile label={bi(`近重复(≥${rho})`, `Near-dup (≥${rho})`)} value={String(n_near_dup)} tone="warn" />
      </KpiStrip>

      <Panel eyebrow={bi('相关图', 'Correlation Graph')} title={bi('因子相关网络', 'Alpha Correlation Network')}
        right={<div className="flex flex-wrap gap-1.5">
          {clusters.map(c => (
            <Chip key={c.name} label={`${c.name}·${c.count}`} tone="muted" />
          ))}
        </div>}>
        <svg viewBox="0 0 600 360" width="100%" role="img"
          aria-label={bi(`Alpha 相关图:${nodes.length} 节点,${clusters.length} 个冗余簇,${n_near_dup} 条近重复边`,
            `Alpha correlation graph: ${nodes.length} nodes, ${clusters.length} redundancy clusters, ${n_near_dup} near-duplicate edges`)}>
          {edges.map(e => {
            const A = byId[e.a], B = byId[e.b];
            if (!A || !B) return null;
            return (
              <g key={`${e.a}-${e.b}`}>
                <line x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                  stroke={e.corr >= rho ? 'var(--pos)' : 'var(--line-strong)'}
                  strokeWidth={Math.max(1, e.corr * 5)} strokeOpacity={e.corr >= rho ? 0.55 : 0.22} />
                {e.corr >= rho && (
                  <text x={(A.x + B.x) / 2} y={(A.y + B.y) / 2 - 3} fontSize="9"
                    fill="var(--muted)" textAnchor="middle">{e.corr.toFixed(2)}</text>
                )}
              </g>
            );
          })}
          {nodes.map(n => (
            <g key={n.id}>
              <circle cx={n.x} cy={n.y} r="15" fill={clusterColor(n.cluster)} fillOpacity="0.85"
                stroke="var(--surface)" strokeWidth="2" />
              <text x={n.x} y={n.y + 28} fontSize="9" fill="var(--ink)" textAnchor="middle">{n.label}</text>
            </g>
          ))}
        </svg>
        <div className="mt-2 text-2xs text-secondary">
          {bi(`连通分量(|corr| ≥ ${rho})即冗余簇 — 同簇因子近重复,优化器只保留一个代表;簇间弱相关。`,
            `Connected components at |corr| ≥ ${rho} are the redundancy clusters — same-cluster factors are near-duplicates (optimizer keeps one representative); clusters are weakly correlated.`)}
        </div>
      </Panel>
    </PageShell>
  );
}
