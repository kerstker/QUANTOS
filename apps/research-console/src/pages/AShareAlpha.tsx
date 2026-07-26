import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, Chip,
  ProgressBar, LoadingVeil, type Column,
} from '@/ui/primitives';
import { pct, fixed, signed, money, num, shortHash, deltaTone, type Tone } from '@/ui/format';
import { EquityCurveChart } from '@/ui/workbench';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  fetchAShareAlphaSnapshot, fetchResearchRun, isResearchConsoleApiEnabled,
} from '@/lib/api/research-console-api';
import {
  fetchRunnerHealth, getRunnerToken, runnerEnabled, triggerRun,
} from '@/lib/api/research-runner-api';
import type { AShareAlphaPayload, ResearchRunDetail } from '@/lib/types/research';

/**
 * A-Share Alpha 工作台 — A股微盘多因子研究栈的第一个前端表面。
 * 只读快照(GET /api/v1/console/ashare-alpha):因子 IC / 去冗余 → 复合冠军 → 篮子 vs 基准
 * (test 窗口)→ 成本/换手敏感性 → 执行现实层(干跑)→ paper-track。诚实局限逐条默认展开。
 * 两个 runner 操作(kind=ashare_alpha / ashare_paper_track)是唯一可变入口,走受守卫的
 * research_runner(白名单 + X-Runner-Token);runner 不可用时按钮禁用并显示原因。
 * L2_DRY_RUN_ONLY —— 实盘 = 非目标。
 */

