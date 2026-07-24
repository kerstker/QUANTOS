import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import { PageShell, PageHeader, ProvenanceRibbon, Panel, Chip, LoadingVeil } from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchResearchFindings, fetchLance1mValidation } from '@/lib/api/research-console-api';
import type { ResearchFindingsPayload, Lance1mValidationPayload, Lance1mFeeResult, Lance1mRollingStress } from '@/lib/types/research';

/**
 * Research Findings — 现实主义/宏观/组合三件研究成果只读聚合:
 *  · 容量带(RE-1,冲击 + 参与率绑定,UNCALIBRATED_PRINCIPLED 区间)
 *  · 宏观 regime 条件化(FRED,carry 爱 risk-on / crowd 爱 risk-off)
 *  · 组合分散(carry+crowd corr≈0 → 组合夏普 > 单条、回撤减半)
 * 研究诊断,非晋级门禁。
 *
 * 2026-07-09 导航重组:并入原独立页 Lance 1m Validation(外部策略审计,一次性单点结论,
 * 与本页其余内容同为"只读研究结论聚合、非晋级门禁"的同一种页面模式)——见文末可折叠区块。
 */

const lanceMockPayload: Lance1mValidationPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_SNAPSHOT',
  doc: 'docs/test/lance_1m_strategy.md',
  fee_sweep: {}, rolling_window_stress_1bps: {}, rolling_window_stress_5bps: {},
  parameter_sensitivity_5bps: {}, ohlc_ambiguity_comparison: {},
  honesty_note: 'No lance-1m-validation snapshot yet — run tools/lance_1m_research.py off-gate.',
};

const LANCE_FEE_ORDER = ['1.0bps', '2.0bps', '5.0bps', '8.0bps', '10.0bps'];
const LANCE_WINDOW_LABELS: Record<string, [string, string]> = {
  'doc_labeled_window_2025-12_2026-06': ['文档自称窗口', 'doc-labeled window'],
  'oos_pre_2025-07_2025-12': ['样本外(窗口前)', 'OOS (pre-window)'],
  'oos_post_2026-06_2026-07-09': ['样本外(窗口后)', 'OOS (post-window)'],
  'full_2025-07_2026-07-09': ['完整 13 个月', 'full 13-month span'],
};

function lanceSharpeTone(v: number | undefined | null): string {
  if (v == null) return 'text-faint';
  return v > 1.0 ? 'text-pos' : v > 0 ? 'text-warn' : 'text-neg';
}
function lancePct(v: number | undefined | null, digits = 1): string {
  return v == null ? '—' : `${(v * 100).toFixed(digits)}%`;
}
function lanceNum(v: number | undefined | null, digits = 2): string {
  return v == null ? '—' : v.toFixed(digits);
}

function LanceFeeSweepCells({ result }: { result?: Lance1mFeeResult }) {
  if (!result) return <td className="text-right text-faint" colSpan={3}>—</td>;
  return (
    <>
      <td className={`text-right mono ${lanceSharpeTone(result.sharpe)}`}>{lanceNum(result.sharpe)}</td>
      <td className="text-right mono text-neg">{lancePct(result.total_return)}</td>
      <td className="text-right mono text-faint">{result.num_trades}</td>
    </>
  );
}

/** 外部策略审计区块——独立发请求(自己的 useConsoleSnapshot),折叠展开时才会被用户注意到,
 * 与本页其余板块(读同一个 research-findings 快照)是两个不同的后端端点,故单独取数。 */
