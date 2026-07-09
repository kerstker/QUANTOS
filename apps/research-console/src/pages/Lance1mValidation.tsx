import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import { PageShell, PageHeader, ProvenanceRibbon, Panel, Chip } from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchLance1mValidation } from '@/lib/api/research-console-api';
import type { Lance1mValidationPayload, Lance1mFeeResult } from '@/lib/types/research';

/**
 * Lance 1m Validation — 用户提供的 1 分钟 BTC 均值回归策略(自称夏普 5.01)的独立复核结果。
 * 只读诊断,非晋级门禁:该策略从未注册进 AlphaCatalog,不驱动任何自动晋级/下单。
 * 结论摘要在最上面(负面结论一句话),细节表格在下面(fee sweep / 滚动窗口压力测试 / 参数敏感性 /
 * OHLC 歧义对照) —— 与 ResearchFindings.tsx 的既有信息层级一致。
 */

const mockPayload: Lance1mValidationPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_SNAPSHOT',
  doc: 'docs/test/lance_1m_strategy.md',
  fee_sweep: {}, rolling_window_stress_1bps: {}, rolling_window_stress_5bps: {},
  parameter_sensitivity_5bps: {}, ohlc_ambiguity_comparison: {},
  honesty_note: 'No lance-1m-validation snapshot yet — run tools/lance_1m_research.py off-gate.',
};

const FEE_ORDER = ['1.0bps', '2.0bps', '5.0bps', '8.0bps', '10.0bps'];
const WINDOW_LABELS: Record<string, [string, string]> = {
  'doc_labeled_window_2025-12_2026-06': ['文档自称窗口', "doc-labeled window"],
  'oos_pre_2025-07_2025-12': ['样本外(窗口前)', 'OOS (pre-window)'],
  'oos_post_2026-06_2026-07-09': ['样本外(窗口后)', 'OOS (post-window)'],
  'full_2025-07_2026-07-09': ['完整 13 个月', 'full 13-month span'],
};

function sharpeTone(v: number | undefined | null): string {
  if (v == null) return 'text-faint';
  return v > 1.0 ? 'text-pos' : v > 0 ? 'text-warn' : 'text-neg';
}
function pct(v: number | undefined | null, digits = 1): string {
  return v == null ? '—' : `${(v * 100).toFixed(digits)}%`;
}
function num(v: number | undefined | null, digits = 2): string {
  return v == null ? '—' : v.toFixed(digits);
}

function FeeSweepTable({ result }: { result?: Lance1mFeeResult }) {
  if (!result) return <td className="text-right text-faint" colSpan={3}>—</td>;
  return (
    <>
      <td className={`text-right mono ${sharpeTone(result.sharpe)}`}>{num(result.sharpe)}</td>
      <td className="text-right mono text-neg">{pct(result.total_return)}</td>
      <td className="text-right mono text-faint">{result.num_trades}</td>
    </>
  );
}

