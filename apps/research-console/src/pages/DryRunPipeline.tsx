import React, { useEffect, useMemo, useState } from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable, StatusChip, PillToggle, LoadingVeil, Callout, type Column,
} from '@/ui/primitives';
import { signed, stamp, type Tone } from '@/ui/format';
import { EquityCurveChart, type EquitySeries } from '@/ui/workbench';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchDryRunSnapshot, fetchResearchRun, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { fetchRunnerHealth, getRunnerToken, runnerEnabled, triggerRun } from '@/lib/api/research-runner-api';
import type { DryRunPipelinePayload, DryRunRunRow, PaperOrderPreviewAlpha, PaperRealtimeAlpha, PaperProfilePayload, PaperRealtimeHistoryPoint, LiveTradingForwardAlpha, ResearchRunDetail } from '@/lib/types/research';

/**
 * Dry-Run Pipeline — the end-to-end run, surfaced read-only. This is the PROOF that the pre-trade risk gate is
 * a RUNTIME chokepoint (not tested dead code): every order intent is screened by the gate (fail-closed,
 * kill-switch latched -> all rejected) before a paper fill is ever simulated. Nothing is sent to a broker;
 * paper fills reconcile to an exact average-cost identity (residual ~0); every step is hash-chain audited.
 * Reads the append-only DryRunStore. L2_DRY_RUN_ONLY — live = swap the adapter + human approval (non-goal).
 */

const mockPayload: DryRunPipelinePayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_RUN',
  n_runs: 0, latest: {}, runs: [],
  safety_note: 'No dry-run yet — run tools/dry_run_carry.py off-gate to produce a DryRunRun.',
};

const VERDICT_ZH: Record<string, string> = {
  DRY_RUN_OK: '干跑跑通 · 门已运行时强制', NO_RUN: '尚无干跑',
};

const fmtUsd = (v?: number) => (v == null ? '—' : `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`);
const fmtResidual = (v?: number) => (v == null ? '—' : Math.abs(v) < 1e-6 ? '≈0' : v.toExponential(1));

// Paper PnL history grouped per alpha — rendered by the shared EquityCurveChart (workbench kit).
const PAPER_TONES: Tone[] = ['accent', 'info', 'warn', 'special', 'pos'];
function paperSeries(history: PaperRealtimeHistoryPoint[]): EquitySeries[] {
  const byAlpha = new Map<string, { t: number; equity: number }[]>();
  for (const p of history) {
    if (typeof p.net_pnl !== 'number' || !Number.isFinite(p.net_pnl)) continue;
    const rows = byAlpha.get(p.alpha_id) ?? [];
    rows.push({ t: p.observed_at, equity: p.net_pnl });
    byAlpha.set(p.alpha_id, rows);
  }
  return Array.from(byAlpha.entries()).map(([alpha, points], i) => ({
    label: alpha,
    points: points.sort((a, b) => a.t - b.t),
    tone: PAPER_TONES[i % PAPER_TONES.length],
  }));
}

