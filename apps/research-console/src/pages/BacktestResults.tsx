import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, StatusChip, Chip, LoadingVeil, Callout, type Column,
} from '@/ui/primitives';
import { BarSeries, DivergingBars } from '@/ui/charts';
import { fixed } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchBacktestResultsSnapshot } from '@/lib/api/research-console-api';
import type { BacktestResultsPayload, BacktestRunBlock } from '@/lib/types/research';

/**
 * Backtest Results — the HONEST results of the real crypto backtests. When the console API is connected
 * these figures stream LIVE from the run_registry (one done run per role: carry / combo / oi_research /
 * survivorship); offline they fall back to the OBSERVED constants below (the same numbers transcribed from
 * tool output). A handful of figures have no single run field (Carry per-year, R² vs carry, the 2.09
 * headline) and stay OBSERVED in both modes — we never fabricate a run field for them. Every figure carries
 * its discount (survivorship, capacity, modeled slippage). Past backtest != future performance.
 */

// ---- OBSERVED fallback constants (also the source for figures with no run field) --------------------
const CARRY_YEARS = [
  { label: '20', value: 4.4 }, { label: '21', value: 2.8 }, { label: '22', value: 1.5 },
  { label: '23', value: 0.5 }, { label: '24', value: 1.4 }, { label: '25', value: 3.0 }, { label: '26', value: 2.0 },
];
const CROWD_YEARS = [
  { label: '22', value: 1.82 }, { label: '23', value: 1.35 }, { label: '24', value: 1.28 },
  { label: '25', value: 1.28 }, { label: '26', value: 0.84 },
];
const COMBO_YEARS = [
  { label: '21', value: 1.62 }, { label: '22', value: 2.2 }, { label: '23', value: 2.07 },
  { label: '24', value: 1.09 }, { label: '25', value: 1.48 }, { label: '26', value: 2.02 },
];
const EXEC_LAG = [{ label: 'lag0', value: 1.18 }, { label: 'lag1', value: 1.03 }, { label: 'lag2', value: 0.89 }];

interface Edge { name: string; what: string; sharpe: string; status: string; note: string; }
const EDGES: Edge[] = [
  { name: 'funding carry', what: bi('资金费 carry(收资金费)', 'collect perp funding'), sharpe: '2.0', status: 'DEPLOYABLE',
    note: bi('唯一可部署边·流动半区净 2.09/年化 47%', 'only deployable edge · liquid half net 2.09 / 47% ann') },
  { name: 'crowd_contra', what: bi('逆向散户持仓', 'fade crowded retail'), sharpe: '0.9', status: 'WATCH',
    note: bi('第二条边·与 carry 正交·幸存者后 ~0.9', 'second edge · orthogonal to carry · ~0.9 post-survivorship') },
  { name: 'carry × crowd', what: bi('双因子组合', 'two-factor book'), sharpe: '1.5', status: 'OK',
    note: bi('组合 SR 1.50 / maxDD 0.12(回撤腰斩)', 'combo SR 1.50 / maxDD 0.12') },
  { name: 'vol×price', what: bi('量×价(机器发现)', 'machine-found vol×price'), sharpe: '—', status: 'REJECTED',
    note: bi('4 路对抗验证证伪·同 bar 时序假象', 'refuted by 4-vector verify · same-bar timing artifact') },
  { name: 'top_ls / intraday', what: bi('top-trader / 日内动态', 'top-trader / intraday'), sharpe: '—', status: 'REJECTED',
    note: bi('深压测降级 / 0 幸存·非稳健边', 'downgraded / 0 survivors — not robust') },
];

// ---- real-run accessors (fall back to the OBSERVED constant when a block / field is absent) ----------
function metric(block: BacktestRunBlock | null | undefined, key: string, fallback: number): number {
  const v = block?.metrics?.[key];
  return typeof v === 'number' ? v : fallback;
}
function series(block: BacktestRunBlock | null | undefined, key: string,
                fallback: { label: string; value: number }[]) {
  const arr = block?.series?.[key];
  return Array.isArray(arr) && arr.length ? arr : fallback;
}

function edgeColumns(): Column<Edge>[] {
  return [
    { key: 'name', header: bi('因子', 'Edge'), mono: true, render: e => <span className="text-accent">{e.name}</span> },
    { key: 'what', header: bi('是什么', 'What'), render: e => <span className="text-ink">{e.what}</span> },
    { key: 'sharpe', header: bi('夏普', 'Sharpe'), align: 'right', mono: true,
      render: e => <span className={e.sharpe === '—' ? 'text-faint' : 'text-pos'}>{e.sharpe}</span> },
    { key: 'status', header: bi('裁决', 'Verdict'), render: e => <StatusChip value={e.status} /> },
    { key: 'note', header: bi('说明', 'Note'), render: e => <span className="text-2xs text-muted">{e.note}</span> },
  ];
}

