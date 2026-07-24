import React, { useMemo } from 'react';
import { fetchCryptoLiveSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { CryptoFactor, CryptoLivePayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, Panel, KpiStrip, StatTile, DataTable, StatusChip, Chip,
  GateDot, ProvenanceRibbon, LoadingVeil, type Column,
} from '@/ui/primitives';
import { BarSeries } from '@/ui/charts';
import { num, fixed, pct, signed, stamp, shortHash } from '@/ui/format';

/* Short, stable display label for an alpha id (interaction ids are long). */
function shortLabel(id: string): string {
  const s = id.replace(/^hx_/, '').replace(/^h_/, '').replace(/price-/g, '');
  return s.length > 16 ? `${s.slice(0, 15)}…` : s;
}

/* The factor factory as a pipeline — static stages from data to verdict. */
const FACTORY_STAGES: Array<{ zh: string; en: string; sub: string }> = [
  { zh: '数据', en: 'Data', sub: bi('CoinGecko 只读 · 链下', 'CoinGecko read-only') },
  { zh: '入库', en: 'Ingest', sub: bi('PIT · 抗幸存 · 无前视', 'PIT · survivorship-free') },
  { zh: '面板', en: 'Panel', sub: bi('截面 · 美元中性', 'cross-section · neutral') },
  { zh: '因子', en: 'Signal', sub: bi('算子×窗口 · 搜索', 'operators × windows') },
  { zh: '评估', en: 'Evaluate', sub: bi('DSR · OOS · 成本 · regime', 'DSR · OOS · cost') },
  { zh: '裁决', en: 'Lifecycle', sub: bi('容器化 · 人工复核', 'contained · human review') },
];

function FactoryPipeline() {
  return (
    <Panel eyebrow={bi('因子工厂', 'Factor factory')} title={bi('从数据到裁决的流水线', 'Data → verdict pipeline')}
      right={<GateDot ok label={bi('只读 · 离线核心', 'read-only · offline core')} />}>
      <div className="flex flex-wrap items-stretch gap-2">
        {FACTORY_STAGES.map((s, i) => (
          <React.Fragment key={s.en}>
            <div className="flex min-w-[92px] flex-1 flex-col gap-1 rounded-lg px-3 py-2.5"
              style={{ background: 'var(--inset)', boxShadow: 'inset 0 0 0 1px var(--line)' }}>
              <span className="text-[13px] font-medium text-ink">{s.zh}
                <span className="ml-1 text-2xs text-faint">{s.en}</span></span>
              <span className="text-2xs leading-snug text-muted">{s.sub}</span>
            </div>
            {i < FACTORY_STAGES.length - 1 && <span className="self-center text-sm text-faint">›</span>}
          </React.Fragment>
        ))}
      </div>
    </Panel>
  );
}

function Instructions({ connected }: { connected: boolean }) {
  return (
    <Panel eyebrow={bi('使用说明', 'How to populate')} title={bi('生成真实加密研究快照', 'Generate the real crypto snapshot')}>
      <div className="flex flex-col gap-3 text-[13px] leading-relaxed text-muted">
        <p>
          {bi('本页只渲染由只读链下流程生成的真实加密研究结论(非合成演示)。研究核心与门禁完全离线 —— 网络只存在于门禁外的抓取器中。',
            'This page renders only real crypto research produced by the read-only off-gate pipeline (not synthetic demo). The research core and gate are fully offline — the network lives only in the out-of-gate fetcher.')}
        </p>
        <ol className="flex list-decimal flex-col gap-2 pl-5 text-[12.5px] text-ink">
          <li><span className="text-muted">{bi('抓取并跑研究(链下、只读):', 'Fetch + run research (off-gate, read-only):')}</span>
            <div className="mono mt-1 rounded-lg border border-line bg-inset/60 px-3 py-1.5 text-2xs text-accent">python tools/crypto_research_run.py</div></li>
          <li><span className="text-muted">{bi('启动只读 Console API:', 'Start the read-only Console API:')}</span>
            <div className="mono mt-1 rounded-lg border border-line bg-inset/60 px-3 py-1.5 text-2xs text-accent">python -m uvicorn services.console.main:app --port 8400</div></li>
          <li><span className="text-muted">{bi('连接前端(关闭 mock):', 'Point the console at it (mock off):')}</span>
            <div className="mono mt-1 rounded-lg border border-line bg-inset/60 px-3 py-1.5 text-2xs text-accent">VITE_API_URL=http://127.0.0.1:8400 VITE_MOCK_MODE=false npm run dev</div></li>
        </ol>
        {connected && <p className="text-2xs text-info">{bi('后端已连接,但尚未发现快照文件 —— 先执行第 1 步。', 'Backend connected, but no snapshot file found yet — run step 1.')}</p>}
      </div>
    </Panel>
  );
}

export default function CryptoLive() {
  const { payload, apiState, error, usingBackend } =
    useConsoleSnapshot<CryptoLivePayload>(s => fetchCryptoLiveSnapshot(s));

  const present = usingBackend && payload?.snapshot_present === true;
  const meta = payload?.meta;
  const summary = payload?.summary;
  const factors: CryptoFactor[] = present ? payload!.factors : [];
  const oos = present ? payload!.oos : null;
  const synSet = present ? payload!.synergistic_set : null;
  const cost = present ? payload!.cost_stress : null;
  const regime = present ? payload!.regime : null;
  const isGp = meta?.search_mode === 'gp_tree';
  const oosConfirmed = oos ? (oos.selection.mean_oos_sharpe ?? -1) > 0 && (oos.selection.oos_positive_frac ?? 0) >= 0.5 : false;

  /* Top factors by net Sharpe for the raw-vs-deflated comparison (already sorted desc). */
  const top = useMemo(() => factors.slice(0, 12), [factors]);
  const topLabels = useMemo(() => top.map(f => shortLabel(f.alpha_id)), [top]);
  const netSharpeSeries = useMemo(() => top.map(f => f.net_sharpe ?? 0), [top]);
  const deflatedSeries = useMemo(() => top.map(f => f.deflated_sharpe ?? 0), [top]);

  const factorCols: Column<CryptoFactor>[] = [
    { key: 'id', header: bi('因子', 'Factor'), render: f => {
      const disp = f.family === 'gp_tree' && f.expr ? f.expr : shortLabel(f.alpha_id);
      return <span className="mono text-accent" title={f.expr ?? f.alpha_id}>{disp.length > 44 ? `${disp.slice(0, 43)}…` : disp}</span>;
    } },
    { key: 'family', header: bi('族', 'Family'), render: f => {
      const fam = f.family === 'gp_tree' ? { l: bi('树/GP', 'GP tree'), t: 'accent' as const }
        : f.family === 'interaction' ? { l: bi('交互', 'interaction'), t: 'special' as const }
        : { l: bi('原子', 'price'), t: 'info' as const };
      return <Chip label={fam.l} tone={fam.t} />;
    } },
    { key: 'ic', header: 'IC', align: 'right', mono: true, render: f => <span className={(f.ic ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>{fixed(f.ic, 4)}</span> },
    { key: 'net', header: bi('净夏普', 'Net Sharpe'), align: 'right', mono: true, render: f => <span className={(f.net_sharpe ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>{fixed(f.net_sharpe, 3)}</span> },
    { key: 'dsr', header: bi('Deflated 夏普', 'Deflated SR'), align: 'right', mono: true, render: f => <span className="text-warn">{fixed(f.deflated_sharpe, 4)}</span> },
    { key: 'turn', header: bi('换手', 'Turnover'), align: 'right', mono: true, render: f => fixed(f.turnover, 3) },
    { key: 'breadth', header: bi('横截面', 'Breadth'), align: 'right', mono: true, render: f => fixed(f.breadth, 1) },
    { key: 'verdict', header: bi('裁决', 'Verdict'), render: f => <StatusChip value={f.verdict} /> },
    { key: 'state', header: bi('状态', 'State'), render: f => <StatusChip value={f.state ?? '—'} /> },
  ];

  const foldCols: Column<NonNullable<typeof oos>['selection']['folds'][number]>[] = [
    { key: 'w', header: bi('窗口', 'Window'), mono: true, render: f => `#${f.window}` },
    { key: 'sel', header: bi('选中', 'Selected'), render: f => <span className="mono text-accent">{(f.selected ?? []).map(shortLabel).join(', ') || '—'}</span> },
    { key: 'is', header: bi('样本内夏普', 'IS Sharpe'), align: 'right', mono: true, render: f => <span className="text-warn">{fixed(f.is_sharpe, 2)}</span> },
    { key: 'oos', header: bi('样本外夏普', 'OOS Sharpe'), align: 'right', mono: true, render: f => <span className={f.oos_sharpe >= 0 ? 'text-pos' : 'text-neg'}>{fixed(f.oos_sharpe, 2)}</span> },
  ];

  const coinCols: Column<NonNullable<typeof meta>['coins'][number]>[] = [
    { key: 'inst', header: bi('标的', 'Symbol'), render: c => <span className="mono font-semibold text-ink">{c.instrument_id}</span> },
    { key: 'coin', header: 'CoinGecko ID', render: c => <span className="mono text-faint">{c.coin_id}</span> },
    { key: 'bars', header: bi('K线数', 'Bars'), align: 'right', mono: true, render: c => num(c.bars) },
    { key: 'rej', header: bi('拒绝', 'Rejected'), align: 'right', mono: true, render: c => <span className={c.rejected ? 'text-neg' : 'text-pos'}>{num(c.rejected)}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Crypto Live" titleZh="加密实盘研究"
        subtitle={bi('真实只读加密行情(CoinGecko)上的机器因子发现 + 多重检验诚实裁决 —— 离线研究核心,实盘禁用',
          'Machine factor discovery on real read-only crypto (CoinGecko) with multiple-testing-honest verdicts — offline research core, live trading disallowed')}
        actions={present
          ? <Chip label={bi(`${meta!.n_instruments} 标的 · ${meta!.n_periods} 周期`, `${meta!.n_instruments} coins · ${meta!.n_periods} periods`)} tone="pos" />
          : <Chip label={bi('等待快照', 'awaiting snapshot')} tone="warn" />} />

      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} variant="crypto"
        realData={!!present} noDataYet={usingBackend && !present}
        source={payload?.data_provenance ?? 'COINGECKO_OFFLINE'} error={error}
        extraChips={<Chip label={bi('实盘禁用', 'LIVE_DISALLOWED')} tone="neg" />} />

      <LoadingVeil active={apiState === 'loading'}>
      {!present ? (
        <Instructions connected={usingBackend} />
      ) : (
        <>
          <KpiStrip cols={7}>
            <StatTile label={bi('标的数', 'Instruments')} value={num(meta!.n_instruments)} accent
              sub={bi(`${meta!.days}天 · ${meta!.granularity}`, `${meta!.days}d · ${meta!.granularity}`)} />
            <StatTile label={bi('横截面周期', 'Periods')} value={num(meta!.n_periods)} tone="info" />
            <StatTile label={bi('评估因子', 'Factors Evaluated')} value={num(summary!.n_evaluated)}
              sub={bi('= 多重检验试验数', '= multiple-testing trials')} />
            <StatTile label={bi('自动放行', 'Auto-Approved')} value={num(summary!.auto_approved)} tone={summary!.auto_approved === 0 ? 'pos' : 'neg'}
              sub={bi('容器化:恒为 0', 'contained: always 0')} />
            <StatTile label={bi('最高净夏普', 'Max Net Sharpe')} value={fixed(summary!.max_net_sharpe, 2)} tone="warn"
              sub={bi('原始(未校正)', 'raw, uncorrected')} />
            <StatTile label={bi('最高 Deflated', 'Max Deflated SR')} value={fixed(summary!.max_deflated_sharpe, 3)} tone="special"
              sub={bi('多重检验校正后', 'after MT penalty')} />
            <StatTile label={bi('需人工复核', 'Human Review')} value={num(summary!.n_evaluated - summary!.auto_approved)} tone="info"
              sub={bi('全部', 'all of them')} />
          </KpiStrip>

          <FactoryPipeline />

          {oos && (
            <Panel eyebrow={bi('样本外验证 · 无窥视滚动选择', 'Out-of-sample · no-peek walk-forward selection')}
              tone={oosConfirmed ? 'pos' : 'neg'}
              title={bi('样本内 alpha 是真是假?', 'Is the in-sample edge real, or selection overfit?')}
              right={<Chip label={oosConfirmed ? bi('OOS 通过', 'OOS CONFIRMED') : bi('OOS 未通过 · 选择性过拟合', 'OOS NOT CONFIRMED · selection overfit')}
                tone={oosConfirmed ? 'pos' : 'neg'} />}>
              <KpiStrip cols={5}>
                <StatTile label={bi('样本内选择夏普', 'IS selected Sharpe')} value={signed(oos.selection.mean_is_sharpe, 2)} tone="warn"
                  sub={bi('训练窗口上挑出的最优', 'best on train window')} />
                <StatTile label={bi('样本外夏普', 'OOS Sharpe')} value={signed(oos.selection.mean_oos_sharpe, 2)} tone={(oos.selection.mean_oos_sharpe ?? 0) >= 0 ? 'pos' : 'neg'}
                  sub={bi('同因子·未见窗口', 'same factor · unseen window')} />
                <StatTile label={bi('过拟合落差', 'Overfit haircut')} value={signed(oos.selection.oos_minus_is, 2)} tone="neg"
                  sub={bi('OOS − IS', 'OOS − IS')} />
                <StatTile label={bi('OOS 为正占比', 'OOS-positive folds')} value={pct(oos.selection.oos_positive_frac, 0)} tone={(oos.selection.oos_positive_frac ?? 0) >= 0.5 ? 'pos' : 'neg'}
                  sub={bi(`${oos.selection.n_folds} 折`, `${oos.selection.n_folds} folds`)} />
                <StatTile label={bi('候选池', 'Candidate pool')} value={num(oos.n_candidates)} tone="info"
                  sub={bi(`${oos.n_aligned_periods} 对齐周期`, `${oos.n_aligned_periods} aligned periods`)} />
              </KpiStrip>

              <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
                <div>
                  <div className="mb-2 text-2xs uppercase tracking-[0.12em] text-faint">{bi('逐窗口:样本内 vs 样本外', 'Per window: in-sample vs out-of-sample')}</div>
                  <DataTable columns={foldCols} rows={oos.selection.folds} getKey={f => f.window} dense />
                </div>
                <div className="flex flex-col gap-2.5 text-[12.5px]">
                  <div className="text-2xs uppercase tracking-[0.12em] text-faint">{bi('头部因子单独追踪(无选择)', 'Headline factor tracked (no selection)')}</div>
                  <div className="mono truncate text-2xs text-special" title={oos.headline_factor.expr ?? oos.headline_factor.alpha_id}>{oos.headline_factor.expr ?? oos.headline_factor.alpha_id}</div>
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('全样本夏普(窥视)', 'Full-sample Sharpe (peeking)')}</span><span className="mono text-warn">{signed(oos.headline_factor.full_sample_sharpe, 2)}</span></div>
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('样本外均值夏普', 'Mean OOS Sharpe')}</span><span className={`mono ${(oos.headline_factor.mean_oos_sharpe ?? 0) >= 0 ? 'text-pos' : 'text-neg'}`}>{signed(oos.headline_factor.mean_oos_sharpe, 2)}</span></div>
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('朴素全样本最优(窥视)', 'Naive full-sample best (peeking)')}</span><span className="mono text-faint">{shortLabel(oos.naive_full_sample_best.alpha_id)} {signed(oos.naive_full_sample_best.sharpe, 2)}</span></div>
                  <p className="mt-1 border-t border-line/60 pt-2 text-2xs text-faint">
                    {oosConfirmed
                      ? bi('样本外仍为正:该 edge 在未见数据上站得住,可进入下一阶段人工尽调。',
                        'Positive out-of-sample: the edge holds on unseen data — eligible for the next human due-diligence stage.')
                      : bi('样本外转负:样本内的高夏普/Deflated 是「从多次搜索里挑最优」的选择性过拟合,真换手到未见窗口即反转 —— 系统未自动放行任何因子,正是对的。这不是失败,是诚实的回测在替你省钱。',
                        'Negative out-of-sample: the high in-sample Sharpe/Deflated was selection overfit (best-of-many-trials); it inverts on unseen windows. The system auto-promoted nothing — exactly right. Not a failure: an honest backtest saving you money.')}
                  </p>
                </div>
              </div>
            </Panel>
          )}

          {(cost || regime) && (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {cost && cost.points.length > 0 && (
                <Panel eyebrow={bi('成本压力', 'Cost stress')}
                  title={bi(`净夏普 随 单边成本 · ${shortLabel(cost.factor_id)}`, `Net Sharpe vs one-way cost · ${shortLabel(cost.factor_id)}`)}
                  right={<Chip label={bi(`盈亏平衡 ≈ ${cost.breakeven_bps ?? '—'} bps`, `breakeven ≈ ${cost.breakeven_bps ?? '—'} bps`)}
                    tone={(cost.breakeven_bps ?? 0) >= 20 ? 'pos' : 'warn'} />}>
                  <BarSeries data={cost.points.map(p => p.net_sharpe)} labels={cost.points.map(p => `${p.one_way_bps}bps`)}
                    height={172} valueFormat={v => v.toFixed(2)} />
                  <p className="mt-2 text-2xs text-faint">
                    {bi(`换手率 ${fixed(cost.turnover, 3)}/期。现实中盘币 ~10bps 单边仍为正;过 ~${cost.breakeven_bps ?? 30}bps 才转负 —— 真能扛成本,非毛收益幻觉。`,
                      `Turnover ${fixed(cost.turnover, 3)}/period. Still positive at a realistic ~10bps one-way; only turns negative past ~${cost.breakeven_bps ?? 30}bps — cost-robust, not a gross-return illusion.`)}
                  </p>
                </Panel>
              )}
              {regime && (regime.by_trend.length > 0 || regime.by_vol.length > 0) && (
                <Panel eyebrow={bi('Regime 拆解', 'Regime split')}
                  title={bi(`各状态下的表现 · ${shortLabel(regime.factor_id)}`, `Performance by state · ${shortLabel(regime.factor_id)}`)}
                  right={<Chip label={bi('涨跌市 / 波动率', 'trend / vol')} tone="info" />}>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div>
                      <div className="mb-1.5 text-2xs uppercase tracking-[0.12em] text-faint">{bi('按涨跌市(原始每日夏普)', 'By market trend (raw daily Sharpe)')}</div>
                      <BarSeries data={regime.by_trend.map(b => b.sharpe ?? 0)} labels={regime.by_trend.map(b => b.label)}
                        height={150} valueFormat={v => v.toFixed(3)} />
                    </div>
                    <div>
                      <div className="mb-1.5 text-2xs uppercase tracking-[0.12em] text-faint">{bi('按波动率', 'By volatility')}</div>
                      <BarSeries data={regime.by_vol.map(b => b.sharpe ?? 0)} labels={regime.by_vol.map(b => b.label)}
                        height={150} valueFormat={v => v.toFixed(3)} />
                    </div>
                  </div>
                  <p className="mt-2 text-2xs text-faint">
                    {bi('涨市、跌/横盘、低波、高波都为正 → 美元中性相对强度因子,天然与大盘方向无关。',
                      'Positive in up, down/flat, low- and high-vol → a dollar-neutral relative-strength factor, structurally market-direction-neutral.')}
                  </p>
                </Panel>
              )}
            </div>
          )}

          <Panel eyebrow={bi('多重检验诚实', 'Multiple-testing honesty')}
            title={bi('原始净夏普 vs Deflated 夏普(净夏普 Top 12)', 'Raw Net Sharpe vs Deflated Sharpe (top 12 by net Sharpe)')}
            right={<Chip label={bi('搜索越多 · 校正越狠', 'more search → harsher haircut')} tone="warn" />}>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div>
                <div className="mb-1.5 text-2xs uppercase tracking-[0.12em] text-faint">{bi('净夏普(原始,易被噪声抬高)', 'Net Sharpe (raw — inflated by noise)')}</div>
                <BarSeries data={netSharpeSeries} labels={topLabels} height={188} valueFormat={v => v.toFixed(1)} />
              </div>
              <div>
                <div className="mb-1.5 text-2xs uppercase tracking-[0.12em] text-faint">{bi('Deflated 夏普(扣除搜索次数后)', 'Deflated Sharpe (after trial-count penalty)')}</div>
                <BarSeries data={deflatedSeries} labels={topLabels} height={188} posTone="special" valueFormat={v => v.toFixed(3)} />
              </div>
            </div>
            <p className="mt-2 text-2xs text-faint">
              {bi('同一批因子:原始净夏普可达 ~0.9,但 Deflated 夏普扣除"机器一共搜了 60 次"后趋近 0 —— 因此没有任何因子被自动放行,全部进入人工复核。这是系统的统计良知,而非缺陷。',
                'Same factors: raw net Sharpe reaches ~0.9, but Deflated Sharpe — after penalizing "the machine searched 60 times" — collapses toward 0. Hence nothing is auto-promoted; all go to human review. This is the system\'s statistical conscience, not a bug.')}
            </p>
          </Panel>

          {synSet && synSet.selected.length > 0 && (
            <Panel eyebrow={bi('去冗余 · 增量 IC 前向选择', 'De-redundancy · incremental-IR forward selection')}
              title={bi('协同因子集(最优"组合"而非最强"单体")', 'Synergistic factor set (best SET, not greedy top-N)')}
              right={<Chip label={bi(`${synSet.n_candidates} → ${synSet.selected.length} 个独立`, `${synSet.n_candidates} → ${synSet.selected.length} independent`)} tone="pos" />}>
              <p className="mb-3 text-2xs text-faint">
                {bi(`从 ${synSet.n_candidates} 个机器发现因子中,按"对已选集合的增量信息(正交化残差 IR × 新颖方差占比)"前向选择;其余 ${synSet.dropped_redundant.length} 个是冗余克隆(信息已被覆盖)被剔除 —— 这正是 GP 顶树全是低波动克隆问题的解。`,
                  `From ${synSet.n_candidates} machine-discovered factors, forward-selected by INCREMENTAL information vs the chosen set (orthogonalized residual IR × novel-variance share); the other ${synSet.dropped_redundant.length} are redundant clones (information already covered) and were dropped — the fix for the GP top trees all being low-vol clones.`)}
              </p>
              <div className="flex flex-col gap-2">
                {synSet.selected.map((s, i) => (
                  <div key={s.id} className="flex items-center gap-3 border-b border-line/50 pb-2 last:border-0 last:pb-0">
                    <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-2xs font-bold text-ink" style={{ background: 'var(--inset)', boxShadow: 'inset 0 0 0 1px var(--line)' }}>{i + 1}</span>
                    <span className="mono min-w-0 flex-1 truncate text-xs text-accent" title={s.expr ?? s.id}>{s.expr ?? s.id}</span>
                    <span className="shrink-0 text-2xs text-faint">{bi('增量 IR', 'incr. IR')}</span>
                    <span className="tnum shrink-0 text-xs text-special">{fixed(s.incremental_ir, 3)}</span>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Panel className="xl:col-span-2" eyebrow={bi('机器发现', 'Machine-discovered')}
              title={isGp
                ? bi('因子清单(GP 表达式树:机器发现结构本身)', 'Factor catalog (GP expression trees: machine-discovered structure)')
                : bi('因子清单(进化搜索:原子 + 交叉交互)', 'Factor catalog (evolutionary: atoms + crossover interactions)')}
              right={<GateDot ok label={bi('只读', 'read-only')} />}>
              <DataTable columns={factorCols} rows={factors} getKey={f => f.alpha_id} dense />
            </Panel>

            <div className="flex flex-col gap-4">
              <Panel eyebrow={bi('数据', 'Universe')} title={bi('真实标的横截面', 'Real instrument cross-section')}
                right={(meta?.n_dropped ?? 0) > 0 ? <Chip label={bi(`${meta!.n_dropped} 跳过`, `${meta!.n_dropped} dropped`)} tone="warn" /> : undefined}>
                {meta && <DataTable columns={coinCols} rows={meta.coins} getKey={c => c.instrument_id} dense />}
              </Panel>

              <Panel eyebrow={bi('安全边界', 'Safety')} title={bi('容器化 · 实盘禁用', 'Containment · live disallowed')}>
                <div className="flex flex-col gap-2.5 text-[12.5px]">
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('自动放行 (agent)', 'Auto-approved (agent)')}</span><Chip label={`${payload!.safety.auto_approved} ✓`} tone="pos" /></div>
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('agent 可达 APPROVED', 'Agent can reach APPROVED')}</span><Chip label={payload!.safety.agent_can_reach_approved ? 'YES' : 'NO ✓'} tone={payload!.safety.agent_can_reach_approved ? 'neg' : 'pos'} /></div>
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('services/ 含网络', 'Network in services/')}</span><Chip label={payload!.safety.network_in_services ? 'YES' : 'NO ✓'} tone={payload!.safety.network_in_services ? 'neg' : 'pos'} /></div>
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('实盘交易', 'Live trading')}</span><Chip label={payload!.safety.live_trading} tone="neg" /></div>
                  <div className="mono mt-1 border-t border-line/60 pt-2 text-2xs text-faint">{bi('抓取路径', 'fetch path')}: {payload!.safety.fetch_path}</div>
                </div>
              </Panel>

              <Panel eyebrow={bi('可复现', 'Reproducible')} title={bi('数据集版本', 'Dataset version')}>
                <div className="flex flex-col gap-2 text-[12.5px]">
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('内容哈希', 'content hash')}</span><span className="mono text-accent" title={payload!.dataset_version}>{shortHash(payload!.dataset_version, 12)}</span></div>
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('生成时间(UTC)', 'generated (UTC)')}</span><span className="mono text-ink">{stamp(payload!.generated_at)}</span></div>
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('搜索模式', 'search mode')}</span><span className="mono text-ink">{meta!.search_mode}</span></div>
                  <div className="flex items-center justify-between"><span className="text-muted">{bi('进化(代/群/精英)', 'evo (gen/pop/elite)')}</span><span className="mono text-ink">{meta!.evo_generations}/{meta!.evo_population}/{meta!.evo_elite}</span></div>
                </div>
              </Panel>
            </div>
          </div>
        </>
      )}
      </LoadingVeil>
    </PageShell>
  );
}
