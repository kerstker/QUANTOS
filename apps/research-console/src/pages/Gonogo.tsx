import { bi } from '@/lib/i18n/bilingual';
import { PageShell, PageHeader, ProvenanceRibbon, Panel, LoadingVeil } from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchGonogo } from '@/lib/api/research-console-api';
import type { GonogoPayload } from '@/lib/types/research';

/**
 * GoNoGo Evidence — 盘后自动生成的 Go/No-Go 证据包只读展示(Phase 5 item 3):
 *  · 前向证据钟(各 alpha 已积累周数 / 距 8 周门槛 / paper sharpe·IC）
 *  · 逐 venue 执行 PnL/成本归因(权益变动 · 未实现 · gross/net · TCA 执行成本估计）
 *  · 事故台账(风控越限 / 对账硬停 / 紧急减仓 / 哨兵失败 次数）
 * 只聚合既有真实产物,**不下 Go/No-Go 结论**——结论是 Phase 3A 人工评审。
 */

const mockPayload: GonogoPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_SNAPSHOT',
  readiness: 'L2_DRY_RUN_ONLY',
  forward_evidence: { present: false },
  execution_evidence: {},
  incidents: {},
  honesty_note: 'No GoNoGo snapshot yet — run tools/gonogo_report.py (scheduled daily 23:00) to generate one.',
};