export default function DryRunPipeline() {
  const [refreshTick, setRefreshTick] = useState(0);
  const [runnerOnline, setRunnerOnline] = useState<boolean | null>(null);
  const [cycleRunId, setCycleRunId] = useState<string | null>(null);
  const [cycleRun, setCycleRun] = useState<ResearchRunDetail | null>(null);
  const [cycleProfile, setCycleProfile] = useState<'default' | 'small_10k'>('default');
  const [cycleError, setCycleError] = useState<string | null>(null);
  const [cycleBusy, setCycleBusy] = useState(false);
  useEffect(() => {
    if (!isResearchConsoleApiEnabled()) return undefined;
    const timer = window.setInterval(() => setRefreshTick(value => value + 1), 10_000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!runnerEnabled()) {
      setRunnerOnline(false);
      return undefined;
    }
    let disposed = false;
    const check = async () => {
      try {
        const health = await fetchRunnerHealth();
        if (!disposed) setRunnerOnline(health.live_disallowed === true && health.kinds.includes('paper_cycle'));
      } catch {
        if (!disposed) setRunnerOnline(false);
      }
    };
    void check();
    const timer = window.setInterval(() => { void check(); }, 10_000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!cycleRunId || !isResearchConsoleApiEnabled()) return undefined;
    let disposed = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetchResearchRun(cycleRunId);
        if (disposed) return;
        setCycleRun(response.run);
        if (response.run && (response.run.status === 'queued' || response.run.status === 'running')) {
          timer = window.setTimeout(() => { void poll(); }, 2_000);
        } else {
          setRefreshTick(value => value + 1);
        }
      } catch (err) {
        if (!disposed) setCycleError(err instanceof Error ? err.message : String(err));
      }
    };
    void poll();
    return () => { disposed = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, [cycleRunId]);
  const { payload, apiState, error, usingBackend, snapshotState } = useConsoleSnapshot<DryRunPipelinePayload>(
    s => fetchDryRunSnapshot(s), [refreshTick]);
  const p: DryRunPipelinePayload = usingBackend ? payload! : mockPayload;
  const [strategyFilter, setStrategyFilter] = useState('全部');
  const strategies = useMemo(() => ['全部', ...Array.from(new Set(p.runs.map(r => r.strategy_id)))], [p.runs]);
  const filteredRuns = useMemo(
    () => p.runs.filter(r => strategyFilter === '全部' || r.strategy_id === strategyFilter),
    [p.runs, strategyFilter],
  );
  const has = p.snapshot_present && p.runs.length > 0;
  const latest = (has ? (p.latest as DryRunRunRow) : null);
  const pnl = latest?.pnl ?? {};
  const nFills = latest?.detail?.n_fills ?? 0;
  const mp = latest?.detail?.mode === 'multi_period' ? latest.detail : null;  // multi-period rolling run
  type PaperAlphaRow = { alpha: string } & PaperOrderPreviewAlpha;
  const paperPreview = p.paper_order_preview;
  const paperOperator = p.paper_operator;
  const paperRealtime = p.paper_realtime;
  const paperShadow = p.paper_shadow;
  const paperHealth = p.paper_health;
  const forwardTrack = p.forward_track;
  const dataRefresh = p.data_refresh;
  const scheduler = p.scheduler_watchdog;
  const routeTca = p.route_tca_history;
  const paperProfiles = Object.values(p.paper_profiles ?? {}) as PaperProfilePayload[];
  const paperAlphaRows: PaperAlphaRow[] = Object.entries(paperPreview?.alphas ?? {}).map(([alpha, row]) => ({
    alpha,
    ...row,
  }));
  const observedAt = paperRealtime?.observed_at ?? paperHealth?.observed_at;
  const observedAgeMs = typeof observedAt === 'number'
    ? Math.max(0, Date.now() - observedAt)
    : (paperHealth?.age_ms ?? null);
  const maxPaperAgeMs = paperHealth?.max_age_ms ?? 5 * 60 * 1000;
  const detectorStatus = !paperHealth?.snapshot_present || !paperRealtime?.snapshot_present
    ? 'NO_SNAPSHOT'
    : paperHealth.verdict.paper_ready && observedAgeMs != null && observedAgeMs <= maxPaperAgeMs
      ? 'PAPER_HEALTHY'
      : observedAgeMs != null && observedAgeMs > maxPaperAgeMs
        ? 'MARKS_STALE'
        : 'PAPER_BLOCKED';
  const cycleActive = cycleBusy || cycleRun?.status === 'queued' || cycleRun?.status === 'running';
  const cycleCanExecute = usingBackend && runnerOnline === true && Boolean(getRunnerToken()) && !cycleActive;
  const shadowSchedulerChecks = (scheduler?.checks ?? []).filter(check =>
    check.task_name === 'QUANTOS_paper_shadow_auto'
    || check.task_name === 'QUANTOS_paper_shadow_realtime');
  const shadowSchedulerHealthy = shadowSchedulerChecks.length === 2
    && shadowSchedulerChecks.every(check => check.status === 'PASS');
  const backgroundCycleState = paperShadow?.verdict.shadow_paper_running && shadowSchedulerHealthy
    ? 'SCHEDULED_ACTIVE'
    : paperShadow?.snapshot_present
      ? 'SAFE_BLOCKED'
      : 'NO_SHADOW_SNAPSHOT';
  const executePaperCycle = async (small_10k: boolean) => {
    setCycleProfile(small_10k ? 'small_10k' : 'default');
    setCycleError(null);
    setCycleRun(null);
    setCycleRunId(null);
    setCycleBusy(true);
    try {
      const result = await triggerRun('paper_cycle', { small_10k });
      setCycleRunId(result.run_id);
      setRefreshTick(value => value + 1);
    } catch (err) {
      setCycleError(err instanceof Error ? err.message : String(err));
    } finally {
      setCycleBusy(false);
    }
  };

  // pipeline stages (with the latest run's live numbers) — the chokepoint story, left to right
  const stages = [
    { zh: '信号 / 权重', en: 'Signal / weights', val: latest ? `${latest.n_intents} ${bi('意图', 'intents')}` : '—' },
    { zh: '预交易门 (chokepoint)', en: 'Pre-trade gate', val: latest ? `✓${latest.n_approved} ✕${latest.n_rejected}` : '—', accent: true },
    { zh: '干跑成交 (不真发)', en: 'Dry-run fills', val: `${nFills} ${bi('纸面', 'paper')}` },
    { zh: '对账 PnL', en: 'Reconcile', val: fmtResidual(pnl.residual) + bi(' 残差', ' resid') },
    { zh: '哈希链审计', en: 'Audit chain', val: latest ? latest.audit_root.slice(0, 10) : '—' },
  ];

  const posRows = Object.entries(pnl.positions ?? {})
    .map(([inst, qty]) => ({ inst, qty }))
    .sort((a, b) => Math.abs(b.qty) - Math.abs(a.qty))
    .slice(0, 12);
  const posCols: Column<{ inst: string; qty: number }>[] = [
    { key: 'inst', header: bi('标的', 'Instrument'), mono: true, render: r => r.inst },
    { key: 'side', header: bi('方向', 'Side'),
      render: r => <span className={r.qty >= 0 ? 'text-pos' : 'text-neg'}>{r.qty >= 0 ? bi('多', 'LONG') : bi('空', 'SHORT')}</span> },
    { key: 'qty', header: bi('手数(纸面)', 'Qty (paper)'), align: 'right', mono: true,
      render: r => r.qty.toLocaleString('en-US') },
  ];

  const runCols: Column<DryRunRunRow>[] = [
    { key: 'time', header: bi('时间(UTC)', 'Time (UTC)'), mono: true, sortable: true, sortAccessor: r => r.timestamp, render: r => stamp(r.timestamp) },
    { key: 'strat', header: bi('策略', 'Strategy'), sortable: true, sortAccessor: r => r.strategy_id, render: r => r.strategy_id },
    { key: 'intents', header: bi('意图', 'Intents'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.n_intents, render: r => r.n_intents },
    { key: 'appr', header: bi('门批准', 'Approved'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.n_approved,
      render: r => <span className="text-pos">{r.n_approved}</span> },
    { key: 'rej', header: bi('门拒绝', 'Rejected'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.n_rejected,
      render: r => <span className={r.n_rejected ? 'text-neg' : 'text-faint'}>{r.n_rejected}</span> },
    { key: 'fills', header: bi('成交', 'Fills'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.detail?.n_fills ?? 0, render: r => r.detail?.n_fills ?? 0 },
    { key: 'pnl', header: bi('收益', 'Return'), align: 'right', mono: true,
      sortable: true, sortAccessor: r => r.pnl?.total_return ?? r.pnl?.total_pnl ?? 0,
      render: r => {
        // multi-period rows carry total_return (%), single-snapshot rows carry total_pnl ($)
        const mpRet = r.pnl?.total_return, usd = r.pnl?.total_pnl;
        const v = mpRet != null ? mpRet : usd;
        return <span className={(v ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>
          {mpRet != null ? `${(mpRet * 100).toFixed(1)}%` : fmtUsd(usd)}</span>;
      } },
    { key: 'resid', header: bi('对账残差', 'Residual'), align: 'right', mono: true, render: r => fmtResidual(r.pnl?.residual) },
    { key: 'audit', header: bi('审计根', 'Audit'), mono: true, render: r => <span className="text-2xs text-faint">{r.audit_root.slice(0, 10)}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Dry-Run Pipeline" titleZh="端到端干跑流水线"
        subtitle={bi('信号 → 预交易门(运行时 chokepoint,fail-closed)→ 纸面成交(绝不真发)→ 对账 → 哈希链审计',
          'Signal → pre-trade gate (runtime chokepoint, fail-closed) → paper fills (never sent) → reconcile → audit chain')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_DRY_RUN_OFFLINE_SNAPSHOT'}
        source={usingBackend ? (has ? 'REAL_DRY_RUN_OFFLINE_SNAPSHOT' : 'NO_DRY_RUN') : 'FRONTEND_DRY_RUN_MOCK'}
        error={error}
        noDataYet={usingBackend && !p.snapshot_present}
        dataStatus={snapshotState} />

      <LoadingVeil active={apiState === 'loading'}>
      <div className="flex items-center gap-3">
        <StatusChip value={p.verdict} />
        <span className="text-2xs text-muted">{bi(VERDICT_ZH[p.verdict] ?? p.verdict, p.verdict)}</span>
        {has && <span className="text-2xs text-faint">{bi(`共 ${p.n_runs} 次干跑`, `${p.n_runs} dry-runs`)}</span>}
      </div>

      <Callout tone="pos" summary={bi('这页证明预交易风控门是运行时 chokepoint,非测过的死代码',
        'This page proves the pre-trade gate is a runtime chokepoint, not tested dead code')}>
        {bi('这页是"预交易风控门是运行时 chokepoint(而非测过的死代码)"的证明:每一笔订单意图都必须先过门(fail-closed;kill-switch 拉下 → 全部拒绝),通过的才会进入纸面成交。绝不真发券商;纸面成交按平均成本对账到恒等式(残差≈0);每一步进哈希链审计。只读、零下单/券商/凭证路径,L2_DRY_RUN_ONLY。实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。',
            'This page is the PROOF the pre-trade gate is a RUNTIME chokepoint (not tested dead code): every order intent must pass the gate first (fail-closed; kill-switch latched → all rejected) before any paper fill. Nothing is sent to a broker; paper fills reconcile to an exact average-cost identity (residual ≈0); every step is hash-chain audited. Read-only, no broker/order path; L2_DRY_RUN_ONLY. Live = swap the adapter + human approval = non-goal.')}
      </Callout>

      <Panel eyebrow="PAPER CYCLE CONTROL" title="纸面周期自动检测与执行"
        right={<StatusChip value={detectorStatus} />}>
        <div className="grid gap-2 md:grid-cols-4">
          <div className="rounded-md border border-line bg-inset/40 px-3 py-2">
            <div className="text-2xs text-muted">数据检测 / Data detector</div>
            <div className={`mt-1 text-xs mono ${detectorStatus === 'PAPER_HEALTHY' ? 'text-pos' : 'text-warn'}`}>
              {detectorStatus}
            </div>
            <div className="mt-1 text-2xs text-faint">
              {observedAgeMs == null ? 'mark age unavailable' : `mark age ${Math.round(observedAgeMs / 1000)}s / limit ${Math.round(maxPaperAgeMs / 1000)}s`}
            </div>
          </div>
          <div className="rounded-md border border-line bg-inset/40 px-3 py-2">
            <div className="text-2xs text-muted">手动 Runner（可选） / Manual runner</div>
            <div className={`mt-1 text-xs mono ${runnerOnline ? 'text-pos' : 'text-faint'}`}>
              {runnerOnline ? 'ONLINE · PAPER_ONLY' : runnerEnabled() ? 'OFFLINE' : 'NOT_CONFIGURED'}
            </div>
            <div className="mt-1 text-2xs text-faint">
              {getRunnerToken() ? 'token present in this tab' : '后台自动调度不依赖 Runner Token'}
            </div>
          </div>
          <div className="rounded-md border border-line bg-inset/40 px-3 py-2">
            <div className="text-2xs text-muted">自动纸面周期 / Automated cycle</div>
            <div className={`mt-1 text-xs mono ${backgroundCycleState === 'SCHEDULED_ACTIVE' ? 'text-pos' : 'text-warn'}`}>
              {cycleRun?.status?.toUpperCase() ?? backgroundCycleState}
            </div>
            <div className="mt-1 text-2xs text-faint">
              {cycleRunId ? cycleRunId.slice(0, 16) : shadowSchedulerHealthy ? 'Windows scheduler · autonomous' : 'scheduler status unavailable'}
            </div>
          </div>
          <div className="rounded-md border border-line bg-inset/40 px-3 py-2">
            <div className="text-2xs text-muted">安全边界 / Safety</div>
            <div className="mt-1 text-xs mono text-pos">L2_DRY_RUN_ONLY</div>
            <div className="mt-1 text-2xs text-faint">live=false · broker=false · credentials=false</div>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button onClick={() => { void executePaperCycle(false); }} disabled={!cycleCanExecute}
            className={`rounded-md border px-3 py-1.5 text-xs transition ${cycleCanExecute
              ? 'border-accent/50 text-accent hover:bg-accent/10' : 'cursor-not-allowed border-line text-faint'}`}>
            {cycleActive && cycleProfile === 'default' ? '执行中…' : '执行默认纸面周期'}
          </button>
          <button onClick={() => { void executePaperCycle(true); }} disabled={!cycleCanExecute}
            className={`rounded-md border px-3 py-1.5 text-xs transition ${cycleCanExecute
              ? 'border-info/50 text-info hover:bg-info/10' : 'cursor-not-allowed border-line text-faint'}`}>
            {cycleActive && cycleProfile === 'small_10k' ? '执行中…' : '执行 10K 隔离周期'}
          </button>
          <span className="text-2xs text-muted">后台影子纸面已由调度器自动运行；按钮仅用于可选的人工补跑。</span>
        </div>
        {cycleRun && (
          <div className="mt-3 rounded-md border border-line bg-inset/30 px-3 py-2 text-2xs">
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip value={cycleRun.status.toUpperCase()} />
              <StatusChip value={cycleRun.verdict || 'PAPER_CYCLE_PENDING'} />
              <span className="text-faint mono">{cycleRun.run_id}</span>
            </div>
            {cycleRun.status === 'done' && (
              <div className="mt-1 text-muted">
                {cycleRun.verdict === 'PAPER_CYCLE_OK' ? '三 Alpha 周期完成，健康检查通过。' : '周期执行完成，但健康检查未通过，请查看阻塞原因。'}
                {cycleRun.caveats?.length ? ` · ${cycleRun.caveats.slice(-3).join(', ')}` : ''}
              </div>
            )}
            {cycleRun.status === 'error' && <div className="mt-1 text-neg">{cycleRun.error ?? 'paper cycle failed'}</div>}
          </div>
        )}
        {cycleError && <div className="mt-2 text-2xs text-neg">{cycleError}</div>}
        <div className="mt-2 text-2xs text-muted">
          页面每 10 秒刷新真实快照；后台自动调仓不需要浏览器保持打开。手动入口仅允许固定的公共行情纸面驱动器，不读取私钥、不连接券商、不产生真实订单。
        </div>
      </Panel>

      {dataRefresh && (
        <Panel eyebrow="REAL-TIME DATA + GATES" title="实时数据同步与全流程门控">
          <div className="grid gap-2 md:grid-cols-4">
            {[
              ['刷新结果 / Refresh', dataRefresh.outcome ?? 'NO_SNAPSHOT', dataRefresh.outcome === 'PASS'],
              ['数据血缘 / Lineage', dataRefresh.raw_lineage?.status ?? 'UNKNOWN', dataRefresh.raw_lineage?.status === 'PASS'],
              ['失败源 / Failed', `${dataRefresh.failed_total ?? 0} / ${dataRefresh.failed_ticker_total ?? 0}`, (dataRefresh.failed_total ?? 0) === 0],
              ['链 / History chain', dataRefresh.history?.chain_valid === true ? 'PASS' : 'BLOCKED', dataRefresh.history?.chain_valid === true],
            ].map(([label, value, ok]) => (
              <div key={String(label)} className="rounded-md border border-line bg-inset/40 px-3 py-2">
                <div className="text-2xs text-muted">{label}</div>
                <div className={`mt-1 text-xs mono ${ok ? 'text-pos' : 'text-warn'}`}>{value}</div>
              </div>
            ))}
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {(dataRefresh.sources ?? []).map(source => {
              const freshness = dataRefresh.staleness_after?.[source.source];
              const ok = source.outcome === 'PASS';
              return (
                <div key={source.source} className="rounded-md border border-line bg-inset/40 px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs mono text-fg">{source.source}</span>
                    <StatusChip value={ok ? 'PASS' : (source.outcome ?? 'BLOCKED')} />
                  </div>
                  <div className="mt-1 text-2xs text-muted">
                    {freshness?.worst_days == null ? 'freshness unavailable' : `worst age ${freshness.worst_days}d / stale ${freshness.n_stale ?? 0}/${freshness.n_total ?? 0}`}
                    {' · '}{source.failed_count ?? 0} failed
                  </div>
                  {(source.failed_tickers?.length ?? 0) > 0 && (
                    <div className="mt-1 text-2xs text-warn">{source.failed_tickers?.slice(0, 8).join(', ')}{(source.failed_tickers?.length ?? 0) > 8 ? ' …' : ''}</div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-3">
            <div className="rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs">
              <div className="text-muted">前向证据 / Forward clock</div>
              <div className="mt-1 mono text-fg">{forwardTrack?.alphas?.map(row => `${row.alpha_id}:${row.n_forward_periods ?? 0}/${forwardTrack.evidence_gate_weeks ?? 8}`).join(' · ') ?? '—'}</div>
            </div>
            <div className="rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs">
              <div className="text-muted">纸面操作员 / Operator</div>
              <div className="mt-1 mono text-fg">{paperRealtime?.operator_mode ?? (paperOperator?.verdict.operator_completed ? 'PAPER_OPERATOR_READY' : 'PAPER_OPERATOR_BLOCKED')}</div>
              <div className="mt-1 text-faint">new paper orders: {paperRealtime?.verdict.new_paper_orders_allowed ? 'allowed' : 'blocked'}</div>
            </div>
            <div className="rounded-md border border-line bg-inset/40 px-3 py-2 text-2xs">
              <div className="text-muted">安全边界 / Safety</div>
              <div className="mt-1 mono text-pos">L2_DRY_RUN_ONLY · live=false · broker=false</div>
            </div>
          </div>
          {(dataRefresh.alerts?.length ?? 0) > 0 && (
            <div className="mt-3 text-2xs text-warn">{dataRefresh.alerts?.slice(0, 5).join(' · ')}</div>
          )}
        </Panel>
      )}

      {scheduler && (
        <Panel eyebrow="L2 SCHEDULER + OPERATIONS" title="调度、同步与安全任务状态">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <StatusChip value={scheduler.verdict ?? 'NO_SNAPSHOT'} />
            <span className="text-2xs text-muted">
              PASS {scheduler.summary?.PASS ?? 0} · BREACH {scheduler.summary?.BREACH ?? 0} · UNAVAILABLE {scheduler.summary?.UNAVAILABLE ?? 0}
            </span>
            <span className="text-2xs text-faint">只展示 L2 调度结果；任何实盘/券商任务都不在此控制面。</span>
          </div>
          <div className="grid gap-2 md:grid-cols-4">
            {[
              ['健康任务', scheduler.summary?.PASS ?? 0, (scheduler.summary?.PASS ?? 0) > 0],
              ['门控告警', scheduler.summary?.BREACH ?? 0, (scheduler.summary?.BREACH ?? 0) === 0],
              ['外部依赖未配置', scheduler.summary?.UNAVAILABLE ?? 0, (scheduler.summary?.UNAVAILABLE ?? 0) === 0],
              ['事件封存', scheduler.event_seal_configured ? 'CONFIGURED' : 'NOT_CONFIGURED', scheduler.event_seal_configured === true],
            ].map(([label, value, ok]) => (
              <div key={String(label)} className="rounded-md border border-line bg-inset/40 px-3 py-2">
                <div className="text-2xs text-muted">{label}</div>
                <div className={`mt-1 text-xs mono ${ok ? 'text-pos' : 'text-warn'}`}>{value}</div>
              </div>
            ))}
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {scheduler.checks.map(check => (
              <div key={check.task_name} className="rounded-md border border-line bg-inset/30 px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-2xs mono text-fg">{check.task_name}</span>
                  <StatusChip value={check.status ?? 'UNKNOWN'} />
                </div>
                <div className="mt-1 text-2xs text-faint">{check.code ?? 'UNKNOWN'} · {check.detail ?? ''}</div>
              </div>
            ))}
          </div>
          <div className="mt-3 text-2xs text-muted">
            external heartbeat: {scheduler.external_heartbeat_configured ? 'configured' : 'not configured'} · event seal: {scheduler.event_seal_configured ? 'configured' : 'not configured'} · L2 remains paper-only
          </div>
        </Panel>
      )}

      {paperPreview && (
        <Panel eyebrow={bi('纸面订单预览', 'PAPER ORDER PREVIEW')}
          title={bi('当前三条 Alpha 的调仓视图', 'Current three-Alpha rebalance view')}>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {paperOperator && (
              <>
                <StatusChip value={paperOperator.verdict.operator_completed ? 'OPERATOR_COMPLETE' : 'OPERATOR_BLOCKED'} />
                <StatusChip value={paperOperator.verdict.research_packet_ready_for_all_three ? 'RESEARCH_READY' : 'RESEARCH_PENDING'} />
              </>
            )}
            <StatusChip value={paperPreview.verdict.paper_preview_ready ? 'PAPER_READY' : 'PAPER_BLOCKED'} />
            <span className="text-2xs text-muted">
              {bi('仅纸面成交，绝不真发；同一决策重复运行不会重复成交',
                'paper fills only; never sent to a broker; repeated decisions are idempotent')}
            </span>
          </div>
          {paperAlphaRows.length ? (
            <DataTable
              rows={paperAlphaRows}
              columns={[
                { key: 'alpha', header: 'Alpha', mono: true, render: r => r.alpha },
                { key: 'intents', header: bi('意图', 'Intents'), align: 'right', mono: true, render: r => r.n_intents ?? 0 },
                { key: 'approved', header: bi('门批准', 'Approved'), align: 'right', mono: true,
                  render: r => <span className="text-pos">{r.n_approved ?? 0}</span> },
                { key: 'fills', header: bi('纸面成交', 'Paper fills'), align: 'right', mono: true, render: r => r.n_paper_fills ?? 0 },
                { key: 'state', header: bi('状态', 'State'), render: r => r.replayed_from_store ? 'PAPER_REPLAY' : (r.paper_execution ?? 'BLOCKED') },
                { key: 'resid', header: bi('对账残差', 'Residual'), align: 'right', mono: true, render: r => fmtResidual(r.reconciliation_residual) },
              ] as Column<PaperAlphaRow>[]}
              getKey={r => r.alpha}
            />
          ) : <div className="text-2xs text-faint">{bi('尚无纸面订单预览', 'no paper order preview yet')}</div>}
          <div className="mt-2 text-2xs text-muted">
            {bi('live_allowed=false · order_submission_allowed=false · L2_DRY_RUN_ONLY',
              'live_allowed=false · order_submission_allowed=false · L2_DRY_RUN_ONLY')}
          </div>
        </Panel>
      )}

      {paperShadow && (
        <Panel eyebrow="AUTOMATED SHADOW PAPER"
          title={bi('全自动影子纸面驾驶舱', 'Autonomous shadow-paper cockpit')}
          right={<StatusChip value={paperShadow.verdict.shadow_paper_running ? 'RUNNING' : 'SAFE_BLOCKED'} />}>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <StatusChip value={paperShadow.verdict.auto_rebalance_ready ? 'AUTO_REBALANCE_READY' : 'REBALANCE_BLOCKED'} />
            <StatusChip value={paperShadow.verdict.paper_mark_ready ? 'MARKS_READY' : 'MARKS_BLOCKED'} />
            <StatusChip value={paperShadow.persistence.chain_valid ? 'CHAIN_PASS' : 'CHAIN_BLOCKED'} />
            <StatusChip value={shadowSchedulerHealthy ? 'SCHEDULER_ACTIVE' : 'SCHEDULER_BLOCKED'} />
            <span className="text-2xs text-muted">
              {paperShadow.provider ?? 'PUBLIC_MARK_DATA'} · {paperShadow.observed_at ? stamp(paperShadow.observed_at) : '—'}
            </span>
          </div>

          <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {[
              [bi('三策略总权益', 'Total equity'), fmtUsd(paperShadow.totals.equity ?? undefined), 'text-fg'],
              [bi('合计净 PnL', 'Net PnL'), fmtUsd(paperShadow.totals.net_pnl ?? undefined),
                (paperShadow.totals.net_pnl ?? 0) >= 0 ? 'text-pos' : 'text-neg'],
              [bi('累计资金费', 'Funding PnL'), fmtUsd(paperShadow.totals.funding_pnl ?? undefined),
                (paperShadow.totals.funding_pnl ?? 0) >= 0 ? 'text-pos' : 'text-neg'],
              [bi('账本决策数', 'Ledger decisions'), String(paperShadow.paper_ledger.n_decisions ?? '—'), 'text-info'],
            ].map(([label, value, tone]) => (
              <div key={label} className="rounded-md border border-line bg-inset/40 px-3 py-2">
                <div className="text-2xs text-muted">{label}</div>
                <div className={`mt-1 text-sm mono ${tone}`}>{value}</div>
              </div>
            ))}
          </div>

          {Object.keys(paperShadow.alphas).length > 0 ? (
            <DataTable
              rows={Object.entries(paperShadow.alphas).map(([alpha, row]) => ({
                alpha,
                preview: paperShadow.previews[alpha],
                ...row,
              }))}
              columns={[
                { key: 'alpha', header: 'Alpha', mono: true, render: r => r.alpha },
                { key: 'intents', header: bi('意图', 'Intents'), align: 'right', mono: true,
                  render: r => r.preview?.n_intents ?? '—' },
                { key: 'fills', header: bi('模拟成交', 'Paper fills'), align: 'right', mono: true,
                  render: r => r.preview?.n_paper_fills ?? r.n_fills ?? '—' },
                { key: 'positions', header: bi('持仓数', 'Positions'), align: 'right', mono: true,
                  render: r => Object.keys(r.positions ?? {}).length },
                { key: 'equity', header: bi('当前权益', 'Equity'), align: 'right', mono: true,
                  render: r => fmtUsd(r.equity) },
                { key: 'net', header: bi('净 PnL', 'Net PnL'), align: 'right', mono: true,
                  render: r => <span className={(r.net_pnl ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>{fmtUsd(r.net_pnl)}</span> },
                { key: 'funding', header: bi('资金费', 'Funding'), align: 'right', mono: true,
                  render: r => <span className={(r.funding_pnl ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>{fmtUsd(r.funding_pnl)}</span> },
                { key: 'residual', header: bi('对账残差', 'Residual'), align: 'right', mono: true,
                  render: r => fmtResidual(r.reconciliation_residual) },
              ] as Column<{ alpha: string; preview?: PaperOrderPreviewAlpha } & PaperRealtimeAlpha>[]}
              getKey={r => r.alpha}
            />
          ) : (
            <div className="text-2xs text-faint">{bi('尚无影子纸面快照', 'no shadow-paper snapshot yet')}</div>
          )}

          {paperShadow.history.length > 0 && (
            <div className="mt-3 border-t border-line/60 pt-2">
              <div className="mb-1 text-2xs text-muted">{bi('影子纸面净 PnL 历史', 'Shadow-paper net PnL history')}</div>
              <EquityCurveChart height={150} series={paperSeries(paperShadow.history)}
                yFormat={v => `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`} />
            </div>
          )}

          <div className="mt-3 rounded-md border border-info/30 bg-info/[0.06] px-3 py-2 text-2xs text-muted">
            {bi(
              '这是独立、不可晋级的自动观察账户：与正式纸面账本物理隔离，不计入前向证据，不允许部署或实盘。',
              'This is an isolated, non-promotable observation account: separate from official paper, excluded from forward evidence, deployment, and live trading.',
            )}
            <span className="ml-2 mono text-pos">live=false · broker=false · credentials=false</span>
          </div>
        </Panel>
      )}

      {paperRealtime && (
        <Panel eyebrow={bi('正式纸面实时标记', 'OFFICIAL PAPER MARKS')}
          title={bi('公开行情标记 + 回测基准', 'Public marks + backtest reference')}>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <StatusChip value={paperRealtime.verdict.cycle_completed ? 'PAPER_MARKS_READY' : 'PAPER_MARKS_BLOCKED'} />
            <StatusChip value={paperRealtime.readiness_class ?? 'PAPER_MARKS_BLOCKED'} />
            <StatusChip value={paperRealtime.verdict.backtest_reference_present_for_all_three ? 'BACKTESTS_READY' : 'BACKTESTS_PENDING'} />
            <StatusChip value={paperRealtime.funding_refresh?.error ? 'FUNDING_REFRESH_PENDING' : paperRealtime.funding_refresh?.attempted ? 'FUNDING_REFRESHED' : 'FUNDING_CACHE_ONLY'} />
            <span className="text-2xs text-muted">
              {paperRealtime.provider ?? 'PUBLIC_MARK_DATA'} · {paperRealtime.observed_at ? stamp(paperRealtime.observed_at) : '—'}
            </span>
            {usingBackend && <span className="text-2xs text-faint">{bi('控制台 10 秒自动刷新', 'console auto-refresh 10s')}</span>}
          </div>
          {Object.keys(paperRealtime.alphas ?? {}).length ? (
            <DataTable
              rows={Object.entries(paperRealtime.alphas).map(([alpha, row]) => ({ alpha, ...row }))}
              columns={[
                { key: 'alpha', header: 'Alpha', mono: true, render: r => r.alpha },
                { key: 'status', header: bi('状态', 'State'), render: r => r.status ?? '—' },
                { key: 'equity', header: bi('当前权益', 'Equity'), align: 'right', mono: true, render: r => fmtUsd(r.equity) },
                { key: 'net', header: bi('净 PnL', 'Net PnL'), align: 'right', mono: true,
                  render: r => <span className={(r.net_pnl ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>{fmtUsd(r.net_pnl)}</span> },
                { key: 'unrealized', header: bi('未实现', 'Unrealized'), align: 'right', mono: true,
                  render: r => <span className={(r.unrealized_pnl ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>{fmtUsd(r.unrealized_pnl)}</span> },
                { key: 'funding', header: bi('资金费', 'Funding'), align: 'right', mono: true,
                  render: r => <span className={(r.funding_pnl ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>{r.funding_data_complete === false ? '—' : fmtUsd(r.funding_pnl)}</span> },
                { key: 'sharpe', header: bi('回测 SR', 'Backtest SR'), align: 'right', mono: true,
                  render: r => paperRealtime.backtests?.[r.alpha]?.sharpe == null ? '—' : signed(paperRealtime.backtests[r.alpha].sharpe!, 2) },
                { key: 'resid', header: bi('对账残差', 'Residual'), align: 'right', mono: true, render: r => fmtResidual(r.reconciliation_residual) },
              ] as Column<{ alpha: string } & PaperRealtimeAlpha>[]}
              getKey={r => r.alpha}
            />
          ) : <div className="text-2xs text-faint">{bi('尚无实时标记', 'no real-time paper mark yet')}</div>}
          <div className="mt-2 text-2xs text-muted">
            {bi(`实时=公开 mark REST 轮询 · 新增标记 ${paperRealtime.persistence.new_snapshots} · 链完整 ${paperRealtime.persistence.chain_valid ? 'PASS' : 'BLOCKED'} · 实盘禁用`,
              `real-time = public mark REST polling · new snapshots ${paperRealtime.persistence.new_snapshots} · chain ${paperRealtime.persistence.chain_valid ? 'PASS' : 'BLOCKED'} · live disabled`)}
          </div>
          {paperRealtime.history && paperRealtime.history.length > 0 && (
            <div className="mt-3 border-t border-line/60 pt-2">
              <div className="mb-1 text-2xs text-muted">Paper PnL history · recent hash-chained marks</div>
              <EquityCurveChart height={120} series={paperSeries(paperRealtime.history)}
                yFormat={v => `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`} />
            </div>
          )}
        </Panel>
      )}

      {paperHealth && (
        <Panel eyebrow="PAPER HEALTH" title="Semantic readiness gate">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <StatusChip value={paperHealth.verdict.paper_ready ? 'PAPER_READY' : 'PAPER_BLOCKED'} />
            <StatusChip value={paperHealth.verdict.paper_mark_ready ? 'MARKS_READY' : 'MARKS_BLOCKED'} />
            <StatusChip value={paperHealth.verdict.paper_preview_ready ? 'PREVIEW_READY' : 'PREVIEW_BLOCKED'} />
            <StatusChip value={paperHealth.verdict.funding_data_complete_for_all_three ? 'FUNDING_READY' : 'FUNDING_PENDING'} />
            <StatusChip value={paperHealth.readiness_class ?? 'PAPER_BLOCKED'} />
            <span className="text-2xs text-muted">
              {paperHealth.age_ms == null ? 'mark age unavailable' : `mark age ${Math.round(paperHealth.age_ms / 1000)}s`}
            </span>
          </div>
          {paperHealth.blockers.length > 0 ? (
            <div className="text-2xs text-neg">
              Blockers: {paperHealth.blockers.join(', ')}
            </div>
          ) : (
            <div className="text-2xs text-pos">
              All three paper accounts, fills, backtest references, and reconciliation checks are healthy.
            </div>
          )}
          {Object.keys(paperHealth.research_risk_bundles ?? {}).length > 0 && (
            <div className="mt-3 grid gap-2 md:grid-cols-3">
              {Object.entries(paperHealth.research_risk_bundles ?? {}).map(([alpha, bundle]) => {
                const missing = bundle.missing ?? [];
                const reasons = bundle.reasons ?? [];
                const ready = bundle.status === 'READY';
                return (
                  <div key={alpha} className="rounded-md border border-line bg-inset/30 px-3 py-2 text-2xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="mono text-fg">{alpha}</span>
                      <StatusChip value={ready ? 'RISK_BUNDLE_READY' : (bundle.status ?? 'RISK_BUNDLE_PENDING')} />
                    </div>
                    {!ready && (
                      <>
                        <div className="mt-1 text-warn">missing: {missing.length ? missing.join(', ') : 'not supplied'}</div>
                        {reasons.length > 0 && <div className="mt-1 text-faint">{reasons.slice(0, 2).join(' · ')}</div>}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {paperHealth.next_action && (
            <div className="mt-2 text-2xs text-warn">Next action: {paperHealth.next_action}</div>
          )}
          <div className="mt-2 text-2xs text-muted">
            paper-only · live_allowed=false · order_submission_allowed=false
          </div>
        </Panel>
      )}

      {forwardTrack && (
        <Panel eyebrow="FORWARD PAPER CLOCK" title={bi('前向纸面证据时钟', 'Out-of-sample paper evidence clock')}>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <StatusChip value={forwardTrack.present ? 'FORWARD_TRACKING' : 'FORWARD_TRACK_MISSING'} />
            <StatusChip value={forwardTrack.quality_evidence_current === true ? 'DATA_ADMISSION_PASS' : 'DATA_ADMISSION_PENDING'} />
            <StatusChip value={forwardTrack.alphas?.every(row => (row.n_forward_periods ?? 0) >= (forwardTrack.evidence_gate_weeks ?? 8)) ? 'FORWARD_GATE_COMPLETE' : 'FORWARD_GATE_PENDING'} />
            <span className="text-2xs text-muted">
              {bi(`门槛 ${(forwardTrack.evidence_gate_weeks ?? 8)} 个真实前向周期`, `${forwardTrack.evidence_gate_weeks ?? 8} real forward periods required`)}
            </span>
          </div>
          {forwardTrack.alphas && forwardTrack.alphas.length > 0 ? (
            <DataTable
              rows={forwardTrack.alphas as LiveTradingForwardAlpha[]}
              columns={[
                { key: 'alpha_id', header: 'Alpha', mono: true, render: row => row.alpha_id },
                { key: 'periods', header: bi('已实现/门槛', 'Realized / gate'), align: 'right', mono: true,
                  render: row => `${row.n_forward_periods ?? 0}/${forwardTrack.evidence_gate_weeks ?? 8}` },
                { key: 'pending', header: bi('待实现决策', 'Pending decision'), align: 'right', mono: true,
                  render: row => row.pending_decisions ?? 0 },
                { key: 'state', header: bi('证据状态', 'Evidence state'), render: row => row.evidence_state ?? 'UNKNOWN' },
                { key: 'sharpe', header: bi('前向 SR', 'Forward SR'), align: 'right', mono: true,
                  render: row => row.paper_sharpe == null ? '—' : signed(row.paper_sharpe, 2) },
              ] as Column<LiveTradingForwardAlpha>[]}
              getKey={row => row.alpha_id}
            />
          ) : (
            <div className="text-2xs text-faint">{bi('尚无前向证据记录', 'no forward evidence record yet')}</div>
          )}
          <div className="mt-2 text-2xs text-muted">
            {bi('纸面账户健康不等于前向证据完成；待实现决策会在下一真实闭合周期后结算，系统不会用历史数据伪造周期。',
              'Paper-account health is separate from forward evidence. Pending decisions settle only after the next real closed period; historical data is never reused to fake a period.')}
          </div>
          {forwardTrack.alerts && forwardTrack.alerts.length > 0 && (
            <div className="mt-2 text-2xs text-neg">{forwardTrack.alerts.join(', ')}</div>
          )}
        </Panel>
      )}

      {paperProfiles.length > 0 && (
        <Panel eyebrow="PAPER PROFILES" title={bi('隔离纸面账户', 'Isolated paper profiles')}>
          <div className="grid gap-2 md:grid-cols-2">
            {paperProfiles.map(profile => {
              const alphaEquity = profile.realtime.total_equity;
              const alphaNetPnl = profile.realtime.total_net_pnl;
              const paperLedger = profile.realtime.paper_ledger;
              return (
              <div key={profile.profile_id} className="rounded-md border border-line bg-inset/40 px-3 py-2">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="mono text-xs">{profile.profile_id}</span>
                  <StatusChip value={profile.health.paper_ready ? 'PAPER_READY' : 'PAPER_BLOCKED'} />
                  <StatusChip value={profile.realtime.chain_valid ? 'CHAIN_PASS' : 'CHAIN_BLOCKED'} />
                </div>
                <div className="grid grid-cols-3 gap-2 text-2xs text-muted">
                  <span>{bi('账户规模', 'AUM')}<br /><strong className="text-fg">{fmtUsd(profile.aum)}</strong></span>
                  <span>{bi('Alpha 数', 'Alphas')}<br /><strong className="text-fg">{Object.keys(profile.realtime.alphas ?? {}).length}</strong></span>
                  <span>{bi('标记年龄', 'Mark age')}<br /><strong className="text-fg">{profile.health.age_ms == null ? '—' : `${Math.round(profile.health.age_ms / 1000)}s`}</strong></span>
                </div>
                <div className="mt-2 text-2xs text-muted">
                  Paper decisions: <strong className="text-fg">{paperLedger?.n_decisions ?? '—'}</strong>
                </div>
                  <div className="mt-1 text-2xs text-muted">
                    Operator: <strong className="text-fg">{profile.realtime.operator_mode ?? 'UNKNOWN'}</strong>
                    {' · '}new paper orders: <strong className={profile.realtime.new_paper_orders_allowed ? 'text-pos' : 'text-warn'}>{profile.realtime.new_paper_orders_allowed ? 'allowed' : 'blocked'}</strong>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 border-t border-line/60 pt-2 text-2xs text-muted">
                  <span>{bi('三 Alpha 权益', '3-Alpha equity')}<br /><strong className="text-fg">{fmtUsd(alphaEquity ?? undefined)}</strong></span>
                  <span>{bi('合计净 PnL', 'Σ net PnL')}<br /><strong className={alphaNetPnl == null ? 'text-faint' : alphaNetPnl >= 0 ? 'text-pos' : 'text-neg'}>{fmtUsd(alphaNetPnl ?? undefined)}</strong></span>
                </div>
                {profile.health.blockers.length > 0 && (
                  <div className="mt-2 text-2xs text-neg">{profile.health.blockers.join(', ')}</div>
                )}
                <div className="mt-2 text-2xs text-muted">paper-only · live/order submission disabled</div>
              </div>
              );
            })}
          </div>
        </Panel>
      )}

      <KpiStrip cols={6}>
        <StatTile label={bi('意图', 'Intents')} value={latest ? String(latest.n_intents) : '—'} tone="muted" />
        <StatTile label={bi('门批准', 'Gate approved')} value={latest ? String(latest.n_approved) : '—'} tone="pos" accent />
        <StatTile label={bi('门拒绝', 'Gate rejected')} value={latest ? String(latest.n_rejected) : '—'}
          tone={latest && latest.n_rejected > 0 ? 'neg' : 'muted'} />
        <StatTile label={bi('纸面成交', 'Paper fills')} value={has ? String(nFills) : '—'} tone="info" />
        <StatTile label={bi('净 PnL', 'Net PnL')} value={fmtUsd(pnl.total_pnl)} tone="muted"
          sub={bi('= 重平衡成本', '= rebalance cost')} />
        <StatTile label={bi('对账残差', 'Residual')} value={fmtResidual(pnl.residual)}
          tone={pnl.residual != null && Math.abs(pnl.residual) < 1e-6 ? 'pos' : 'warn'} sub={bi('恒等式', 'identity')} />
      </KpiStrip>

      {mp && (
        <>
          <KpiStrip cols={6}>
            <StatTile label={bi('总收益', 'Total return')} value={pnl.total_return == null ? '—' : `${(pnl.total_return * 100).toFixed(1)}%`}
              tone={(pnl.total_return ?? 0) >= 0 ? 'pos' : 'neg'} accent />
            <StatTile label={bi('夏普(周→年)', 'Sharpe (wk→yr)')} value={pnl.sharpe == null ? '—' : signed(pnl.sharpe, 2)} tone="info"
              sub={pnl.sharpe_vt == null ? undefined : bi(`VT ${signed(pnl.sharpe_vt, 2)}`, `VT ${signed(pnl.sharpe_vt, 2)}`)} />
            <StatTile label={bi('最大回撤', 'Max drawdown')} value={pnl.max_drawdown == null ? '—' : `${(pnl.max_drawdown * 100).toFixed(0)}%`} tone="warn"
              sub={pnl.max_drawdown_vt == null
                ? bi('截面价差,非净敞口', 'cross-sectional, not net')
                : bi(`VT ${(pnl.max_drawdown_vt * 100).toFixed(0)}% · 截面价差,非净敞口`, `VT ${(pnl.max_drawdown_vt * 100).toFixed(0)}% · cross-sectional, not net`)} />
            <StatTile label={bi('净敞口', 'Net exposure')} value={pnl.avg_net_exposure_pct == null ? '—' : `${(pnl.avg_net_exposure_pct * 100).toFixed(1)}%`}
              tone={(pnl.avg_net_exposure_pct ?? 1) < 0.05 ? 'pos' : 'warn'} sub={bi('≈0 = 美元中性', '≈0 = dollar-neutral')} />
            <StatTile label={bi('周期数', 'Periods')} value={String(mp.n_periods ?? mp.equity_curve?.length ?? 0)} tone="muted" sub={bi('周度', 'weekly')} />
            <StatTile label={bi('累计被门拒', 'Total gate-rejected')} value={String(latest?.n_rejected ?? 0)}
              tone={(latest?.n_rejected ?? 0) > 0 ? 'neg' : 'muted'} />
          </KpiStrip>
          <Panel eyebrow={bi('净值曲线', 'EQUITY CURVE')}
            className="terminal-panel"
            title={bi('门强制下的滚动净值(蓝实线=raw,虚线=vol-target 压尾)· 含资金费现金流',
              'Gate-enforced rolling equity (solid=raw, dashed=vol-target) · funding cash-flow included')}>
            <EquityCurveChart height={170}
              series={[
                { label: bi('raw 净值', 'raw'), points: mp.equity_curve ?? [], tone: 'accent' },
                { label: bi('vol-target 压尾', 'vol-target'), points: mp.equity_curve_vt ?? [], tone: 'info', dashed: true },
              ]}
              yFormat={v => v.toFixed(2)} />
            <div className="mt-1 text-2xs text-muted">
              {bi(`累计资金费 $${(pnl.total_funding ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} · 对账残差 ${fmtResidual(pnl.residual)}(价格腿恒等式)。诚实发现:书本已近美元中性(净敞口 ${((pnl.avg_net_exposure_pct ?? 0) * 100).toFixed(1)}%),故 ${((pnl.max_drawdown ?? 0) * 100).toFixed(0)}% 回撤是 L/S 截面价差本身的波动、非净市场敞口/整数手舍入(经 $1M↔$100M 规模不变验证);funding 是骑在这条波动价差上的较小稳定边,无法干净隔离。`,
                `funding $${(pnl.total_funding ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} · residual ${fmtResidual(pnl.residual)}. Honest finding: the book is already ~dollar-neutral (net ${((pnl.avg_net_exposure_pct ?? 0) * 100).toFixed(1)}%), so the ${((pnl.max_drawdown ?? 0) * 100).toFixed(0)}% drawdown is the inherent L/S cross-sectional spread — NOT net-market exposure or integer-lot rounding (verified scale-invariant $1M↔$100M); funding is the smaller steady edge riding on that volatile spread, not cleanly separable.`)}
            </div>
          </Panel>
        </>
      )}

      <Panel eyebrow={bi('流水线', 'PIPELINE')} title={bi('一条连续的链(最新一次干跑)', 'One continuous flow (latest dry-run)')}>
        <div className="flex flex-wrap items-stretch gap-1.5">
          {stages.map((s, i) => (
            <React.Fragment key={s.en}>
              <div className={`flex-1 min-w-[120px] rounded-md border px-2.5 py-2 ${s.accent ? 'border-accent/40 bg-accent/[0.07]' : 'border-line bg-inset/40'}`}>
                <div className="text-2xs text-muted">{bi(s.zh, s.en)}</div>
                <div className={`mt-1 text-xs mono ${s.accent ? 'text-accent' : 'text-fg'}`}>{s.val}</div>
              </div>
              {i < stages.length - 1 && <div className="self-center text-muted">→</div>}
            </React.Fragment>
          ))}
        </div>
        {has && (
          <div className="mt-2 text-2xs text-muted">
            {bi(`换手 ${fmtUsd(pnl.turnover)} · 成本 ${fmtUsd(pnl.costs)} · 期末权益 ${fmtUsd(pnl.equity)} · 适配器二次拒 ${latest?.detail?.n_adapter_rejected ?? 0}`,
              `turnover ${fmtUsd(pnl.turnover)} · costs ${fmtUsd(pnl.costs)} · end equity ${fmtUsd(pnl.equity)} · adapter-rejected ${latest?.detail?.n_adapter_rejected ?? 0}`)}
          </div>)}
      </Panel>

      <Panel eyebrow={bi('最新组合(纸面持仓)', 'LATEST BOOK')}
        title={bi('门批准后的纸面目标持仓(按手数绝对值前 12)', 'Gate-approved paper target positions (top 12 by |qty|)')}>
        {posRows.length ? <DataTable rows={posRows} columns={posCols} getKey={r => r.inst} density="compact" /> : (
          <div className="text-2xs text-faint">{bi('无持仓(尚无干跑或全被门拒)', 'no positions (no run yet, or all gate-rejected)')}</div>)}
      </Panel>

      <Panel eyebrow={bi('运行历史', 'RUN HISTORY')} title={bi('每次干跑都是一条可复现记录(append-only)', 'Each dry-run is a reproducible record (append-only)')}>
        {p.runs.length ? (
          <>
            {strategies.length > 2 && (
              <div className="mb-3 flex flex-wrap items-center gap-2">
                {strategies.map(s => (
                  <PillToggle key={s} label={s} active={strategyFilter === s} onClick={() => setStrategyFilter(s)} />
                ))}
              </div>
            )}
            <DataTable rows={[...filteredRuns].reverse()} columns={runCols} getKey={r => r.run_id}
              stickyHeader maxHeight={420} density="compact"
              searchable={r => `${r.run_id} ${r.strategy_id} ${r.audit_root}`}
              searchPlaceholder={bi('按 run_id / 策略 / 审计根搜索…', 'search by run_id / strategy / audit root…')} />
          </>
        ) : (
          <div className="text-2xs text-faint">{bi('无干跑记录 —— 门外跑 tools/dry_run_carry.py 生成', 'no dry-run records — run tools/dry_run_carry.py off-gate')}</div>)}
      </Panel>

      <Panel eyebrow={bi('多场所纸面 TCA', 'MULTI-VENUE PAPER TCA')}
        title={bi('纵向路由成交质量（只读、仿真）', 'Longitudinal route execution quality (read-only simulation)')}>
        <KpiStrip cols={4}>
          <StatTile label={bi('状态', 'Status')} value={routeTca?.status ?? 'NO_ROUTE_TCA'}
            tone={routeTca?.status === 'LONGITUDINAL_READY' ? 'pos' : routeTca?.status === 'INVALID_RETAINED_EVIDENCE' ? 'neg' : 'muted'} />
          <StatTile label={bi('路由运行', 'Routed runs')} value={String(routeTca?.run_count ?? 0)} tone="info" />
          <StatTile label={bi('TCA 记录', 'TCA records')} value={String(routeTca?.record_count ?? 0)} tone="muted" />
          <StatTile label={bi('加权短缺', 'Weighted shortfall')}
            value={routeTca?.weighted_shortfall_bps == null ? '—' : `${Number(routeTca.weighted_shortfall_bps).toFixed(2)} bps`}
            tone="warn" />
        </KpiStrip>
        {(routeTca?.venues.length ?? 0) > 0 ? (
          <DataTable
            rows={routeTca!.venues}
            getKey={row => row.venue_id}
            density="compact"
            columns={[
              { key: 'venue', header: bi('场所', 'Venue'), render: row => <span className="mono text-xs">{row.venue_id}</span> },
              { key: 'records', header: bi('记录', 'Records'), align: 'right', render: row => row.record_count },
              { key: 'qty', header: bi('成交量', 'Filled qty'), align: 'right', render: row => row.filled_quantity },
              { key: 'notional', header: bi('参考名义额', 'Reference notional'), align: 'right', render: row => `$${Number(row.reference_notional).toLocaleString('en-US', { maximumFractionDigits: 0 })}` },
              { key: 'shortfall', header: bi('加权短缺', 'Weighted shortfall'), align: 'right', render: row => row.weighted_shortfall_bps == null ? '—' : `${Number(row.weighted_shortfall_bps).toFixed(2)} bps` },
              { key: 'last', header: bi('最近', 'Latest'), align: 'right', render: row => stamp(row.last_timestamp) },
            ]}
          />
        ) : (
          <div className="text-2xs text-faint">
            {bi('尚无启用 SOR 的纸面运行；不会把普通干跑伪装成多场所 TCA。',
              'No SOR-enabled paper runs yet; ordinary dry-runs are not presented as multi-venue TCA.')}
          </div>
        )}
        <div className="mt-2 text-2xs text-muted">
          {bi('数据来自 append-only DryRunStore；每条记录先校验 SHA-256、路由绑定和算术恒等式。仅公开行情驱动的纸面仿真，不连接券商。',
            'Backed by append-only DryRunStore; every record is verified for SHA-256, route binding and arithmetic identities. Public-market-data paper simulation only; no broker connectivity.')}
        </div>
      </Panel>

      <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
        {bi('只读 · 研究/干跑(L2)· DryRunStore(data/cache/dry_run.db)· 门 fail-closed + kill-switch + 哈希链审计 · 绝不真发券商 · 晋级实盘人工专属',
          'Read-only · research / dry-run (L2) · DryRunStore · gate fail-closed + kill-switch + hash-chain audit · never sent to a broker · promotion to live is human-only')}
      </div>
      </LoadingVeil>
    </PageShell>
  );
}
