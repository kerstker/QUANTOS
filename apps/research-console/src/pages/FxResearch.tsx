import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, LoadingVeil, type Column,
} from '@/ui/primitives';
import { signed } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchFxResearchSnapshot } from '@/lib/api/research-console-api';
import type { FxResearchPayload, FxCostSweepRow, FxYearRow } from '@/lib/types/research';

/**
 * FX Research (ECB / Frankfurter daily reference rates, ~46 currencies 1999–2026). Shows the SAME-gauntlet
 * result as crypto on a different asset class: a full-universe no-peek OOS that looked positive (+0.70) but
 * was REFUTED by the tradable-universe + cost sweep (OOS negative at every cost). HONESTY: ECB fixings are
 * not executable quotes; the tradable gauntlet is the arbiter. Read-only; research-only (no broker/order).
 */

const mockPayload: FxResearchPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_SNAPSHOT',
  full_universe: {}, tradable_gauntlet: {},
  honesty_note: 'No FX snapshot generated yet — run tools/fx_research.py + tools/fx_gauntlet.py off-gate.',
};

const VERDICT_ZH: Record<string, string> = {
  REFUTED_NO_TRADABLE_EDGE: '可交易关卡证伪 · 无 edge',
  SURVIVES_TRADABLE_GAUNTLET: '挺过可交易关卡',
  LEAD_UNGATED: '线索 · 未过关卡',
  NO_SNAPSHOT: '尚无快照',
};

