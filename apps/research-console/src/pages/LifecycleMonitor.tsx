import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, StatusChip, Chip, type Column,
} from '@/ui/primitives';
import { signed, fixed } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchLifecycleMonitorSnapshot } from '@/lib/api/research-console-api';
import type { LifecycleMonitorPayload, LifecycleRow } from '@/lib/types/research';

/**
 * Alpha Lifecycle Monitor (Phase 6 surface). Reads the REAL experiment_registry: each alpha's lifecycle
 * STATE, real backtest IC / Sharpe / p-value / deflated-Sharpe and the immutable evaluation verdict.
 * Read-only. NO action control — promotion to APPROVED is human-only and live trading is not built
 * (L2_DRY_RUN_ONLY). HONESTY: there is no live / paper IC feed in L2, so `live_ic` is null and the
 * degradation recommendation is NO_LIVE_DATA — we never fabricate a live-vs-backtest decay verdict.
 */

const mockRows: LifecycleRow[] = [
  { alpha_id: 'h_zscore_3', state: 'TESTING', generator: 'heuristic:zscore', days_in_state: null,
    backtest_ic: 0.047, sharpe: 0.13, pvalue: 0.18, net_sharpe: 1.99, deflated_sharpe: 0.21, turnover: 0.58,
    verdict: 'REQUIRES_HUMAN_REVIEW', live_ic: null, recommendation: 'NO_LIVE_DATA', alerts: [] },
  { alpha_id: 'h_accel_10', state: 'TESTING', generator: 'heuristic:accel', days_in_state: null,
    backtest_ic: -0.024, sharpe: -0.11, pvalue: 0.57, net_sharpe: -1.69, deflated_sharpe: -0.30, turnover: 0.58,
    verdict: 'REQUIRES_HUMAN_REVIEW', live_ic: null, recommendation: 'NO_LIVE_DATA', alerts: [] },
  { alpha_id: 'h_dist_from_high_20', state: 'VALIDATION', generator: 'heuristic:dist_from_high', days_in_state: null,
    backtest_ic: 0.031, sharpe: 0.42, pvalue: 0.09, net_sharpe: 0.88, deflated_sharpe: 0.11, turnover: 0.33,
    verdict: 'REQUIRES_HUMAN_REVIEW', live_ic: null, recommendation: 'NO_LIVE_DATA', alerts: [] },
];

const mockPayload: LifecycleMonitorPayload['summary'] = {
  total: 3, state_counts: { TESTING: 2, VALIDATION: 1 }, live_tracked: 0,
  awaiting_review: 3, needs_action: 3, positive_sharpe: 2, avg_backtest_ic: 0.018,
};

function pctOf(n: number, d: number) {
  return d > 0 ? `${Math.round((n / d) * 100)}%` : '—';
}

