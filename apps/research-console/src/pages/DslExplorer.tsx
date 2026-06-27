import React, { useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, GateDot, Chip,
} from '@/ui/primitives';
import { fixed } from '@/ui/format';

/**
 * DSL Explorer (Phase 1 surface). Shows cross-sectional alpha expressions with their static analysis —
 * the Type/Dimension checker verdict, operator-weighted complexity, and the machine-proof PIT-safety
 * (leakage) result. Read-only; the checker is exactly what the backend dsl_checker / expression_leakage run.
 */

interface ExprRow {
  expr: string;
  valid: boolean;
  complexity: number;
  leakage_ok: boolean | null;
  deps: string[];
  note: string;
}

const exprs: ExprRow[] = [
  { expr: '(cs_zscore (neg (decay_linear (field funding) 7)))', valid: true, complexity: 3.0, leakage_ok: true, deps: ['funding'], note: '资金费 carry · 经济夏普 ~2.0 / funding carry' },
  { expr: '(cs_rank (ts_delta (field close) 5))', valid: true, complexity: 3.0, leakage_ok: true, deps: ['close'], note: '价格动量 · 跨 regime null / price momentum (null)' },
  { expr: '(cs_residualize (field close) (field volume))', valid: true, complexity: 5.0, leakage_ok: true, deps: ['close', 'volume'], note: '中性化原语 · 对量能正交 / size-neutralized' },
  { expr: '(cs_zscore (ts_cov (field close) (field volume) 7))', valid: true, complexity: 4.5, leakage_ok: true, deps: ['close', 'volume'], note: '窗口协方差 / windowed covariance' },
  { expr: '(cs_rank (ts_lead (field close) 1))', valid: false, complexity: 0, leakage_ok: false, deps: ['close'], note: '读未来 → 检查器拒绝 + 泄漏证伪 / future-reading: rejected & leaks' },
];

export default function DslExplorer() {
  const [sel, setSel] = useState(0);
  const r = exprs[sel];

  return (
    <PageShell>
      <PageHeader title="DSL Explorer" titleZh="因子 DSL 浏览器"
        subtitle={bi('截面因子表达式 + 静态检查器 + 算子加权复杂度 + PIT 泄漏机器证明(只读)',
          'Cross-sectional alpha expressions + static checker + operator-weighted complexity + PIT leakage machine-proof (read-only)')} />
      <ProvenanceRibbon usingBackend={false} apiState="disabled" />

      <KpiStrip cols={4}>
        <StatTile label={bi('表达式', 'Expressions')} value={String(exprs.length)} />
        <StatTile label={bi('类型/维度检查通过', 'Checker pass')} value={String(exprs.filter(e => e.valid).length)} tone="pos" />
        <StatTile label={bi('PIT 安全证明', 'PIT-safe proven')} value={String(exprs.filter(e => e.leakage_ok === true).length)} tone="info" />
        <StatTile label={bi('泄漏/拒绝', 'Rejected / leaks')} value={String(exprs.filter(e => !e.valid || e.leakage_ok === false).length)} tone="warn" />
      </KpiStrip>

      <Panel eyebrow={bi('表达式', 'Expressions')} title={bi('因子库', 'Factor Library')}>
        <div className="flex flex-col gap-2">
          {exprs.map((e, i) => (
            <button key={e.expr} onClick={() => setSel(i)}
              className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition ${i === sel ? 'border-accent bg-accent/5' : 'border-line hover:border-line-strong'}`}>
              <GateDot ok={e.valid && e.leakage_ok !== false} label={e.valid && e.leakage_ok !== false ? bi('安全', 'safe') : bi('拒绝', 'rejected')} />
              <span className="mono text-2xs text-ink flex-1 truncate">{e.expr}</span>
              <Chip label={bi(`复杂度 ${fixed(e.complexity, 1)}`, `cx ${fixed(e.complexity, 1)}`)} tone="muted" />
            </button>
          ))}
        </div>
      </Panel>

      <Panel eyebrow={bi('静态分析', 'Static Analysis')} title={bi('选中表达式', 'Selected Expression')}
        right={<Chip label={r.valid && r.leakage_ok !== false ? bi('可入库', 'admissible') : bi('被拦', 'blocked')}
          tone={r.valid && r.leakage_ok !== false ? 'pos' : 'neg'} />}>
        <div className="mono mb-3 rounded-lg border border-line bg-inset/40 px-3 py-2 text-xs text-ink">{r.expr}</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-lg bg-inset/40 px-3 py-2.5">
            <div className="text-2xs text-muted">{bi('类型/维度检查', 'Type/Dimension')}</div>
            <div className="mt-1"><GateDot ok={r.valid} label={r.valid ? bi('通过', 'PASS') : bi('未过', 'FAIL')} /></div>
          </div>
          <div className="rounded-lg bg-inset/40 px-3 py-2.5">
            <div className="text-2xs text-muted">{bi('PIT 泄漏证明', 'Leakage proof')}</div>
            <div className="mt-1">{r.leakage_ok === null ? <Chip label={bi('不可评', 'N/A')} tone="muted" />
              : <GateDot ok={r.leakage_ok} label={r.leakage_ok ? bi('PIT 安全', 'PIT-safe') : bi('读未来', 'LEAKS')} />}</div>
          </div>
          <div className="rounded-lg bg-inset/40 px-3 py-2.5">
            <div className="text-2xs text-muted">{bi('复杂度', 'Complexity')}</div>
            <div className="mt-1 mono text-sm text-ink">{fixed(r.complexity, 1)}</div>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-2xs text-muted">{bi('数据依赖', 'Data deps')}:</span>
          {r.deps.map(d => <Chip key={d} label={d} tone="info" />)}
        </div>
        <div className="mt-2 text-2xs text-secondary">{r.note}</div>
      </Panel>
    </PageShell>
  );
}
