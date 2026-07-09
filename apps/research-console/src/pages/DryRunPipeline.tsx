import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, StatusChip, type Column,
} from '@/ui/primitives';
import { signed, stamp } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchDryRunSnapshot } from '@/lib/api/research-console-api';
import type { DryRunPipelinePayload, DryRunRunRow } from '@/lib/types/research';

/**
 * Dry-Run Pipeline — the end-to-end run, surfaced read-only. This is the PROOF that the pre-trade risk gate is
 * a RUNTIME chokepoint (not tested dead code): every order intent is screened by the gate (fail-closed,
 * kill-switch latched -> all rejected) before a paper fill is ever simulated. Nothing is sent to a broker;
 * paper fills reconcile to an exact average-cost identity (residual ~0); every step is hash-chain audited.
 * Reads the append-only DryRunStore. L2_DRY_RUN_ONLY — live = swap the adapter + human approval (non-goal).
 */

const mockPayload: DryRunPipelinePayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_RUN',
  n_runs: 0, latest: {}, runs: [],
  safety_note: 'No dry-run yet — run tools/dry_run_carry.py off-gate to produce a DryRunRun.',
};

const VERDICT_ZH: Record<string, string> = {
  DRY_RUN_OK: '干跑跑通 · 门已运行时强制', NO_RUN: '尚无干跑',
};

const fmtUsd = (v?: number) => (v == null ? '—' : `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`);
const fmtResidual = (v?: number) => (v == null ? '—' : Math.abs(v) < 1e-6 ? '≈0' : v.toExponential(1));

