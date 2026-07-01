import React, { useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, DataTable, type Column,
} from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchIcPanelSnapshot } from '@/lib/api/research-console-api';
import type { IcPanelPayload, IcPanelRow } from '@/lib/types/research';

/**
 * IC Panel — 截面 IC 诊断面板:任意已注册因子 × 任意已注册宇宙的逐期截面 IC 横向对比。
 * 初筛诊断工具,不是晋级门禁——真正的门禁是 evaluator 的 DSR/PBO/walk-forward。
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

export default function ICPanel() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<IcPanelPayload>(
    s => fetchIcPanelSnapshot(s));
  const p: IcPanelPayload = usingBackend ? payload! : mockPayload;
  const [expanded, setExpanded] = useState<string | null>(null);

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
    { key: 'detail', header: '', render: r => (
      <button className="text-2xs text-accent underline" onClick={() => setExpanded(expanded === r.name ? null : r.name)}>
        {bi('逐年', 'per-year')}
      </button>
    ) },
  ];

  const expandedRow = p.rows.find(r => r.name === expanded);

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
        {p.rows.length ? <DataTable rows={p.rows} columns={cols} getKey={r => r.name} /> : (
          <div className="text-2xs text-faint">{bi('无快照 —— 门外跑 tools/ic_panel_research.py 生成', 'no snapshot — run tools/ic_panel_research.py off-gate')}</div>)}
      </Panel>

      {expandedRow && (
        <Panel eyebrow={bi('逐年', 'PER-YEAR')} title={expandedRow.name}>
          <div className="flex flex-wrap gap-2 text-2xs">
            {Object.entries(expandedRow.per_year).map(([y, v]) => (
              <span key={y} className="rounded border border-line px-2 py-1 mono">
                {y}: <span className={icTone(v)}>{v == null ? '—' : v.toFixed(4)}</span>
              </span>
            ))}
          </div>
        </Panel>
      )}

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究诊断 · 不驱动任何自动晋级', 'Read-only · research diagnostic · drives no automatic promotion')}
      </div>
    </PageShell>
  );
}
