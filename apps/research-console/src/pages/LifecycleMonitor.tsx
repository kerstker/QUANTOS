import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, StatusChip, Chip, type Column,
} from '@/ui/primitives';
import { signed, fixed } from '@/ui/format';

/**
 * Alpha Lifecycle Monitor (Phase 6 surface). Shows each alpha's lifecycle STATE — including the new
 * dry-run monitoring states SHADOW / DEGRADED / PAUSED — and the degradation monitor's live-vs-backtest
 * recommendation. Mock-first (the other 25 console pages are too); read-only. NO action control here:
 * promotion to APPROVED is human-only and live trading is not built (L2_DRY_RUN_ONLY).
 */

interface LifecycleRow {
  alpha_id: string;
  state: string;          // DRAFT/TESTING/VALIDATED/PAPER_TRADING/SHADOW/APPROVED/DEGRADED/PAUSED/RETIRED
  generator: string;
  days_in_state: number;
  backtest_ic: number;
  live_ic: number | null; // null until the alpha is paper/shadow/approved
  recommendation: string; // OK / DEGRADED / PAUSED / RETIRED
  alerts: string[];
}

const mockRows: LifecycleRow[] = [
  { alpha_id: 'ax_funding_carry', state: 'APPROVED', generator: 'cs_genome', days_in_state: 41,
    backtest_ic: 0.052, live_ic: 0.048, recommendation: 'OK', alerts: [] },
  { alpha_id: 'ax_funding_decay14', state: 'SHADOW', generator: 'cs_genome', days_in_state: 12,
    backtest_ic: 0.044, live_ic: 0.041, recommendation: 'OK', alerts: [] },
  { alpha_id: 'ax_oi_skew', state: 'DEGRADED', generator: 'gp', days_in_state: 6,
    backtest_ic: 0.038, live_ic: 0.009, recommendation: 'DEGRADED', alerts: ['IC retention 0.24 < 0.30'] },
  { alpha_id: 'ax_basis_mom', state: 'PAUSED', generator: 'mcts', days_in_state: 2,
    backtest_ic: 0.031, live_ic: 0.030, recommendation: 'PAUSED', alerts: ['slippage 4.1x backtest', 'drawdown 0.34 > 0.30'] },
  { alpha_id: 'ax_px_reversal', state: 'PAPER_TRADING', generator: 'evolutionary', days_in_state: 19,
    backtest_ic: 0.027, live_ic: -0.004, recommendation: 'RETIRED', alerts: ['live IC -0.004 <= 0 vs backtest 0.027'] },
  { alpha_id: 'ax_vol_thrust', state: 'VALIDATED', generator: 'cs_genome', days_in_state: 3,
    backtest_ic: 0.041, live_ic: null, recommendation: 'OK', alerts: [] },
];

function count(rows: LifecycleRow[], state: string) {
  return rows.filter(r => r.state === state).length;
}