// Pure SVG two-line equity curve (raw=accent solid, vol-target=info dashed). Deterministic; no Date/random.
function EquityCurve({ raw, vt }: { raw: { equity: number }[]; vt: { equity: number }[] }) {
  const W = 640, H = 160, pad = 6;
  const all = [...raw.map(p => p.equity), ...vt.map(p => p.equity)];
  if (!raw.length || !all.length) return null;
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const path = (pts: { equity: number }[]) => pts.map((p, i) => {
    const x = pad + (i / Math.max(pts.length - 1, 1)) * (W - 2 * pad);
    const y = H - pad - ((p.equity - lo) / span) * (H - 2 * pad);
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="equity curve">
      <path d={path(raw)} fill="none" stroke="var(--accent, #6ea8fe)" strokeWidth={1.5} />
      {vt.length > 0 && <path d={path(vt)} fill="none" stroke="var(--info, #4fd1c5)" strokeWidth={1.2} strokeDasharray="3 2" />}
    </svg>
  );
}

export default function DryRunPipeline() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<DryRunPipelinePayload>(
    s => fetchDryRunSnapshot(s));
  const p: DryRunPipelinePayload = usingBackend ? payload! : mockPayload;
  const has = p.snapshot_present && p.runs.length > 0;
  const latest = (has ? (p.latest as DryRunRunRow) : null);
  const pnl = latest?.pnl ?? {};
  const nFills = latest?.detail?.n_fills ?? 0;
  const mp = latest?.detail?.mode === 'multi_period' ? latest.detail : null;  // multi-period rolling run

  // pipeline stages (with the latest run's live numbers) — the chokepoint story, left to right
  const stages = [
    { zh: '信号 / 权重', en: 'Signal / weights', val: latest ? `${latest.n_intents} ${bi('意图', 'intents')}` : '—' },
    { zh: '预交易门 (chokepoint)', en: 'Pre-trade gate', val: latest ? `✓${latest.n_approved} ✕${latest.n_rejected}` : '—', accent: true },
    { zh: '干跑成交 (不真发)', en: 'Dry-run fills', val: `${nFills} ${bi('纸面', 'paper')}` },
    { zh: '对账 PnL', en: 'Reconcile', val: fmtResidual(pnl.residual) + bi(' 残差', ' resid') },
    { zh: '哈希链审计', en: 'Audit chain', val: latest ? latest.audit_root.slice(0, 10) : '—' },
  ];

  const posRows = Object.entries(pnl.positions ?? {})
    .map(([inst, qty]) => ({ inst, qty }))
    .sort((a, b) => Math.abs(b.qty) - Math.abs(a.qty))
    .slice(0, 12);
  const posCols: Column<{ inst: string; qty: number }>[] = [
    { key: 'inst', header: bi('标的', 'Instrument'), mono: true, render: r => r.inst },
    { key: 'side', header: bi('方向', 'Side'),
      render: r => <span className={r.qty >= 0 ? 'text-pos' : 'text-neg'}>{r.qty >= 0 ? bi('多', 'LONG') : bi('空', 'SHORT')}</span> },
    { key: 'qty', header: bi('手数(纸面)', 'Qty (paper)'), align: 'right', mono: true,
      render: r => r.qty.toLocaleString('en-US') },
  ];

  const runCols: Column<DryRunRunRow>[] = [
    { key: 'time', header: bi('时间(UTC)', 'Time (UTC)'), mono: true, render: r => stamp(r.timestamp) },
    { key: 'strat', header: bi('策略', 'Strategy'), render: r => r.strategy_id },
    { key: 'intents', header: bi('意图', 'Intents'), align: 'right', mono: true, render: r => r.n_intents },
    { key: 'appr', header: bi('门批准', 'Approved'), align: 'right', mono: true,
      render: r => <span className="text-pos">{r.n_approved}</span> },
    { key: 'rej', header: bi('门拒绝', 'Rejected'), align: 'right', mono: true,
      render: r => <span className={r.n_rejected ? 'text-neg' : 'text-faint'}>{r.n_rejected}</span> },
    { key: 'fills', header: bi('成交', 'Fills'), align: 'right', mono: true, render: r => r.detail?.n_fills ?? 0 },
    { key: 'pnl', header: bi('收益', 'Return'), align: 'right', mono: true,
      render: r => {
        // multi-period rows carry total_return (%), single-snapshot rows carry total_pnl ($)
        const mpRet = r.pnl?.total_return, usd = r.pnl?.total_pnl;
        const v = mpRet != null ? mpRet : usd;
        return <span className={(v ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>
          {mpRet != null ? `${(mpRet * 100).toFixed(1)}%` : fmtUsd(usd)}</span>;
      } },
    { key: 'resid', header: bi('对账残差', 'Residual'), align: 'right', mono: true, render: r => fmtResidual(r.pnl?.residual) },
    { key: 'audit', header: bi('审计根', 'Audit'), mono: true, render: r => <span className="text-2xs text-faint">{r.audit_root.slice(0, 10)}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Dry-Run Pipeline" titleZh="端到端干跑流水线"
        subtitle={bi('信号 → 预交易门(运行时 chokepoint,fail-closed)→ 纸面成交(绝不真发)→ 对账 → 哈希链审计',
          'Signal → pre-trade gate (runtime chokepoint, fail-closed) → paper fills (never sent) → reconcile → audit chain')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_DRY_RUN_OFFLINE_SNAPSHOT'}
        source={usingBackend ? (has ? 'REAL_DRY_RUN_OFFLINE_SNAPSHOT' : 'NO_DRY_RUN') : 'FRONTEND_DRY_RUN_MOCK'}
        error={error} />

      <div className="flex items-center gap-3">
        <StatusChip value={p.verdict} />
        <span className="text-2xs text-muted">{bi(VERDICT_ZH[p.verdict] ?? p.verdict, p.verdict)}</span>
        {has && <span className="text-2xs text-faint">{bi(`共 ${p.n_runs} 次干跑`, `${p.n_runs} dry-runs`)}</span>}
      </div>

      <div className="rounded-md border border-emerald-500/30 bg-emerald-500/[0.06] px-3 py-2 text-2xs leading-relaxed text-emerald-200/90">
        {bi('这页是"预交易风控门是运行时 chokepoint(而非测过的死代码)"的证明:每一笔订单意图都必须先过门(fail-closed;kill-switch 拉下 → 全部拒绝),通过的才会进入纸面成交。绝不真发券商;纸面成交按平均成本对账到恒等式(残差≈0);每一步进哈希链审计。只读、零下单/券商/凭证路径,L2_DRY_RUN_ONLY。实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。',
            'This page is the PROOF the pre-trade gate is a RUNTIME chokepoint (not tested dead code): every order intent must pass the gate first (fail-closed; kill-switch latched → all rejected) before any paper fill. Nothing is sent to a broker; paper fills reconcile to an exact average-cost identity (residual ≈0); every step is hash-chain audited. Read-only, no broker/order path; L2_DRY_RUN_ONLY. Live = swap the adapter + human approval = non-goal.')}
      </div>

      <KpiStrip cols={6}>
        <StatTile label={bi('意图', 'Intents')} value={latest ? String(latest.n_intents) : '—'} tone="muted" />
        <StatTile label={bi('门批准', 'Gate approved')} value={latest ? String(latest.n_approved) : '—'} tone="pos" accent />
        <StatTile label={bi('门拒绝', 'Gate rejected')} value={latest ? String(latest.n_rejected) : '—'}
          tone={latest && latest.n_rejected > 0 ? 'neg' : 'muted'} />
        <StatTile label={bi('纸面成交', 'Paper fills')} value={has ? String(nFills) : '—'} tone="info" />
        <StatTile label={bi('净 PnL', 'Net PnL')} value={fmtUsd(pnl.total_pnl)} tone="muted"
          sub={bi('= 重平衡成本', '= rebalance cost')} />
        <StatTile label={bi('对账残差', 'Residual')} value={fmtResidual(pnl.residual)}
          tone={pnl.residual != null && Math.abs(pnl.residual) < 1e-6 ? 'pos' : 'warn'} sub={bi('恒等式', 'identity')} />
      </KpiStrip>

      {mp && (
        <>
          <KpiStrip cols={6}>
            <StatTile label={bi('总收益', 'Total return')} value={pnl.total_return == null ? '—' : `${(pnl.total_return * 100).toFixed(1)}%`}
              tone={(pnl.total_return ?? 0) >= 0 ? 'pos' : 'neg'} accent />
            <StatTile label={bi('夏普(周→年)', 'Sharpe (wk→yr)')} value={pnl.sharpe == null ? '—' : signed(pnl.sharpe, 2)} tone="info"
              sub={pnl.sharpe_vt == null ? undefined : bi(`VT ${signed(pnl.sharpe_vt, 2)}`, `VT ${signed(pnl.sharpe_vt, 2)}`)} />
            <StatTile label={bi('最大回撤', 'Max drawdown')} value={pnl.max_drawdown == null ? '—' : `${(pnl.max_drawdown * 100).toFixed(0)}%`} tone="warn"
              sub={pnl.max_drawdown_vt == null
                ? bi('截面价差,非净敞口', 'cross-sectional, not net')
                : bi(`VT ${(pnl.max_drawdown_vt * 100).toFixed(0)}% · 截面价差,非净敞口`, `VT ${(pnl.max_drawdown_vt * 100).toFixed(0)}% · cross-sectional, not net`)} />
            <StatTile label={bi('净敞口', 'Net exposure')} value={pnl.avg_net_exposure_pct == null ? '—' : `${(pnl.avg_net_exposure_pct * 100).toFixed(1)}%`}
              tone={(pnl.avg_net_exposure_pct ?? 1) < 0.05 ? 'pos' : 'warn'} sub={bi('≈0 = 美元中性', '≈0 = dollar-neutral')} />
            <StatTile label={bi('周期数', 'Periods')} value={String(mp.n_periods ?? mp.equity_curve?.length ?? 0)} tone="muted" sub={bi('周度', 'weekly')} />
            <StatTile label={bi('累计被门拒', 'Total gate-rejected')} value={String(latest?.n_rejected ?? 0)}
              tone={(latest?.n_rejected ?? 0) > 0 ? 'neg' : 'muted'} />
          </KpiStrip>
          <Panel eyebrow={bi('净值曲线', 'EQUITY CURVE')}
            title={bi('门强制下的滚动净值(蓝实线=raw,虚线=vol-target 压尾)· 含资金费现金流',
              'Gate-enforced rolling equity (solid=raw, dashed=vol-target) · funding cash-flow included')}>
            <EquityCurve raw={mp.equity_curve ?? []} vt={mp.equity_curve_vt ?? []} />
            <div className="mt-1 text-2xs text-muted">
              {bi(`累计资金费 $${(pnl.total_funding ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} · 对账残差 ${fmtResidual(pnl.residual)}(价格腿恒等式)。诚实发现:书本已近美元中性(净敞口 ${((pnl.avg_net_exposure_pct ?? 0) * 100).toFixed(1)}%),故 ${((pnl.max_drawdown ?? 0) * 100).toFixed(0)}% 回撤是 L/S 截面价差本身的波动、非净市场敞口/整数手舍入(经 $1M↔$100M 规模不变验证);funding 是骑在这条波动价差上的较小稳定边,无法干净隔离。`,
                `funding $${(pnl.total_funding ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} · residual ${fmtResidual(pnl.residual)}. Honest finding: the book is already ~dollar-neutral (net ${((pnl.avg_net_exposure_pct ?? 0) * 100).toFixed(1)}%), so the ${((pnl.max_drawdown ?? 0) * 100).toFixed(0)}% drawdown is the inherent L/S cross-sectional spread — NOT net-market exposure or integer-lot rounding (verified scale-invariant $1M↔$100M); funding is the smaller steady edge riding on that volatile spread, not cleanly separable.`)}
            </div>
          </Panel>
        </>
      )}

      <Panel eyebrow={bi('流水线', 'PIPELINE')} title={bi('一条连续的链(最新一次干跑)', 'One continuous flow (latest dry-run)')}>
        <div className="flex flex-wrap items-stretch gap-1.5">
          {stages.map((s, i) => (
            <React.Fragment key={s.en}>
              <div className={`flex-1 min-w-[120px] rounded-md border px-2.5 py-2 ${s.accent ? 'border-accent/40 bg-accent/[0.07]' : 'border-line bg-inset/40'}`}>
                <div className="text-2xs text-muted">{bi(s.zh, s.en)}</div>
                <div className={`mt-1 text-xs mono ${s.accent ? 'text-accent' : 'text-fg'}`}>{s.val}</div>
              </div>
              {i < stages.length - 1 && <div className="self-center text-muted">→</div>}
            </React.Fragment>
          ))}
        </div>
        {has && (
          <div className="mt-2 text-2xs text-muted">
            {bi(`换手 ${fmtUsd(pnl.turnover)} · 成本 ${fmtUsd(pnl.costs)} · 期末权益 ${fmtUsd(pnl.equity)} · 适配器二次拒 ${latest?.detail?.n_adapter_rejected ?? 0}`,
              `turnover ${fmtUsd(pnl.turnover)} · costs ${fmtUsd(pnl.costs)} · end equity ${fmtUsd(pnl.equity)} · adapter-rejected ${latest?.detail?.n_adapter_rejected ?? 0}`)}
          </div>)}
      </Panel>

      <Panel eyebrow={bi('最新组合(纸面持仓)', 'LATEST BOOK')}
        title={bi('门批准后的纸面目标持仓(按手数绝对值前 12)', 'Gate-approved paper target positions (top 12 by |qty|)')}>
        {posRows.length ? <DataTable rows={posRows} columns={posCols} getKey={r => r.inst} /> : (
          <div className="text-2xs text-faint">{bi('无持仓(尚无干跑或全被门拒)', 'no positions (no run yet, or all gate-rejected)')}</div>)}
      </Panel>

      <Panel eyebrow={bi('运行历史', 'RUN HISTORY')} title={bi('每次干跑都是一条可复现记录(append-only)', 'Each dry-run is a reproducible record (append-only)')}>
        {p.runs.length ? <DataTable rows={[...p.runs].reverse()} columns={runCols} getKey={r => r.run_id} /> : (
          <div className="text-2xs text-faint">{bi('无干跑记录 —— 门外跑 tools/dry_run_carry.py 生成', 'no dry-run records — run tools/dry_run_carry.py off-gate')}</div>)}
      </Panel>

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究/干跑(L2)· DryRunStore(data/cache/dry_run.db)· 门 fail-closed + kill-switch + 哈希链审计 · 绝不真发券商 · 晋级实盘人工专属',
          'Read-only · research / dry-run (L2) · DryRunStore · gate fail-closed + kill-switch + hash-chain audit · never sent to a broker · promotion to live is human-only')}
      </div>
    </PageShell>
  );
}