/* 确定性 mock(无 Math.random/Date.now):后端不可达时的演示回退,provenance 明确标 SYNTHETIC。 */
const MOCK_N = 49;
const mockEquity = Array.from({ length: MOCK_N }, (_, i) => 1 + 0.0115 * i + 0.04 * Math.sin(i * 0.55) + 0.02 * Math.sin(i * 0.13));
const mockBench = Array.from({ length: MOCK_N }, (_, i) => 1 + 0.004 * i + 0.06 * Math.sin(i * 0.4 + 1));
const mockPayload: AShareAlphaPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true,
  provenance: 'SYNTHETIC_DETERMINISTIC', generated_at: '2026-07-21T00:00:00Z',
  data_freshness: { stocks: 4873, bars: 2284160, last_date: 20260718, sources: { wind: 3412, ifind: 1461 } },
  universe: { file_count: 5120, used: 4873 },
  factors: [
    { name: 'rev_5d', train_ic: 0.061, train_t: 5.8, test_ic: 0.054, test_t: 4.6, kept: true },
    { name: 'turnover_20d', train_ic: 0.048, train_t: 4.9, test_ic: 0.041, test_t: 3.8, kept: true },
    { name: 'vol_20d', train_ic: 0.039, train_t: 3.7, test_ic: 0.031, test_t: 2.9, kept: true },
    { name: 'size_lncap', train_ic: 0.044, train_t: 4.2, test_ic: 0.036, test_t: 3.1, kept: true },
    { name: 'mom_20d', train_ic: 0.021, train_t: 1.9, test_ic: 0.012, test_t: 1.1, kept: false },
    { name: 'rev_1d', train_ic: 0.058, train_t: 5.1, test_ic: 0.049, test_t: 4.0, kept: false },
  ],
  combo: {
    champion: 'icir_weighted_h1',
    weights: { rev_5d: 0.34, turnover_20d: 0.27, vol_20d: 0.22, size_lncap: 0.17 },
    h1_test_ic: 0.068, h1_test_t: 5.9, h5_test_ic: 0.052, h5_test_t: 4.4, hit_rate: 0.58,
  },
  basket: {
    n: 50, aum_cny: 5_000_000, cost_bps: 25,
    test: { total_return: 0.612, sharpe: 1.83, max_dd: 0.214, avg_turnover: 0.31 },
    bench_test: { total_return: 0.221, sharpe: 0.72, max_dd: 0.286 },
    equity_test: mockEquity, bench_equity_test: mockBench,
  },
  variants: [
    { name: 'icir_weighted_h1', train_sharpe: 2.41, test_return: 0.612, test_sharpe: 1.83, test_mdd: 0.214, turnover: 0.31, is_champion: true },
    { name: 'equal_weight_h1', train_sharpe: 2.18, test_return: 0.542, test_sharpe: 1.64, test_mdd: 0.231, turnover: 0.29, is_champion: false },
    { name: 'icir_weighted_h5', train_sharpe: 2.05, test_return: 0.487, test_sharpe: 1.52, test_mdd: 0.243, turnover: 0.14, is_champion: false },
    { name: 'top30_concentrated', train_sharpe: 2.22, test_return: 0.503, test_sharpe: 1.47, test_mdd: 0.278, turnover: 0.35, is_champion: false },
    { name: 'no_size_factor', train_sharpe: 1.86, test_return: 0.402, test_sharpe: 1.28, test_mdd: 0.251, turnover: 0.3, is_champion: false },
  ],
  dry_run: {
    run_id: 'a3f9c1e2b7d4480a9e5f1c2d3b4a5f60', total_return: 0.034, sharpe: 1.42,
    max_drawdown: 0.041, n_approved: 148, n_rejected: 0, residual: 7.2e-9,
  },
  paper_track: {
    alpha_id: 'ashare_microcap_icir_h1', n_weeks: 14, cum_return: 0.087, sharpe: 1.96,
    weekly_ic_mean: 0.049, weekly_ic_t: 3.7, kind: 'BACKFILL',
  },
  cost_grid: [
    { cost_bps: 10, total_return: 0.712, sharpe: 2.05 },
    { cost_bps: 20, total_return: 0.648, sharpe: 1.9 },
    { cost_bps: 30, total_return: 0.584, sharpe: 1.74 },
    { cost_bps: 50, total_return: 0.462, sharpe: 1.43 },
    { cost_bps: 80, total_return: 0.281, sharpe: 0.98 },
  ],
  turnover_caps: [
    { cap: 0.1, train_sharpe: 1.62, test_return: 0.381, test_sharpe: 1.31 },
    { cap: 0.2, train_sharpe: 2.11, test_return: 0.512, test_sharpe: 1.62 },
    { cap: 0.3, train_sharpe: 2.41, test_return: 0.612, test_sharpe: 1.83 },
    { cap: 0.5, train_sharpe: 2.48, test_return: 0.588, test_sharpe: 1.77 },
    { cap: 1.0, train_sharpe: 2.5, test_return: 0.541, test_sharpe: 1.66 },
  ],
  holdings_latest: [
    { code: '002647.SZ', weight: 0.0241 }, { code: '300742.SZ', weight: 0.0236 },
    { code: '603966.SH', weight: 0.0231 }, { code: '002883.SZ', weight: 0.0228 },
    { code: '300981.SZ', weight: 0.0224 }, { code: '603178.SH', weight: 0.022 },
    { code: '002931.SZ', weight: 0.0217 }, { code: '300865.SZ', weight: 0.0213 },
    { code: '603655.SH', weight: 0.0209 }, { code: '002795.SZ', weight: 0.0206 },
    { code: '300611.SZ', weight: 0.0202 }, { code: '603949.SH', weight: 0.0199 },
    { code: '002969.SZ', weight: 0.0195 }, { code: '300506.SZ', weight: 0.0192 },
    { code: '603758.SH', weight: 0.0188 }, { code: '002634.SZ', weight: 0.0185 },
    { code: '300736.SZ', weight: 0.0181 }, { code: '603045.SH', weight: 0.0178 },
    { code: '002806.SZ', weight: 0.0174 }, { code: '300535.SZ', weight: 0.0171 },
  ],
  honest_limitations: [
    '演示数据(SYNTHETIC_DETERMINISTIC):后端快照未接入时展示,非真实研究结果。',
    '微盘流动性:等权小市值篮子对冲击成本与停牌的敏感性只在成本网格内近似,未建模个股层面流动性枯竭。',
    'IC 衰减:h1 口径 IC 最强,持有期拉长到 h5 明显衰减;周频调仓假设信号在周末前可执行。',
    'paper-track 当前为 BACKFILL(历史回填),非真实前向周期;回填周数不等于前向证据。',
    '幸存者偏差:宇宙为静态文件合集,未严格做到逐期 PIT 退市处理。',
    '干跑为已知历史上的执行现实检验,非前向/实盘;L2_DRY_RUN_ONLY。',
  ],
};

const fmtResidual = (v: number) => (Math.abs(v) < 1e-6 ? '≈0' : v.toExponential(1));

