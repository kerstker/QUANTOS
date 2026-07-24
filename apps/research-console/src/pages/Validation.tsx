import React, { useState } from 'react';
import { fetchValidationSnapshot } from '@/lib/api/research-console-api';
import { mockValidationReports } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { ValidationDashboardPayload, ValidationReport, CalculationLineageItem } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, KeyValue, LoadingVeil, type Column,
} from '@/ui/primitives';
import { RadialGauge, Histogram } from '@/ui/charts';
import { fixed, signed, num, shortHash, statusTone, type Tone } from '@/ui/format';

/** The six discriminating leakage/robustness gates surfaced per report. */
const gateDefs: Array<{ key: string; zh: string; en: string; get: (r: ValidationReport) => boolean }> = [
  { key: 'leakage', zh: '泄露检查', en: 'Leakage', get: r => r.leakage_pass },
  { key: 'walk_forward', zh: '滚动样本外', en: 'Walk-Forward', get: r => r.walk_forward_pass },
  { key: 'multiple_testing', zh: '多重检验', en: 'Multiple Testing', get: r => r.multiple_testing_pass },
  { key: 'costs', zh: '成本检查', en: 'Costs', get: r => r.costs_pass },
  { key: 'capacity', zh: '容量检查', en: 'Capacity', get: r => r.capacity_pass },
  { key: 'decay', zh: '衰减检查', en: 'Decay', get: r => r.decay_pass },
];

