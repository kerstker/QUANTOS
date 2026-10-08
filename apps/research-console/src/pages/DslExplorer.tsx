import { useState } from 'react';
import { fetchDslExplorerSnapshot, checkDslExpression } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { DslExplorerPayload, DslExprRow } from '@/lib/types/research';
import { ConsoleSnapshotUnavailablePage } from '@/ui/ConsoleSnapshotUnavailablePage';
import { selectConsoleDisplayPayload } from '@/ui/console-display-payload';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, GateDot, Chip, LoadingVeil,
} from '@/ui/primitives';
import { fixed } from '@/ui/format';
import { useToast } from '@/ui/toast';

/**
 * DSL Explorer. Cross-sectional alpha expressions with their REAL static analysis — the dsl_checker
 * Type/Dimension verdict, operator-weighted complexity, and the machine-proof PIT-safety (the real
 * expression_leakage detector perturbs strictly-future rows). Verdicts are COMPUTED by the real engines,
 * not hand-authored. Read-only; synthetic fallback when the backend is offline.
 */

type ExprRow = DslExprRow;

const fallback: DslExplorerPayload = {
  source: 'FRONTEND_DSL_FALLBACK', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', analysis_backend: 'REAL_DSL_CHECKER+LEAKAGE+COMPLEXITY',
  exprs: [
    { expr: '(cs_zscore (neg (decay_linear (field funding) 7)))', valid: true, complexity: 4.5, leakage_ok: true, deps: ['funding'], note: '资金费 carry / funding carry', errors: [] },
    { expr: '(cs_rank (ts_delta (field close) 5))', valid: true, complexity: 4.0, leakage_ok: true, deps: ['close'], note: '价格动量 / price momentum', errors: [] },
    { expr: '(cs_residualize (field close) (field volume))', valid: true, complexity: 5.0, leakage_ok: true, deps: ['close', 'volume'], note: '对量能中性化 / size-neutralized', errors: [] },
    { expr: '(cs_rank (ts_lead (field close) 1))', valid: false, complexity: 0, leakage_ok: null, deps: ['close'], note: '读未来 → 检查器拒绝 / future-reading: rejected', errors: ["root.1: unknown operator 'ts_lead'"] },
  ],
};

export default function DslExplorer() {
  const snapshot = useConsoleSnapshot<DslExplorerPayload>(s => fetchDslExplorerSnapshot(s));
  const { apiState, error, usingBackend } = snapshot;
  const selectedData = selectConsoleDisplayPayload(snapshot, fallback);
  // Keep hooks unconditional across loading -> connected transitions. The
  // fixture supplies internal shapes only and is never mounted unless mock
  // mode was explicitly requested.
  const data = selectedData ?? fallback;
  const exprs = data.exprs;
  const [sel, setSel] = useState(0);
  const [customExpr, setCustomExpr] = useState('');
  const [customRow, setCustomRow] = useState<ExprRow | null>(null);
  const [customBusy, setCustomBusy] = useState(false);
  const toast = useToast();
  const r = customRow ?? exprs[Math.min(sel, exprs.length - 1)];

  async function runCustomCheck() {
    if (!customExpr.trim()) return;
    setCustomBusy(true);
    try {
      if (usingBackend) {
        setCustomRow(await checkDslExpression(customExpr));
      } else {
        // offline fallback: no engine to call — report honestly rather than fabricate a verdict.
        setCustomRow({
          expr: customExpr, valid: false, complexity: 0, leakage_ok: null, deps: [],
          note: bi('离线模式:无法调用真实检查器', 'offline mode: real checker unavailable'),
          errors: [bi('后端未连接', 'backend not connected')],
        });
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      setCustomBusy(false);
    }
  }

  if (selectedData === null) {
    return <ConsoleSnapshotUnavailablePage endpoint="/api/v1/console/dsl-explorer" apiState={apiState} error={error} />;
  }

  return (
    <PageShell>
      <PageHeader title="DSL Explorer" titleZh="因子 DSL 浏览器"
        subtitle={bi('截面因子表达式 + 静态检查器 + 算子加权复杂度 + PIT 泄漏机器证明(真引擎,只读)',
          'Cross-sectional alpha expressions + static checker + operator-weighted complexity + PIT leakage machine-proof (real engines, read-only)')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={data.source} error={error} />

      <LoadingVeil active={apiState === 'loading'}>
      <KpiStrip cols={4}>
        <StatTile label={bi('表达式', 'Expressions')} value={String(exprs.length)} />
        <StatTile label={bi('类型/维度检查通过', 'Checker pass')} value={String(exprs.filter(e => e.valid).length)} tone="pos" />
        <StatTile label={bi('PIT 安全证明', 'PIT-safe proven')} value={String(exprs.filter(e => e.leakage_ok === true).length)} tone="info" />
        <StatTile label={bi('泄漏/拒绝', 'Rejected / leaks')} value={String(exprs.filter(e => !e.valid || e.leakage_ok === false).length)} tone="warn" />
      </KpiStrip>

      <Panel eyebrow={bi('表达式', 'Expressions')} title={bi('因子库', 'Factor Library')}
        right={<Chip label={data.analysis_backend} tone="info" />}>
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

      <Panel eyebrow={bi('自由输入', 'Free Input')} title={bi('测试你自己的表达式', 'Test Your Own Expression')}>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
          <input
            value={customExpr}
            onChange={e => setCustomExpr(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') runCustomCheck(); }}
            placeholder={bi('输入 S 表达式,例如 (cs_rank (ts_mean (field close) 5))',
              'Type an S-expression, e.g. (cs_rank (ts_mean (field close) 5))')}
            className="mono flex-1 rounded-lg border border-line bg-inset/40 px-3 py-2 text-xs text-ink placeholder:text-faint" />
          <div className="flex gap-2">
            <button onClick={runCustomCheck} disabled={customBusy || !customExpr.trim()}
              className="rounded-lg border border-accent/50 bg-accent/10 px-3 py-2 text-2xs font-medium text-accent hover:bg-accent/20 disabled:opacity-50">
              {customBusy ? bi('检查中…', 'checking…') : bi('运行检查器', 'Run Checker')}
            </button>
            {customRow && (
              <button onClick={() => { setCustomRow(null); setCustomExpr(''); }}
                className="rounded-lg border border-line px-3 py-2 text-2xs text-muted hover:text-ink">
                {bi('清除', 'Clear')}
              </button>
            )}
          </div>
        </div>
        <div className="mt-2 text-2xs text-secondary">
          {bi('调用与因子库同一套真实引擎(dsl_checker + complexity + expression_leakage);只做静态分析,不执行/不接触真实数据。',
            'Runs the same real engines as the curated library (dsl_checker + complexity + expression_leakage); static analysis only — never executes against real data.')}
        </div>
      </Panel>

      <Panel eyebrow={bi('静态分析', 'Static Analysis')}
        title={customRow ? bi('自定义表达式', 'Custom Expression') : bi('选中表达式', 'Selected Expression')}
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
      </LoadingVeil>
    </PageShell>
  );
}
