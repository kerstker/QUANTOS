import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, StatusChip, type Column,
} from '@/ui/primitives';
import { signed, fixed } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchCarryReadinessSnapshot } from '@/lib/api/research-console-api';
import type { CarryReadinessPayload, CarrySpec, CarryCapacityRow } from '@/lib/types/research';

/**
 * Carry Readiness — funder-facing summary of the ONE signal that survived the full gauntlet (funding carry):
 * economic basis, design-robustness (simplicity ladder), regime-robustness, realistic-cost survival, capacity,
 * crowding decay, and the honest remaining deployment gaps. Read-only; research-only (no broker/order path).
 * HONESTY: the headline Sharpe is inflated by the rich 2020–21 premium — funding has compressed ~5×, so the
 * forward expectation is lower. Never presented as a live/deployable promise.
 */

const mockPayload: CarryReadinessPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_SNAPSHOT',
  strategy: {}, robustness: {}, capacity: {}, deployment_gaps: [],
  honesty_note: 'No carry snapshot generated yet — run tools/crypto_carry_{strategy,walkforward,capacity}.py off-gate.',
};

const VERDICT_ZH: Record<string, string> = {
  VALIDATED_NOT_LIVE_READY: '过全套研究关卡 · 尚未实盘就绪',
  PARTIAL_OVERFIT_RISK: '部分稳健 · 有过拟合风险',
  REFUTED: '被证伪', INCOMPLETE: '验证不完整', NO_SNAPSHOT: '尚无快照',
};
const SPEC_ZH: Record<string, string> = {
  raw_neg_funding: 'raw −funding(最笨)', z_neg_decay7: 'z(decay7)', composite: '调优 composite',
};