export default function Lance1mValidation() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<Lance1mValidationPayload>(
    s => fetchLance1mValidation(s));
  const p: Lance1mValidationPayload = usingBackend ? payload! : mockPayload;

  const windows = Object.entries(p.fee_sweep);
  const paramSens = Object.entries(p.parameter_sensitivity_5bps ?? {});
  const ohlc = p.ohlc_ambiguity_comparison ?? {};

  return (
    <PageShell>
      <PageHeader title="Lance 1m Strategy Validation" titleZh="Lance 1分钟策略验证"
        subtitle={bi('用户提供策略的独立复核 — 只读诊断,非晋级门禁,不驱动任何自动晋级/下单',
          "Independent validation of a user-supplied strategy — read-only diagnostic, drives no promotion/trading")} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_LANCE_1M_VALIDATION'}
        source={usingBackend ? (p.snapshot_present ? 'REAL_LANCE_1M_VALIDATION' : 'NO_VALIDATION_SNAPSHOT') : 'FRONTEND_LANCE_1M_MOCK'}
        error={error} />

      {/* 结论摘要 */}
      <Panel eyebrow={bi('结论', 'VERDICT')}
        title={bi('文档自称夏普 5.01,真实数据独立复核结果为负', "Doc claims Sharpe 5.01 — independent real-data validation is negative")}>
        <div className="flex flex-col gap-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <Chip label={p.verdict === 'REFUTED' ? 'REFUTED' : p.verdict} tone={p.verdict === 'REFUTED' ? 'neg' : 'muted'} />
            <span className="text-2xs text-faint">{p.doc}</span>
          </div>
          <p className="max-w-3xl text-[13px] leading-relaxed text-muted">
            {bi('用 53.7 万根真实 1 分钟 BTC K 线(2025-07 至 2026-07,零缺口零重复,无前视)独立复核。'
              + '在文档自称窗口 + 文档自己最乐观的 1bps 手续费假设下,实测夏普为负(非文档声称的 +5.01);'
              + '4 种窗口切分 × 5 档手续费 × 107 个滚动窗口压力测试 × 4 个参数 ±30% 敏感性,全部一致为负。'
              + '已用合成数据双向验证引擎本身无 bug,已剥离保守裁决重跑排除工程决策导致负号的可能,'
              + '已用 0bps 分解排除成本模型单位错误的可能。',
              'Independently validated against 537,121 real 1-minute BTC bars (2025-07~2026-07, gap-free, '
              + 'no lookahead). Under the doc’s own labeled window and its own most optimistic 1bps fee '
              + 'assumption, measured Sharpe is negative (not the claimed +5.01); consistently negative across '
              + '4 window definitions × 5 fee levels × a 107-window rolling stress test × 4 parameters’ '
              + '±30% sensitivity. Engine correctness validated on synthetic data both directions; ruled out '
              + 'this module’s conservative tie-break and cost-model unit errors as explanations.')}
          </p>
        </div>
      </Panel>

      {/* Fee sweep across windows */}
      <Panel eyebrow={bi('手续费敏感性 × 窗口切分', 'FEE SENSITIVITY × WINDOW')}
        title={bi('4 种独立窗口定义,每种窗口 5 档手续费(1/2/5/8/10bps)', '4 window definitions × 5 fee levels')}>
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
                  const [zh, en] = WINDOW_LABELS[key] ?? [key, key];
                  return FEE_ORDER.map((fee, i) => (
                    <tr key={key + fee} className="border-t border-line/50">
                      {i === 0 ? (
                        <td rowSpan={FEE_ORDER.length} className="align-top py-1 text-ink">
                          <div className="font-medium">{bi(zh, en)}</div>
                          <div className="text-2xs text-faint">{w.n_bars.toLocaleString()} bars</div>
                        </td>
                      ) : null}
                      <td className="text-left text-faint">{fee}</td>
                      <FeeSweepTable result={w.by_fee?.[fee]} />
                    </tr>
                  ));
                })}
              </tbody>
            </table>
          </div>
        ) : <div className="text-2xs text-faint">{bi('无验证快照', 'no validation snapshot')}</div>}
      </Panel>

      {/* Rolling window stress */}
      <Panel eyebrow={bi('滚动窗口压力测试', 'ROLLING WINDOW STRESS')}
        title={bi('5000 根一组的滚动窗口(镜像文档自己的“168窗口”方法论)', '5000-bar rolling windows (mirrors the doc’s own methodology)')}>
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {[['1bps', p.rolling_window_stress_1bps], ['5bps', p.rolling_window_stress_5bps]].map(([label, s]: any) => (
            <div key={label} className="rounded border border-line px-3 py-2.5">
              <div className="text-xs font-medium text-ink">{label} · {s.n_windows ?? '—'} windows</div>
              <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 text-2xs">
                <span className="text-muted">{bi('正收益窗口比例', 'pct positive')}</span>
                <span className={`text-right mono ${(s.pct_positive ?? 0) > 0.5 ? 'text-pos' : 'text-neg'}`}>{pct(s.pct_positive, 1)}</span>
                <span className="text-muted">{bi('平均夏普', 'mean sharpe')}</span>
                <span className={`text-right mono ${sharpeTone(s.mean_sharpe)}`}>{num(s.mean_sharpe)}</span>
                <span className="text-muted">{bi('中位夏普', 'median sharpe')}</span>
                <span className={`text-right mono ${sharpeTone(s.median_sharpe)}`}>{num(s.median_sharpe)}</span>
                <span className="text-muted">{bi('平均 alpha ($)', 'mean alpha ($)')}</span>
                <span className={`text-right mono ${(s.mean_alpha ?? 0) > 0 ? 'text-pos' : 'text-neg'}`}>{num(s.mean_alpha, 1)}</span>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      {/* Parameter sensitivity */}
      <Panel eyebrow={bi('参数敏感性 · ±30%', 'PARAMETER SENSITIVITY ±30%')}
        title={bi('每个可调参数单独 ±30%(5bps),文档自称“全正”', 'each tunable parameter ±30% (5bps); doc claims “all positive”')}>
        {paramSens.length ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {paramSens.map(([name, vals]) => (
              <div key={name} className="rounded border border-line px-3 py-2">
                <div className="text-2xs font-medium text-ink">{name}</div>
                <div className="mt-1 flex items-center gap-3 text-2xs mono">
                  {['-30%', 'base', '+30%'].map(k => (
                    <span key={k} className="flex flex-col items-center">
                      <span className="text-faint">{k}</span>
                      <span className={sharpeTone(vals[k])}>{num(vals[k])}</span>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : <div className="text-2xs text-faint">{bi('无参数敏感性快照', 'no parameter sensitivity snapshot')}</div>}
      </Panel>

      {/* OHLC ambiguity comparison */}
      <Panel eyebrow={bi('OHLC 歧义裁决对照', 'OHLC AMBIGUITY OVERRIDE')}
        title={bi('本模块保守裁决 vs 文档字面顺序(排除工程决策导致负号的可能)', 'this module’s conservative tie-break vs. doc-literal order')}>
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          {([['conservative_ohlc_ambiguity', bi('保守裁决(默认)', 'conservative (default)')],
            ['doc_literal_no_override', bi('文档字面顺序', 'doc-literal order')]] as const).map(([key, label]) => {
            const r = (ohlc as any)[key] as Lance1mFeeResult | undefined;
            return (
              <div key={key} className="rounded border border-line px-3 py-2">
                <div className="text-2xs font-medium text-ink">{label}</div>
                {r ? (
                  <div className="mt-1 flex items-center gap-3 text-2xs mono">
                    <span className={sharpeTone(r.sharpe)}>sharpe {num(r.sharpe)}</span>
                    <span className="text-neg">{pct(r.total_return)}</span>
                  </div>
                ) : <div className="mt-1 text-2xs text-faint">—</div>}
              </div>
            );
          })}
        </div>
      </Panel>

      <div className="rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">{p.honesty_note}</div>
    </PageShell>
  );
}