/** 小型指标卡(干跑 / paper-track 面板内的网格项)。 */
interface MiniStat { k: React.ReactNode; v: string; tone?: Tone; mono?: boolean }
const MINI_TONE: Record<Tone, string> = {
  pos: 'text-pos', neg: 'text-neg', warn: 'text-warn', info: 'text-info',
  accent: 'text-accent', special: 'text-special', muted: 'text-muted',
};
function MiniStatGrid({ items }: { items: ReadonlyArray<MiniStat> }) {
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      {items.map(it => (
        <div key={String(it.k)} className="rounded-md border border-line bg-inset/40 px-3 py-2">
          <div className="text-2xs text-muted">{it.k}</div>
          <div className={`mt-1 truncate text-sm mono tnum ${it.tone ? MINI_TONE[it.tone] : 'text-fg'}`}>{it.v}</div>
        </div>
      ))}
    </div>
  );
}

const RUN_ACTIONS = [
  { kind: 'ashare_alpha', zh: '重跑 A股Alpha 研究', en: 'Re-run A-share alpha research' },
  { kind: 'ashare_paper_track', zh: '推进 paper-track', en: 'Advance paper-track' },
] as const;

export default function AShareAlpha() {
  const [refreshTick, setRefreshTick] = useState(0);
  const [runnerKinds, setRunnerKinds] = useState<string[] | null>(null);
  const [runnerOnline, setRunnerOnline] = useState<boolean | null>(null);
  const [activeKind, setActiveKind] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<ResearchRunDetail | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  // 控制台快照定时刷新(仅后端模式)
  useEffect(() => {
    if (!isResearchConsoleApiEnabled()) return undefined;
    const timer = window.setInterval(() => setRefreshTick(v => v + 1), 10_000);
    return () => window.clearInterval(timer);
  }, []);

  // runner 健康检查(参照 DryRunPipeline:未配置即离线,配置后每 10s 轮询)
  useEffect(() => {
    if (!runnerEnabled()) {
      setRunnerOnline(false);
      setRunnerKinds(null);
      return undefined;
    }
    let disposed = false;
    const check = async () => {
      try {
        const health = await fetchRunnerHealth();
        if (!disposed) {
          setRunnerOnline(health.live_disallowed === true);
          setRunnerKinds(health.kinds);
        }
      } catch {
        if (!disposed) { setRunnerOnline(false); setRunnerKinds(null); }
      }
    };
    void check();
    const timer = window.setInterval(() => { void check(); }, 10_000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, []);

  // 触发的 run 轮询直到终态,然后刷新快照
  useEffect(() => {
    if (!runId || !isResearchConsoleApiEnabled()) return undefined;
    let disposed = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetchResearchRun(runId);
        if (disposed) return;
        setRunDetail(response.run);
        if (response.run && (response.run.status === 'queued' || response.run.status === 'running')) {
          timer = window.setTimeout(() => { void poll(); }, 2_000);
        } else {
          setActiveKind(null);
          setRefreshTick(v => v + 1);
        }
      } catch (err) {
        if (!disposed) { setRunError(err instanceof Error ? err.message : String(err)); setActiveKind(null); }
      }
    };
    void poll();
    return () => { disposed = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, [runId]);

  const { payload, apiState, error, usingBackend, snapshotState } = useConsoleSnapshot<AShareAlphaPayload>(
    s => fetchAShareAlphaSnapshot(s), [refreshTick]);
  const p: AShareAlphaPayload = usingBackend ? payload! : mockPayload;

  const { basket, combo, dry_run: dr, paper_track: pt, data_freshness: fresh } = p;
  const holdings = useMemo(() => p.holdings_latest.slice(0, 20), [p.holdings_latest]);
  const maxHoldingW = Math.max(...holdings.map(h => h.weight), 1e-9);
  const weightEntries = useMemo(
    () => Object.entries(combo.weights).sort((a, b) => b[1] - a[1]),
    [combo.weights],
  );
  const maxW = Math.max(...weightEntries.map(([, w]) => w), 1e-9);

  const runnerReason = !runnerEnabled()
    ? bi('未配置 VITE_RUNNER_URL', 'VITE_RUNNER_URL not configured')
    : runnerOnline !== true
      ? bi('runner 离线', 'runner offline')
      : !usingBackend
        ? bi('后端未连接', 'backend not connected')
        : !getRunnerToken()
          ? bi('缺少本标签页 Runner Token', 'runner token missing in this tab')
          : null;

  const canRun = (kind: string) =>
    runnerReason === null && activeKind === null && (runnerKinds?.includes(kind) ?? false);

  const runAction = async (kind: string) => {
    setRunError(null);
    setRunDetail(null);
    setRunId(null);
    setActiveKind(kind);
    try {
      const result = await triggerRun(kind, {});
      setRunId(result.run_id);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : String(err));
      setActiveKind(null);
    }
  };

  const factorCols: Column<AShareAlphaPayload['factors'][number]>[] = [
    { key: 'name', header: bi('因子', 'Factor'), mono: true,
      render: r => (r.kept ? <span className="text-accent font-semibold">{r.name}</span> : <span className="text-faint">{r.name}</span>) },
    { key: 'train_ic', header: bi('train IC', 'train IC'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.train_ic,
      render: r => <span className={r.train_ic >= 0 ? 'text-pos' : 'text-neg'}>{fixed(r.train_ic, 3)}</span> },
    { key: 'train_t', header: bi('train t', 'train t'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.train_t,
      render: r => fixed(r.train_t, 2) },
    { key: 'test_ic', header: bi('test IC', 'test IC'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.test_ic,
      render: r => <span className={r.test_ic >= 0 ? 'text-pos' : 'text-neg'}>{fixed(r.test_ic, 3)}</span> },
    { key: 'test_t', header: bi('test t', 'test t'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.test_t,
      render: r => <span className={r.test_t >= 2 ? 'text-pos' : 'text-warn'}>{fixed(r.test_t, 2)}</span> },
    { key: 'kept', header: bi('去冗余', 'De-redundant'),
      render: r => (r.kept
        ? <Chip label={bi('保留', 'KEPT')} tone="pos" />
        : <Chip label={bi('已丢弃 · 去冗余', 'DROPPED')} tone="warn" />) },
  ];

  const variantCols: Column<AShareAlphaPayload['variants'][number]>[] = [
    { key: 'name', header: bi('变体', 'Variant'), mono: true,
      render: r => (
        <span className={r.is_champion ? 'text-accent font-semibold' : undefined}>
          {r.name}{r.is_champion && <> <Chip label={bi('冠军', 'CHAMPION')} tone="accent" className="ml-1" /></>}
        </span>
      ) },
    { key: 'train_sharpe', header: bi('train Sharpe', 'train Sharpe'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.train_sharpe,
      render: r => signed(r.train_sharpe, 2) },
    { key: 'test_return', header: bi('test 收益', 'test return'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.test_return,
      render: r => <span className={r.test_return >= 0 ? 'text-pos' : 'text-neg'}>{pct(r.test_return, 1)}</span> },
    { key: 'test_sharpe', header: bi('test Sharpe', 'test Sharpe'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.test_sharpe,
      render: r => <span className={r.is_champion ? 'text-accent font-semibold' : undefined}>{signed(r.test_sharpe, 2)}</span> },
    { key: 'test_mdd', header: bi('test 回撤', 'test MDD'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.test_mdd,
      render: r => <span className="text-warn">{pct(r.test_mdd, 1)}</span> },
    { key: 'turnover', header: bi('换手', 'Turnover'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.turnover,
      render: r => pct(r.turnover, 1) },
  ];

  const holdingCols: Column<AShareAlphaPayload['holdings_latest'][number]>[] = [
    { key: 'code', header: bi('代码', 'Code'), mono: true, render: r => r.code },
    { key: 'weight', header: bi('权重', 'Weight'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.weight,
      render: r => pct(r.weight, 2) },
    { key: 'bar', header: bi('占比', 'Share'), width: '40%',
      render: r => <ProgressBar value={r.weight} max={maxHoldingW} tone="accent" height={6} /> },
  ];

  return (
    <PageShell>
      <PageHeader title="A-Share Alpha · Micro-Cap Multi-Factor" titleZh="A股Alpha · 微盘多因子"
        subtitle={bi('微盘宇宙多因子研究:截面 IC 初筛 → 去冗余 → 复合冠军 → 篮子 vs 基准 → 执行现实(干跑)→ paper-track,全程只读、诚实局限默认展开',
          'Micro-cap multi-factor research: cross-sectional IC screen → de-redundancy → combo champion → basket vs benchmark → execution reality (dry-run) → paper-track. Read-only, honest limitations expanded')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.provenance === 'REAL_ASHARE_ALPHA_SNAPSHOT'}
        source={usingBackend ? p.provenance : 'FRONTEND_ASHARE_ALPHA_MOCK'}
        error={error} dataStatus={snapshotState}
        extraChips={!usingBackend ? <Chip label={bi('演示数据', 'DEMO DATA')} tone="warn" /> : undefined} />

      <LoadingVeil active={apiState === 'loading'}>
        {/* 数据新鲜度条 */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-line bg-inset/40 px-3.5 py-2 text-2xs">
          <span className="text-muted">{bi('数据新鲜度', 'Data freshness')}</span>
          <span className="mono tnum text-ink">{bi('股票', 'stocks')} <strong>{num(fresh.stocks)}</strong></span>
          <span className="mono tnum text-ink">{bi('K线', 'bars')} <strong>{num(fresh.bars)}</strong></span>
          <span className="mono tnum text-ink">{bi('最后交易日', 'last date')} <strong>{String(fresh.last_date)}</strong></span>
          <span className="mono tnum text-ink">wind <strong>{num(fresh.sources.wind)}</strong> · ifind <strong>{num(fresh.sources.ifind)}</strong></span>
          <span className="mono tnum text-faint">{bi('宇宙', 'universe')} {num(p.universe.used)}/{num(p.universe.file_count)}</span>
          <span className="mx-1 h-3 w-px bg-line-strong" />
          <span className="text-muted">paper-track</span>
          <Chip label={pt.kind === 'FORWARD' ? 'FORWARD' : 'BACKFILL'} tone={pt.kind === 'FORWARD' ? 'pos' : 'warn'} />
          <span className="ml-auto mono text-faint">{bi('生成于', 'generated')} {p.generated_at}</span>
        </div>

        {/* 顶层 KPI */}
        <KpiStrip cols={6}>
          <StatTile label={bi('复合 h1 test IC', 'Combo h1 test IC')} value={fixed(combo.h1_test_ic, 3)}
            tone={combo.h1_test_ic >= 0.03 ? 'pos' : 'warn'} accent
            sub={bi(`h5 ${fixed(combo.h5_test_ic, 3)} · 命中率 ${pct(combo.hit_rate, 0)}`, `h5 ${fixed(combo.h5_test_ic, 3)} · hit ${pct(combo.hit_rate, 0)}`)} />
          <StatTile label={bi('h1 t-stat', 'h1 t-stat')} value={fixed(combo.h1_test_t, 2)}
            tone={combo.h1_test_t >= 2 ? 'pos' : 'warn'}
            sub={bi(`h5 t ${fixed(combo.h5_test_t, 2)}`, `h5 t ${fixed(combo.h5_test_t, 2)}`)} />
          <StatTile label={bi('篮子 test Sharpe', 'Basket test Sharpe')} value={signed(basket.test.sharpe, 2)}
            tone="info" sub={bi(`收益 ${pct(basket.test.total_return, 1)} · 回撤 ${pct(basket.test.max_dd, 1)}`, `ret ${pct(basket.test.total_return, 1)} · mdd ${pct(basket.test.max_dd, 1)}`)} />
          <StatTile label={bi('基准 Sharpe', 'Benchmark Sharpe')} value={signed(basket.bench_test.sharpe, 2)}
            tone="muted" sub={bi(`基准收益 ${pct(basket.bench_test.total_return, 1)}`, `bench ret ${pct(basket.bench_test.total_return, 1)}`)} />
          <StatTile label={bi('干跑 Sharpe', 'Dry-run Sharpe')} value={signed(dr.sharpe, 2)}
            tone={deltaTone(dr.sharpe)} sub={bi('执行现实层', 'execution reality')} />
          <StatTile label={bi('paper-track 周数', 'Paper-track weeks')} value={String(pt.n_weeks)}
            tone={pt.kind === 'FORWARD' ? 'pos' : 'warn'}
            sub={bi(`周 IC 均值 ${fixed(pt.weekly_ic_mean, 3)} · t ${fixed(pt.weekly_ic_t, 2)}`, `weekly IC ${fixed(pt.weekly_ic_mean, 3)} · t ${fixed(pt.weekly_ic_t, 2)}`)} />
        </KpiStrip>

        {/* 篮子 vs 基准 */}
        <Panel eyebrow={bi('净值对比', 'BASKET VS BENCHMARK')} className="terminal-panel"
          title={bi('篮子 vs 基准(test 窗口)', 'Basket vs benchmark (test window)')}
          right={<span className="text-2xs text-faint mono">{bi(`n=${basket.n} · ${money(basket.aum_cny)} · 成本 ${num(basket.cost_bps)}bps · 换手 ${pct(basket.test.avg_turnover, 1)}`, `n=${basket.n} · ${money(basket.aum_cny)} · cost ${num(basket.cost_bps)}bps · turnover ${pct(basket.test.avg_turnover, 1)}`)}</span>}>
          <EquityCurveChart height={190}
            series={[
              { label: bi(`篮子(终值 ${fixed(basket.equity_test[basket.equity_test.length - 1], 2)})`, `basket (final ${fixed(basket.equity_test[basket.equity_test.length - 1], 2)})`),
                points: basket.equity_test.map((equity, i) => ({ t: i, equity })), tone: 'accent' },
              { label: bi(`基准(终值 ${fixed(basket.bench_equity_test[basket.bench_equity_test.length - 1], 2)})`, `benchmark (final ${fixed(basket.bench_equity_test[basket.bench_equity_test.length - 1], 2)})`),
                points: basket.bench_equity_test.map((equity, i) => ({ t: i, equity })), tone: 'muted', dashed: true },
            ]}
            yFormat={v => v.toFixed(2)} />
          <div className="mt-1 text-2xs text-muted">
            {bi(`test 窗口:篮子 ${pct(basket.test.total_return, 1)} / SR ${signed(basket.test.sharpe, 2)} / 回撤 ${pct(basket.test.max_dd, 1)},基准 ${pct(basket.bench_test.total_return, 1)} / SR ${signed(basket.bench_test.sharpe, 2)} / 回撤 ${pct(basket.bench_test.max_dd, 1)}。历史回测,非未来收益承诺。`,
              `test window: basket ${pct(basket.test.total_return, 1)} / SR ${signed(basket.test.sharpe, 2)} / mdd ${pct(basket.test.max_dd, 1)} vs benchmark ${pct(basket.bench_test.total_return, 1)} / SR ${signed(basket.bench_test.sharpe, 2)} / mdd ${pct(basket.bench_test.max_dd, 1)}. Historical backtest, not a promise of future returns.`)}
          </div>
        </Panel>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {/* 因子 IC 面板 */}
          <Panel eyebrow={bi('因子截面 IC', 'FACTOR IC PANEL')}
            title={bi('因子 IC 面板(train / test + 去冗余)', 'Factor IC panel (train / test + de-redundancy)')}>
            <DataTable rows={p.factors} columns={factorCols} getKey={r => r.name} density="compact" />
            <div className="mt-2 text-2xs text-muted">
              {bi('accent = 复合保留因子;warn = 被去冗余丢弃(高相关或 test t 不足)。初筛诊断,非晋级门禁。',
                'accent = kept in combo; warn = dropped by de-redundancy (high correlation or weak test t). Screening diagnostic, not a promotion gate.')}
            </div>
          </Panel>

          {/* 复合与变体 */}
          <Panel eyebrow={bi('复合与变体', 'COMBO & VARIANTS')}
            title={bi(`复合冠军 ${combo.champion}`, `Combo champion ${combo.champion}`)}>
            <div className="mb-4 space-y-1.5">
              {weightEntries.map(([name, w]) => (
                <div key={name} className="grid grid-cols-[110px_1fr_52px] items-center gap-2">
                  <span className="mono text-2xs text-muted">{name}</span>
                  <ProgressBar value={w} max={maxW} tone="accent" height={7} />
                  <span className="mono tnum text-right text-2xs text-ink">{pct(w, 1)}</span>
                </div>
              ))}
            </div>
            <DataTable rows={p.variants} columns={variantCols} getKey={r => r.name} density="compact" />
          </Panel>
        </div>

        {/* 成本与换手敏感性 */}
        <Panel eyebrow={bi('敏感性', 'SENSITIVITY')}
          title={bi('成本与换手敏感性(test Sharpe)', 'Cost & turnover sensitivity (test Sharpe)')}>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <div className="mb-1 text-2xs text-muted">{bi('单边成本(bps)→ test Sharpe', 'one-way cost (bps) → test Sharpe')}</div>
              <div className="flex items-end gap-1.5">
                {p.cost_grid.map(c => {
                  const maxS = Math.max(...p.cost_grid.map(x => x.sharpe), 1e-9);
                  return (
                    <div key={c.cost_bps} className="flex flex-1 flex-col items-center gap-1">
                      <span className="mono tnum text-2xs text-muted">{fixed(c.sharpe, 2)}</span>
                      <div className="w-full rounded-sm bg-info/70" style={{ height: `${Math.max(4, (c.sharpe / maxS) * 90)}px` }} />
                      <span className="mono text-2xs text-faint">{c.cost_bps}</span>
                    </div>
                  );
                })}
              </div>
              <div className="mt-1 text-2xs text-faint">
                {bi(`当前假设 ${num(basket.cost_bps)}bps;80bps 下收益 ${pct(p.cost_grid[p.cost_grid.length - 1]?.total_return, 1)}`,
                  `current assumption ${num(basket.cost_bps)}bps; at 80bps return ${pct(p.cost_grid[p.cost_grid.length - 1]?.total_return, 1)}`)}
              </div>
            </div>
            <div>
              <div className="mb-1 text-2xs text-muted">{bi('周换手上限 → test Sharpe', 'weekly turnover cap → test Sharpe')}</div>
              <div className="flex items-end gap-1.5">
                {p.turnover_caps.map(t => {
                  const maxS = Math.max(...p.turnover_caps.map(x => x.test_sharpe), 1e-9);
                  return (
                    <div key={t.cap} className="flex flex-1 flex-col items-center gap-1">
                      <span className="mono tnum text-2xs text-muted">{fixed(t.test_sharpe, 2)}</span>
                      <div className="w-full rounded-sm bg-accent/70" style={{ height: `${Math.max(4, (t.test_sharpe / maxS) * 90)}px` }} />
                      <span className="mono text-2xs text-faint">{pct(t.cap, 0)}</span>
                    </div>
                  );
                })}
              </div>
              <div className="mt-1 text-2xs text-faint">
                {bi('换手上限压缩收益但也压缩成本敏感性;冠军取 30%。', 'Tighter caps cut both return and cost sensitivity; champion uses 30%.')}
              </div>
            </div>
          </div>
        </Panel>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {/* 执行现实层(干跑) */}
          <Panel eyebrow={bi('执行现实层', 'EXECUTION REALITY')}
            title={bi('干跑(门 fail-closed · 纸面成交 · 对账恒等式)', 'Dry-run (fail-closed gate · paper fills · reconciled identity)')}
            right={<Link to="/research/dry-run" className="text-2xs text-accent hover:underline">{bi('→ 干跑流水线页', '→ Dry-run pipeline')}</Link>}>
            <MiniStatGrid items={[
              { k: 'run_id', v: shortHash(dr.run_id, 10), mono: true },
              { k: bi('总收益', 'total return'), v: pct(dr.total_return, 1), tone: deltaTone(dr.total_return) },
              { k: bi('夏普', 'Sharpe'), v: signed(dr.sharpe, 2), tone: deltaTone(dr.sharpe) },
              { k: bi('最大回撤', 'max drawdown'), v: pct(dr.max_drawdown, 1), tone: 'warn' },
              { k: bi('门批准 / 拒绝', 'approved / rejected'), v: `${num(dr.n_approved)} / ${num(dr.n_rejected)}`, tone: dr.n_rejected > 0 ? 'neg' : 'pos' },
              { k: bi('对账残差', 'residual'), v: fmtResidual(dr.residual), tone: 'pos' },
            ]} />
            <div className="mt-2 text-2xs text-muted">
              {bi('已知历史上的执行现实检验(整手 + 门 + 成本),残差≈0 恒等式成立;非前向/实盘。',
                'Execution-reality check on known history (integer lots + gate + costs), residual ≈0 identity holds; not forward/live.')}
            </div>
          </Panel>

          {/* paper-track */}
          <Panel eyebrow="PAPER TRACK" title={bi('paper-track 证据(回填 / 前向)', 'Paper-track evidence (backfill / forward)')}
            right={<Chip label={pt.kind === 'FORWARD' ? 'FORWARD' : 'BACKFILL'} tone={pt.kind === 'FORWARD' ? 'pos' : 'warn'} />}>
            <MiniStatGrid items={[
              { k: 'alpha_id', v: pt.alpha_id, mono: true },
              { k: bi('周数', 'weeks'), v: String(pt.n_weeks) },
              { k: bi('累计收益', 'cum return'), v: pct(pt.cum_return, 1), tone: deltaTone(pt.cum_return) },
              { k: bi('夏普', 'Sharpe'), v: signed(pt.sharpe, 2), tone: deltaTone(pt.sharpe) },
              { k: bi('周 IC 均值', 'weekly IC mean'), v: fixed(pt.weekly_ic_mean, 3), tone: deltaTone(pt.weekly_ic_mean) },
              { k: bi('周 IC t-stat', 'weekly IC t'), v: fixed(pt.weekly_ic_t, 2), tone: pt.weekly_ic_t >= 2 ? 'pos' : 'warn' },
            ]} />
            <div className="mt-2 text-2xs text-muted">
              {pt.kind === 'FORWARD'
                ? bi('FORWARD:真实前向周期累计,证据等级高于回填。', 'FORWARD: real forward periods — stronger evidence than backfill.')
                : bi('BACKFILL:历史回填,不等于真实前向证据;前向周期需逐周自然累积,系统不会用历史伪造。', 'BACKFILL: historical backfill, not real forward evidence; forward periods accrue naturally week by week and are never faked from history.')}
            </div>
          </Panel>
        </div>

        {/* 最新持仓 top-20 */}
        <Panel eyebrow={bi('最新持仓', 'LATEST HOLDINGS')}
          title={bi('最新持仓 top-20(复合冠军篮子)', 'Latest holdings top-20 (champion basket)')}>
          <DataTable rows={holdings} columns={holdingCols} getKey={r => r.code} density="compact"
            searchable={r => r.code} searchPlaceholder={bi('按代码搜索…', 'search by code…')} />
        </Panel>

        {/* 诚实局限 —— 默认展开(项目的诚实姿态) */}
        <Panel tone="warn" eyebrow={bi('诚实局限', 'HONEST LIMITATIONS')}
          title={bi('诚实局限(默认展开 —— 这些是结论的边界,不是脚注)', 'Honest limitations (expanded by default — these bound every conclusion above)')}>
          <ul className="list-disc space-y-1.5 pl-5 text-[13px] leading-relaxed text-muted">
            {p.honest_limitations.map((lim, i) => <li key={i}>{lim}</li>)}
          </ul>
        </Panel>

        {/* Runner 操作区 */}
        <Panel eyebrow={bi('研究操作', 'RESEARCH OPS')}
          title={bi('重跑研究 / 推进 paper-track(受守卫 runner)', 'Re-run research / advance paper-track (guarded runner)')}
          right={<Chip label={runnerOnline === true ? bi('RUNNER 在线', 'RUNNER ONLINE') : bi('RUNNER 离线', 'RUNNER OFFLINE')} tone={runnerOnline === true ? 'pos' : 'muted'} />}>
          <div className="flex flex-wrap items-center gap-2">
            {RUN_ACTIONS.map(a => (
              <button key={a.kind} onClick={() => { void runAction(a.kind); }} disabled={!canRun(a.kind)}
                className={`rounded-md border px-3 py-1.5 text-xs transition ${canRun(a.kind)
                  ? 'border-accent/50 text-accent hover:bg-accent/10' : 'cursor-not-allowed border-line text-faint'}`}>
                {activeKind === a.kind ? bi('执行中…', 'running…') : bi(a.zh, a.en)}
              </button>
            ))}
            {runnerReason !== null && (
              <span className="text-2xs text-muted">{bi('不可用原因', 'unavailable')}: {runnerReason}</span>
            )}
            {runnerReason === null && activeKind === null && !runId && (
              <span className="text-2xs text-muted">
                {bi('runner 白名单 kind;不触碰下单/券商/凭证路径', 'whitelisted runner kinds; no order/broker/credential path')}
              </span>
            )}
          </div>
          {runDetail && (
            <div className="mt-3 rounded-md border border-line bg-inset/30 px-3 py-2 text-2xs">
              <div className="flex flex-wrap items-center gap-2">
                <Chip label={runDetail.status.toUpperCase()} tone={runDetail.status === 'done' ? 'pos' : runDetail.status === 'error' ? 'neg' : 'info'} />
                <span className="mono text-faint">{runDetail.run_id}</span>
              </div>
              {runDetail.status === 'error' && <div className="mt-1 text-neg">{runDetail.error ?? 'run failed'}</div>}
            </div>
          )}
          {runError && <div className="mt-2 text-2xs text-neg">{runError}</div>}
          <div className="mt-2 text-2xs text-faint">
            {bi(`kind: ${RUN_ACTIONS.map(a => a.kind).join(' / ')}(后端并行实现中,前端按契约调用)· X-Runner-Token 仅存 sessionStorage`,
              `kinds: ${RUN_ACTIONS.map(a => a.kind).join(' / ')} (backend landing in parallel; the frontend calls the contract) · X-Runner-Token lives only in sessionStorage`)}
          </div>
        </Panel>

        <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
          {bi('只读 · 研究/干跑(L2)· 历史回测非未来收益承诺 · 绝不真发券商 · 晋级实盘人工专属',
            'Read-only · research / dry-run (L2) · historical backtest is not a promise of future returns · never sent to a broker · promotion to live is human-only')}
        </div>
      </LoadingVeil>
    </PageShell>
  );
}
