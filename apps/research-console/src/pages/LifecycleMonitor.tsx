import { useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, StatusChip, Chip, LoadingVeil,
  PillToggle, type Column,
} from '@/ui/primitives';
import { signed, fixed } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchLifecycleMonitorSnapshot } from '@/lib/api/research-console-api';
import type { LifecycleMonitorPayload, LifecycleRow } from '@/lib/types/research';
import { PaperTrackDetail } from './_shared/PaperTrackDetail';

/** 2026-07-09 从已并入本页的独立页 PaperTrading.tsx 迁移而来:该页原来用不对应真实 ID 的通用
 * "alpha-N" 马甲展示这 4 个真实 alpha 的纸面回放,核实后确认它们就是本页这张真实表格里的 4 行——
 * 现在按行钻取,不再用马甲 ID,消除"同一批 alpha 在两个页面套两套 ID"的用户困惑。 */
const PAPER_TRACK_SLOT: Record<string, string> = {
  h_zscore_3: 'alpha-1',
  h_zscore_5: 'alpha-2',
  'hx_sum_price-zscore-3_price-momentum-10': 'alpha-3',
  'hx_sum_price-zscore-3_price-dist_from_high-10': 'alpha-4',
};

/**
 * Alpha Lifecycle Monitor (Phase 6 surface). Reads the REAL experiment_registry: each alpha's lifecycle
 * STATE, real backtest IC / Sharpe / p-value / deflated-Sharpe and the immutable evaluation verdict.
 * Read-only. NO action control — promotion to APPROVED is human-only and live trading is not built
 * (L2_DRY_RUN_ONLY). HONESTY: the forward paper-track store now feeds a REAL held-out paper Sharpe per
 * alpha, so the recommendation can be a genuine PAPER_RETURN_ONLY (paper return holds up, no decay) or
 * DEGRADED/RETIRED (real out-of-sample Sharpe drop). `live_ic` stays null because the offline source has
 * per-period portfolio returns but no cross-sectional IC panel — we never fabricate an IC we cannot
 * compute, and the paper track NEVER unlocks any automatic promotion (auto_approved=0).
 */

const mockRows: LifecycleRow[] = [
  { alpha_id: 'h_zscore_3', state: 'TESTING', generator: 'heuristic:zscore', days_in_state: null,
    backtest_ic: 0.047, sharpe: 0.13, pvalue: 0.18, net_sharpe: 1.99, deflated_sharpe: 0.21, turnover: 0.58,
    verdict: 'REQUIRES_HUMAN_REVIEW', live_ic: null, recommendation: 'NO_LIVE_DATA', alerts: [],
    research_state: 'TESTING', deployment_state: 'OFFLINE', health_state: 'HEALTHY',
    health_reason: 'NONE', triple_source: 'SYNTHETIC' },
  { alpha_id: 'h_accel_10', state: 'TESTING', generator: 'heuristic:accel', days_in_state: null,
    backtest_ic: -0.024, sharpe: -0.11, pvalue: 0.57, net_sharpe: -1.69, deflated_sharpe: -0.30, turnover: 0.58,
    verdict: 'REQUIRES_HUMAN_REVIEW', live_ic: null, recommendation: 'NO_LIVE_DATA', alerts: [],
    research_state: 'TESTING', deployment_state: 'OFFLINE', health_state: 'HEALTHY',
    health_reason: 'NONE', triple_source: 'SYNTHETIC' },
  { alpha_id: 'h_dist_from_high_20', state: 'VALIDATION', generator: 'heuristic:dist_from_high', days_in_state: null,
    backtest_ic: 0.031, sharpe: 0.42, pvalue: 0.09, net_sharpe: 0.88, deflated_sharpe: 0.11, turnover: 0.33,
    verdict: 'REQUIRES_HUMAN_REVIEW', live_ic: null, recommendation: 'NO_LIVE_DATA', alerts: [],
    research_state: 'VALIDATED', deployment_state: 'OFFLINE', health_state: 'HEALTHY',
    health_reason: 'NONE', triple_source: 'SYNTHETIC' },
];