function LanceValidationSection() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<Lance1mValidationPayload>(
    s => fetchLance1mValidation(s));
  const p: Lance1mValidationPayload = usingBackend ? payload! : lanceMockPayload;
  const windows = Object.entries(p.fee_sweep);
  const paramSens = Object.entries(p.parameter_sensitivity_5bps ?? {});
  const ohlc = p.ohlc_ambiguity_comparison ?? {};

  return (
    <details className="group rounded-lg border border-line">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3.5 py-3 text-[13px] font-medium text-ink hover:bg-elevated/40">
        <span className="flex items-center gap-2">
          <span className="text-faint transition-transform group-open:rotate-90">▶</span>
          {bi('外部策略审计:Lance 1分钟均值回归', 'External strategy audit: Lance 1m mean-reversion')}
        </span>
        <Chip label={p.verdict === 'REFUTED' ? 'REFUTED' : p.verdict} tone={p.verdict === 'REFUTED' ? 'neg' : 'muted'} />
      </summary>
      <div className="flex flex-col gap-3 border-t border-line px-3.5 py-3.5">
        <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
          realData={usingBackend && p.data_provenance === 'REAL_LANCE_1M_VALIDATION'}
          source={usingBackend ? (p.snapshot_present ? 'REAL_LANCE_1M_VALIDATION' : 'NO_VALIDATION_SNAPSHOT') : 'FRONTEND_LANCE_1M_MOCK'}
          error={error} />
        <LoadingVeil active={apiState === 'loading'}>
        <p className="max-w-3xl text-[13px] leading-relaxed text-muted">
          {bi('用户提供的第三方策略文档(' + p.doc + ')自称夏普 5.01/总收益 +17.9%。用 53.7 万根真实 1 分钟 '
            + 'BTC K 线(2025-07 至 2026-07,零缺口零重复,无前视)独立复核:在文档自称窗口 + 文档自己最乐观的 '
            + '1bps 手续费假设下,实测夏普为负(非文档声称的 +5.01);4 种窗口切分 × 5 档手续费 × 107 个滚动'
            + '窗口压力测试 × 4 个参数 ±30% 敏感性,全部一致为负。已用合成数据双向验证引擎本身无 bug,已剥离'
            + '保守裁决重跑排除工程决策导致负号的可能,已用 0bps 分解排除成本模型单位错误的可能。',
            'A user-supplied strategy doc (' + p.doc + ') claims Sharpe 5.01 / +17.9% return. Independently '
            + 'validated against 537,121 real 1-minute BTC bars (2025-07~2026-07, gap-free, no lookahead): under '
            + 'the doc’s own labeled window and its own most optimistic 1bps fee assumption, measured Sharpe '
            + 'is negative (not the claimed +5.01); consistently negative across 4 window definitions × 5 fee '
            + 'levels × a 107-window rolling stress test × 4 parameters’ ±30% sensitivity. Engine correctness '
            + 'validated on synthetic data both directions; ruled out this module’s conservative tie-break and '
            + 'cost-model unit errors as explanations.')}
        </p>

        {windows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-2xs">
              <thead><tr className="text-muted">
                <th className="text-left py-1">{bi('窗口', 'window')}</th>
                <th className="text-left">{bi('手续费', 'fee')}</th>
                <th className="text-right">{bi('夏普', 'sharpe')}</th>
                <th className="text-right">{bi('总收益', 'total return')}</th>
                <th className="text-right">{bi('交易数', 'trades')}</th>
              </tr></thead>
              <tbody className="mono">
                {windows.map(([key, w]) => {
                  const [zh, en] = LANCE_WINDOW_LABELS[key] ?? [key, key];
                  return LANCE_FEE_ORDER.map((fee, i) => (
                    <tr key={key + fee} className="border-t border-line/50">
                      {i === 0 ? (
                        <td rowSpan={LANCE_FEE_ORDER.length} className="align-top py-1 text-ink">
                          <div className="font-medium">{bi(zh, en)}</div>
                          <div className="text-2xs text-faint">{w.n_bars.toLocaleString()} bars</div>
                        </td>
                      ) : null}
                      <td className="text-left text-faint">{fee}</td>
                      <LanceFeeSweepCells result={w.by_fee?.[fee]} />
                    </tr>
                  ));
                })}
              </tbody>
            </table>
          </div>
        ) : <div className="text-2xs text-faint">{bi('无验证快照', 'no validation snapshot')}</div>}

        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {([['1bps', p.rolling_window_stress_1bps], ['5bps', p.rolling_window_stress_5bps]] as
            [string, Lance1mRollingStress][]).map(([label, s]) => (
            <div key={label} className="rounded border border-line px-3 py-2.5">
              <div className="text-xs font-medium text-ink">{label} · {s.n_windows ?? '—'} windows</div>
              <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 text-2xs">
                <span className="text-muted">{bi('正收益窗口比例', 'pct positive')}</span>
                <span className={`text-right mono ${(s.pct_positive ?? 0) > 0.5 ? 'text-pos' : 'text-neg'}`}>{lancePct(s.pct_positive, 1)}</span>
                <span className="text-muted">{bi('平均夏普', 'mean sharpe')}</span>
                <span className={`text-right mono ${lanceSharpeTone(s.mean_sharpe)}`}>{lanceNum(s.mean_sharpe)}</span>
                <span className="text-muted">{bi('中位夏普', 'median sharpe')}</span>
                <span className={`text-right mono ${lanceSharpeTone(s.median_sharpe)}`}>{lanceNum(s.median_sharpe)}</span>
                <span className="text-muted">{bi('平均 alpha ($)', 'mean alpha ($)')}</span>
                <span className={`text-right mono ${(s.mean_alpha ?? 0) > 0 ? 'text-pos' : 'text-neg'}`}>{lanceNum(s.mean_alpha, 1)}</span>
              </div>
            </div>
          ))}
        </div>

        {paramSens.length ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {paramSens.map(([name, vals]) => (
              <div key={name} className="rounded border border-line px-3 py-2">
                <div className="text-2xs font-medium text-ink">{name}</div>
                <div className="mt-1 flex items-center gap-3 text-2xs mono">
                  {['-30%', 'base', '+30%'].map(k => (
                    <span key={k} className="flex flex-col items-center">
                      <span className="text-faint">{k}</span>
                      <span className={lanceSharpeTone(vals[k])}>{lanceNum(vals[k])}</span>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {([['conservative_ohlc_ambiguity', bi('保守裁决(默认)', 'conservative (default)')],
            ['doc_literal_no_override', bi('文档字面顺序', 'doc-literal order')]] as const).map(([key, label]) => {
            const r: Lance1mFeeResult | undefined = ohlc[key];
            return (
              <div key={key} className="rounded border border-line px-3 py-2">
                <div className="text-2xs font-medium text-ink">{label}</div>
                {r ? (
                  <div className="mt-1 flex items-center gap-3 text-2xs mono">
                    <span className={lanceSharpeTone(r.sharpe)}>sharpe {lanceNum(r.sharpe)}</span>
                    <span className="text-neg">{lancePct(r.total_return)}</span>
                  </div>
                ) : <div className="mt-1 text-2xs text-faint">—</div>}
              </div>
            );
          })}
        </div>

        <div className="text-2xs text-faint">{p.honesty_note}</div>
        </LoadingVeil>
      </div>
    </details>
  );
}

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
          'Capacity · macro-regime conditioning · diversification — read-only research aggregate, diagnostic not a gate')}
        actions={<Chip label={bi('↓ 另含外部策略审计(Lance 1m)', '↓ also includes external audit (Lance 1m)')} tone="muted" />} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_RESEARCH_FINDINGS'}
        source={usingBackend ? (p.snapshot_present ? 'REAL_RESEARCH_FINDINGS' : 'NO_FINDINGS_SNAPSHOT') : 'FRONTEND_FINDINGS_MOCK'}
        error={error} />

      <div className="rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">{p.honesty_note}</div>

      <LoadingVeil active={apiState === 'loading'}>
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

      </LoadingVeil>

      <LanceValidationSection />

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究诊断 · gross/regime 口径见 honesty_note · 不驱动任何自动晋级/下单',
          'Read-only · research diagnostic · drives no automatic promotion/trading')}
      </div>
    </PageShell>
  );
}
