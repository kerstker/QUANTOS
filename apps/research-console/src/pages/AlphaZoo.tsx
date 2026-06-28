import React, { useState, useMemo } from 'react';
import { fetchAlphaZooSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { AlphaZooPayload, AlphaZooRow } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, StatusChip, Chip, type Column,
} from '@/ui/primitives';
import { fixed } from '@/ui/format';

/**
 * Alpha Zoo. The searchable / filterable catalog of rich Alpha Assets — filter by generator, status,
 * cluster, and metric floors. Backed by the real research snapshot's factor catalog (real generator /
 * state / DSR / net-Sharpe); falls back to a synthetic catalog when no snapshot. Read-only (L2_DRY_RUN_ONLY).
 */

type AlphaRow = AlphaZooRow;

const mockRows: AlphaRow[] = [
  { alpha_id: 'ax_funding_decay7', name: 'funding carry · decay7', generator: 'cs_genome', status: 'APPROVED', cluster_id: 'cl_carry', deflated_sharpe: 0.92, net_sharpe: 2.18, tags: ['carry', 'funding'] },
  { alpha_id: 'ax_funding_mean3', name: 'funding carry · mean3', generator: 'cs_genome', status: 'SHADOW', cluster_id: 'cl_carry', deflated_sharpe: 0.88, net_sharpe: 2.12, tags: ['carry', 'funding'] },
  { alpha_id: 'ax_funding_decay14', name: 'funding carry · decay14', generator: 'gp', status: 'PAPER_TRADING', cluster_id: 'cl_carry', deflated_sharpe: 0.81, net_sharpe: 1.80, tags: ['carry'] },
  { alpha_id: 'ax_oi_skew', name: 'open-interest skew', generator: 'mcts', status: 'DEGRADED', cluster_id: 'cl_oi', deflated_sharpe: 0.41, net_sharpe: 0.30, tags: ['positioning'] },
  { alpha_id: 'ax_vol_thrust', name: 'volume thrust', generator: 'cs_genome', status: 'VALIDATED', cluster_id: 'cl_px', deflated_sharpe: 0.55, net_sharpe: 0.32, tags: ['volume'] },
  { alpha_id: 'ax_px_reversal', name: 'price reversal', generator: 'evolutionary', status: 'REJECTED', cluster_id: 'cl_px', deflated_sharpe: 0.12, net_sharpe: -0.37, tags: ['price'] },
];

const fallback: AlphaZooPayload = {
  source: 'FRONTEND_ALPHAZOO_FALLBACK', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', rows: mockRows,
};

export default function AlphaZoo() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<AlphaZooPayload>(
    s => fetchAlphaZooSnapshot(s));
  const data = usingBackend ? payload! : fallback;
  const allRows = data.rows;
  const [gen, setGen] = useState('全部');
  const [promotableOnly, setPromotableOnly] = useState(false);

  const GENERATORS = useMemo(() => ['全部', ...Array.from(new Set(allRows.map(r => r.generator)))], [allRows]);
  const rows = useMemo(() => allRows.filter(r =>
    (gen === '全部' || r.generator === gen) &&
    (!promotableOnly || r.deflated_sharpe >= 0.6)), [allRows, gen, promotableOnly]);

  const cols: Column<AlphaRow>[] = [
    { key: 'alpha', header: bi('因子', 'Alpha'), render: r => (
      <div className="flex flex-col"><span className="text-ink">{r.name}</span>
        <span className="mono text-2xs text-faint">{r.alpha_id}</span></div>) },
    { key: 'gen', header: bi('生成器', 'Generator'), render: r => <Chip label={r.generator} tone="muted" /> },
    { key: 'status', header: bi('状态', 'Status'), render: r => <StatusChip value={r.status} /> },
    { key: 'cluster', header: bi('簇', 'Cluster'), render: r => <span className="mono text-2xs text-muted">{r.cluster_id}</span> },
    { key: 'dsr', header: bi('Deflated 夏普', 'DSR'), align: 'right', mono: true, render: r => (
      <span className={r.deflated_sharpe >= 0.6 ? 'text-pos' : 'text-muted'}>{fixed(r.deflated_sharpe)}</span>) },
    { key: 'ns', header: bi('净夏普', 'Net Sharpe'), align: 'right', mono: true, render: r => (
      <span className={r.net_sharpe >= 1 ? 'text-pos' : r.net_sharpe > 0 ? 'text-warn' : 'text-neg'}>{fixed(r.net_sharpe)}</span>) },
    { key: 'tags', header: bi('标签', 'Tags'), render: r => (
      <div className="flex flex-wrap gap-1">{r.tags.map(t => <Chip key={t} label={t} tone="muted" />)}</div>) },
  ];

  return (
    <PageShell>
      <PageHeader title="Alpha Zoo" titleZh="Alpha 资产库"
        subtitle={bi('按生成器 / 状态 / 簇 / 指标筛选的富 Alpha 资产目录(只读)',
          'Filterable catalog of rich Alpha Assets — by generator / status / cluster / metric (read-only)')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={data.source} error={error}
        realData={data.data_provenance === 'REAL_CRYPTO_SERIES'} />

      <KpiStrip cols={4}>
        <StatTile label={bi('资产总数', 'Total')} value={String(allRows.length)} />
        <StatTile label={bi('可晋级(DSR≥0.6)', 'Promotable')} value={String(allRows.filter(r => r.deflated_sharpe >= 0.6).length)} tone="pos" />
        <StatTile label={bi('簇数', 'Clusters')} value={String(new Set(allRows.map(r => r.cluster_id)).size)} tone="info" />
        <StatTile label={bi('当前筛出', 'Filtered')} value={String(rows.length)} accent />
      </KpiStrip>

      <Panel eyebrow={bi('检索', 'Search')} title={bi('Alpha 资产', 'Alpha Assets')}
        right={<Chip label={bi(`${rows.length} 项`, `${rows.length} shown`)} tone="info" />}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {GENERATORS.map(g => (
            <button key={g} onClick={() => setGen(g)}
              className={`rounded-md border px-2.5 py-1 text-xs transition ${gen === g ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-ink'}`}>{g}</button>
          ))}
          <button onClick={() => setPromotableOnly(v => !v)}
            className={`rounded-md border px-2.5 py-1 text-xs transition ${promotableOnly ? 'border-pos bg-pos/10 text-pos' : 'border-line text-muted hover:text-ink'}`}>
            {bi('仅可晋级', 'promotable only')}</button>
        </div>
        <DataTable columns={cols} rows={rows} getKey={r => r.alpha_id} />
      </Panel>
    </PageShell>
  );
}
