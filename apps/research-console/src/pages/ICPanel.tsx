import React, { useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, DataTable, type Column,
} from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchIcPanelSnapshot } from '@/lib/api/research-console-api';
import type { IcPanelAgg, IcPanelPayload, IcPanelRow } from '@/lib/types/research';

/**
 * IC Panel — 截面 IC 诊断面板:任意已注册因子 × 任意已注册宇宙的逐期截面 IC 横向对比。
 * 初筛诊断工具,不是晋级门禁——真正的门禁是 evaluator 的 DSR/PBO/walk-forward。
 * schema v2:主表列 = h=1 口径(跨因子可比);"多期 t" 列 + 展开详情 = per-factor 持有期口径
 * (h>1 非重叠抽样,t-stat 诚实;持有期单位 = 面板自身周期,当前四宇宙全日频)。
 */

const mockPayload: IcPanelPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_SNAPSHOT',
  rows: [], universes: {},
  honesty_note: 'No IC panel snapshot yet — run tools/ic_panel_research.py off-gate.',
};

function icTone(v: number | null): string {
  if (v == null) return 'text-faint';
  return v > 0.03 ? 'text-pos' : v > 0 ? 'text-info' : v > -0.03 ? 'text-warn' : 'text-neg';
}

function tTone(v: number | null): string {
  if (v == null) return 'text-faint';
  return Math.abs(v) >= 2 ? (v > 0 ? 'text-pos' : 'text-neg') : 'text-muted';
}

/** by_period 键按持有期数值升序排(对象键序不可信)。 */
function sortedPeriods(row: IcPanelRow): [string, IcPanelAgg][] {
  const bp = row.by_period;
  if (!bp) return [];
  return Object.entries(bp).sort((a, b) => Number(a[0]) - Number(b[0]));
}