export default function BacktestResults() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<BacktestResultsPayload>(
    sig => fetchBacktestResultsSnapshot(sig));
  const carry = payload?.carry, combo = payload?.combo, oi = payload?.oi_research, surv = payload?.survivorship;

  // figures WITH a real run field bind live; figures WITHOUT stay OBSERVED (constants) in both modes.
  const comboYears = series(combo, 'per_year', COMBO_YEARS);
  const crowdYears = series(surv, 'per_year', CROWD_YEARS);   // honest correction: real survivorship per-year
  const execLag = series(oi, 'exec_lag', EXEC_LAG);
  const comboSharpe = metric(combo, 'combo_sharpe', 1.50);
  const priceSharpe = execLag[0]?.value ?? 1.18;
  const afterLag = execLag[1]?.value ?? 1.03;

  return (
    <PageShell>
      <PageHeader title="Backtest Results" titleZh="回测战果"
        subtitle={bi('真实加密回测的诚实战果:1 条可部署边 + 1 条机器发现的第二条边;每个数字都带折扣',
          'Honest results of the real crypto backtests — 1 deployable edge + 1 machine-found second edge; every figure carries its discount')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && payload?.data_provenance === 'RESEARCH_RUN_REGISTRY'}
        source={usingBackend ? 'RESEARCH_RUN_REGISTRY' : 'FRONTEND_OBSERVED_SNAPSHOT'} error={error}
        extraChips={<>
          <Chip label="L2_RESEARCH_ONLY" tone="info" />
          <Chip label={bi('非投资建议', 'NOT ADVICE')} tone="warn" />
        </>} />

      <LoadingVeil active={apiState === 'loading'}>
      <KpiStrip cols={4}>
        <StatTile label={bi('稳健正交边', 'Robust edges')} value="2" accent
          sub={bi('carry + crowd_contra', 'carry + crowd_contra')} />
        <StatTile label={bi('资金费 carry 净夏普', 'Carry net Sharpe')}
          value={fixed(metric(carry, 'sharpe_raw', 2.09), 2)} tone="pos"
          sub={bi(`流动半区·年化 ${fixed(metric(carry, 'ann_return', 0.47) * 100, 0)}%`,
            `liquid half · ${fixed(metric(carry, 'ann_return', 0.47) * 100, 0)}% ann`)} />
        <StatTile label={bi('第二条边 与 carry 相关', '2nd edge R² vs carry')}
          value={`${fixed(metric(oi, 'crowd_contra_r2_vs_carry', 0.005) * 100, 1)}%`} tone="info"
          sub={bi('几乎完全正交', 'near-orthogonal')} />
        <StatTile label={bi('双因子组合夏普', 'Combo Sharpe')} value={fixed(comboSharpe, 2)} tone="pos"
          sub={bi(`maxDD ${fixed(metric(combo, 'max_drawdown', 0.12), 2)}`, `maxDD ${fixed(metric(combo, 'max_drawdown', 0.12), 2)}`)} />
      </KpiStrip>

      <Panel eyebrow={bi('诚实地图', 'Honest Map')} title={bi('我们到底有什么', 'What We Actually Have')}
        right={<Chip label={bi('2 真 / 3 证伪', '2 real / 3 rejected')} tone="muted" />}>
        <DataTable columns={edgeColumns()} rows={EDGES} dense getKey={e => e.name} />
        <Callout tone="muted" summary={bi('数百因子自主搜索 → 诚实 gauntlet 只留两条稳健正交边',
          'Hundreds of factors autonomously searched → the honesty gauntlet leaves two robust orthogonal edges')}>
          {bi('机器在 9–15 维(价/量/资金费/持仓/基差/日内)自主搜了数百因子;诚实 gauntlet(IC/滞后/成本/多重检验/幸存者)把脆弱假象全毙了。免费加密日频的稳健正交空间就这两条。',
            'The machine autonomously searched hundreds of factors across 9–15 dims; the honesty gauntlet (IC/lag/cost/multiplicity/survivorship) killed every fragile artifact. Only two robust orthogonal edges exist in free crypto daily data.')}
        </Callout>
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel eyebrow={bi('第一条边', 'Edge #1')} title={bi('资金费 Carry(可部署)', 'Funding Carry (deployable)')}
          tone="pos">
          <KpiStrip cols={3}>
            <StatTile label={bi('净夏普@5bps', 'Net Sharpe @5bps')} value={fixed(metric(carry, 'net_sharpe_5bps', 2.03), 2)} tone="pos" />
            <StatTile label={bi('日均收资金费', 'Funding/day')} value={`${fixed(metric(carry, 'funding_bps_day', 5.43), 2)} bps`} tone="pos" />
            <StatTile label={bi('换手', 'Turnover')} value={fixed(metric(carry, 'turnover', 0.28), 2)} tone="info" />
            <StatTile label={bi('流动半区年化', 'Liquid ann')}
              value={`${fixed(metric(carry, 'ann_return', 0.47) * 100, 0)}%`} tone="pos" />
            <StatTile label={bi('最大回撤', 'Max DD')}
              value={`${fixed(metric(carry, 'max_drawdown', 0.26) * 100, 0)}%`} tone="warn" />
            <StatTile label={bi('最差单日', 'Worst day')}
              value={`${fixed(metric(carry, 'worst_day', -0.068) * 100, 1)}%`} tone="neg" />
          </KpiStrip>
          <div className="mt-3 text-2xs text-muted">{bi('逐年净夏普(流动半区,@5bps)— 7/7 年全正含 2022 熊市 · 观察值',
            'Per-year net Sharpe (liquid half, @5bps) — 7/7 years positive incl. 2022 bear · observed')}</div>
          <BarSeries data={CARRY_YEARS.map(y => y.value)} labels={CARRY_YEARS.map(y => `'${y.label}`)}
            valueFormat={v => fixed(v, 1)} />
          <Callout tone="muted" summary={bi('是 yield 不是价格预测 · 折扣:幸存者/滑点/尾部/容量',
            'A yield, not price prediction · discounts: survivorship / slippage / tail / capacity')}>
            {bi('多空两腿都收资金费(做多负费币、做空正费币),是 yield 不是价格预测。折扣:幸存者偏差、滑点为建模值非实测、爆仓尾部风险未建模、容量有限。',
              'Collects funding on both legs — a yield, not price prediction. Discounts: survivorship, modeled (not measured) slippage, unmodeled liquidation tail risk, finite capacity.')}
          </Callout>
        </Panel>

        <Panel eyebrow={bi('第二条边', 'Edge #2')} title={bi('逆向散户持仓 crowd_contra', 'Fade Crowded Retail')}
          tone="info" right={<StatusChip value="WATCH" />}>
          <KpiStrip cols={3}>
            <StatTile label={bi('价夏普', 'Price Sharpe')} value={fixed(priceSharpe, 2)} tone="pos" />
            <StatTile label={bi('与 carry 相关 R²', 'R² vs carry')}
              value={`${fixed(metric(oi, 'crowd_contra_r2_vs_carry', 0.005) * 100, 1)}%`} tone="info" />
            <StatTile label={bi('增量 IR', 'Incremental IR')} value={`+${fixed(metric(oi, 'incremental_ir', 1.13), 2)}`} tone="pos" />
            <StatTile label={bi('滞后 1 天后', 'After 1-day lag')} value={fixed(afterLag, 2)} tone="pos" />
            <StatTile label={bi('幸存者检验后', 'Post-survivorship')} value={fixed(metric(surv, 'complete_sharpe_raw', 0.93), 2)} tone="warn" />
            <StatTile label={bi('换手', 'Turnover')} value="0.26" tone="info" />
          </KpiStrip>
          <div className="mt-3 text-2xs text-muted">{bi('逐年价夏普(幸存者完整口径)— 含 2022 熊市',
            'Per-year Sharpe (complete survivorship basis) — incl. 2022 bear')}</div>
          <BarSeries data={crowdYears.map(y => y.value)} labels={crowdYears.map(y => `'${y.label}`)}
            posTone="info" valueFormat={v => fixed(v, 2)} />
          <Callout tone="muted" summary={bi('候选,非确认 · WEAK_LEAD(多重检验 + 近端软化 + 幸存者折扣)',
            'A candidate, not confirmed · WEAK_LEAD (multiplicity + recent softening + survivorship haircut)')}>
            {bi('散户多空比太偏多头就做空,与资金费 carry 几乎完全正交。4 路对抗验证无致命缺陷但裁决 WEAK_LEAD(多重检验+近端软化+幸存者打 ~0.3 折)。是候选,非确认。',
              'Short the coins retail is over-long — near-orthogonal to carry. 4-vector verify found no kill-shot but verdict WEAK_LEAD (multiplicity + recent softening + ~0.3 survivorship haircut). A candidate, not confirmed.')}
          </Callout>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('组合', 'Portfolio')}
          title={bi('双因子组合(carry × crowd_contra)', 'Two-Factor Book')} tone="pos">
          <KpiStrip cols={4}>
            <StatTile label={bi('组合夏普', 'Combo Sharpe')} value={fixed(comboSharpe, 2)} tone="pos" />
            <StatTile label={bi('最大回撤', 'Max DD')} value={fixed(metric(combo, 'max_drawdown', 0.12), 2)} tone="pos" sub={bi('低于任一单因子', 'below either alone')} />
            <StatTile label={bi('净@10bps', 'Net @10bps')} value={fixed(metric(combo, 'net_10bps', 1.02), 2)} tone="pos" />
            <StatTile label={bi('比最佳单因子', 'vs best alone')} value={`+${fixed(metric(combo, 'lift_vs_best', 0.23), 2)}`} tone="pos" />
          </KpiStrip>
          <div className="mt-3 text-2xs text-muted">{bi('逐年组合经济夏普(2020–26)— 全正',
            'Per-year combo economic Sharpe (2020–26) — all positive')}</div>
          <BarSeries data={comboYears.map(y => y.value)} labels={comboYears.map(y => `'${y.label}`)}
            valueFormat={v => fixed(v, 2)} />
          <Callout tone="muted" summary={bi('正交边组合的教科书效果 · 继承部分未重测的幸存者折扣',
            'Textbook effect of combining orthogonal edges · inherits some un-retested survivorship discount')}>
            {bi('真优化器把两条正交边去冗余组合 → 更高夏普 + 更低回撤(正交边组合的教科书效果)。组合数字继承 crowd_contra 部分未重测的幸存者折扣。',
              'The real optimizer de-redundantly combines the two orthogonal edges → higher Sharpe + lower drawdown. The combo figure inherits part of crowd_contra\'s un-retested survivorship discount.')}
          </Callout>
        </Panel>

        <Panel eyebrow={bi('诚实折扣', 'Discounts')} title={bi('每个数字都要打折', 'Every Figure Carries These')}
          tone="warn">
          <div className="flex flex-col gap-2">
            {[
              bi('历史回测 ≠ 未来收益', 'Backtest ≠ future returns'),
              bi('幸存者偏差(只含活到今天的币)', 'Survivorship (only coins alive today)'),
              bi('滑点为建模值,非真实成交', 'Slippage modeled, not live-filled'),
              bi('carry 是拥挤 yield,会衰减', 'Carry is a crowded yield, may decay'),
              bi('尾部/爆仓风险未完全建模', 'Tail / liquidation risk under-modeled'),
              bi('容量有限,大资金会自我冲击', 'Finite capacity'),
            ].map((t, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg border border-line bg-inset/40 px-3 py-1.5">
                <span className="text-warn">▸</span><span className="text-2xs text-muted">{t}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('机器自主发现', 'Autonomous Discovery')}
        title={bi('引擎能力 + 被诚实机器毙掉的假阳性', 'Engine Capability + the False Positives It Killed')}>
        <DivergingBars height={150} valueFormat={v => v > 0 ? bi('真', 'real') : bi('毙', 'killed')}
          items={[
            { label: 'carry', value: 1 }, { label: 'crowd_contra', value: 1 },
            { label: 'top_ls', value: -0.5 }, { label: 'vol×price', value: -1 }, { label: 'intraday×6', value: -1 },
          ]} />
        <Callout tone="muted" summary={bi('能力已落地,稳健空间已挖尽 · 下一步需要另一种数据',
          'Capability landed, robust space mined out · next lever is a different kind of data')}>
          {bi('机器把已知边作为「正交奖励」基准,自主在多维空间繁殖跨维度因子;诚实 gauntlet 把同 bar 时序假象(量×价纸面 1.38)和脆弱近年 lead(top_ls)全部毙掉。能力已落地,稳健空间已挖尽 —— 下一步要的是另一种数据(盘口微结构/链上/跨资产)。',
            'The machine breeds cross-dim factors rewarded for orthogonality to known edges; the honesty gauntlet killed same-bar timing artifacts (vol×price showed 1.38 in-sample) and fragile recent-years leads (top_ls). Capability landed, robust space mined out — the next lever is a different kind of data (order-book microstructure / on-chain / cross-asset).')}
        </Callout>
      </Panel>
      </LoadingVeil>
    </PageShell>
  );
}
