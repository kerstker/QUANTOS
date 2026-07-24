import React from 'react';
import { bi } from '@/lib/i18n/bilingual';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, LoadingVeil,
  DataTable, StatusChip, type Column,
} from '@/ui/primitives';
import { AsOfStamp, EndpointFootnote, StatusBanner, EquityCurveChart } from '@/ui/workbench';
import { pct, signed, stamp } from '@/ui/format';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import { fetchMexcStrategySnapshot } from '@/lib/api/research-console-api';
import type { DryRunRunRow, MexcStrategyPayload } from '@/lib/types/research';

/**
 * MEXC 策略 — btc_spot_trend end-to-end dry-run vs a buy-and-hold benchmark,
 * read-only from /api/v1/console/mexc-strategy. Execution-reality backtest on
 * KNOWN history (gate fail-closed + paper fills + average-cost reconciliation),
 * NOT forward/live. Multiple runs in the store = the cost-basis variants
 * (e.g. 0bps vs 5bps); they are tabled side by side, never merged.
 */

const mockPayload: MexcStrategyPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
  data_provenance: 'SYNTHETIC_DETERMINISTIC', snapshot_present: false, verdict: 'NO_RUN',
  venue: 'MEXC', strategy_id: 'btc_spot_trend', n_runs: 0,
  metrics: {
    total_return: null, sharpe: null, sharpe_vt: null, max_drawdown: null,
    max_drawdown_vt: null, turnover: null, n_periods: null,
    n_approved: null, n_rejected: null, reconciliation_residual: null,
  },
  equity_curve: [], equity_curve_vt: [],
  buy_and_hold: { present: false },
  latest: {}, runs: [],
  honesty_note: 'Mock fixture — no btc_spot_trend dry-run in dry_run.db. Run the dry-run driver off-gate to produce one.',
};

const fmtResidual = (v?: number | null) => (v == null ? '—' : Math.abs(v) < 1e-6 ? '≈0' : v.toExponential(1));
const fmtTurnover = (v?: number | null) => (v == null ? '—' : `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`);