export default function ICPanel() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<IcPanelPayload>(
    s => fetchIcPanelSnapshot(s));
  const p: IcPanelPayload = usingBackend ? payload! : mockPayload;
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selPeriod, setSelPeriod] = useState<string>('1');

  const cols: Column<IcPanelRow>[] = [
    { key: 'name', header: bi('因子', 'Factor'), render: r => <span className="font-medium text-ink">{r.name}</span> },
    { key: 'universe', header: bi('宇宙', 'Universe'), render: r => <span className="text-muted">{r.universe}</span> },
    { key: 'mean_ic', header: bi('均值 IC', 'Mean IC'), align: 'right', mono: true,
      render: r => <span className={icTone(r.mean_ic)}>{r.mean_ic == null ? '—' : r.mean_ic.toFixed(4)}</span> },
    { key: 'icir', header: 'ICIR', align: 'right', mono: true,
      render: r => r.icir == null ? '—' : r.icir.toFixed(2) },
    { key: 'hit_rate', header: bi('命中率', 'Hit rate'), align: 'right', mono: true,
      render: r => r.hit_rate == null ? '—' : `${(r.hit_rate * 100).toFixed(0)}%` },
    { key: 't_stat', header: 't-stat', align: 'right', mono: true,
      render: r => r.t_stat == null ? '—' : r.t_stat.toFixed(2) },
    { key: 'n', header: bi('期数', 'Periods'), align: 'right', mono: true, render: r => r.n_periods },
    { key: 'multi_t', header: bi('多期 t', 'Multi-h t'), align: 'right', mono: true,
      render: r => {
        const periods = sortedPeriods(r);
        if (!periods.length) return <span className="text-faint">—</span>;
        return (
          <span className="text-2xs">
            {periods.map(([h, agg], i) => (
              <span key={h}>
                {i > 0 && <span className="text-faint"> / </span>}
                <span className="text-faint">h{h}:</span>
                <span className={tTone(agg.t_stat)}>{agg.t_stat == null ? '—' : agg.t_stat.toFixed(2)}</span>
              </span>
            ))}
          </span>
        );
      } },
    { key: 'detail', header: '', render: r => (
      <button className="text-2xs text-accent underline"
        onClick={() => { setExpanded(expanded === r.name ? null : r.name); setSelPeriod('1'); }}>
        {bi('详情', 'detail')}
      </button>
    ) },
  ];

  const expandedRow = p.rows.find(r => r.name === expanded);
  const expandedPeriods = expandedRow ? sortedPeriods(expandedRow) : [];
  // 选中持有期的 per_year:v2 走 by_period;旧快照兜底顶层 per_year(= h=1)
  const selAgg: IcPanelAgg | undefined = expandedRow
    ? (expandedRow.by_period?.[selPeriod] ?? (selPeriod === '1' ? expandedRow : undefined))
    : undefined;

  const periodCols: Column<[string, IcPanelAgg]>[] = [
    { key: 'h', header: bi('持有期', 'Horizon'), mono: true,
      render: ([h]) => (
        <button className={`underline ${h === selPeriod ? 'text-accent font-medium' : 'text-muted'}`}
          onClick={() => setSelPeriod(h)}>h={h}</button>
      ) },
    { key: 'mean_ic', header: bi('均值 IC', 'Mean IC'), align: 'right', mono: true,
      render: ([, a]) => <span className={icTone(a.mean_ic)}>{a.mean_ic == null ? '—' : a.mean_ic.toFixed(4)}</span> },
    { key: 'icir', header: 'ICIR', align: 'right', mono: true,
      render: ([, a]) => a.icir == null ? '—' : a.icir.toFixed(2) },
    { key: 'hit_rate', header: bi('命中率', 'Hit rate'), align: 'right', mono: true,
      render: ([, a]) => a.hit_rate == null ? '—' : `${(a.hit_rate * 100).toFixed(0)}%` },
    { key: 't_stat', header: 't-stat', align: 'right', mono: true,
      render: ([, a]) => <span className={tTone(a.t_stat)}>{a.t_stat == null ? '—' : a.t_stat.toFixed(2)}</span> },
    { key: 'n', header: bi('期数', 'Periods'), align: 'right', mono: true, render: ([, a]) => a.n_periods },
  ];

  return (
    <PageShell>
      <PageHeader title="IC Panel" titleZh="截面 IC 面板"
        subtitle={bi('任意已注册因子 × 任意已注册宇宙的逐期截面 IC 横向对比 · 初筛诊断,非晋级门禁',
          'Cross-sectional IC across any registered factor × universe · first-look diagnostic, not a promotion gate')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_IC_PANEL_OFFLINE_SNAPSHOT'}
        source={usingBackend ? (p.snapshot_present ? 'REAL_IC_PANEL_OFFLINE_SNAPSHOT' : 'NO_IC_PANEL_SNAPSHOT') : 'FRONTEND_IC_PANEL_MOCK'}
        error={error} />

      <div className="rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi(p.honesty_note, p.honesty_note)}
      </div>

      <Panel eyebrow={bi('因子横向对比', 'FACTOR COMPARISON')}
        title={bi(`${p.rows.length} 个已注册因子 · ${Object.keys(p.universes).length} 个宇宙`,
          `${p.rows.length} registered factors · ${Object.keys(p.universes).length} universes`)}>
        {p.rows.length ? (
          <>
            <DataTable rows={p.rows} columns={cols} getKey={r => r.name} />
            <div className="mt-2 text-2xs text-faint">
              {bi('主表六列 = h=1 口径(跨因子可比);"多期 t" = per-factor 持有期(h>1 非重叠抽样,期数缩 ≈ n/h;单位 = 面板自身周期,当前全日频,h7≈周度)',
                'Main columns = h=1 (comparable across factors); "Multi-h t" = per-factor horizons (h>1 non-overlapping sampling, n shrinks ≈ n/h; unit = panel period, all daily today, h7≈weekly)')}
            </div>
          </>
        ) : (
          <div className="text-2xs text-faint">{bi('无快照 —— 门外跑 tools/ic_panel_research.py 生成', 'no snapshot — run tools/ic_panel_research.py off-gate')}</div>)}
      </Panel>

      {expandedRow && (
        <Panel eyebrow={bi('持有期详情', 'PER-HORIZON DETAIL')} title={expandedRow.name}>
          {expandedPeriods.length ? (
            <DataTable rows={expandedPeriods} columns={periodCols} getKey={([h]) => h} />
          ) : (
            <div className="text-2xs text-faint">
              {bi('旧快照无 by_period —— 重跑 tools/ic_panel_research.py 迁移到 schema v2', 'pre-v2 snapshot has no by_period — rerun tools/ic_panel_research.py')}
            </div>
          )}
          <div className="mt-3 text-2xs text-muted">
            {bi(`逐年均值 IC(持有期 h=${selPeriod})`, `Per-year mean IC (horizon h=${selPeriod})`)}
          </div>
          <div className="mt-1 flex flex-wrap gap-2 text-2xs">
            {selAgg ? Object.entries(selAgg.per_year).map(([y, v]) => (
              <span key={y} className="rounded border border-line px-2 py-1 mono">
                {y}: <span className={icTone(v)}>{v == null ? '—' : v.toFixed(4)}</span>
              </span>
            )) : <span className="text-faint">—</span>}
          </div>
        </Panel>
      )}

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究诊断 · 不驱动任何自动晋级', 'Read-only · research diagnostic · drives no automatic promotion')}
      </div>
    </PageShell>
  );
}
