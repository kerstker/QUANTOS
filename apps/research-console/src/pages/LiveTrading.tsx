import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, Chip, StatusChip, LoadingVeil, Callout,
  type Column,
} from '@/ui/primitives';
import { money, pct, pctPoints, fixed, stamp } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchLiveTradingSnapshot } from '@/lib/api/research-console-api';
import type {
  LiveTradingPayload, LiveTradingForwardAlpha, LiveTradingWouldSubmitOrder, LiveTradingTca,
} from '@/lib/types/research';

/**
 * Live Trading — Stage 0(真前向 paper-track 证据钟)+ Stage 1(Binance 测试网执行栈)只读状态展示。
 * 聚合 tools/{forward_track_advance,live_track_advance}.py 写的真实 JSON 快照。
 * 诚实边界:纯诊断,不驱动任何自动化或晋级;真钱 Go/No-Go 是 Phase 3A 人工评审。测试网是交易所
 * 官方沙盒零真实资本,不是真钱——L2_DRY_RUN_ONLY 全程未破。
 */

const mockPayload: LiveTradingPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_SNAPSHOT',
  forward_track: { present: false }, execution_stack: { present: false },
  honesty_note: '尚无实盘路线快照 — 运行 python -m tools.forward_track_advance / tools.live_track_advance。',
};

const TIER_ZH: Record<string, string> = { AUTO: '自动执行', NEEDS_HUMAN_REVIEW: '需人工审查' };
const MODE_ZH: Record<string, string> = { preview: '预览(不下单)', submit: '提交' };

