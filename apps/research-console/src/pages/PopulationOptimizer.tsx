import React from 'react';
import { fetchPopulationOptimizerSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { PopulationOptimizerPayload, PopOptSelected, PopOptExcluded } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, Chip, ProgressBar, type Column,
} from '@/ui/primitives';
import { fixed, signed } from '@/ui/format';

/**
 * Population Optimizer. Real de-redundant SET selection (synergistic forward-selection by incremental IR
 * over the real per-factor return series) + inverse-vol weights + marginal IR / marginal drawdown +
 * excluded-with-reasons + diversity (1−mean|corr|). Backed by services/research/factor_set.select_synergistic_set;
 * falls back to a deterministic synthetic set when the offline snapshot is absent. Read-only.
 */

type Selected = PopOptSelected;
type Excluded = PopOptExcluded;

const fallback: PopulationOptimizerPayload = {
  source: 'FRONTEND_POPOPT_FALLBACK', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', method: 'FORWARD_SELECTION_INCREMENTAL_IR', n_candidates: 7,
  diversity: 0.71, gross: 1.0,
  selected: [
    { alpha_id: 'ax_funding_decay7', weight: 0.34, marginal_ir: 0.41, marginal_dd: 0.02 },
    { alpha_id: 'ax_oi_skew', weight: 0.28, marginal_ir: 0.19, marginal_dd: -0.03 },
    { alpha_id: 'ax_vol_thrust', weight: 0.22, marginal_ir: 0.11, marginal_dd: 0.01 },
    { alpha_id: 'ax_basis_mom', weight: 0.16, marginal_ir: 0.07, marginal_dd: 0.04 },
  ],
  excluded: [
    { alpha_id: 'ax_funding_mean3', reason: 'REDUNDANT_CORRELATED' },
    { alpha_id: 'ax_funding_decay14', reason: 'REDUNDANT_CORRELATED' },
    { alpha_id: 'ax_px_reversal', reason: 'DROPPED_LOW_INCREMENTAL_IR' },
  ],
};

const reasonZh: Record<string, string> = {
  REDUNDANT_CORRELATED: '与已选高相关(冗余)',
  DROPPED_LOW_INCREMENTAL_IR: '增量 IR 过低',
  DROPPED_BY_EVOLUTIONARY: '进化搜索剔除',
  BEYOND_MAX_K: '超出 max_k',
};

export default function PopulationOptimizer() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<PopulationOptimizerPayload>(
    s => fetchPopulationOptimizerSnapshot(s));
  const data = usingBackend ? payload! : fallback;
  const selected = data.selected;
  const excluded = data.excluded;
  const gross = data.gross;

  const cols: Column<Selected>[] = [
    { key: 'alpha', header: bi('因子', 'Alpha'), render: r => <span className="mono text-ink">{r.alpha_id}</span> },
    { key: 'w', header: bi('权重', 'Weight'), width: '160px', render: r => (
      <div className="flex items-center gap-2"><ProgressBar value={r.weight} max={0.4} tone="accent" />
        <span className="tnum w-12 text-right text-muted">{fixed(r.weight, 3)}</span></div>) },
    { key: 'mir', header: bi('边际 IR', 'Marginal IR'), align: 'right', mono: true, render: r => (
      <span className="text-pos">{signed(r.marginal_ir, 3)}</span>) },
    { key: 'mdd', header: bi('边际回撤贡献', 'Marginal DD'), align: 'right', mono: true, render: r => (
      <span className={r.marginal_dd > 0 ? 'text-neg' : 'text-pos'}>{signed(r.marginal_dd, 3)}</span>) },
  ];

  return (
    <PageShell>
      <PageHeader title="Population Optimizer" titleZh="种群优化器"
        subtitle={bi('集合级联合优化 — 去冗余选集合 + 定权 + 边际贡献(含回撤);只读',
          'Set-level optimization — de-redundant selection + weights + marginal contribution incl. drawdown (read-only)')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={data.source} error={error}
        realData={data.data_provenance === 'REAL_CRYPTO_SERIES'} />

      <KpiStrip cols={4}>
        <StatTile label={bi('已选', 'Selected')} value={String(selected.length)} tone="pos" />
        <StatTile label={bi('剔除', 'Excluded')} value={String(excluded.length)} tone="warn" />
        <StatTile label={bi('组合毛敞口', 'Gross')} value={fixed(gross, 2)} accent />
        <StatTile label={bi('多样性', 'Diversity')} value={fixed(data.diversity, 2)} sub={bi('1 − 均|corr|', '1 − mean |corr|')} tone="info" />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('因子组合', 'Alpha Portfolio')}
          title={bi('已选集合与权重', 'Selected Set & Weights')}
          right={<Chip label={bi('inverse-vol 权重', 'inverse-vol')} tone="muted" />}>
          <DataTable columns={cols} rows={selected} getKey={r => r.alpha_id} />
        </Panel>

        <Panel eyebrow={bi('去冗余', 'De-redundancy')} title={bi('剔除原因', 'Excluded & Why')}
          right={<Chip label={bi(`${excluded.length} 项`, `${excluded.length}`)} tone="warn" />}>
          <div className="flex flex-col gap-2">
            {excluded.map(e => (
              <div key={e.alpha_id} className="rounded-lg border border-line bg-inset/40 px-3 py-2">
                <div className="mono text-xs text-ink">{e.alpha_id}</div>
                <div className="mt-0.5 text-2xs text-warn">{bi(reasonZh[e.reason] ?? e.reason, e.reason)}</div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </PageShell>
  );
}