export default function Validation() {
  const { payload, apiState, error, usingBackend, snapshotState } = useConsoleSnapshot<ValidationDashboardPayload>(
    s => fetchValidationSnapshot(s));

  const reports: ValidationReport[] = usingBackend ? payload!.reports : mockValidationReports;
  const r: ValidationReport = usingBackend ? payload!.selected_report : mockValidationReports[0];
  const lineage: CalculationLineageItem[] = usingBackend ? (payload?.lineage ?? []) : [];

  // What-if 成本/压力滑杆(2026-07-09 从已并入的 ComputationLab 页迁移而来,改造成基于本页
  // 真实 net_sharpe 的本地估算——不是 ComputationLab 那套合成情景曲线,是纯前端估算工具,
  // 不产生新的后端调用,仅供"这个已选因子在更高成本/更大压力下大概会怎样"的直觉参考,不是
  // 权威结论(权威口径仍是 costs/decay 两道真实门禁本身)。
  const [whatIfCostBps, setWhatIfCostBps] = useState(0);
  const [whatIfStress, setWhatIfStress] = useState(1.0);
  const costDrag = whatIfCostBps / 100;
  const stressPenalty = Math.max(0, whatIfStress - 1) * 0.18;
  const whatIfNetSharpe = r.net_sharpe - costDrag - stressPenalty;

  const gates = gateDefs.map(g => ({ ...g, pass: g.get(r) }));
  const decisionTone: Tone = statusTone(r.decision);

  // Cohort-level decision distribution → labelled histogram.
  const decisionCounts = reports.reduce<Record<string, number>>((acc, rep) => {
    acc[rep.decision] = (acc[rep.decision] ?? 0) + 1;
    return acc;
  }, {});
  const decisionBins = Object.entries(decisionCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([label, count]) => ({ label: label.replace(/^REJECTED_/, '✕ '), count }));

  const approvedCount = reports.filter(rep => statusTone(rep.decision) === 'pos').length;

  const reportCols: Column<ValidationReport>[] = [
    { key: 'alpha', header: bi('因子', 'Alpha'), render: rep => (
      <div className="flex flex-col">
        <span className="mono font-medium text-ink">{rep.alpha_id}</span>
        <span className="mono text-2xs text-faint">{shortHash(rep.run_id)}</span>
      </div>
    ) },
    { key: 'decision', header: bi('决策', 'Decision'), render: rep => <StatusChip value={rep.decision} /> },
    { key: 'gates', header: bi('门禁', 'Gates'), width: '150px', render: rep => (
      <div className="flex items-center gap-2">
        <ProgressBar value={rep.gates_passed} max={rep.gates_total} tone={statusTone(rep.decision)} />
        <span className="tnum w-10 text-right text-muted">{rep.gates_passed}/{rep.gates_total}</span>
      </div>
    ) },
    { key: 'oos_sharpe', header: bi('样本外夏普', 'OOS Sharpe'), align: 'right', mono: true, sortable: true, sortAccessor: rep => rep.oos_sharpe, render: rep => fixed(rep.oos_sharpe) },
    { key: 'oos_ic', header: bi('样本外 IC', 'OOS IC'), align: 'right', mono: true, sortable: true, sortAccessor: rep => rep.oos_ic, render: rep => signed(rep.oos_ic, 3) },
    { key: 'net_sharpe', header: bi('净夏普', 'Net Sharpe'), align: 'right', mono: true, sortable: true, sortAccessor: rep => rep.net_sharpe, render: rep => (
      <span className={rep.net_sharpe >= 0.5 ? 'text-pos' : rep.net_sharpe > 0 ? 'text-warn' : 'text-neg'}>{fixed(rep.net_sharpe)}</span>
    ) },
    { key: 'haircut', header: bi('净/毛差', 'Net − OOS'), align: 'right', mono: true, render: rep => (
      <span className="text-muted">{signed(rep.net_sharpe - rep.oos_sharpe)}</span>
    ) },
  ];

  return (
    <PageShell>
      <PageHeader title="Alpha Validation" titleZh="Alpha 验证"
        subtitle={bi('六道泄露/稳健性门禁的样本外验证快照 — 决策、门禁与衰减证据',
          'Out-of-sample validation snapshot across six leakage / robustness gates — decision, gates, decay evidence')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} realData={(payload as { data_provenance?: string } | null | undefined)?.data_provenance === 'REAL_CRYPTO_SERIES'} source={payload?.source} error={error}
        dataStatus={usingBackend && r.decision.startsWith('REJECTED') ? 'BLOCKED' : snapshotState} />

      <LoadingVeil active={apiState === 'loading'}>
      <KpiStrip cols={6}>
        <StatTile label={bi('选中决策', 'Selected Decision')} value={<StatusChip value={r.decision} />} />
        <StatTile label={bi('门禁通过', 'Gates Passed')} value={`${r.gates_passed}/${r.gates_total}`}
          tone={r.gates_passed === r.gates_total ? 'pos' : 'warn'} />
        <StatTile label={bi('样本外夏普', 'OOS Sharpe')} value={fixed(r.oos_sharpe)} accent />
        <StatTile label={bi('样本外 IC', 'OOS IC')} value={signed(r.oos_ic, 3)} tone="info" />
        <StatTile label={bi('净夏普', 'Net Sharpe')} value={fixed(r.net_sharpe)}
          tone={r.net_sharpe >= 0.5 ? 'pos' : r.net_sharpe > 0 ? 'warn' : 'neg'} />
        <StatTile label={bi('队列已批准', 'Cohort Approved')} value={`${approvedCount}/${reports.length}`}
          sub={bi('队列内通过数', 'approved in cohort')} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('门禁评分', 'Gate Score')} title={bi('通过率', 'Pass Rate')}
          right={<Chip label={r.alpha_id} tone="info" />}>
          <div className="flex flex-col items-center gap-3">
            <RadialGauge value={r.gates_passed} max={r.gates_total} size={156}
              label={bi('门禁', 'gates')} tone={r.gates_passed === r.gates_total ? 'pos' : undefined} />
            <Chip label={bi(`决策 · ${r.decision}`, `Decision · ${r.decision}`)} tone={decisionTone} />
          </div>
        </Panel>

        <Panel className="xl:col-span-2" eyebrow={bi('稳健性门禁', 'Robustness Gates')}
          title={bi('六道泄露与衰减检查', 'Six Leakage & Decay Gates')}
          right={<Chip label={bi(`${gates.filter(g => g.pass).length}/${gates.length} 通过`, `${gates.filter(g => g.pass).length}/${gates.length} pass`)}
            tone={gates.every(g => g.pass) ? 'pos' : 'warn'} />}>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {gates.map(g => (
              <div key={g.key}
                className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset/40 px-3 py-2.5">
                <span className="text-xs text-ink">{bi(g.zh, g.en)}</span>
                <GateDot ok={g.pass} label={g.pass ? bi('通过', 'PASS') : bi('未过', 'FAIL')} />
              </div>
            ))}
          </div>
          {r.reasons.length > 0 && (
            <div className="mt-3 rounded-lg border border-neg/30 bg-neg/5 px-3 py-2">
              <div className="text-2xs uppercase tracking-wide text-neg">{bi('拒绝原因', 'Rejection Reasons')}</div>
              <ul className="mt-1 flex flex-col gap-1">
                {r.reasons.map(reason => (
                  <li key={reason} className="mono text-2xs text-neg/90">• {reason}</li>
                ))}
              </ul>
            </div>
          )}
        </Panel>
      </div>

      <Panel eyebrow={bi('What-if · 成本/压力', 'WHAT-IF · COST/STRESS')}
        title={bi(`成本/压力敏感性估算(基于 ${r.alpha_id} 真实净夏普 ${fixed(r.net_sharpe)})`,
          `cost/stress sensitivity estimate (off ${r.alpha_id}'s real net Sharpe ${fixed(r.net_sharpe)})`)}
        right={<Chip label={bi('本地估算,非新后端调用', 'local estimate, no new backend call')} tone="muted" />}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <label className="block sm:col-span-1">
            <div className="flex items-center justify-between text-2xs uppercase tracking-wide text-faint">
              <span>{bi('额外成本拖累', 'Extra Cost Drag')}</span>
              <span className="tnum font-semibold text-ink">{whatIfCostBps} bps</span>
            </div>
            <input className="mt-2 w-full accent-[var(--accent)]" type="range" min={0} max={80}
              value={whatIfCostBps} onChange={e => setWhatIfCostBps(Number(e.target.value))} />
          </label>
          <label className="block sm:col-span-1">
            <div className="flex items-center justify-between text-2xs uppercase tracking-wide text-faint">
              <span>{bi('压力倍数', 'Stress Multiplier')}</span>
              <span className="tnum font-semibold text-ink">{whatIfStress.toFixed(1)}×</span>
            </div>
            <input className="mt-2 w-full accent-[var(--accent)]" type="range" min={1} max={2.5} step={0.1}
              value={whatIfStress} onChange={e => setWhatIfStress(Number(e.target.value))} />
          </label>
          <div className="flex flex-col justify-center sm:col-span-1">
            <div className="text-2xs uppercase tracking-wide text-faint">{bi('估算净夏普', 'Estimated Net Sharpe')}</div>
            <div className={`tnum text-lg font-semibold ${whatIfNetSharpe >= 0.5 ? 'text-pos' : whatIfNetSharpe > 0 ? 'text-warn' : 'text-neg'}`}>
              {fixed(whatIfNetSharpe)}
            </div>
            <div className="text-2xs text-faint">{bi('原始', 'gross')} {fixed(r.net_sharpe)} − {bi('成本', 'cost')} {num(costDrag, 3)} − {bi('压力', 'stress')} {num(stressPenalty, 3)}</div>
          </div>
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('队列', 'Cohort')} title={bi('全部验证报告', 'All Validation Reports')}
          right={<Chip label={bi(`${reports.length} 份`, `${reports.length} reports`)} tone="info" />}>
          <DataTable columns={reportCols} rows={reports} getKey={rep => rep.run_id}
            searchable={rep => `${rep.alpha_id} ${rep.run_id} ${rep.decision}`}
            searchPlaceholder={bi('搜索因子 / run_id / 决策…', 'Search alpha / run_id / decision…')} />
        </Panel>

        <Panel eyebrow={bi('决策分布', 'Decision Mix')} title={bi('队列决策构成', 'Cohort Decision Breakdown')}>
          <Histogram bins={decisionBins} height={200} tone="accent" />
        </Panel>
      </div>

      {lineage.length > 0 && (
        <Panel eyebrow={bi('可复现性', 'Reproducibility')} title={bi('计算血缘', 'Calculation Lineage')}
          right={<Chip label={bi(`${lineage.length} 步`, `${lineage.length} steps`)} tone="muted" />}>
          <div className="flex flex-col gap-2.5">
            {lineage.map(item => (
              <div key={item.id} className="rounded-lg border border-line bg-inset/30 p-3">
                <div className="mb-1.5 flex items-center gap-2">
                  <Chip label={item.layer} tone="special" />
                  <span className="mono text-2xs text-faint">{item.backend}</span>
                  <span className="mono ml-auto text-2xs text-muted">→ {item.output}</span>
                </div>
                <KeyValue cols={1} items={[{ k: bi('公式', 'Formula'), v: item.formula }]} />
                {item.inputs.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {item.inputs.map(inp => <Chip key={inp} label={inp} tone="muted" />)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Panel>
      )}
      </LoadingVeil>
    </PageShell>
  );
}
