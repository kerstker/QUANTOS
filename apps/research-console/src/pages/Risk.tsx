import React, { useState } from 'react';
import { fetchRiskDashboardSnapshot, fetchSafetyPrimitivesSnapshot } from '@/lib/api/research-console-api';
import { mockRiskReports } from '@/lib/mock-data/research-mocks';
import { bi } from '@/lib/i18n/bilingual';
import type { CalculationLineageItem, ComputationRiskReport, RiskDashboardPayload, RiskReport, SafetyPrimitivesPayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, KeyValue, Chip, type Column,
} from '@/ui/primitives';
import { RadialGauge, DivergingBars } from '@/ui/charts';
import { num, pct, fixed, shortHash, statusTone } from '@/ui/format';

type AnyRiskReport = ComputationRiskReport | RiskReport;

const ALPHA_IDS = ['alpha-1', 'alpha-2', 'alpha-3', 'alpha-4'];

const gateLabels: Record<string, string> = {
  market: bi('市场', 'Market'),
  liquidity: bi('流动性', 'Liquidity'),
  concentration: bi('集中度', 'Concentration'),
  correlation: bi('相关性', 'Correlation'),
  drawdown: bi('回撤', 'Drawdown'),
  regime: bi('市场状态', 'Regime'),
  stress: bi('压力测试', 'Stress'),
  capacity: bi('容量', 'Capacity'),
  model_alpha: bi('模型Alpha', 'Model Alpha'),
};

const gateLabel = (g: string) => gateLabels[g] ?? g.replace(/_/g, ' ');