export default function FxResearch() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<FxResearchPayload>(
    s => fetchFxResearchSnapshot(s));

  const p: FxResearchPayload = usingBackend ? payload! : mockPayload;
  const full = p.full_universe ?? {};
  const gaunt = p.tradable_gauntlet ?? {};
  const sweep: FxCostSweepRow[] = gaunt.cost_sweep ?? [];
  const fullOos = full.oos?.mean_oos_sharpe;
  const refuted = p.verdict === 'REFUTED_NO_TRADABLE_EDGE';

  const sweepCols: Column<FxCostSweepRow>[] = [
    { key: 'cost', header: bi('成本 bps', 'Cost bps'), align: 'right', mono: true, render: r => String(r.cost_bps) },
    { key: 'best', header: bi('样本内最优', 'IS best'), render: r => <span className="mono text-muted">{r.is_best}</span> },
    { key: 'issr', header: bi('样本内 SR', 'IS SR'), align: 'right', mono: true, render: r => signed(r.is_sharpe, 2) },
    { key: 'oos', header: bi('无窥视 OOS SR', 'OOS SR'), align: 'right', mono: true,
      render: r => r.oos_mean_sharpe == null ? <span className="text-faint">—</span> : (
        <span className={r.oos_mean_sharpe > 0 ? 'text-pos' : 'text-neg'}>{signed(r.oos_mean_sharpe, 2)}</span>) },
    { key: 'folds', header: bi('折数', 'Folds'), align: 'right', mono: true, render: r => String(r.n_folds) },
  ];

  const yearCols: Column<FxYearRow>[] = [
    { key: 'year', header: bi('年份', 'Year'), mono: true, render: r => String(r.year) },
    { key: 'n', header: 'n', align: 'right', mono: true, render: r => String(r.n) },
    { key: 'sr', header: bi('净 SR', 'Net SR'), align: 'right', mono: true,
      render: r => r.net_sharpe == null ? <span className="text-faint">—</span> : (
        <span className={r.net_sharpe > 0 ? 'text-pos' : 'text-neg'}>{signed(r.net_sharpe, 2)}</span>) },
  ];
  const years: FxYearRow[] = (gaunt.per_year_5bps ?? []).filter((r: FxYearRow) => r.n > 5);

  return (
    <PageShell>
      <PageHeader title="FX Research" titleZh="FX 研究(ECB 汇率)"
        subtitle={bi('46 货币 · 1999–2026 · 同一套关卡跑在不同资产类 / 真实出-regime',
          'ECB/Frankfurter daily rates · 46 currencies 1999–2026 · the SAME gauntlet on a different asset class')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_FX_ECB_OFFLINE_SNAPSHOT'}
        source={usingBackend ? (p.snapshot_present ? 'REAL_FX_ECB_OFFLINE_SNAPSHOT' : 'NO_FX_SNAPSHOT') : 'FRONTEND_FX_MOCK'}
        error={error} />

      <div className="rounded-md border border-amber-500/30 bg-amber-500/[0.06] px-3 py-2 text-2xs leading-relaxed text-amber-200/90">
        {bi('诚实标注:全 universe 无窥视 OOS 看着是正的(+0.70),但被「只留可交易浮动货币 + 成本扫描」的关卡证伪——所有成本档 OOS 全负。+0.70 来自 pre-Euro 趋同一次性行情 + 钉住/管制/不可成交货币的污染。ECB 日定盘非可成交价;可交易关卡才是裁判。研究只读,无券商/下单路径。',
          'Honest note: the full-universe no-peek OOS looked positive (+0.70) but the gauntlet (tradable floaters only + cost sweep) REFUTED it — OOS negative at every cost. The +0.70 came from pre-Euro convergence one-offs + pegged/controlled/non-executable currencies. ECB daily fixings are not executable quotes; the tradable gauntlet is the arbiter. Read-only, no broker/order path.')}
      </div>

      <LoadingVeil active={apiState === 'loading'}>
      <KpiStrip cols={5}>
        <StatTile label={bi('货币数(全)', 'Currencies (full)')} value={String(full.n_currencies ?? '—')} accent />
        <StatTile label={bi('日线期数', 'Daily periods')} value={String(full.n_periods ?? '—')} tone="info" />
        <StatTile label={bi('泄漏校验', 'Leakage verified')} value={full.leakage_verified ?? '—'} tone="pos"
          sub={bi('真 PIT 检测', 'real detector')} />
        <StatTile label={bi('全 universe OOS', 'Full-universe OOS')}
          value={fullOos == null ? '—' : signed(fullOos, 2)} tone={fullOos != null && fullOos > 0 ? 'warn' : 'muted'}
          sub={bi('未去污染,误导', 'contaminated · misleading')} />
        <StatTile label={bi('裁决', 'Verdict')}
          value={apiState === 'loading' ? bi('加载中…', 'loading…') : bi(VERDICT_ZH[p.verdict] ?? p.verdict, p.verdict)}
          tone={apiState === 'loading' ? 'muted' : refuted ? 'neg' : p.verdict === 'SURVIVES_TRADABLE_GAUNTLET' ? 'pos' : 'muted'} />
      </KpiStrip>

      <Panel eyebrow={bi('可交易关卡', 'TRADABLE GAUNTLET')}
        title={bi('成本扫描下的无窥视 OOS 存活曲线(18 浮动货币)', 'No-peek OOS survival vs cost (18 floaters)')}>
        {sweep.length ? <DataTable rows={sweep} columns={sweepCols} getKey={r => String(r.cost_bps)} /> : (
          <div className="text-2xs text-faint">{bi('无 gauntlet 快照', 'no gauntlet snapshot')}</div>)}
        <div className="mt-2 text-2xs text-muted">
          {bi('剔除 pre-Euro legacy + 钉住/管制/制裁货币后,信号在所有成本档 OOS 转负 → 不是可交易 edge。',
            'After dropping pre-Euro legacy + pegged/controlled/sanctioned currencies, OOS is negative at every cost → not a tradable edge.')}
        </div>
      </Panel>

      <Panel eyebrow={bi('多 regime', 'PER-YEAR')}
        title={bi('头号因子逐年净 SR @5bps(可交易)', `Headline ${gaunt.headline ?? ''} per-year net SR @5bps (tradable)`)}>
        {years.length ? <DataTable rows={years} columns={yearCols} getKey={r => String(r.year)} /> : (
          <div className="text-2xs text-faint">{bi('无逐年数据', 'no per-year data')}</div>)}
      </Panel>

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究/干跑(L2)· 离线 ECB 数据 · 同一套关卡(泄漏/regime/PBO/无窥视 OOS)· 结论与加密一致:干净可交易数据上简单截面趋势无 OOS edge',
          'Read-only · research / dry-run (L2) · offline ECB data · the same gauntlet (leakage/regime/PBO/no-peek OOS) · same verdict as crypto: no OOS edge for simple cross-sectional trend on clean tradable data')}
      </div>
      </LoadingVeil>
    </PageShell>
  );
}