export default function CarryReadiness() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<CarryReadinessPayload>(
    s => fetchCarryReadinessSnapshot(s));
  const p: CarryReadinessPayload = usingBackend ? payload! : mockPayload;
  const st = p.strategy ?? {};
  const specs = p.robustness?.specs ?? {};
  const band = p.capacity?.capacity_curve?.['impact_10bps'] ?? p.capacity?.capacity_curve?.['impact_20bps'] ?? {};
  const fundByYear = p.capacity?.funding_bps_per_year ?? {};

  const specRows = ['raw_neg_funding', 'z_neg_decay7', 'composite']
    .filter(k => specs[k]).map(k => ({ key: k, ...specs[k] } as CarrySpec & { key: string }));
  const _py = (py?: Record<string, number>) =>
    py ? Object.entries(py).map(([y, v]) => `${y.slice(2)}:${v > 0 ? '+' : ''}${v}`).join(' ') : '—';

  const specCols: Column<CarrySpec & { key: string }>[] = [
    { key: 'spec', header: bi('信号(笨→精)', 'Spec'), render: r => bi(SPEC_ZH[r.key] ?? r.key, r.key) },
    { key: 'sr', header: bi('净夏普', 'Net SR'), align: 'right', mono: true,
      render: r => r.net_sharpe == null ? <span className="text-faint">—</span> : (
        <span className={r.net_sharpe > 0 ? 'text-pos' : 'text-neg'}>{signed(r.net_sharpe, 2)}</span>) },
    { key: 'dd', header: bi('最大回撤', 'maxDD'), align: 'right', mono: true,
      render: r => r.max_drawdown == null ? '—' : `${(r.max_drawdown * 100).toFixed(0)}%` },
    { key: 'worst', header: bi('最差日', 'worst'), align: 'right', mono: true,
      render: r => r.worst_day == null ? '—' : `${(r.worst_day * 100).toFixed(1)}%` },
    { key: 'py', header: bi('逐年夏普', 'per-year'), mono: true, render: r => <span className="text-2xs">{_py(r.per_year)}</span> },
  ];

  const capRows: CarryCapacityRow[] = band.rows ?? [];
  const capCols: Column<CarryCapacityRow>[] = [
    { key: 'aum', header: 'AUM', align: 'right', mono: true, render: r => `$${(r.aum / 1e6).toFixed(0)}M` },
    { key: 'sr', header: bi('净夏普', 'Net SR'), align: 'right', mono: true,
      render: r => <span className={r.net_sharpe > 0.5 ? 'text-pos' : r.net_sharpe > 0 ? 'text-warn' : 'text-neg'}>{signed(r.net_sharpe, 2)}</span> },
    { key: 'ann', header: bi('年化', 'ann%'), align: 'right', mono: true, render: r => `${(r.ann_return * 100).toFixed(1)}%` },
    { key: 'pnl', header: bi('年化盈亏', 'ann $P&L'), align: 'right', mono: true, render: r => `$${(r.ann_pnl_usd / 1e6).toFixed(1)}M` },
  ];

  return (
    <PageShell>
      <PageHeader title="Carry Readiness" titleZh="Carry 实盘就绪"
        subtitle={bi('唯一过全套关卡的真信号(资金费 carry)· 给资金方的诚实尽调摘要',
          'The one signal that survived the full gauntlet (funding carry) · honest diligence summary for allocators')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_CARRY_OFFLINE_SNAPSHOT'}
        source={usingBackend ? (p.snapshot_present ? 'REAL_CARRY_OFFLINE_SNAPSHOT' : 'NO_CARRY_SNAPSHOT') : 'FRONTEND_CARRY_MOCK'}
        error={error} />

      <div className="flex items-center gap-3">
        <StatusChip value={p.verdict} />
        <span className="text-2xs text-muted">{bi(VERDICT_ZH[p.verdict] ?? p.verdict, p.verdict)}</span>
      </div>

      <div className="rounded-md border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 text-2xs leading-relaxed text-amber-200/90">
        {bi('诚实标注:资金费 carry 是有经济学根据的风险溢价,过了全套关卡(非过拟合——连最笨的 −funding 都成立、扛过 2022 熊市、真实分层滑点下存活)。但头条夏普被肥沃的 2020–21 抬高:资金费 5 年压缩约 5×,前向预期更低;且未上实盘、回撤尾巴(25–30% / 最差日 −8%)未控。研究只读、零下单/券商/凭证路径,L2_DRY_RUN_ONLY,晋级实盘人工专属。',
            'Honest note: funding carry is an economically-grounded risk premium that passed the full gauntlet (NOT overfit — even the dumbest −funding spec holds, survived the 2022 bear, lives under realistic tiered slippage). But the headline Sharpe is inflated by the rich 2020–21 premium: funding has compressed ~5× over 5 years, so the forward expectation is lower; and it is not live-executed, with an uncontrolled 25–30% drawdown tail (−8% worst day). Read-only, no broker/order path; L2_DRY_RUN_ONLY, promotion to live is human-only.')}
      </div>

      <KpiStrip cols={5}>
        <StatTile label={bi('净夏普 @5bps', 'Net SR @5bps')} value={st.sharpe_net_5bps == null ? '—' : signed(st.sharpe_net_5bps, 2)}
          accent sub={bi('全历史·偏乐观', 'full hist · optimistic')} />
        <StatTile label={bi('日收资金费', 'Funding/day')} value={st.avg_funding_bps_day == null ? '—' : `${st.avg_funding_bps_day}bps`}
          tone="info" sub={bi('在衰减', 'decaying')} />
        <StatTile label={bi('日换手', 'Turnover/day')} value={st.avg_turnover == null ? '—' : fixed(st.avg_turnover, 2)} tone="muted" />
        <StatTile label={bi('设计稳健性', 'Design-robust')} value={bi(p.robustness?.verdict === 'DESIGN_ROBUST' ? '通过' : (p.robustness?.verdict ?? '—'), p.robustness?.verdict ?? '—')}
          tone={p.robustness?.verdict === 'DESIGN_ROBUST' ? 'pos' : 'muted'} sub={bi('简化阶梯', 'simplicity ladder')} />
        <StatTile label={bi('流动币数', 'Liquid coins')} value={String(p.robustness?.n_liquid ?? p.capacity?.base_sharpe != null ? (p.robustness?.n_liquid ?? '—') : '—')} tone="muted" />
      </KpiStrip>

      <Panel eyebrow={bi('非过拟合检验', 'DESIGN ROBUSTNESS')}
        title={bi('简化阶梯:连最笨的 carry 也成立 → 真溢价非调参', 'Simplicity ladder: even the dumbest carry holds → real premium, not tuning')}>
        {specRows.length ? <DataTable rows={specRows} columns={specCols} getKey={r => r.key} /> : (
          <div className="text-2xs text-faint">{bi('无稳健性快照', 'no robustness snapshot')}</div>)}
      </Panel>

      <Panel eyebrow={bi('容量', 'CAPACITY')}
        title={bi('容量曲线(流动半数 · sqrt 市场冲击 @10bps/10%ADV)', 'Capacity curve (liquid half · sqrt impact @10bps/10%ADV)')}>
        {capRows.length ? <DataTable rows={capRows} columns={capCols} getKey={r => String(r.aum)} /> : (
          <div className="text-2xs text-faint">{bi('无容量快照', 'no capacity snapshot')}</div>)}
        {band.capacity_half_sharpe_usd != null && (
          <div className="mt-2 text-2xs text-muted">
            {bi(`容量(夏普≥半)≈ $${(band.capacity_half_sharpe_usd / 1e6).toFixed(0)}M · $盈亏峰值 @ $${((band.pnl_peak_aum_usd ?? 0) / 1e6).toFixed(0)}M = $${((band.pnl_peak_usd ?? 0) / 1e6).toFixed(0)}M/年 · 超峰值后投更多赚更少`,
              `capacity (Sharpe≥half) ≈ $${(band.capacity_half_sharpe_usd / 1e6).toFixed(0)}M · $P&L peak @ $${((band.pnl_peak_aum_usd ?? 0) / 1e6).toFixed(0)}M = $${((band.pnl_peak_usd ?? 0) / 1e6).toFixed(0)}M/yr · beyond peak, more capital earns less`)}
          </div>)}
      </Panel>

      <Panel eyebrow={bi('拥挤 / 溢价衰减', 'CROWDING')}
        title={bi('逐年资金费收取(bps/天)—— 压缩 = 溢价被套利', 'Funding collected per year (bps/day) — compression = premium being arbed')}>
        <div className="flex flex-wrap gap-2 text-2xs">
          {Object.keys(fundByYear).length ? Object.entries(fundByYear).map(([y, v]) => (
            <span key={y} className="rounded border border-line px-2 py-1 mono">
              {y}: <span className={v >= 5 ? 'text-pos' : v >= 3 ? 'text-warn' : 'text-neg'}>{v}</span>
            </span>)) : <span className="text-faint">{bi('无数据', 'no data')}</span>}
        </div>
        {p.capacity?.pnl_top5_share != null && (
          <div className="mt-2 text-2xs text-muted">
            {bi(`P&L 集中度:前 5 币占 ${(p.capacity.pnl_top5_share * 100).toFixed(0)}%(越低越分散越稳)`,
              `P&L concentration: top-5 coins ${(p.capacity.pnl_top5_share * 100).toFixed(0)}% (lower = better diversified)`)}
          </div>)}
      </Panel>

      <Panel eyebrow={bi('上实盘前的缺口', 'DEPLOYMENT GAPS')} title={bi('诚实清单:这些没解决前不上实盘', 'Honest list: not live until these are closed')}>
        <ul className="list-disc pl-5 text-2xs text-muted leading-relaxed">
          {(p.deployment_gaps.length ? p.deployment_gaps : [bi('无快照', 'no snapshot')]).map((g, i) => <li key={i}>{g}</li>)}
        </ul>
      </Panel>

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究/干跑(L2)· 三份离线快照(strategy/walkforward/capacity)· 晋级实盘人工专属 · 头条夏普非前向承诺',
          'Read-only · research / dry-run (L2) · three offline snapshots · promotion to live is human-only · the headline Sharpe is not a forward promise')}
      </div>
    </PageShell>
  );
}
