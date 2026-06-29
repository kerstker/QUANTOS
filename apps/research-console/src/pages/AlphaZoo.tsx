import React, { useState, useMemo } from 'react';
import { fetchAlphaZooSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { AlphaZooPayload, AlphaZooRow, ZooDimension } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, StatusChip, Chip,
} from '@/ui/primitives';

/**
 * Alpha Zoo — the multi-dimensional factor-analysis cockpit. Each alpha carries its FULL evaluation across
 * ~12 real dimensions (IC / ICIR / t-stat / hit-rate / net-Sharpe / Deflated-SR / PBO / turnover /
 * marginal-IR / crowding / capacity / breadth), sourced from the immutable experiment_registry. Sort by any
 * dimension; click a row to drill into all dimensions grouped. Read-only (L2_DRY_RUN_ONLY); falls back to a
 * synthetic catalog when no registry.
 */

// curated table columns (the rest live in the per-alpha drill-down)
const TABLE_DIMS = ['ic', 'net_sharpe', 'deflated_sharpe', 'pbo', 'turnover', 'marginal_ir'] as const;

const FALLBACK_DIMS: ZooDimension[] = [
  { key: 'ic', zh: 'IC', en: 'IC', group: '预测力', fmt: 'signed3', good: 'high' },
  { key: 'icir', zh: 'ICIR', en: 'ICIR', group: '预测力', fmt: 'signed2', good: 'high' },
  { key: 'ic_tstat', zh: 'IC t值', en: 'IC t-stat', group: '预测力', fmt: 'signed2', good: 'high' },
  { key: 'hit_rate', zh: '命中率', en: 'Hit rate', group: '预测力', fmt: 'pct', good: 'high' },
  { key: 'net_sharpe', zh: '净夏普', en: 'Net Sharpe', group: '业绩', fmt: 'signed2', good: 'high' },
  { key: 'deflated_sharpe', zh: '紧缩夏普', en: 'Deflated SR', group: '过拟合', fmt: 'signed2', good: 'high' },
  { key: 'pbo', zh: 'PBO', en: 'PBO', group: '过拟合', fmt: 'pct', good: 'low' },
  { key: 'turnover', zh: '换手', en: 'Turnover', group: '成本', fmt: 'signed2', good: 'low' },
  { key: 'marginal_ir', zh: '增量IR', en: 'Marginal IR', group: '正交', fmt: 'signed2', good: 'high' },
  { key: 'crowding_score', zh: '拥挤度', en: 'Crowding', group: '拥挤/容量', fmt: 'signed2', good: 'low' },
  { key: 'capacity_adj_sharpe', zh: '容量调整夏普', en: 'Cap-adj SR', group: '拥挤/容量', fmt: 'signed2', good: 'high' },
  { key: 'mean_breadth', zh: '广度', en: 'Breadth', group: '广度', fmt: 'num1', good: 'high' },
];

const mockRows: AlphaZooRow[] = [
  { alpha_id: 'ax_funding_decay7', name: 'funding carry · decay7', generator: 'cs_genome', status: 'APPROVED', cluster_id: 'cl_carry', tags: ['carry', 'low-turnover'], verdict: 'PASS', ic: 0.061, icir: 0.41, ic_tstat: 3.1, hit_rate: 0.58, net_sharpe: 2.18, deflated_sharpe: 0.92, pbo: 0.08, turnover: 0.27, marginal_ir: 1.13, crowding_score: 0.21, capacity_adj_sharpe: 2.0, mean_breadth: 58 },
  { alpha_id: 'ax_oi_skew', name: 'open-interest skew', generator: 'mcts', status: 'DEGRADED', cluster_id: 'cl_oi', tags: ['positioning'], verdict: 'WARN', ic: 0.018, icir: 0.09, ic_tstat: 0.7, hit_rate: 0.51, net_sharpe: 0.30, deflated_sharpe: 0.41, pbo: 0.34, turnover: 0.62, marginal_ir: 0.4, crowding_score: 0.55, capacity_adj_sharpe: 0.2, mean_breadth: 24 },
  { alpha_id: 'ax_px_reversal', name: 'price reversal', generator: 'evolutionary', status: 'REJECTED', cluster_id: 'cl_px', tags: ['price', 'overfit-risk'], verdict: 'FAIL', ic: -0.004, icir: -0.02, ic_tstat: -0.3, hit_rate: 0.49, net_sharpe: -0.37, deflated_sharpe: 0.12, pbo: 0.71, turnover: 0.81, marginal_ir: -0.2, crowding_score: 0.4, capacity_adj_sharpe: -0.4, mean_breadth: 30 },
];