export default function LifecycleMonitor() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<LifecycleMonitorPayload>(
    s => fetchLifecycleMonitorSnapshot(s));

  const rows: LifecycleRow[] = usingBackend ? payload!.rows : mockRows;
  const summary = usingBackend ? payload!.summary : mockPayload;
  const stateEntries = Object.entries(summary.state_counts).sort((a, b) => b[1] - a[1]);

  const cols: Column<LifecycleRow>[] = [
    { key: 'alpha', header: bi('因子', 'Alpha'), render: r => <span className="mono text-ink">{r.alpha_id}</span> },
    { key: 'state', header: bi('状态', 'State'), render: r => <StatusChip value={r.state} /> },
    { key: 'gen', header: bi('生成器', 'Generator'), render: r => <Chip label={r.generator} tone="muted" /> },
    { key: 'bt_ic', header: bi('回测 IC', 'Backtest IC'), align: 'right', mono: true,
      render: r => r.backtest_ic === null ? <span className="text-faint">—</span> : signed(r.backtest_ic, 3) },
    { key: 'sharpe', header: bi('夏普', 'Sharpe'), align: 'right', mono: true,
      render: r => r.sharpe === null ? <span className="text-faint">—</span> : (
        <span className={r.sharpe > 0 ? 'text-pos' : 'text-neg'}>{signed(r.sharpe, 2)}</span>) },
    { key: 'pval', header: bi('p 值', 'p-value'), align: 'right', mono: true,
      render: r => r.pvalue === null ? <span className="text-faint">—</span> : (
        <span className={r.pvalue < 0.05 ? 'text-pos' : 'text-muted'}>{fixed(r.pvalue, 3)}</span>) },
    { key: 'dsr', header: bi('紧缩夏普', 'Deflated SR'), align: 'right', mono: true,
      render: r => r.deflated_sharpe === null ? <span className="text-faint">—</span> : signed(r.deflated_sharpe, 2) },
    { key: 'verdict', header: bi('评估裁决', 'Verdict'), render: r => <StatusChip value={r.verdict} /> },
    { key: 'live_ic', header: bi('实时 IC', 'Live IC'), align: 'right', mono: true,
      render: r => (
        <span className={r.live_ic === null ? 'text-faint' : r.live_ic > 0 ? 'text-pos' : 'text-neg'}>
          {r.live_ic === null ? '—' : signed(r.live_ic, 3)}
        </span>) },
    { key: 'rec', header: bi('建议', 'Recommendation'), render: r => <StatusChip value={r.recommendation} /> },
  ];

  return (
    <PageShell>
      <PageHeader title="Alpha Lifecycle Monitor" titleZh="Alpha 生命周期监控"
        subtitle={bi('每个 Alpha 的真实生命周期态与回测评估制品(IC/夏普/p值/裁决);实时衰减建议需实盘数据',
          'Each alpha’s real lifecycle state + backtest evaluation artifacts (IC/Sharpe/p-value/verdict); the live-decay recommendation needs a live feed')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && payload?.data_provenance === 'EXPERIMENT_REGISTRY'}
        source={usingBackend ? 'EXPERIMENT_REGISTRY' : 'FRONTEND_LIFECYCLE_MOCK'} error={error} />

      {/* Honesty: backtest-side fields are REAL (immutable experiment_registry artifacts). The live side is
          null because L2_DRY_RUN_ONLY persists no live / paper realized IC — so the degradation engine has
          no live input and the recommendation is NO_LIVE_DATA, not a fabricated DEGRADED. State today is
          uniformly TESTING in the real registry — nothing has been promoted (auto_approved = 0). */}
      <div className="rounded-md border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 text-2xs leading-relaxed text-amber-200/90">
        {bi('诚实标注:回测侧字段(IC/夏普/p值/紧缩夏普/裁决)来自真实 experiment_registry 不可变制品。实时 IC 在 L2_DRY_RUN_ONLY 下无实盘/纸面源,置空;衰减建议为 NO_LIVE_DATA(不伪造 DEGRADED)。真库当前状态均为 TESTING——尚无晋级(auto_approved=0)。在态天数无逐 Alpha 真实时长,省略。',
            'Honest note: backtest-side fields (IC/Sharpe/p-value/deflated-SR/verdict) come from the real immutable experiment_registry. Live IC has no live/paper source under L2_DRY_RUN_ONLY and is null; the decay recommendation is NO_LIVE_DATA (no fabricated DEGRADED). The real registry is uniformly TESTING — nothing promoted (auto_approved=0). Per-alpha tenure is unavailable and omitted.')}
      </div>

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究/干跑(L2)· 监控只给建议不驱动状态 · 晋级到 APPROVED 人工专属 · 实盘未构建',
          'Read-only · research / dry-run (L2) · the monitor only RECOMMENDS, it drives no state · promotion to APPROVED is human-only · live trading is not built')}
      </div>

      <KpiStrip cols={6}>
        <StatTile label={bi('总数', 'Total')} value={String(summary.total)} accent />
        <StatTile label={bi('在测', 'Testing')} value={String(summary.state_counts['TESTING'] ?? 0)} tone="info"
          sub={bi(`${stateEntries.length} 个状态`, `${stateEntries.length} states`)} />
        <StatTile label={bi('实盘追踪', 'Live-tracked')} value={String(summary.live_tracked)} tone="muted"
          sub={bi('L2 无实盘', 'no live in L2')} />
        <StatTile label={bi('正夏普', 'Positive Sharpe')} value={String(summary.positive_sharpe)}
          tone={summary.positive_sharpe > 0 ? 'pos' : 'muted'}
          sub={bi(pctOf(summary.positive_sharpe, summary.total), pctOf(summary.positive_sharpe, summary.total))} />
        <StatTile label={bi('待人工复核', 'Awaiting Review')} value={String(summary.awaiting_review)}
          tone={summary.awaiting_review > 0 ? 'warn' : 'pos'} sub={bi('裁决=需复核', 'verdict=review')} />
        <StatTile label={bi('平均回测 IC', 'Avg Backtest IC')} value={signed(summary.avg_backtest_ic, 4)}
          tone={summary.avg_backtest_ic > 0 ? 'pos' : 'neg'} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
        <Panel eyebrow={bi('状态分布', 'States')} title={bi('生命周期状态', 'Lifecycle States')}>
          <div className="flex flex-col gap-2">
            {stateEntries.map(([state, count]) => (
              <div key={state} className="flex items-center justify-between gap-2">
                <StatusChip value={state} />
                <span className="tnum text-sm text-ink">{count}</span>
              </div>
            ))}
            {stateEntries.length === 1 && (
              <div className="mt-1 text-2xs text-faint">
                {bi('真库当前仅 TESTING 态(尚无晋级)', 'real registry is TESTING-only (nothing promoted)')}
              </div>
            )}
          </div>
        </Panel>

        <Panel className="xl:col-span-3" eyebrow={bi('生命周期', 'Lifecycle')}
          title={bi('Alpha 状态与回测评估制品', 'Alpha States & Backtest Evaluation Artifacts')}
          right={<Chip label={bi(`${rows.length} 个 Alpha`, `${rows.length} alphas`)} tone="info" />}>
          <DataTable columns={cols} rows={rows} getKey={r => r.alpha_id} />
        </Panel>
      </div>
    </PageShell>
  );
}
