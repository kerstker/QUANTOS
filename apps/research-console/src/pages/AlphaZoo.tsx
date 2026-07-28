import React, { useState, useMemo } from 'react';
import { fetchAlphaZooSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { AlphaZooPayload, AlphaZooRow, ZooDimension } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, StatusChip, Chip, LoadingVeil, PillToggle,
} from '@/ui/primitives';
import { Donut } from '@/ui/charts';
import { statusTone } from '@/ui/format';

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
  { key: 'paper_ic', zh: '纸面IC', en: 'Paper IC', group: '时间/活体征', fmt: 'signed3', good: 'high' },
  { key: 'paper_sharpe', zh: '纸面夏普', en: 'Paper SR', group: '时间/活体征', fmt: 'signed2', good: 'high' },
  { key: 'perf_gap', zh: '回测-纸面差', en: 'BT-paper gap', group: '时间/活体征', fmt: 'signed2', good: 'low' },
  { key: 'n_paper_periods', zh: '纸面期数', en: 'Paper periods', group: '时间/活体征', fmt: 'num1', good: 'high' },
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

function verdictColor(v: string | undefined): string {
  return v === 'PASS' ? 'var(--pos)' : v === 'FAIL' ? 'var(--neg)'
    : v === 'WARN' ? 'var(--warn)' : 'var(--muted)';
}

const numOf = (v: unknown): number => (typeof v === 'number' ? v : NaN);

// Honest one-click multi-dimensional screens (factor screener). Each is a predicate over a real dimension;
// a missing value (NaN) fails the comparison -> the factor is screened OUT (fail-closed, not assumed-good).
const SCREENS: { key: string; zh: string; en: string; test: (r: AlphaZooRow) => boolean }[] = [
  { key: 'promotable', zh: '可晋级 DSR≥0.6', en: 'DSR≥0.6', test: r => numOf(r.deflated_sharpe) >= 0.6 },
  { key: 'lowturn', zh: '低换手 <0.5', en: 'turnover<0.5', test: r => numOf(r.turnover) < 0.5 },
  { key: 'robust', zh: '抗过拟合 PBO<0.3', en: 'PBO<0.3', test: r => numOf(r.pbo) <= 0.3 },
  { key: 'orthogonal', zh: '正交 增量IR>0.3', en: 'mIR>0.3', test: r => numOf(r.marginal_ir) > 0.3 },
  { key: 'positiveIC', zh: '正 IC', en: 'IC>0', test: r => numOf(r.ic) > 0 },
];

// Multi-dimensional scatter: each factor a point at (xDim, yDim), coloured by verdict, with median
// quadrant guides. Click a point to drill into that factor (shared openId with the table). Points whose
// chosen dimension is null are skipped (honest — not plotted at a fake 0).
function DimScatter({ rows, dimByKey, x, y, openId, onPick }: {
  rows: AlphaZooRow[]; dimByKey: Record<string, ZooDimension>; x: string; y: string;
  openId: string | null; onPick: (id: string | null) => void;
}) {
  const W = 680, H = 300, padL = 50, padR = 18, padT = 14, padB = 40;
  const dx = dimByKey[x], dy = dimByKey[y];
  const pts = rows
    .filter(r => typeof r[x] === 'number' && typeof r[y] === 'number')
    .map(r => ({ id: r.alpha_id, name: r.name, x: r[x] as number, y: r[y] as number, verdict: r.verdict }));
  if (!pts.length) return <div className="py-8 text-center text-2xs text-faint">{bi('选中维度无可绘点(值为空)', 'no plottable points (selected dims are null)')}</div>;
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
  const xlo = Math.min(...xs), xhi = Math.max(...xs), ylo = Math.min(...ys), yhi = Math.max(...ys);
  const sx = (v: number) => padL + (xhi === xlo ? 0.5 : (v - xlo) / (xhi - xlo)) * (W - padL - padR);
  const sy = (v: number) => (H - padB) - (yhi === ylo ? 0.5 : (v - ylo) / (yhi - ylo)) * (H - padT - padB);
  const med = (a: number[]) => { const s = [...a].sort((m, n) => m - n); return s[Math.floor(s.length / 2)]; };
  const mx = sx(med(xs)), my = sy(med(ys));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="multi-dimensional factor scatter">
      <line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke="var(--border)" />
      <line x1={padL} y1={padT} x2={padL} y2={H - padB} stroke="var(--border)" />
      <line x1={mx} y1={padT} x2={mx} y2={H - padB} stroke="var(--border)" strokeDasharray="3 3" opacity="0.5" />
      <line x1={padL} y1={my} x2={W - padR} y2={my} stroke="var(--border)" strokeDasharray="3 3" opacity="0.5" />
      {pts.map(p => (
        <circle key={p.id} cx={sx(p.x)} cy={sy(p.y)} r={openId === p.id ? 6 : 4}
          fill={verdictColor(p.verdict)} fillOpacity={openId === p.id ? 1 : 0.7}
          stroke={openId === p.id ? 'var(--text-primary)' : 'none'} strokeWidth="1.5"
          style={{ cursor: 'pointer' }} onClick={() => onPick(openId === p.id ? null : p.id)}>
          <title>{p.name} — {dx?.zh} {fmtDim(p.x, dx?.fmt ?? '')}, {dy?.zh} {fmtDim(p.y, dy?.fmt ?? '')}</title>
        </circle>
      ))}
      <text x={(padL + W - padR) / 2} y={H - 8} textAnchor="middle" fontSize="11" fill="var(--text-secondary)">{dx?.zh} / {dx?.en}</text>
      <text x={16} y={(padT + H - padB) / 2} textAnchor="middle" fontSize="11" fill="var(--text-secondary)" transform={`rotate(-90 16 ${(padT + H - padB) / 2})`}>{dy?.zh} / {dy?.en}</text>
      <text x={padL} y={H - padB + 14} fontSize="10" fill="var(--muted)">{fmtDim(xlo, dx?.fmt ?? '')}</text>
      <text x={W - padR} y={H - padB + 14} textAnchor="end" fontSize="10" fill="var(--muted)">{fmtDim(xhi, dx?.fmt ?? '')}</text>
      <text x={padL - 5} y={H - padB} textAnchor="end" fontSize="10" fill="var(--muted)">{fmtDim(ylo, dy?.fmt ?? '')}</text>
      <text x={padL - 5} y={padT + 8} textAnchor="end" fontSize="10" fill="var(--muted)">{fmtDim(yhi, dy?.fmt ?? '')}</text>
    </svg>
  );
}