function fmtNum(v: number | null | undefined, digits = 2): string {
  return v == null ? '—' : v.toFixed(digits);
}
function fmtSigned(v: number | null | undefined): string {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}`;
}
function ddTone(v: number | undefined): string {
  return v == null ? 'text-muted' : v > 0.2 ? 'text-neg' : v > 0.1 ? 'text-warn' : 'text-info';
}

export default function Gonogo() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<GonogoPayload>(
    s => fetchGonogo(s));
  const p: GonogoPayload = usingBackend ? payload! : mockPayload;

  const fwAlphas = Object.entries(p.forward_evidence.alphas ?? {});
  const venues = Object.entries(p.execution_evidence ?? {});
  const inc = p.incidents ?? {};

  return (
    <PageShell>
      <PageHeader title="GoNoGo Evidence" titleZh="GoNoGo 证据"
        subtitle={bi('前向证据 · 逐 venue PnL/成本归因 · 事故台账 — 盘后自动聚合,只读,结论由 Phase 3A 人工评审',
          'Forward evidence · per-venue PnL/cost attribution · incidents — auto post-close aggregate, read-only')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_GONOGO_SNAPSHOT'}
        source={usingBackend ? (p.snapshot_present ? 'REAL_GONOGO_SNAPSHOT' : 'NO_GONOGO_SNAPSHOT') : 'FRONTEND_GONOGO_MOCK'}
        error={error} />

      <div className="rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">{p.honesty_note}</div>

      <LoadingVeil active={apiState === 'loading'}>
      {/* 前向证据钟 */}
      <Panel eyebrow={bi('前向证据钟 · Stage 0', 'FORWARD EVIDENCE · Stage 0')}
        title={bi(`距 8 周门槛的证据积累(gate_weeks ${p.forward_evidence.gate_weeks ?? 8})`,
          'Evidence accumulated toward the 8-week gate')}>
        {fwAlphas.length ? (
          <table className="w-full text-2xs">
            <thead><tr className="text-muted">
              <th className="text-left py-1">alpha</th><th className="text-right">{bi('已积累周', 'weeks')}</th>
              <th className="text-right">{bi('距门槛', 'to gate')}</th><th className="text-right">paper SR</th>
              <th className="text-right">paper IC</th><th className="text-right">{bi('累计', 'cum ret')}</th>
            </tr></thead>
            <tbody className="mono">
              {fwAlphas.map(([aid, a]) => (
                <tr key={aid} className="border-t border-line/50">
                  <td className="py-1 text-ink">{aid}</td>
                  <td className="text-right">{a.weeks_accumulated ?? '—'}</td>
                  <td className="text-right text-warn">{a.weeks_to_gate ?? '—'}</td>
                  <td className="text-right">{fmtNum(a.paper_sharpe)}</td>
                  <td className="text-right">{fmtNum(a.paper_ic, 4)}</td>
                  <td className="text-right">{a.cum_return == null ? '—' : `${(a.cum_return * 100).toFixed(1)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="text-2xs text-faint">{bi('无前向证据快照', 'no forward evidence snapshot')}</div>}
      </Panel>

      {/* 逐 venue PnL/成本归因 */}
      <Panel eyebrow={bi('执行 PnL/成本归因 · Stage 1', 'EXECUTION PnL/COST · Stage 1')}
        title={bi('逐 venue 权益变动 · 未实现 · gross/net · TCA 执行成本估计', 'Per-venue equity change · unrealized · TCA cost')}>
        {venues.length ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {venues.map(([name, v]) => {
              const pn = v.pnl;
              const tca = v.tca;
              return (
                <div key={name} className="rounded border border-line px-3 py-2">
                  <div className="text-xs font-medium text-ink">{name}</div>
                  {pn?.present ? (
                    <>
                      <div className="mono text-sm">
                        <span className="text-muted">{bi('权益', 'equity')} </span>
                        {fmtNum(pn.first_equity)} → {fmtNum(pn.last_equity)}{' '}
                        <span className={pn.total_equity_change != null && pn.total_equity_change >= 0 ? 'text-pos' : 'text-neg'}>
                          ({fmtSigned(pn.total_equity_change)})
                        </span>
                      </div>
                      <div className="text-2xs text-muted mono">
                        {bi('未实现', 'uPnL')} {fmtNum(pn.latest_unrealized_pnl)} · gross {fmtNum(pn.latest_gross_notional)} ·
                        net {fmtNum(pn.latest_net_notional)}
                      </div>
                      <div className="text-2xs text-muted mono">
                        {bi('执行成本估计', 'exec cost est')} {fmtNum(pn.execution_cost_estimate)}
                        {tca?.notional_weighted_slippage_bps != null &&
                          ` · TCA ${fmtNum(tca.notional_weighted_slippage_bps)}bps vs ${fmtNum(tca.assumed_cost_bps, 0)}bps`}
                      </div>
                    </>
                  ) : <div className="text-2xs text-faint">{bi('无 PnL(该 venue 无权益曲线)', 'no PnL (no equity curve)')}</div>}
                  {v.equity && (
                    <div className={`text-2xs mono ${ddTone(v.equity.max_drawdown_observed)}`}>
                      {bi('观测期最大回撤', 'observed max DD')} {v.equity.max_drawdown_observed == null ? '—'
                        : `${(v.equity.max_drawdown_observed * 100).toFixed(1)}%`}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : <div className="text-2xs text-faint">{bi('无执行证据(驱动器还没跑过 / 无 key)', 'no execution evidence')}</div>}
      </Panel>

      {/* 事故台账 */}
      <Panel eyebrow={bi('事故台账', 'INCIDENTS')}
        title={bi('风控越限 · 对账硬停 · 紧急减仓 · 哨兵失败 次数', 'Risk breaches · recon hard-stops · emergency flattens · sentinel failures')}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {([
            ['风控越限', 'risk breaches', inc.risk_breaches],
            ['对账硬停', 'recon hard-stops', inc.reconciliation_hard_stops],
            ['紧急减仓', 'emergency flattens', inc.emergency_flattens],
            ['哨兵失败', 'sentinel failures', inc.sentinel_failures],
          ] as const).map(([zh, en, n]) => (
            <div key={en} className="rounded border border-line px-3 py-2 text-center">
              <div className={`mono text-lg ${n ? 'text-warn' : 'text-muted'}`}>{n ?? 0}</div>
              <div className="text-2xs text-faint">{bi(zh, en)}</div>
            </div>
          ))}
        </div>
      </Panel>

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 盘后自动聚合 · PnL 归因是权益总账(含已实现+未实现+资金费)· 不下 Go/No-Go 结论(Phase 3A 人工评审)',
          'Read-only · auto post-close · PnL is total equity ledger · draws NO Go/No-Go conclusion (Phase 3A human review)')}
      </div>
      </LoadingVeil>
    </PageShell>
  );
}