const fallback: AlphaZooPayload = {
  source: 'FRONTEND_ALPHAZOO_FALLBACK', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', rows: mockRows, dimensions: FALLBACK_DIMS,
};

function fmtDim(v: unknown, fmt: string): string {
  if (typeof v !== 'number' || Number.isNaN(v)) return '—';
  if (fmt === 'pct') return `${Math.round(v * 100)}%`;
  if (fmt === 'num1') return v.toFixed(1);
  if (fmt === 'signed3') return (v >= 0 ? '+' : '') + v.toFixed(3);
  return (v >= 0 ? '+' : '') + v.toFixed(2);
}

// conditional colour by the dimension's "good" direction (sign-based heuristic, readable not precise)
function toneCls(v: unknown, good: string): string {
  if (typeof v !== 'number') return 'text-faint';
  if (good === 'low') return v <= 0.3 ? 'text-pos' : v >= 0.6 ? 'text-neg' : 'text-muted';
  return v > 0.5 ? 'text-pos' : v > 0 ? 'text-warn' : 'text-neg';
}

export default function AlphaZoo() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<AlphaZooPayload>(
    s => fetchAlphaZooSnapshot(s));
  const data = usingBackend ? payload! : fallback;
  const allRows = data.rows;
  const dims = data.dimensions?.length ? data.dimensions : FALLBACK_DIMS;
  const dimByKey = useMemo(() => Object.fromEntries(dims.map(d => [d.key, d])), [dims]);

  const [gen, setGen] = useState('全部');
  const [promotableOnly, setPromotableOnly] = useState(false);
  const [sortKey, setSortKey] = useState<string>('deflated_sharpe');
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');
  const [openId, setOpenId] = useState<string | null>(null);

  const GENERATORS = useMemo(() => ['全部', ...Array.from(new Set(allRows.map(r => r.generator)))], [allRows]);

  const rows = useMemo(() => {
    const filtered = allRows.filter(r =>
      (gen === '全部' || r.generator === gen) &&
      (!promotableOnly || (typeof r.deflated_sharpe === 'number' && r.deflated_sharpe >= 0.6)));
    const sign = sortDir === 'desc' ? -1 : 1;
    const miss = sortDir === 'desc' ? -Infinity : Infinity;   // nulls sort last
    return [...filtered].sort((a, b) => {
      const av = typeof a[sortKey] === 'number' ? (a[sortKey] as number) : miss;
      const bv = typeof b[sortKey] === 'number' ? (b[sortKey] as number) : miss;
      return sign * (bv - av);
    });
  }, [allRows, gen, promotableOnly, sortKey, sortDir]);

  function sortOn(key: string) {
    if (sortKey === key) setSortDir(d => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(key); setSortDir('desc'); }
  }
  const arrow = (key: string) => (sortKey === key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : '');

  const groups = useMemo(() => {
    const g: Record<string, ZooDimension[]> = {};
    for (const d of dims) (g[d.group] ||= []).push(d);
    return g;
  }, [dims]);

  const promotable = allRows.filter(r => typeof r.deflated_sharpe === 'number' && r.deflated_sharpe >= 0.6).length;

  return (
    <PageShell>
      <PageHeader title="Alpha Zoo" titleZh="Alpha 资产库"
        subtitle={bi('多维因子分析驾驶舱:每个因子 ~12 个真实评估维度,可按任意维度排序,点行钻取全维度',
          'Multi-dimensional factor cockpit — ~12 real evaluation dims per alpha; sort by any, click a row to drill in')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={data.source} error={error}
        realData={usingBackend && data.data_provenance === 'EXPERIMENT_REGISTRY'} />

      <KpiStrip cols={4}>
        <StatTile label={bi('资产总数', 'Total')} value={String(allRows.length)} />
        <StatTile label={bi('可晋级(DSR≥0.6)', 'Promotable')} value={String(promotable)} tone="pos" />
        <StatTile label={bi('分析维度', 'Dimensions')} value={String(dims.length)} tone="info" />
        <StatTile label={bi('当前筛出', 'Filtered')} value={String(rows.length)} accent />
      </KpiStrip>

      <Panel eyebrow={bi('多维检索', 'Multi-dim search')} title={bi('Alpha 资产', 'Alpha Assets')}
        right={<Chip label={bi(`按 ${dimByKey[sortKey]?.zh ?? sortKey} 排序`, `sorted by ${dimByKey[sortKey]?.en ?? sortKey}`)} tone="info" />}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {GENERATORS.map(g => (
            <button key={g} onClick={() => setGen(g)}
              className={`rounded-md border px-2.5 py-1 text-xs transition ${gen === g ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-ink'}`}>{g}</button>
          ))}
          <button onClick={() => setPromotableOnly(v => !v)}
            className={`rounded-md border px-2.5 py-1 text-xs transition ${promotableOnly ? 'border-pos bg-pos/10 text-pos' : 'border-line text-muted hover:text-ink'}`}>
            {bi('仅可晋级', 'promotable only')}</button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-2xs">
            <thead>
              <tr className="border-b border-line text-faint">
                <th className="px-2 py-1.5 text-left font-medium">{bi('因子', 'Alpha')}</th>
                <th className="px-2 py-1.5 text-left font-medium">{bi('状态', 'Status')}</th>
                {TABLE_DIMS.map(k => (
                  <th key={k} onClick={() => sortOn(k)}
                    className="cursor-pointer select-none px-2 py-1.5 text-right font-medium hover:text-ink">
                    {bi(dimByKey[k]?.zh ?? k, dimByKey[k]?.en ?? k)}{arrow(k)}
                  </th>
                ))}
                <th className="px-2 py-1.5 text-right font-medium">{bi('裁决', 'Verdict')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <React.Fragment key={r.alpha_id}>
                  <tr onClick={() => setOpenId(openId === r.alpha_id ? null : r.alpha_id)}
                    className={`cursor-pointer border-b border-line/50 hover:bg-elevated/40 ${openId === r.alpha_id ? 'bg-elevated/40' : ''}`}>
                    <td className="px-2 py-1.5">
                      <div className="flex flex-col">
                        <span className="text-ink">{r.name}</span>
                        <span className="mono text-faint">{r.generator} · {r.cluster_id}</span>
                      </div>
                    </td>
                    <td className="px-2 py-1.5"><StatusChip value={r.status} /></td>
                    {TABLE_DIMS.map(k => (
                      <td key={k} className={`tnum px-2 py-1.5 text-right ${toneCls(r[k], dimByKey[k]?.good ?? 'high')}`}>
                        {fmtDim(r[k], dimByKey[k]?.fmt ?? 'signed2')}
                      </td>
                    ))}
                    <td className="px-2 py-1.5 text-right"><StatusChip value={r.verdict ?? '—'} /></td>
                  </tr>
                  {openId === r.alpha_id && (
                    <tr className="border-b border-line bg-inset/40">
                      <td colSpan={3 + TABLE_DIMS.length}>
                        <div className="px-3 py-3">
                          <div className="mb-2 flex flex-wrap items-center gap-2">
                            <span className="mono text-2xs text-faint">{r.alpha_id}</span>
                            {r.tags.map(t => <Chip key={t} label={t} tone="muted" />)}
                          </div>
                          <div className="grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-3">
                            {Object.entries(groups).map(([group, gdims]) => (
                              <div key={group}>
                                <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-faint">{group}</div>
                                {gdims.map(d => (
                                  <div key={d.key} className="flex items-center justify-between gap-2 py-0.5">
                                    <span className="text-2xs text-muted">{bi(d.zh, d.en)}</span>
                                    <span className={`tnum text-2xs ${toneCls(r[d.key], d.good)}`}>{fmtDim(r[d.key], d.fmt)}</span>
                                  </div>
                                ))}
                              </div>
                            ))}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </PageShell>
  );
}