const mockPayload: LifecycleMonitorPayload['summary'] = {
  total: 3, state_counts: { TESTING: 2, VALIDATION: 1 }, live_tracked: 0, paper_tracked: 0,
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
  const [paperDetailAlphaId, setPaperDetailAlphaId] = useState<string | null>(null);

  const cols: Column<LifecycleRow>[] = [
    { key: 'alpha', header: bi('因子', 'Alpha'), render: r => <span className="mono text-ink">{r.alpha_id}</span> },
    { key: 'state', header: bi('状态', 'State'), render: r => <StatusChip value={r.state} /> },
    { key: 'triple', header: bi('三态 研究/部署/健康', 'Machines R/D/H'), render: r => {
      if (r.triple_source === 'UNKNOWN' || !r.research_state) return <span className="text-faint">—</span>;
      const unhealthy = r.health_state !== null && r.health_state !== 'HEALTHY';
      return (
        <span className="flex flex-col gap-0.5 text-[11px] leading-tight" title={
          bi(`三态来源:${r.triple_source}(STORED=双写列,PROJECTED=旧态投影,SYNTHETIC=合成)`,
             `triple source: ${r.triple_source}`)}>
          <span className="text-faint">{bi('研究', 'R')} <span className="mono text-ink">{r.research_state}</span></span>
          <span className="text-faint">{bi('部署', 'D')} <span className="mono text-ink">{r.deployment_state}</span></span>
          <span className="text-faint">{bi('健康', 'H')} <span className={`mono ${unhealthy ? 'text-neg' : 'text-ink'}`}>
            {r.health_state}{r.health_reason && r.health_reason !== 'NONE' ? ` · ${r.health_reason}` : ''}
          </span></span>
        </span>
      );
    } },
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
    { key: 'paper_track', header: bi('纸面轨迹', 'Paper Track'), render: r => {
      const slot = PAPER_TRACK_SLOT[r.alpha_id];
      if (!slot) return <span className="text-faint">—</span>;
      const active = paperDetailAlphaId === slot;
      return (
        <PillToggle label={active ? bi('收起', 'hide') : bi('查看', 'view')} active={active}
          onClick={() => setPaperDetailAlphaId(active ? null : slot)} />
      );
    } },
  ];

  return (
    <PageShell>
      <PageHeader title="Alpha Lifecycle Monitor" titleZh="Alpha 生命周期监控"
        subtitle={bi('每个 Alpha 的真实生命周期态与回测评估制品(IC/夏普/p值/裁决);实时衰减建议需实盘数据',
          'Each alpha’s real lifecycle state + backtest evaluation artifacts (IC/Sharpe/p-value/verdict); the live-decay recommendation needs a live feed')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && payload?.data_provenance === 'EXPERIMENT_REGISTRY'}
        source={usingBackend ? 'EXPERIMENT_REGISTRY' : 'FRONTEND_LIFECYCLE_MOCK'} error={error} />

      <LoadingVeil active={apiState === 'loading'}>
      {/* Honesty: backtest-side fields are REAL (immutable experiment_registry artifacts). The forward
          paper-track store feeds a REAL held-out paper Sharpe per tracked alpha, compared APPLES-TO-APPLES
          to the train Sharpe — PAPER_RETURN_ONLY means paper return holds up (no IC to certify further),
          DEGRADED/RETIRED means a real out-of-sample Sharpe drop, NO_LIVE_DATA means no paper track yet.
          `live_ic` stays null: the offline source has no cross-sectional IC panel. CAVEAT: held-out tails
          are short (~24 periods) so the paper Sharpe is noise-dominated — read it as plumbing/coverage, not
          proven edge. State is uniformly TESTING in the real registry — nothing promoted (auto_approved=0). */}
      <div className="rounded-md border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 text-2xs leading-relaxed text-amber-200/90">
        {bi('诚实标注:回测侧字段(IC/夏普/p值/紧缩夏普/裁决)来自真实 experiment_registry 不可变制品。前向 paper 追踪库现按真实「留出尾部」给每个被追踪 Alpha 算真样本外纸面夏普,与训练段夏普同口径对比——PAPER_RETURN_ONLY=纸面收益未衰退(但无 IC 故不下完全健康结论),DEGRADED/RETIRED=真样本外夏普下滑,NO_LIVE_DATA=尚无纸面追踪。实时 IC 仍置空:离线源无截面 IC 面板,不伪造算不出的 IC。注意:留出尾部很短(约 24 期),纸面夏普被噪声主导——当作管路/覆盖度看,不是已证实的 edge。真库状态均为 TESTING——尚无晋级(auto_approved=0)。',
            'Honest note: backtest-side fields (IC/Sharpe/p-value/deflated-SR/verdict) come from the real immutable experiment_registry. The forward paper-track store now feeds a REAL held-out paper Sharpe per tracked alpha, compared apples-to-apples to the train Sharpe — PAPER_RETURN_ONLY = paper return holds up (no IC to certify full health), DEGRADED/RETIRED = a real out-of-sample Sharpe drop, NO_LIVE_DATA = no paper track yet. Live IC stays null: the offline source has no cross-sectional IC panel and we never fabricate an IC. CAVEAT: held-out tails are short (~24 periods) so the paper Sharpe is noise-dominated — read it as plumbing/coverage, not proven edge. The real registry is uniformly TESTING — nothing promoted (auto_approved=0).')}
      </div>

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究/干跑(L2)· 监控只给建议不驱动状态 · 晋级到 APPROVED 人工专属 · 实盘未构建',
          'Read-only · research / dry-run (L2) · the monitor only RECOMMENDS, it drives no state · promotion to APPROVED is human-only · live trading is not built')}
      </div>

      <KpiStrip cols={7}>
        <StatTile label={bi('总数', 'Total')} value={String(summary.total)} accent />
        <StatTile label={bi('在测', 'Testing')} value={String(summary.state_counts['TESTING'] ?? 0)} tone="info"
          sub={bi(`${stateEntries.length} 个状态`, `${stateEntries.length} states`)} />
        <StatTile label={bi('纸面追踪', 'Paper-tracked')} value={String(summary.paper_tracked ?? 0)}
          tone={(summary.paper_tracked ?? 0) > 0 ? 'info' : 'muted'}
          sub={bi('真留出尾部', 'held-out tail')} />
        <StatTile label={bi('实时 IC 追踪', 'Live-IC tracked')} value={String(summary.live_tracked)} tone="muted"
          sub={bi('离线源无 IC', 'no IC in source')} />
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

      {paperDetailAlphaId && (
        <Panel eyebrow={bi('纸面回放钻取', 'Paper-Track Drill-Down')}
          title={bi(`${paperDetailAlphaId} 的纸面回放详情`, `Paper-replay detail for ${paperDetailAlphaId}`)}>
          <PaperTrackDetail alphaId={paperDetailAlphaId} />
        </Panel>
      )}
      </LoadingVeil>
    </PageShell>
  );
}