export default function MexcStrategy() {
  const { payload, apiState, error, usingBackend, snapshotState } = useConsoleSnapshot<MexcStrategyPayload>(
    s => fetchMexcStrategySnapshot(s));
  const p: MexcStrategyPayload = usingBackend ? payload! : mockPayload;
  const has = p.snapshot_present && p.runs.length > 0;
  const m = p.metrics;
  const bnh = p.buy_and_hold;

  const runCols: Column<DryRunRunRow>[] = [
    { key: 'time', header: bi('时间(UTC)', 'Time (UTC)'), mono: true, sortable: true, sortAccessor: r => r.timestamp, render: r => stamp(r.timestamp) },
    { key: 'run', header: 'run_id', mono: true, render: r => <span className="text-2xs text-faint">{r.run_id.slice(0, 12)}</span> },
    { key: 'intents', header: bi('意图', 'Intents'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.n_intents, render: r => r.n_intents },
    { key: 'appr', header: bi('门批准', 'Approved'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.n_approved,
      render: r => <span className="text-pos">{r.n_approved}</span> },
    { key: 'rej', header: bi('门拒绝', 'Rejected'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.n_rejected,
      render: r => <span className={r.n_rejected ? 'text-neg' : 'text-faint'}>{r.n_rejected}</span> },
    { key: 'ret', header: bi('总收益', 'Return'), align: 'right', mono: true, sortable: true, sortAccessor: r => r.pnl?.total_return ?? 0,
      render: r => <span className={(r.pnl?.total_return ?? 0) >= 0 ? 'text-pos' : 'text-neg'}>{pct(r.pnl?.total_return)}</span> },
    { key: 'sr', header: bi('夏普', 'Sharpe'), align: 'right', mono: true,
      render: r => (r.pnl?.sharpe == null ? '—' : signed(r.pnl.sharpe, 2)) },
    { key: 'dd', header: bi('最大回撤', 'Max DD'), align: 'right', mono: true, render: r => pct(r.pnl?.max_drawdown, 0) },
    { key: 'resid', header: bi('对账残差', 'Residual'), align: 'right', mono: true, render: r => fmtResidual(r.pnl?.residual) },
  ];

  return (
    <PageShell>
      <PageHeader title="MEXC Strategy" titleZh="MEXC 策略"
        subtitle={bi('btc_spot_trend 端到端干跑 vs 买入持有基准 · 已知历史的执行现实回测,非前向/实盘',
          'btc_spot_trend end-to-end dry-run vs buy-and-hold · execution-reality backtest on known history, not forward/live')} />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState}
        realData={usingBackend && p.data_provenance === 'REAL_DRY_RUN_OFFLINE_SNAPSHOT'}
        source={usingBackend ? (has ? 'REAL_DRY_RUN_OFFLINE_SNAPSHOT' : 'NO_RUN') : 'FRONTEND_MEXC_STRATEGY_MOCK'}
        error={error}
        noDataYet={usingBackend && !p.snapshot_present}
        dataStatus={snapshotState} />
      <StatusBanner />

      <LoadingVeil active={apiState === 'loading'}>
        <div className="flex flex-wrap items-center gap-3">
          <StatusChip value={p.verdict} />
          <span className="text-2xs text-muted">{p.venue} · {p.strategy_id}</span>
          {has && <span className="text-2xs text-faint">{bi(`共 ${p.n_runs} 次干跑`, `${p.n_runs} dry-runs`)}</span>}
        </div>

        <KpiStrip cols={6}>
          <StatTile label={bi('总收益(RAW)', 'Total return (RAW)')}
            value={m.total_return == null ? '—' : pct(m.total_return)}
            tone={(m.total_return ?? 0) >= 0 ? 'pos' : 'neg'} accent
            sub={bnh.present && bnh.total_return != null ? bi(`基准 ${pct(bnh.total_return)}`, `B&H ${pct(bnh.total_return)}`) : undefined} />
          <StatTile label={bi('夏普(周→年)', 'Sharpe (wk→yr)')}
            value={m.sharpe == null ? '—' : signed(m.sharpe, 2)} tone="info"
            sub={m.sharpe_vt == null ? undefined : `VT ${signed(m.sharpe_vt, 2)}`} />
          <StatTile label={bi('最大回撤', 'Max drawdown')}
            value={m.max_drawdown == null ? '—' : pct(m.max_drawdown, 0)} tone="warn"
            sub={m.max_drawdown_vt == null ? undefined : `VT ${pct(m.max_drawdown_vt, 0)}`} />
          <StatTile label={bi('门批准 / 拒绝', 'Gate appr / rej')}
            value={m.n_approved == null ? '—' : `${m.n_approved} / ${m.n_rejected ?? 0}`}
            tone={(m.n_rejected ?? 0) > 0 ? 'warn' : 'pos'} />
          <StatTile label={bi('对账残差', 'Recon residual')}
            value={fmtResidual(m.reconciliation_residual)}
            tone={m.reconciliation_residual != null && Math.abs(m.reconciliation_residual) < 1e-6 ? 'pos' : 'warn'}
            sub={bi('恒等式', 'identity')} />
          <StatTile label={bi('周期数', 'Periods')} value={m.n_periods == null ? '—' : String(m.n_periods)}
            tone="muted" sub={bi('周度', 'weekly')} />
        </KpiStrip>

        <Panel eyebrow={bi('净值曲线', 'EQUITY CURVE')}
          title={bi('RAW / vol-target vs 买入持有基准(同一 MEXC 行情对齐)', 'RAW / vol-target vs buy-and-hold (aligned on the same MEXC bars)')}
          className="terminal-panel">
          <EquityCurveChart height={220}
            series={[
              { label: bi('RAW 净值', 'RAW'), points: p.equity_curve, tone: 'accent' },
              { label: bi('vol-target 压尾', 'vol-target'), points: p.equity_curve_vt, tone: 'info', dashed: true },
              { label: bi('买入持有基准', 'buy & hold'), points: bnh.present ? bnh.curve ?? [] : [], tone: 'muted' },
            ]}
            yFormat={v => v.toFixed(2)} />
          <div className="mt-1 flex flex-wrap gap-x-4 text-2xs text-muted">
            {bnh.present && (
              <span>{bi('基准', 'benchmark')}: {bi('收益', 'return')} {pct(bnh.total_return)} · SR {bnh.sharpe == null ? '—' : signed(bnh.sharpe, 2)} · DD {pct(bnh.max_drawdown, 0)}</span>
            )}
            <span>{bi('换手', 'turnover')} {fmtTurnover(m.turnover)}</span>
          </div>
        </Panel>

        <Panel eyebrow={bi('运行历史(成本口径变体并列)', 'RUN HISTORY (cost-basis variants side by side)')}
          title={bi('每次干跑都是一条可复现记录(append-only dry_run.db)', 'Each dry-run is a reproducible record (append-only dry_run.db)')}>
          {p.runs.length ? (
            <DataTable rows={[...p.runs].reverse()} columns={runCols} getKey={r => r.run_id}
              stickyHeader maxHeight={360} density="compact"
              searchable={r => `${r.run_id} ${r.strategy_id}`}
              searchPlaceholder={bi('按 run_id 搜索…', 'search by run_id…')} />
          ) : (
            <div className="text-2xs text-faint">{bi('无 btc_spot_trend 干跑记录 —— 门外跑 dry-run 驱动器生成', 'no btc_spot_trend dry-run records — run the dry-run driver off-gate')}</div>
          )}
        </Panel>

        <div className="rounded-lg border border-line bg-inset/40 px-3 py-2 text-2xs text-muted">
          {p.honesty_note}
        </div>
        <EndpointFootnote path="/api/v1/console/mexc-strategy"
          note={bi('只读 · MEXC 无现货测试网,毕业路径 = 干跑/paper + 人工 mandate 下的微小真实金额', 'read-only · MEXC has no spot testnet; graduation = dry-run/paper + tiny real size under a human mandate')} />
      </LoadingVeil>
    </PageShell>
  );
}