export default function LiveTrading() {
  const { payload, apiState, error, usingBackend, snapshotState } = useConsoleSnapshot<LiveTradingPayload>(
    s => fetchLiveTradingSnapshot(s));
  const p: LiveTradingPayload = usingBackend ? payload! : mockPayload;
  const ft = p.forward_track ?? { present: false };
  const lt = p.execution_stack ?? { present: false };
  const alphas: LiveTradingForwardAlpha[] = ft.alphas ?? [];
  const wouldSubmit: LiveTradingWouldSubmitOrder[] = lt.would_submit ?? [];
  const gateWeeks = ft.evidence_gate_weeks ?? 8;

  const alphaCols: Column<LiveTradingForwardAlpha>[] = [
    { key: 'id', header: bi('因子', 'Alpha'), render: a => <span className="mono text-ink">{a.alpha_id}</span> },
    { key: 'weeks', header: bi('证据钟', 'Evidence Clock'), align: 'right', render: a => {
        const done = a.n_forward_periods ?? 0;
        const ok = (a.weeks_to_go_no_go ?? gateWeeks) <= 0;
        return <span className={ok ? 'text-pos' : 'text-info'}>{done}/{gateWeeks} {bi('周', 'wk')}</span>;
      } },
    { key: 'sharpe', header: bi('前向夏普', 'Paper Sharpe'), align: 'right', mono: true,
      render: a => a.paper_sharpe == null ? <span className="text-faint">—</span> : (
        <span className={a.paper_sharpe > 0 ? 'text-pos' : 'text-neg'}>{fixed(a.paper_sharpe, 2)}</span>) },
    { key: 'ref', header: bi('历史参照', 'Backtest Ref'), align: 'right', mono: true,
      render: a => a.backtest_reference_sharpe == null ? '—' :
        `${fixed(a.backtest_reference_sharpe, 2)} (${a.reference_weeks ?? '—'}${bi('周', 'wk')})` },
    { key: 'ic', header: bi('前向 IC', 'Paper IC'), align: 'right', mono: true,
      render: a => a.paper_ic == null ? <span className="text-faint">—</span> : fixed(a.paper_ic, 3) },
    { key: 'ret', header: bi('累计收益', 'Cum Return'), align: 'right', mono: true,
      render: a => a.cum_return == null ? '—' : <span className={a.cum_return >= 0 ? 'text-pos' : 'text-neg'}>{pct(a.cum_return, 1)}</span> },
    { key: 'pending', header: bi('待兑现', 'Pending'), align: 'right',
      render: a => (a.pending_decisions ?? 0) > 0
        ? <Chip label={a.pending_decisions} tone="warn" /> : <span className="text-faint">0</span> },
    { key: 'last', header: bi('最后决策', 'Last Decision'), mono: true,
      render: a => a.last_decision_ts ? stamp(a.last_decision_ts) : '—' },
  ];

  const orderCols: Column<LiveTradingWouldSubmitOrder>[] = [
    { key: 'inst', header: bi('标的', 'Instrument'), render: o => <span className="mono text-ink">{o.instrument_id}</span> },
    { key: 'side', header: bi('方向', 'Side'),
      render: o => <Chip label={o.side} tone={o.side === 'BUY' ? 'pos' : 'neg'} /> },
    { key: 'qty', header: bi('数量', 'Quantity'), align: 'right', mono: true, render: o => fixed(o.quantity, 6) },
  ];

  const recon = lt.reconciliation ?? {};
  const reconOk = recon.proceed !== false;
  const riskBreach = lt.risk_breach;
  const tca: LiveTradingTca | null = lt.tca ?? null;
  const orderStats = lt.order_stats ?? null;
  const parity = lt.parity ?? null;
  const emergency = lt.emergency ?? null;
  const tcaByInst = tca ? Object.entries(tca.by_instrument ?? {}) : [];

  return (
    <PageShell>
      <PageHeader title="Live Trading" titleZh="实盘路线状态"
        subtitle={bi('Stage 0 真前向证据钟 + Stage 1 测试网执行栈 · 只读诊断展示,不驱动任何自动化或晋级',
          'Stage 0 true forward evidence clock + Stage 1 testnet execution stack · read-only diagnostic, drives no automation or promotion')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_LIVE_TRADING_SNAPSHOT'}
        source={usingBackend ? (p.snapshot_present ? 'REAL_LIVE_TRADING_SNAPSHOT' : 'NO_LIVE_TRADING_SNAPSHOT')
          : 'FRONTEND_LIVE_TRADING_MOCK'}
        error={error}
        noDataYet={usingBackend && !p.snapshot_present}
        dataStatus={usingBackend && lt.hard_stop ? 'BLOCKED' : snapshotState} />

      <LoadingVeil active={apiState === 'loading'}>
      <div className="flex items-center gap-3">
        <StatusChip value={p.verdict} />
        <span className="text-2xs text-muted">{p.honesty_note}</span>
      </div>

      <Callout tone="warn" summary={bi('L2_DRY_RUN_ONLY 未破 · 测试网零真实资本 · 无自动下单',
        'L2_DRY_RUN_ONLY intact · testnet is zero real capital · nothing here auto-trades')}>
        {bi('L2_DRY_RUN_ONLY 未破:测试网(testnet)是交易所官方沙盒、零真实资本,不是真钱;真钱 Go/No-Go '
          + '需 ≥8 周前向证据 + 人工 Phase 3A 评审。这里没有任何自动下单——AUTO 层是人工签署 mandate 限额内的机械执行,'
          + 'NEEDS_HUMAN_REVIEW 永远不提交。',
          'L2_DRY_RUN_ONLY intact: testnet is the exchange’s own sandbox with zero real capital, not real money; '
          + 'real-money Go/No-Go needs ≥8 weeks of forward evidence + human Phase 3A review. Nothing here auto-trades — '
          + 'the AUTO tier is mechanical execution within a human-signed mandate’s limits; NEEDS_HUMAN_REVIEW never submits.')}
      </Callout>

      {/* --- Stage 0: 前向证据钟 --- */}
      <Panel eyebrow={bi('Stage 0', 'STAGE 0')} title={bi('真前向 paper-track 证据钟', 'True Forward Paper-Track Evidence Clock')}
        right={ft.present ? <Chip label={bi(`更新于 ${stamp(ft.ran_at)}`, `updated ${stamp(ft.ran_at)}`)} tone="info" /> : undefined}>
        {ft.present ? (
          <>
            <DataTable rows={alphas} columns={alphaCols} getKey={a => a.alpha_id}
              empty={bi('尚无前向决策', 'No forward decisions yet')} />
            {(ft.alerts ?? []).length > 0 && (
              <div className="mt-3 flex flex-col gap-1">
                {ft.alerts!.map((a, i) => (
                  <div key={i} className="rounded border border-warn/30 bg-warn/[0.06] px-2.5 py-1.5 text-2xs text-warn">
                    ⚠ {a}
                  </div>
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="py-6 text-center text-2xs text-faint">
            {bi('尚无前向跟踪快照 — 运行 python -m tools.forward_track_advance', 'No forward-track snapshot yet — run python -m tools.forward_track_advance')}
          </div>
        )}
      </Panel>

      {/* --- Stage 1: 测试网执行 --- */}
      <Panel eyebrow={bi('Stage 1', 'STAGE 1')} title={bi('测试网执行栈', 'Testnet Execution Stack')}
        right={lt.present ? (
          <div className="flex items-center gap-2">
            {lt.venue && <Chip label={lt.venue} tone="info" />}
            <Chip label={MODE_ZH[lt.mode ?? ''] ?? lt.mode} tone={lt.mode === 'submit' ? 'warn' : 'info'} />
            <Chip label={bi(`更新于 ${stamp(lt.ran_at)}`, `updated ${stamp(lt.ran_at)}`)} tone="muted" />
          </div>
        ) : undefined}>
        {lt.present ? (
          <div className="flex flex-col gap-4">
            {lt.hard_stop && (
              <div className="rounded border border-neg/40 bg-neg/[0.08] px-3 py-2 text-2xs text-neg">
                ⛔ {bi('本轮硬停(HARD STOP)——对账 drift 或风险越限,已拒绝本轮提交', 'HARD STOP this cycle — reconciliation drift or risk breach, submission refused')}
              </div>
            )}
            <KpiStrip cols={6}>
              <StatTile label={bi('分级', 'Tier')}
                value={<Chip label={TIER_ZH[lt.tier ?? ''] ?? lt.tier ?? '—'} tone={lt.tier === 'AUTO' ? 'pos' : 'warn'} />} />
              <StatTile label={bi('已提交', 'Submitted')} value={String(lt.n_submitted ?? 0)} tone="pos" />
              <StatTile label={bi('待人工审查', 'Pending Review')} value={String(lt.n_pending_review ?? 0)}
                tone={(lt.n_pending_review ?? 0) > 0 ? 'warn' : 'muted'} />
              <StatTile label={bi('门拒绝', 'Gate Rejected')} value={String(lt.n_rejected_by_gate ?? 0)} tone="muted" />
              <StatTile label={bi('过滤丢弃', 'Filter Dropped')} value={String(lt.n_dropped_by_filters ?? 0)} tone="muted" />
              <StatTile label={bi('提交错误', 'Submit Errors')} value={String(lt.n_submission_errors ?? 0)}
                tone={(lt.n_submission_errors ?? 0) > 0 ? 'neg' : 'muted'} />
            </KpiStrip>

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
              <div className="rounded-lg border border-line bg-inset/40 px-3.5 py-3">
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi('账户', 'Account')}
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="tnum text-lg font-bold text-ink">{lt.aum != null ? money(lt.aum, 'USD') : '—'}</span>
                  <span className="text-2xs text-faint">{bi('可用余额', 'available')}</span>
                </div>
                {lt.peak_equity != null && (
                  <div className="mt-1 text-2xs text-muted">{bi('峰值权益', 'peak equity')}: {money(lt.peak_equity, 'USD')}</div>
                )}
              </div>

              <div className="rounded-lg border border-line bg-inset/40 px-3.5 py-3">
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi('对账', 'Reconciliation')}
                </div>
                <div className="flex items-center gap-2">
                  <Chip label={reconOk ? bi('一致', 'in sync') : bi('存在 drift', 'drift')} tone={reconOk ? 'pos' : 'neg'} />
                  {recon.max_abs_drift != null && (
                    <span className="text-2xs text-muted mono">max|drift|={fixed(recon.max_abs_drift, 6)}</span>
                  )}
                </div>
              </div>

              <div className="rounded-lg border border-line bg-inset/40 px-3.5 py-3">
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi('风险监控', 'Risk Monitor')}
                </div>
                {riskBreach ? (
                  <div className="flex flex-col gap-1">
                    <Chip label={bi('越限', 'breach')} tone="neg" />
                    <span className="text-2xs text-muted">
                      {bi('回撤', 'drawdown')} {pctPoints(riskBreach.drawdown_pct ?? 0, 1)}
                    </span>
                  </div>
                ) : <Chip label={bi('正常', 'nominal')} tone="pos" />}
              </div>
            </div>

            {lt.mode === 'preview' && wouldSubmit.length > 0 && (
              <div>
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi('预览模式:本会真实提交的订单(未真提交)', 'Preview mode: orders that would submit (not actually sent)')}
                </div>
                <DataTable rows={wouldSubmit} columns={orderCols} getKey={(o, i) => `${o.instrument_id}-${i}`} />
              </div>
            )}

            {emergency && (
              <div className="rounded border border-neg/40 bg-neg/[0.06] px-3 py-2 text-2xs">
                <div className="mb-1 font-semibold text-neg">
                  {bi('紧急减仓(独立通道,不经共用风控门)', 'Emergency flatten (independent channel, bypasses the shared gate)')}
                </div>
                <span className="text-muted mono">
                  target_gross={money(emergency.target_gross, 'USD')} · {bi('提交', 'submitted')} {emergency.n_submitted} ·{' '}
                  {bi('拒绝', 'rejected')} {emergency.n_rejected} · {bi('错误', 'errors')} {emergency.n_errors}
                </span>
              </div>
            )}

            {/* --- TCA 成交质量 + live-paper 对账 + 订单统计(2026-07-05 机构级补全) --- */}
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
              <div className="rounded-lg border border-line bg-inset/40 px-3.5 py-3">
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi('成交质量 TCA(累计)', 'TCA — cumulative fill quality')}
                </div>
                {tca && tca.n_with_slippage > 0 ? (
                  <div className="flex flex-col gap-1 text-2xs text-muted">
                    <div>
                      <span className="tnum text-base font-bold text-ink">
                        {tca.notional_weighted_slippage_bps != null ? fixed(tca.notional_weighted_slippage_bps, 2) : '—'}
                      </span>{' '}
                      bps {bi('名义额加权滑点', 'notional-weighted slippage')}
                    </div>
                    <div className="mono">
                      mean {tca.mean_slippage_bps != null ? fixed(tca.mean_slippage_bps, 2) : '—'} · worst{' '}
                      {tca.worst_slippage_bps != null ? fixed(tca.worst_slippage_bps, 2) : '—'} · n={tca.n_with_slippage}
                    </div>
                    <div className={tca.weighted_minus_assumed_bps != null && tca.weighted_minus_assumed_bps > 0 ? 'text-neg' : 'text-pos'}>
                      {bi(`vs 回测假设 ${fixed(tca.assumed_cost_bps, 0)}bps:`, `vs backtest assumption ${fixed(tca.assumed_cost_bps, 0)}bps:`)}{' '}
                      {tca.weighted_minus_assumed_bps != null
                        ? `${tca.weighted_minus_assumed_bps > 0 ? '+' : ''}${fixed(tca.weighted_minus_assumed_bps, 2)}bps`
                        : '—'}
                    </div>
                    {tcaByInst.length > 0 && (
                      <div className="mono text-faint">
                        {tcaByInst.map(([inst, b]) => `${inst}:${b.mean_slippage_bps != null ? fixed(b.mean_slippage_bps, 1) : '—'}bps`).join(' · ')}
                      </div>
                    )}
                  </div>
                ) : (
                  <span className="text-2xs text-faint">{bi('尚无确认成交(preview 模式不产生成交)', 'No confirmed fills yet (preview mode produces none)')}</span>
                )}
              </div>

              <div className="rounded-lg border border-line bg-inset/40 px-3.5 py-3">
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi('live-paper 对账(本周期)', 'Live-vs-paper parity (this cycle)')}
                </div>
                {parity ? (
                  <div className="flex flex-col gap-1 text-2xs text-muted">
                    <div>
                      <Chip label={parity.paper_source === 'forward_ledger' ? bi('纸面=台账记录', 'paper=recorded ledger') : bi('纸面=重算信号', 'paper=recomputed')}
                        tone={parity.paper_source === 'forward_ledger' ? 'pos' : 'warn'} />
                    </div>
                    <div className="mono">
                      {bi('纸面腿', 'paper legs')} {parity.n_paper_legs} · {bi('有订单', 'with order')} {parity.n_with_live_order} ·{' '}
                      max|gap|={parity.max_abs_gap_notional != null ? money(parity.max_abs_gap_notional, 'USD') : '—'}
                    </div>
                    {parity.unexpected_live_positions.length > 0 && (
                      <div className="text-warn">
                        ⚠ {bi('账外仓位', 'unexpected positions')}: {parity.unexpected_live_positions.join(', ')}
                      </div>
                    )}
                  </div>
                ) : (
                  <span className="text-2xs text-faint">{bi('尚无对账快照', 'No parity snapshot yet')}</span>
                )}
              </div>

              <div className="rounded-lg border border-line bg-inset/40 px-3.5 py-3">
                <div className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-faint">
                  {bi('订单统计(账本累计,含撤单/超时归因)', 'Order stats (ledger cumulative, incl. cancel/timeout)')}
                </div>
                {orderStats && Object.keys(orderStats).length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(orderStats).map(([st, n]) => (
                      <Chip key={st} label={`${st}: ${n}`}
                        tone={st === 'FILLED' ? 'pos' : st === 'REJECTED' || st === 'EXPIRED' ? 'neg' : st === 'CANCELED' ? 'warn' : 'muted'} />
                    ))}
                  </div>
                ) : (
                  <span className="text-2xs text-faint">{bi('账本暂无订单', 'No orders in the ledger yet')}</span>
                )}
              </div>
            </div>

            {(lt.alerts ?? []).length > 0 && (
              <div className="flex flex-col gap-1">
                {lt.alerts!.map((a, i) => (
                  <div key={i} className="rounded border border-warn/30 bg-warn/[0.06] px-2.5 py-1.5 text-2xs text-warn">
                    ⚠ {a}
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="py-6 text-center text-2xs text-faint">
            {bi('尚无测试网执行快照 — 运行 python -m tools.live_track_advance', 'No testnet execution snapshot yet — run python -m tools.live_track_advance')}
          </div>
        )}
      </Panel>

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 两份离线快照(forward_track_status.json / live_track_status.json)· 分级授权(mandate)fail-closed 到底 · '
          + '紧急减仓走独立 reduce-only 通道(2026-07-05 用户拍板;结构上只能减仓,治理测试钉死)',
          'Read-only · two offline snapshots (forward_track_status.json / live_track_status.json) · tiered mandate authorization is fail-closed to the core · '
          + 'emergency flatten runs an independent reduce-only channel (user-approved 2026-07-05; structurally reduce-only, governance-pinned)')}
      </div>
      </LoadingVeil>
    </PageShell>
  );
}