export default function LifecycleMonitor() {
  const rows = mockRows;
  const needsAction = rows.filter(r => r.recommendation !== 'OK');

  const cols: Column<LifecycleRow>[] = [
    { key: 'alpha', header: bi('因子', 'Alpha'), render: r => <span className="mono text-ink">{r.alpha_id}</span> },
    { key: 'state', header: bi('状态', 'State'), render: r => <StatusChip value={r.state} /> },
    { key: 'gen', header: bi('生成器', 'Generator'), render: r => <Chip label={r.generator} tone="muted" /> },
    { key: 'days', header: bi('在态天数', 'Days'), align: 'right', mono: true, render: r => String(r.days_in_state) },
    { key: 'bt_ic', header: bi('回测 IC', 'Backtest IC'), align: 'right', mono: true, render: r => signed(r.backtest_ic, 3) },
    { key: 'live_ic', header: bi('实时 IC', 'Live IC'), align: 'right', mono: true, render: r => (
      <span className={r.live_ic === null ? 'text-faint' : r.live_ic > 0 ? 'text-pos' : 'text-neg'}>
        {r.live_ic === null ? '—' : signed(r.live_ic, 3)}
      </span>
    ) },
    { key: 'retention', header: bi('IC 留存', 'IC Retention'), align: 'right', mono: true, render: r => (
      r.live_ic === null || r.backtest_ic <= 0 ? <span className="text-faint">—</span>
        : <span className="text-muted">{fixed(r.live_ic / r.backtest_ic, 2)}</span>
    ) },
    { key: 'rec', header: bi('建议', 'Recommendation'), render: r => <StatusChip value={r.recommendation} /> },
  ];

  return (
    <PageShell>
      <PageHeader title="Alpha Lifecycle Monitor" titleZh="Alpha 生命周期监控"
        subtitle={bi('每个 Alpha 的生命周期态(含 SHADOW/DEGRADED/PAUSED)与实时-vs-回测衰减建议',
          'Each alpha’s lifecycle state (incl. SHADOW / DEGRADED / PAUSED) and the live-vs-backtest degradation recommendation')} />
      {/* Honesty: this page is front-end mock data (no backend endpoint yet — see audit). The real engine is
          services/alpha lifecycle SM + degradation_monitor; wiring is roadmap Phase 3. */}
      <ProvenanceRibbon usingBackend={false} apiState="disabled" source="FRONTEND_LIFECYCLE_MOCK" />

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究/干跑(L2)· 监控只给建议不驱动状态 · 晋级到 APPROVED 人工专属 · 实盘未构建',
          'Read-only · research / dry-run (L2) · the monitor only RECOMMENDS, it drives no state · promotion to APPROVED is human-only · live trading is not built')}
      </div>

      <KpiStrip cols={6}>
        <StatTile label={bi('总数', 'Total')} value={String(rows.length)} />
        <StatTile label={bi('影子', 'Shadow')} value={String(count(rows, 'SHADOW'))} tone="info" />
        <StatTile label={bi('已批准', 'Approved')} value={String(count(rows, 'APPROVED'))} tone="pos" />
        <StatTile label={bi('衰减', 'Degraded')} value={String(count(rows, 'DEGRADED'))} tone="warn" />
        <StatTile label={bi('暂停', 'Paused')} value={String(count(rows, 'PAUSED'))} tone="warn" />
        <StatTile label={bi('需处理', 'Needs Action')} value={String(needsAction.length)}
          tone={needsAction.length > 0 ? 'warn' : 'pos'} sub={bi('建议非 OK', 'recommendation != OK')} />
      </KpiStrip>

      <Panel eyebrow={bi('生命周期', 'Lifecycle')} title={bi('Alpha 状态与衰减建议', 'Alpha States & Degradation Recommendations')}
        right={<Chip label={bi(`${rows.length} 个 Alpha`, `${rows.length} alphas`)} tone="info" />}>
        <DataTable columns={cols} rows={rows} getKey={r => r.alpha_id} />
      </Panel>

      {needsAction.length > 0 && (
        <Panel eyebrow={bi('衰减告警', 'Degradation Alerts')} title={bi('需人工裁决的退化', 'Degradations Awaiting Human Decision')}
          right={<Chip label={bi(`${needsAction.length} 项`, `${needsAction.length} items`)} tone="warn" />}>
          <div className="flex flex-col gap-2.5">
            {needsAction.map(r => (
              <div key={r.alpha_id} className="rounded-lg border border-warn/30 bg-warn/5 p-3">
                <div className="mb-1.5 flex items-center gap-2">
                  <span className="mono text-xs text-ink">{r.alpha_id}</span>
                  <StatusChip value={r.state} />
                  <span className="ml-auto"><StatusChip value={r.recommendation} /></span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {r.alerts.map(a => <Chip key={a} label={a} tone="warn" />)}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </PageShell>
  );
}
