import type { ReactNode } from 'react';

import { PageShell } from '@/ui/primitives';
import type { JsonObject, MftSnapshot } from '@/workbench/contracts';
import { usePortfolioRiskData } from '@/workbench/usePortfolioRiskData';
import type { MftReadModelState } from '@/workbench/useMftReadModel';

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(section: JsonObject, key: string): string | null {
  const value = section[key];
  return typeof value === 'string' && value.trim().length > 0 ? value : null;
}

function number(section: JsonObject, key: string): number | null {
  const value = section[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function boolean(section: JsonObject, key: string): boolean | null {
  const value = section[key];
  return typeof value === 'boolean' ? value : null;
}

function percent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

function points(value: number): string {
  return `${value.toFixed(2)} pp`;
}

function fixed(value: number, digits = 2): string {
  return Number.isFinite(value) ? value.toFixed(digits) : 'UNAVAILABLE';
}

function currency(value: number | null): string {
  return value === null
    ? 'UNAVAILABLE'
    : new Intl.NumberFormat('en-US', {
      style: 'currency', currency: 'USD', maximumFractionDigits: 2,
    }).format(value);
}

function timestamp(value: number | null): string {
  return value === null ? 'UNAVAILABLE' : new Date(value).toISOString().replace('.000Z', 'Z');
}

function Panel({ eyebrow, title, right, testId, children }: {
  eyebrow: string;
  title: string;
  right?: ReactNode;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-panel shadow-sm" data-testid={testId}>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-faint">{eyebrow}</p>
          <h2 className="mt-1 text-base font-semibold text-ink">{title}</h2>
        </div>
        {right}
      </header>
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

function SourceStatus({ label, status, detail }: { label: string; status: string; detail: string | null }) {
  const available = status === 'AVAILABLE' || status === 'LIVE';
  return (
    <div className="rounded-lg border border-line bg-inset/45 p-3">
      <p className="text-[10px] uppercase tracking-[0.12em] text-faint">{label}</p>
      <p className={`mt-1 break-all font-mono text-xs font-semibold ${available ? 'text-pos' : 'text-warn'}`}>
        {status} · {detail ?? 'VERIFIED'}
      </p>
    </div>
  );
}

function trustedMft(state: MftReadModelState): MftSnapshot | null {
  const snapshot = state.snapshot;
  if (state.phase !== 'LIVE' || snapshot === null || snapshot.status !== 'AVAILABLE'
    || snapshot.freshness.status !== 'CURRENT' || snapshot.missing_sections.length !== 0) return null;
  return snapshot;
}

function mftUnavailableReason(state: MftReadModelState): string {
  if (state.reason) return state.reason;
  if (state.snapshot?.missing_sections.length) {
    return `MISSING_SECTIONS:${state.snapshot.missing_sections.join(',')}`;
  }
  return state.phase;
}

interface ActualPosition {
  instrument: string;
  quantity: number;
}

type ActualPositionResult =
  | { status: 'AVAILABLE'; items: ActualPosition[] }
  | { status: 'MISSING' }
  | { status: 'MALFORMED' };

function actualPositions(snapshot: MftSnapshot): ActualPositionResult {
  const raw = snapshot.positions.items;
  if (raw === undefined) return { status: 'MISSING' };
  if (!Array.isArray(raw)) return { status: 'MALFORMED' };
  const parsed: ActualPosition[] = [];
  for (const item of raw) {
    if (!record(item) || typeof item.instrument !== 'string' || !item.instrument.trim()
      || typeof item.quantity !== 'number' || !Number.isFinite(item.quantity)) {
      return { status: 'MALFORMED' };
    }
    parsed.push({ instrument: item.instrument, quantity: item.quantity });
  }
  return { status: 'AVAILABLE', items: parsed };
}

function unavailableActualReason(snapshot: MftSnapshot | null, state: MftReadModelState): string | null {
  if (snapshot === null) return mftUnavailableReason(state);
  const status = text(snapshot.positions, 'status');
  if (status === null || status === 'UNAVAILABLE' || status === 'MISSING') {
    return 'POSITIONS_SECTION_UNAVAILABLE';
  }
  return null;
}

function EmergencyCard({ label, value, detail, blocked }: {
  label: string;
  value: string;
  detail: string;
  blocked: boolean | null;
}) {
  const tone = blocked === null ? 'border-line bg-inset/45 text-muted'
    : blocked ? 'border-neg/35 bg-neg/10 text-neg' : 'border-pos/30 bg-pos/5 text-pos';
  return (
    <div className={`rounded-lg border p-3 ${tone}`}>
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] opacity-75">{label}</p>
      <p className="mt-1 break-all font-mono text-sm font-semibold">{value}</p>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[10px]">
        <span className="break-all opacity-80">{detail}</span>
        {blocked !== null && <span className="rounded border border-current px-1.5 py-0.5 font-mono font-semibold">{blocked ? 'BLOCK' : 'CLEAR'}</span>}
      </div>
    </div>
  );
}

export default function PortfolioRisk() {
  const data = usePortfolioRiskData();
  const portfolio = data.portfolio.status === 'AVAILABLE' ? data.portfolio.payload : null;
  const risk = data.risk.status === 'AVAILABLE' ? data.risk.payload : null;
  const mft = trustedMft(data.mft);
  const actualReason = unavailableActualReason(mft, data.mft);
  const actual = mft && actualReason === null ? actualPositions(mft) : null;
  const targets = portfolio ? Object.entries(portfolio.weights.weights).sort(([a], [b]) => a.localeCompare(b)) : [];
  const riskRows = portfolio?.risk_contributions ?? [];
  const attribution = portfolio?.return_attribution ?? [];

  const killLatched = mft ? boolean(mft.risk, 'kill_switch_latched') : null;
  const mandateStatus = mft ? text(mft.mandate_expiry, 'status') : null;
  const mandateExpiresAt = mft ? number(mft.mandate_expiry, 'expires_at_ms') : null;
  const reconciliationStatus = mft ? text(mft.reconciliation, 'status') : null;
  const marketStatus = mft ? text(mft.market_gap, 'status') : null;
  const reconciliationReview = mft ? boolean(mft.reconciliation, 'requires_manual_review') : null;
  const missingIntervals = mft && Array.isArray(mft.market_gap.missing_intervals)
    ? mft.market_gap.missing_intervals.length
    : null;

  return (
    <PageShell>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Portfolio &amp; Risk</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">组合与风险</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">
            研究组合目标、只读风险归因与 MFT 实际仓位并列工作区。目标和实际保持不同身份、单位与权威来源。
          </p>
        </div>
        <div className="rounded-lg border border-warn/35 bg-warn/10 px-3 py-2 text-right font-mono text-xs font-semibold text-warn">
          L2_DRY_RUN_ONLY · READ ONLY<br />NO BROWSER EDIT / RESET
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <SourceStatus label="Research portfolio" status={data.portfolio.status}
          detail={data.portfolio.reason ?? portfolio?.data_provenance ?? null} />
        <SourceStatus label="Research risk diagnostic" status={data.risk.status}
          detail={data.risk.reason ?? risk?.data_provenance ?? null} />
        <SourceStatus label="MFT actual authority" status={mft ? 'LIVE' : 'UNAVAILABLE'}
          detail={mft ? `${mft.source} · ${mft.freshness.status}` : mftUnavailableReason(data.mft)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel eyebrow="Research target · alpha sleeve weight" title="RESEARCH TARGET（Alpha sleeves）"
          testId="research-target-positions"
          right={<span className="font-mono text-[10px] text-faint">dimension: ALPHA_WEIGHT</span>}>
          {portfolio === null ? (
            <p className="break-all font-mono text-xs text-warn">
              UNAVAILABLE · {data.portfolio.reason ?? data.portfolio.status}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] text-left text-xs">
                <thead className="text-[10px] uppercase tracking-[0.1em] text-faint">
                  <tr><th className="pb-2">Sleeve</th><th className="pb-2 text-right">Target weight</th><th className="pb-2 text-right">Risk budget</th></tr>
                </thead>
                <tbody className="divide-y divide-line/70 font-mono">
                  {targets.map(([alphaId, weight]) => {
                    const contribution = riskRows.find(row => row.alpha_id === alphaId)?.contribution;
                    return (
                      <tr key={alphaId}>
                        <td className="py-2.5 text-accent">{alphaId}</td>
                        <td className="py-2.5 text-right text-ink">{percent(weight)}</td>
                        <td className="py-2.5 text-right text-muted">{contribution === undefined ? 'UNAVAILABLE' : percent(contribution)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {portfolio && (
            <p className="mt-3 border-t border-line/70 pt-3 font-mono text-[10px] text-faint">
              gross target {fixed(portfolio.weights.gross_exposure, 4)} · {portfolio.weights.method ?? 'METHOD_UNAVAILABLE'} · {portfolio.analytics_method}
            </p>
          )}
        </Panel>

        <Panel eyebrow="MFT actual · instrument quantity" title="MFT ACTUAL（Instrument positions）"
          testId="mft-actual-positions"
          right={<span className="font-mono text-[10px] text-faint">dimension: INSTRUMENT_QUANTITY</span>}>
          {actualReason !== null ? (
            <p className="break-all font-mono text-xs text-warn">UNAVAILABLE · {actualReason}</p>
          ) : actual?.status === 'MALFORMED' ? (
            <p className="font-mono text-xs text-neg">UNAVAILABLE · MALFORMED_POSITION_DETAIL</p>
          ) : actual?.status === 'MISSING' || actual === null ? (
            <p className="font-mono text-xs text-warn">
              UNAVAILABLE_IN_V1_DETAIL · position_count {mft ? number(mft.positions, 'position_count') ?? 'UNKNOWN' : 'UNKNOWN'}
            </p>
          ) : actual.items.length === 0 ? (
            <p className="font-mono text-xs text-muted">EMPTY · 0 authority positions</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] text-left text-xs">
                <thead className="text-[10px] uppercase tracking-[0.1em] text-faint">
                  <tr><th className="pb-2">Instrument</th><th className="pb-2 text-right">Actual quantity</th><th className="pb-2 text-right">Target gap</th></tr>
                </thead>
                <tbody className="divide-y divide-line/70 font-mono">
                  {actual.items.map(item => (
                    <tr key={item.instrument}>
                      <td className="py-2.5 text-accent">{item.instrument}</td>
                      <td className="py-2.5 text-right text-ink">{item.quantity.toFixed(6)}</td>
                      <td className="py-2.5 text-right text-warn">UNAVAILABLE</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-3 border-t border-line/70 pt-3 font-mono text-[10px] text-warn">
            GAP UNAVAILABLE · TARGET_ALPHA_VS_ACTUAL_INSTRUMENT · no unit or identity coercion
          </p>
        </Panel>
      </div>

      <Panel eyebrow="Emergency state · always visible" title="应急与对账状态" testId="emergency-state">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <EmergencyCard label="Kill-switch"
            value={killLatched === null ? 'UNAVAILABLE' : killLatched ? 'LATCHED' : 'CLEAR'}
            detail={mft ? text(mft.risk, 'status') ?? 'STATUS_UNKNOWN' : mftUnavailableReason(data.mft)}
            blocked={killLatched === null ? null : killLatched} />
          <EmergencyCard label="Mandate expiry"
            value={mandateStatus ?? 'UNAVAILABLE'}
            detail={mft ? timestamp(mandateExpiresAt) : mftUnavailableReason(data.mft)}
            blocked={mandateStatus === null ? null
              : !mandateStatus.startsWith('VALID') || mandateExpiresAt === null || mandateExpiresAt <= mft!.as_of_ms} />
          <EmergencyCard label="Reconciliation"
            value={reconciliationStatus ?? 'UNAVAILABLE'}
            detail={mft ? `manual_review=${reconciliationReview ?? 'UNKNOWN'}` : mftUnavailableReason(data.mft)}
            blocked={reconciliationStatus === null ? null
              : !['IN_SYNC', 'RECONCILED'].includes(reconciliationStatus) || reconciliationReview !== false} />
          <EmergencyCard label="Market gap"
            value={marketStatus ?? 'UNAVAILABLE'}
            detail={mft ? `missing_intervals=${missingIntervals ?? 'UNKNOWN'}` : mftUnavailableReason(data.mft)}
            blocked={marketStatus === null ? null : marketStatus !== 'CONTIGUOUS' || missingIntervals !== 0} />
        </div>
        <p className="mt-3 text-[10px] leading-4 text-faint">
          这些字段只反映当前 MFT authority；页面没有 kill-switch reset、mandate 修改、风险额度编辑或下单能力。
        </p>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel eyebrow="Research risk budget & exposure" title="风险预算与敞口">
          {portfolio === null ? (
            <p className="font-mono text-xs text-warn">UNAVAILABLE · {data.portfolio.reason ?? data.portfolio.status}</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-line bg-inset/45 p-3">
                <p className="text-[10px] uppercase text-faint">Target gross multiplier</p>
                <p className="mt-1 font-mono text-lg font-semibold text-ink">{fixed(portfolio.weights.gross_exposure, 4)}</p>
              </div>
              <div className="rounded-lg border border-line bg-inset/45 p-3">
                <p className="text-[10px] uppercase text-faint">MFT actual gross notional</p>
                <p className="mt-1 font-mono text-lg font-semibold text-ink">{mft ? currency(number(mft.risk, 'gross_exposure')) : 'UNAVAILABLE'}</p>
              </div>
              {riskRows.map(row => (
                <div key={row.alpha_id} className="rounded-lg border border-line bg-inset/45 p-3">
                  <p className="font-mono text-xs text-accent">{row.alpha_id}</p>
                  <p className="mt-1 font-mono text-sm text-ink">budget {percent(row.contribution)}</p>
                  <p className="mt-1 text-[10px] text-muted">ann. vol {percent(row.annualized_volatility)}</p>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel eyebrow="Backend research attribution" title="收益归因">
          {attribution.length === 0 ? (
            <p className="font-mono text-xs text-warn">UNAVAILABLE · NO_ADMITTED_ATTRIBUTION</p>
          ) : (
            <div className="space-y-2">
              {attribution.map(row => (
                <div key={row.alpha_id} className="grid grid-cols-[1fr_auto_auto] items-center gap-3 rounded-lg border border-line bg-inset/45 p-3 text-xs">
                  <span className="font-mono text-accent">{row.alpha_id}</span>
                  <span className="font-mono text-muted">alpha {points(row.alpha_total_return)}</span>
                  <span className={`font-mono font-semibold ${row.contribution_return >= 0 ? 'text-pos' : 'text-neg'}`}>
                    {points(row.contribution_return)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.85fr_1.15fr]">
        <Panel eyebrow="Drift, decay & reconciliation" title="漂移与衰退">
          <div className="space-y-2">
            <div className="rounded-lg border border-warn/35 bg-warn/10 p-3">
              <p className="text-[10px] uppercase text-warn">Target / actual drift</p>
              <p className="mt-1 font-mono text-xs text-warn">UNAVAILABLE · TARGET_ALPHA_VS_ACTUAL_INSTRUMENT</p>
            </div>
            <div className="rounded-lg border border-line bg-inset/45 p-3">
              <p className="text-[10px] uppercase text-faint">Research decay proxy</p>
              <p className="mt-1 font-mono text-xs text-ink">
                {risk ? `${fixed(risk.alpha.decay_half_life, 1)} periods · ${risk.alpha.candidate_id}` : `UNAVAILABLE · ${data.risk.reason ?? data.risk.status}`}
              </p>
              <p className="mt-1 text-[10px] text-faint">research candidate metric; not MFT live drift</p>
            </div>
            <div className="rounded-lg border border-line bg-inset/45 p-3">
              <p className="text-[10px] uppercase text-faint">MFT reconciliation</p>
              <p className="mt-1 font-mono text-xs text-ink">{reconciliationStatus ?? 'UNAVAILABLE'}</p>
            </div>
          </div>
        </Panel>

        <Panel eyebrow="Diagnostic-only research gate" title="风险报告">
          {risk === null ? (
            <p className="break-all font-mono text-xs text-warn">UNAVAILABLE · {data.risk.reason ?? data.risk.status}</p>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-neg/30 bg-neg/5 p-3">
                <div>
                  <p className="font-mono text-sm font-semibold text-neg">{risk.primary_report.decision}</p>
                  <p className="mt-1 font-mono text-[10px] text-muted">
                    {risk.primary_report.gates_passed}/{risk.primary_report.gates_total} gates · admission not granted
                  </p>
                </div>
                <span className="rounded border border-warn/35 px-2 py-1 font-mono text-[10px] text-warn">DIAGNOSTIC_ONLY</span>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {Object.entries(risk.primary_report.gate_results).map(([gate, passed]) => (
                  <div key={gate} className={`rounded border px-3 py-2 font-mono text-xs ${passed ? 'border-pos/30 text-pos' : 'border-neg/30 text-neg'}`}>
                    {gate} · {passed ? 'PASS' : 'BLOCK'}
                  </div>
                ))}
              </div>
              {risk.primary_report.reasons.length > 0 && (
                <ul className="space-y-1 font-mono text-[10px] text-warn">
                  {risk.primary_report.reasons.map(reason => <li key={reason}>{reason}</li>)}
                </ul>
              )}
            </div>
          )}
        </Panel>
      </div>
    </PageShell>
  );
}
