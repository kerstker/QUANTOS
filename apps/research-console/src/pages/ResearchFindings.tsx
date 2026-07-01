import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import { PageShell, PageHeader, ProvenanceRibbon, Panel } from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchResearchFindings } from '@/lib/api/research-console-api';
import type { ResearchFindingsPayload } from '@/lib/types/research';

/**
 * Research Findings — 现实主义/宏观/组合三件研究成果只读聚合:
 *  · 容量带(RE-1,冲击 + 参与率绑定,UNCALIBRATED_PRINCIPLED 区间)
 *  · 宏观 regime 条件化(FRED,carry 爱 risk-on / crowd 爱 risk-off)
 *  · 组合分散(carry+crowd corr≈0 → 组合夏普 > 单条、回撤减半)
 * 研究诊断,非晋级门禁。
 */

const mockPayload: ResearchFindingsPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_SNAPSHOT',
  capacity: {}, macro_regime: {}, combo: {},
  honesty_note: 'No research-findings snapshots yet — run tools/{capacity_research,macro_regime_research,combo_book_research}.py off-gate.',
};

function fmtAum(v: number | null | undefined): string {
  if (v == null) return '—';
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}k`;
  return `$${v.toFixed(0)}`;
}
function shTone(v: number): string {
  return v > 1.0 ? 'text-pos' : v > 0.3 ? 'text-info' : v > 0 ? 'text-warn' : 'text-neg';
}

export default function ResearchFindings() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<ResearchFindingsPayload>(
    s => fetchResearchFindings(s));
  const p: ResearchFindingsPayload = usingBackend ? payload! : mockPayload;

  const capBooks = Object.entries(p.capacity.books ?? {});
  const comboStd = p.combo.standalone ?? {};
  const comboC = p.combo.combo ?? {};
  const macroBooks = Object.entries(p.macro_regime.books ?? {});

  return (
    <PageShell>
      <PageHeader title="Research Findings" titleZh="研究成果"
        subtitle={bi('容量带 · 宏观 regime 条件化 · 组合分散 — 现实主义/数据方向成果只读聚合,诊断非门禁',
          'Capacity · macro-regime conditioning · diversification — read-only research aggregate, diagnostic not a gate')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_RESEARCH_FINDINGS'}
        source={usingBackend ? (p.snapshot_present ? 'REAL_RESEARCH_FINDINGS' : 'NO_FINDINGS_SNAPSHOT') : 'FRONTEND_FINDINGS_MOCK'}
        error={error} />

      <div className="rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">{p.honesty_note}</div>

      {/* 容量带 */}
      <Panel eyebrow={bi('容量带 · RE-1', 'CAPACITY BAND · RE-1')}
        title={bi('参与率绑定的诚实容量区间(参与率 5–20% × 执行铺开 1–5 天)', 'Participation-bound honest capacity range')}>
        {capBooks.length ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {capBooks.map(([name, b]) => (
              <div key={name} className="rounded border border-line px-3 py-2">
                <div className="text-xs font-medium text-ink">{name}</div>
                <div className="mono text-lg text-accent">
                  {fmtAum(b.calibrated_range?.low ?? b.aum_low)} – {fmtAum(b.calibrated_range?.high ?? b.aum_high)}
                </div>
                <div className="text-2xs text-muted">
                  {bi('绑定', 'binding')}={b.binding ?? '—'} · {bi('毛夏普', 'gross SR')} {b.gross_sharpe ?? '—'}
                </div>
              </div>
            ))}
          </div>
        ) : <div className="text-2xs text-faint">{bi('无容量快照', 'no capacity snapshot')}</div>}
      </Panel>

      {/* 组合分散 */}
      <Panel eyebrow={bi('组合分散 · carry+crowd', 'DIVERSIFICATION · carry+crowd')}
        title={bi(`两条边相关性 ${p.combo.corr_carry_crowd ?? '—'} → 组合是否更平滑`,
          `edge correlation ${p.combo.corr_carry_crowd ?? '—'} → does combining smooth it`)}>
        {Object.keys(comboStd).length ? (
          <table className="w-full text-2xs">
            <thead><tr className="text-muted">
              <th className="text-left py-1">book</th><th className="text-right">{bi('年化夏普', 'ann SR')}</th>
              <th className="text-right">{bi('最大回撤', 'max DD')}</th><th className="text-right">{bi('总收益', 'total ret')}</th>
            </tr></thead>
            <tbody className="mono">
              {[['carry', comboStd.carry], ['crowd_contra', comboStd.crowd_contra],
                ['combo 50/50', comboC.equal_weight], ['combo 逆波动', comboC.inverse_vol]].map(([lbl, m]: any) => m ? (
                <tr key={lbl} className="border-t border-line/50">
                  <td className="py-1 text-ink">{lbl}</td>
                  <td className={`text-right ${shTone(m.ann_sharpe)}`}>{m.ann_sharpe.toFixed(2)}</td>
                  <td className="text-right text-warn">{(m.max_drawdown * 100).toFixed(0)}%</td>
                  <td className="text-right">{(m.total_return * 100).toFixed(0)}%</td>
                </tr>) : null)}
            </tbody>
          </table>
        ) : <div className="text-2xs text-faint">{bi('无组合快照', 'no combo snapshot')}</div>}
        {p.combo.note && <div className="mt-2 text-2xs text-faint">{p.combo.note}</div>}
      </Panel>

      {/* 宏观 regime */}
      <Panel eyebrow={bi('宏观 regime 条件化 · FRED', 'MACRO REGIME · FRED')}
        title={bi('边在不同宏观 regime 的年化夏普(carry 爱 risk-on / crowd 爱 risk-off)',
          'edge Sharpe by macro regime')}>
        {macroBooks.length ? (
          <div className="space-y-2">
            {macroBooks.map(([name, b]) => (
              <div key={name} className="rounded border border-line px-3 py-2">
                <div className="text-xs font-medium text-ink">{name}</div>
                {Object.entries(b.dims ?? {}).map(([dim, d]) => (
                  <div key={dim} className="mt-1 flex flex-wrap items-center gap-2 text-2xs">
                    <span className="text-muted w-28">{dim}</span>
                    {Object.entries(d.buckets ?? {}).sort().map(([lb, bk]) => (
                      <span key={lb} className="rounded border border-line px-2 py-0.5 mono">
                        {lb} <span className={shTone(bk.ann_sharpe)}>{bk.ann_sharpe >= 0 ? '+' : ''}{bk.ann_sharpe.toFixed(2)}</span>
                        <span className="text-faint"> n{bk.n}</span>
                      </span>
                    ))}
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : <div className="text-2xs text-faint">{bi('无宏观快照', 'no macro snapshot')}</div>}
        {p.macro_regime.note && <div className="mt-2 text-2xs text-faint">{p.macro_regime.note}</div>}
      </Panel>

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究诊断 · gross/regime 口径见 honesty_note · 不驱动任何自动晋级/下单',
          'Read-only · research diagnostic · drives no automatic promotion/trading')}
      </div>
    </PageShell>
  );
}