export default function AlphaZoo() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<AlphaZooPayload>(
    s => fetchAlphaZooSnapshot(s));
  const data = usingBackend ? payload! : fallback;
  const allRows = data.rows;
  const dims = data.dimensions?.length ? data.dimensions : FALLBACK_DIMS;
  const dimByKey = useMemo(() => Object.fromEntries(dims.map(d => [d.key, d])), [dims]);

  const [gen, setGen] = useState('全部');
  const [screens, setScreens] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState<string>('deflated_sharpe');
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc');
  const [openId, setOpenId] = useState<string | null>(null);
  const [xDim, setXDim] = useState('turnover');
  const [yDim, setYDim] = useState('deflated_sharpe');

  const GENERATORS = useMemo(() => ['全部', ...Array.from(new Set(allRows.map(r => r.generator)))], [allRows]);

  const rows = useMemo(() => {
    const active = SCREENS.filter(s => screens.has(s.key));
    const filtered = allRows.filter(r =>
      (gen === '全部' || r.generator === gen) && active.every(s => s.test(r)));
    const sign = sortDir === 'desc' ? -1 : 1;
    const miss = sortDir === 'desc' ? -Infinity : Infinity;   // nulls sort last
    return [...filtered].sort((a, b) => {
      const av = typeof a[sortKey] === 'number' ? (a[sortKey] as number) : miss;
      const bv = typeof b[sortKey] === 'number' ? (b[sortKey] as number) : miss;
      return sign * (bv - av);
    });
  }, [allRows, gen, screens, sortKey, sortDir]);

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

  // lifecycle composition (migrated from the retired Alpha Factory page, 2026-07-09 nav reorg —
  // same donut, now driven by the full 全量 rows instead of a 12-candidate subset).
  const lifecycleSegments = useMemo(() => {
    const counts = allRows.reduce<Record<string, number>>((acc, r) => {
      acc[r.status] = (acc[r.status] ?? 0) + 1;
      return acc;
    }, {});
    return Object.entries(counts).map(([label, value]) => ({ label, value, tone: statusTone(label) }));
  }, [allRows]);

  return (
    <PageShell>
      <PageHeader title="Alpha Zoo" titleZh="Alpha 资产库"
        subtitle={bi('多维因子分析驾驶舱:每个因子 ~12 个真实评估维度,可按任意维度排序,点行钻取全维度',
          'Multi-dimensional factor cockpit — ~12 real evaluation dims per alpha; sort by any, click a row to drill in')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={data.source} error={error}
        realData={usingBackend && data.data_provenance === 'EXPERIMENT_REGISTRY'} />

      <LoadingVeil active={apiState === 'loading'}>
      <KpiStrip cols={4}>
        <StatTile label={bi('资产总数', 'Total')} value={String(allRows.length)} />
        <StatTile label={bi('可晋级(DSR≥0.6)', 'Promotable')} value={String(promotable)} tone="pos" />
        <StatTile label={bi('分析维度', 'Dimensions')} value={String(dims.length)} tone="info" />
        <StatTile label={bi('当前筛出', 'Filtered')} value={String(rows.length)} accent />
      </KpiStrip>

      <Panel eyebrow={bi('生命周期构成', 'Lifecycle composition')} title={bi('全量资产的生命周期状态分布', 'Lifecycle-state distribution across all assets')}>
        <div className="flex items-center gap-4">
          <Donut size={132} segments={lifecycleSegments} />
          <div className="flex flex-col gap-1 text-2xs">
            {lifecycleSegments.map(s => (
              <span key={s.label} className="flex items-center gap-1.5">
                <StatusChip value={s.label} /><span className="text-muted">× {s.value}</span>
              </span>
            ))}
          </div>
        </div>
      </Panel>

      <Panel eyebrow={bi('多维散点', 'Multi-dim scatter')} title={bi('因子分布(任选两维)', 'Factor map (pick any two dims)')}
        right={
          <div className="flex items-center gap-2 text-xs">
            <label className="flex items-center gap-1.5">
              <span className="text-faint">X</span>
              <select value={xDim} onChange={e => setXDim(e.target.value)}
                aria-label={bi('X 轴维度', 'X-axis dimension')}
                className="rounded-md border border-line bg-inset/40 px-2 py-1 text-xs text-ink">
                {dims.map(d => <option key={d.key} value={d.key}>{d.zh}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1.5">
              <span className="text-faint">Y</span>
              <select value={yDim} onChange={e => setYDim(e.target.value)}
                aria-label={bi('Y 轴维度', 'Y-axis dimension')}
                className="rounded-md border border-line bg-inset/40 px-2 py-1 text-xs text-ink">
                {dims.map(d => <option key={d.key} value={d.key}>{d.zh}</option>)}
              </select>
            </label>
          </div>
        }>
        <DimScatter rows={rows} dimByKey={dimByKey} x={xDim} y={yDim} openId={openId} onPick={setOpenId} />
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-faint">
          <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ background: 'var(--pos)' }} />PASS</span>
          <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ background: 'var(--warn)' }} />WARN</span>
          <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ background: 'var(--neg)' }} />FAIL</span>
          <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ background: 'var(--muted)' }} />{bi('待人工复核', 'review')}</span>
          <span>· {bi('虚线 = 中位数,把因子分到四象限;点一个点钻取', 'dashed = medians (quadrants); click a point to drill in')}</span>
        </div>
      </Panel>

      <Panel eyebrow={bi('多维检索', 'Multi-dim search')} title={bi('Alpha 资产', 'Alpha Assets')}
        right={<Chip label={bi(`按 ${dimByKey[sortKey]?.zh ?? sortKey} 排序`, `sorted by ${dimByKey[sortKey]?.en ?? sortKey}`)} tone="info" />}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {GENERATORS.map(g => (
            <PillToggle key={g} label={g} active={gen === g} onClick={() => setGen(g)} />
          ))}
          <span className="mx-0.5 h-4 w-px self-center bg-line" />
          {SCREENS.map(s => (
            <PillToggle key={s.key} label={bi(s.zh, s.en)} tone="pos" active={screens.has(s.key)}
              onClick={() => setScreens(prev => {
                const n = new Set(prev);
                if (n.has(s.key)) n.delete(s.key);
                else n.add(s.key);
                return n;
              })} />
          ))}
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
                          <div className="mt-3 rounded-md border border-line/70 bg-elevated/30 px-3 py-2 text-2xs">
                            <div className="mb-1 font-medium text-muted">
                              {bi('目录验证证据（仅展示摘要）', 'Catalog validation evidence (redacted)')}
                            </div>
                            <div className="flex flex-wrap gap-x-4 gap-y-1 text-faint">
                              <span>
                                {bi('原始/有效试验', 'raw/effective trials')}{' '}
                                <b className="tnum text-ink">
                                  {r.catalog_trial_count ?? '—'}/{r.catalog_effective_trial_count ?? '—'}
                                </b>
                              </span>
                              <span>
                                {bi('平均绝对相关', 'mean |corr|')}{' '}
                                <b className="tnum text-ink">
                                  {typeof r.catalog_mean_abs_correlation === 'number'
                                    ? r.catalog_mean_abs_correlation.toFixed(4) : '—'}
                                </b>
                              </span>
                              <span>
                                var(SR){' '}
                                <b className="tnum text-ink">
                                  {typeof r.catalog_var_sr === 'number' ? r.catalog_var_sr.toFixed(6) : '—'}
                                </b>
                              </span>
                              <span className="mono">
                                snapshot {r.catalog_snapshot_hash?.slice(0, 12) ?? '—'}
                              </span>
                              <span className="mono">
                                evidence {r.catalog_evidence_hash?.slice(0, 12) ?? '—'}
                              </span>
                            </div>
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
      </LoadingVeil>
    </PageShell>
  );
}