export default function Risk() {
  const [selectedAlpha, setSelectedAlpha] = useState('alpha-1');
  const { payload, apiState, error: apiError, usingBackend } = useConsoleSnapshot<RiskDashboardPayload>(
    s => fetchRiskDashboardSnapshot(selectedAlpha, s),
    [selectedAlpha],
  );

  // The REAL kill-switch / pre-trade gate / SafetyConfig status (read-only — never armed/reset here).
  const sp = useConsoleSnapshot<SafetyPrimitivesPayload>(s => fetchSafetyPrimitivesSnapshot(s));
  const safety = sp.payload;

  const primaryReport: AnyRiskReport = usingBackend ? payload!.primary_report : mockRiskReports[0];
  const reports: AnyRiskReport[] = usingBackend ? payload!.reports : mockRiskReports;
  const alpha = usingBackend ? payload!.alpha : undefined;
  const selectedAlphaId = usingBackend ? payload!.selected_alpha_id : selectedAlpha;

  const gateEntries = Object.entries(primaryReport.gate_results);
  const passed = primaryReport.gates_passed;
  const total = primaryReport.gates_total;
  const failedGates = gateEntries.filter(([, ok]) => !ok).map(([g]) => g);
  const decisionTone = statusTone(primaryReport.decision);
  const reproHash = 'reproducibility_hash' in primaryReport ? primaryReport.reproducibility_hash : undefined;

  // Gate-pass attribution: +1 per passing gate, -1 per breached gate (deterministic).
  const gateBars = gateEntries.map(([g, ok]) => ({ label: gateLabel(g), value: ok ? 1 : -1 }));

  const reportCols: Column<AnyRiskReport>[] = [
    { key: 'run', header: bi('风控运行', 'Risk Run'), mono: true, render: r => r.run_id },
    { key: 'pf', header: bi('组合', 'Portfolio'), mono: true, render: r => r.portfolio_id },
    { key: 'decision', header: bi('决策', 'Decision'), render: r => <StatusChip value={r.decision} /> },
    { key: 'gates', header: bi('门禁', 'Gates'), align: 'right', width: '150px', render: r => (
      <div className="flex items-center justify-end gap-2">
        <ProgressBar value={r.gates_passed} max={r.gates_total || 1}
          tone={r.gates_passed >= r.gates_total ? 'pos' : 'warn'} />
        <span className="tnum w-10 text-right text-muted">{num(r.gates_passed)}/{num(r.gates_total)}</span>
      </div>
    ) },
    { key: 'breaches', header: bi('破限门', 'Breaches'), render: r => {
      const fails = Object.entries(r.gate_results).filter(([, ok]) => !ok).map(([g]) => g);
      return fails.length
        ? <div className="flex flex-wrap gap-1">{fails.map(g => <Chip key={g} label={gateLabel(g)} tone="neg" />)}</div>
        : <Chip label={bi('全部通过', 'All clear')} tone="pos" />;
    } },
    { key: 'reasons', header: bi('原因', 'Reasons'), render: r => (
      <span className="text-muted">{r.reasons.length ? r.reasons.join(' · ') : '—'}</span>
    ) },
  ];

  return (
    <PageShell>
      <PageHeader title="Research Risk" titleZh="研究风险"
        subtitle={bi('确定性、失败即拒的 9 引擎研究风控门 · 仅研究态(无活盘、无下单)',
          'Deterministic fail-closed 9-engine research-risk gate · research-only (no live trading, no order submission)')}
        actions={
          <div className="flex flex-wrap gap-1.5">
            {ALPHA_IDS.map(id => (
              <button key={id} onClick={() => setSelectedAlpha(id)}
                className={`rounded-md px-3 py-1.5 text-2xs font-semibold uppercase tracking-wide transition-colors ${
                  selectedAlpha === id
                    ? 'bg-accent/15 text-accent ring-1 ring-accent/40'
                    : 'border border-line bg-elevated/40 text-muted hover:bg-elevated'}`}>
                {id}
              </button>
            ))}
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} realData={(payload as { data_provenance?: string } | null | undefined)?.data_provenance === 'REAL_CRYPTO_SERIES'} source={payload?.source} error={apiError} />

      {safety && (
        <Panel eyebrow={bi('安全原语(只读)', 'Safety Primitives (read-only)')}
          title={bi('熔断 / 盘前门 / 安全配置', 'Kill-Switch / Pre-Trade Gate / Safety Config')}
          right={<Chip label={bi('真实安全原语', 'REAL_SAFETY_PRIMITIVES')} tone="pos" />}>
          <KpiStrip cols={4}>
            <StatTile label={bi('熔断器', 'Kill-Switch')} value={safety.kill_switch.is_active ? bi('已触发', 'TRIPPED') : bi('未触发', 'CLEAR')}
              tone={safety.kill_switch.is_active ? 'neg' : 'pos'} sub={bi(`触发次数 ${safety.kill_switch.trip_count} · 失效即停`, `trips ${safety.kill_switch.trip_count} · fail-closed`)} />
            <StatTile label={bi('盘前检查', 'Pre-Trade Checks')} value={String(safety.pre_trade_gate.checks_registered)}
              tone="info" sub={bi('未知即拒', 'fail-closed on unknown')} />
            <StatTile label={bi('实盘授权', 'Live Auth')} value={bi('禁用', 'DENIED')} tone="neg" sub="L2_DRY_RUN_ONLY" />
            <StatTile label={bi('人工审批', 'Human Approval')} value={safety.safety_config.require_human_approval ? bi('必需', 'REQUIRED') : 'OFF'}
              tone={safety.safety_config.require_human_approval ? 'pos' : 'neg'} />
          </KpiStrip>
          <div className="mt-3 flex flex-wrap gap-2">
            {([
              ['live_trading_enabled', 'LIVE_TRADING'], ['real_broker_enabled', 'REAL_BROKER'],
              ['allow_order_submission', 'ORDER_SUBMIT'], ['allow_ai_direct_trading', 'AI_DIRECT_TRADE'],
            ] as const).map(([k, label]) => (
              <span key={k} className="flex items-center gap-1.5 rounded-md border border-line bg-inset/40 px-2.5 py-1 text-2xs">
                <GateDot ok={!safety.safety_config[k]} label="" />
                <span className="mono text-faint">{label}</span>
                <span className={safety.safety_config[k] ? 'text-neg' : 'text-pos'}>{safety.safety_config[k] ? 'ON' : 'OFF'}</span>
              </span>
            ))}
          </div>
          <div className="mt-2 text-2xs text-faint">
            {bi('只读:此处从不武装/复位熔断,从不下单 — 复位仅人工、事件绑定。真实 services/risk/pre_trade。',
              'Read-only: never arms/resets the switch, never places an order — reset is human-only, incident-bound. Real services/risk/pre_trade.')}
          </div>
        </Panel>
      )}

      <KpiStrip cols={5}>
        <StatTile label={bi('已选 Alpha', 'Selected Alpha')} value={selectedAlphaId} accent />
        <StatTile label={bi('风控决策', 'Risk Decision')}
          value={<StatusChip value={primaryReport.decision} />} tone={decisionTone} />
        <StatTile label={bi('门禁通过', 'Gates Passed')} value={`${num(passed)}/${num(total)}`}
          tone={passed >= total ? 'pos' : 'warn'} sub={bi(`${total - passed} 项受阻`, `${total - passed} blocked`)} />
        <StatTile label={bi('通过率', 'Pass Rate')} value={pct(total ? passed / total : 0, 0)}
          tone={passed >= total ? 'pos' : 'warn'} />
        <StatTile label={bi('破限门数', 'Breached Gates')} value={num(failedGates.length)}
          tone={failedGates.length ? 'neg' : 'pos'}
          sub={failedGates.length ? failedGates.map(gateLabel).join(', ') : bi('无', 'none')} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('门禁评分', 'Gate Score')} title={bi('风控门通过率', 'Risk Gate Pass Rate')}
          tone={passed >= total ? 'pos' : 'warn'}>
          <div className="flex flex-col items-center gap-3">
            <RadialGauge value={total ? passed / total : 0} max={1} size={168}
              tone={passed >= total ? 'pos' : passed / Math.max(total, 1) >= 0.66 ? 'warn' : 'neg'}
              label={bi('门禁', 'gates')} />
            <div className="flex items-center gap-2">
              <StatusChip value={primaryReport.decision} />
              <span className="tnum text-xs text-muted">{num(passed)} / {num(total)}</span>
            </div>
          </div>
        </Panel>

        <Panel className="xl:col-span-2" eyebrow={bi('九引擎', '9 Engines')}
          title={bi('风险门禁矩阵', 'Risk Gate Matrix')}
          right={<GateDot ok={failedGates.length === 0} label={failedGates.length === 0
            ? bi('全部通过', 'All pass') : bi(`${failedGates.length} 项破限`, `${failedGates.length} breached`)} />}>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {gateEntries.map(([g, ok]) => (
              <div key={g}
                className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2.5"
                style={{ borderColor: ok ? 'var(--line)' : 'color-mix(in srgb, var(--neg) 45%, var(--line))',
                  background: ok ? 'transparent' : 'color-mix(in srgb, var(--neg) 7%, transparent)' }}>
                <span className="truncate text-xs text-ink">{gateLabel(g)}</span>
                <GateDot ok={ok} label={ok ? bi('通过', 'PASS') : bi('破限', 'BREACH')} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('归因', 'Attribution')}
          title={bi('门禁通过/破限分布', 'Gate Pass / Breach Spread')}>
          <DivergingBars items={gateBars} valueFormat={v => (v >= 0 ? bi('通过', 'pass') : bi('破限', 'breach'))} />
        </Panel>

        <Panel eyebrow={bi('Alpha 画像', 'Alpha Profile')} title={bi('候选因子指标', 'Candidate Metrics')}>
          {alpha ? (
            <KeyValue cols={2} items={[
              { k: bi('候选 ID', 'Candidate'), v: alpha.candidate_id },
              { k: bi('生命周期', 'Lifecycle'), v: <StatusChip value={alpha.lifecycle_state} /> },
              { k: 'IC', v: fixed(alpha.ic, 3) },
              { k: bi('秩 IC', 'Rank IC'), v: fixed(alpha.rank_ic, 3) },
              { k: 'ICIR', v: fixed(alpha.icir, 2) },
              { k: 'Sharpe', v: fixed(alpha.sharpe, 2) },
              { k: bi('换手', 'Turnover'), v: pct(alpha.turnover, 1) },
              { k: bi('衰减半衰期', 'Decay HL'), v: fixed(alpha.decay_half_life, 1) },
              { k: bi('综合分', 'Composite'), v: fixed(alpha.composite_score, 3) },
              { k: bi('相关惩罚', 'Corr Penalty'), v: fixed(alpha.correlation_penalty, 3) },
            ]} />
          ) : (
            <KeyValue cols={1} items={[
              { k: bi('已选 Alpha', 'Selected Alpha'), v: selectedAlphaId },
              { k: bi('主组合', 'Primary Portfolio'), v: primaryReport.portfolio_id },
              { k: bi('风控运行', 'Risk Run'), v: primaryReport.run_id },
              { k: bi('数据源', 'Source'), v: bi('前端 mock · 无 Alpha 画像', 'frontend mock · no alpha profile') },
            ]} />
          )}
        </Panel>
      </div>

      {primaryReport.reasons.length > 0 && (
        <Panel eyebrow={bi('失败即拒', 'Fail-Closed')} title={bi('拒绝原因', 'Rejection Reasons')} tone="neg">
          <div className="flex flex-col gap-2">
            {primaryReport.reasons.map((reason, i) => (
              <div key={i} className="flex items-start gap-2 rounded-md border border-neg/30 bg-neg/5 px-3 py-2">
                <span className="mt-0.5 text-neg">⚠</span>
                <span className="text-xs text-ink">{reason}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <Panel eyebrow={bi('风控运行', 'Risk Runs')} title={bi('风控报告(只读)', 'Risk Reports (read-only)')}
        right={<Chip label={bi(`${reports.length} 份`, `${reports.length} reports`)} tone="info" />}>
        <DataTable columns={reportCols} rows={reports} getKey={r => r.run_id} />
        {reproHash && (
          <div className="mt-3 flex items-center gap-2 text-2xs text-faint">
            <span className="uppercase tracking-wide">{bi('复现哈希', 'Reproducibility Hash')}</span>
            <span className="mono text-muted">{shortHash(reproHash, 16)}</span>
          </div>
        )}
      </Panel>

      {usingBackend && payload!.lineage.length > 0 && (
        <Panel eyebrow={bi('血缘', 'Lineage')} title={bi('计算血缘', 'Calculation Lineage')}>
          <DataTable
            dense
            rows={payload!.lineage}
            getKey={l => l.id}
            columns={[
              { key: 'id', header: 'ID', mono: true, render: l => l.id },
              { key: 'layer', header: bi('层', 'Layer'), render: l => <Chip label={l.layer} tone="info" /> },
              { key: 'formula', header: bi('公式', 'Formula'), mono: true, render: l => l.formula },
              { key: 'backend', header: bi('后端', 'Backend'), mono: true, render: l => l.backend },
              { key: 'output', header: bi('输出', 'Output'), mono: true, render: l => l.output },
            ] as Column<CalculationLineageItem>[]}
          />
        </Panel>
      )}
    </PageShell>
  );
}
