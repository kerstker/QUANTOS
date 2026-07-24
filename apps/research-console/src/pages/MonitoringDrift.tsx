import React, { useMemo } from 'react';
import { fetchMonitoringDriftSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { MonitoringDriftPayload, MonitoringDriftRecord } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, LoadingVeil, Callout, type Column,
} from '@/ui/primitives';
import { Donut, Histogram } from '@/ui/charts';
import { num, pct, fixed, stamp, statusTone, type Tone } from '@/ui/format';

const fallbackMonitoring: MonitoringDriftPayload = {
  source: 'FRONTEND_MONITORING_DRIFT_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  monitoring_mode: 'READ_ONLY_MONITORING_DRIFT',
  summary: {
    monitors: 6,
    ok: 2,
    watch: 2,
    breach: 2,
    categories: 5,
    blocking_monitors: 2,
    avg_drift_score: 0.214,
    live_authorization_allowed: false,
  },
  status_counts: {OK: 2, WATCH: 2, BREACH: 2},
  category_counts: {ALPHA_DECAY: 1, FEATURE_DRIFT: 1, DATA_FRESHNESS: 1, MODEL_RISK_AGING: 1, REVIEWER_SLA: 2},
  monitors: [
    {monitor_id: 'MON-ALPHA-FB-001', category: 'ALPHA_DECAY', subject_id: 'alpha-fallback-1', metric: 'health_drift', baseline: 0.052, current: 0.039, drift_score: 0.25, warn_threshold: 0.18, breach_threshold: 0.32, status: 'WATCH', owner: 'Alpha Research', evidence_ref: 'fallback-exp-alpha', last_checked: 1_781_222_400, remediation: 'Review alpha decay, retrain window, or retire candidate'},
    {monitor_id: 'MON-FEATURE-FB-001', category: 'FEATURE_DRIFT', subject_id: 'feature-fallback-1', metric: 'quality_score_delta', baseline: 0.982, current: 0.941, drift_score: 0.041, warn_threshold: 0.02, breach_threshold: 0.04, status: 'BREACH', owner: 'Research Platform', evidence_ref: 'fallback-feature-hash', last_checked: 1_781_222_640, remediation: 'Run feature distribution audit and point-in-time sample check'},
    {monitor_id: 'MON-DATA-FB-001', category: 'DATA_FRESHNESS', subject_id: 'dataset-fallback-1', metric: 'freshness_hours', baseline: 6, current: 18, drift_score: 0.75, warn_threshold: 12, breach_threshold: 24, status: 'WATCH', owner: 'Data Platform', evidence_ref: 'fallback-data-hash', last_checked: 1_781_222_880, remediation: 'Refresh source snapshot and rerun data quality checks'},
    {monitor_id: 'MON-MRM-FB-001', category: 'MODEL_RISK_AGING', subject_id: 'MRM-FB-001', metric: 'issue_age_hours', baseline: 0, current: 84, drift_score: 0.7, warn_threshold: 48, breach_threshold: 72, status: 'BREACH', owner: 'Model Governance', evidence_ref: 'fallback-model-risk', last_checked: 1_781_223_120, remediation: 'Escalate model risk issue owner and update reviewer workflow'},
    {monitor_id: 'MON-SLA-FB-001', category: 'REVIEWER_SLA', subject_id: 'RW-FB-001', metric: 'overdue_hours', baseline: 0, current: 0, drift_score: 0, warn_threshold: 0, breach_threshold: 1, status: 'OK', owner: 'Risk Reviewer', evidence_ref: 'fallback-reviewer-1', last_checked: 1_781_223_360, remediation: 'Track blocking task closure'},
    {monitor_id: 'MON-SLA-FB-002', category: 'REVIEWER_SLA', subject_id: 'RW-FB-002', metric: 'overdue_hours', baseline: 0, current: 0, drift_score: 0, warn_threshold: 0, breach_threshold: 1, status: 'OK', owner: 'Compliance Reviewer', evidence_ref: 'fallback-reviewer-2', last_checked: 1_781_223_600, remediation: 'Track blocking task closure'},
  ],
  top_breaches: [
    {monitor_id: 'MON-MRM-FB-001', category: 'MODEL_RISK_AGING', subject_id: 'MRM-FB-001', metric: 'issue_age_hours', baseline: 0, current: 84, drift_score: 0.7, warn_threshold: 48, breach_threshold: 72, status: 'BREACH', owner: 'Model Governance', evidence_ref: 'fallback-model-risk', last_checked: 1_781_223_120, remediation: 'Escalate model risk issue owner and update reviewer workflow'},
    {monitor_id: 'MON-FEATURE-FB-001', category: 'FEATURE_DRIFT', subject_id: 'feature-fallback-1', metric: 'quality_score_delta', baseline: 0.982, current: 0.941, drift_score: 0.041, warn_threshold: 0.02, breach_threshold: 0.04, status: 'BREACH', owner: 'Research Platform', evidence_ref: 'fallback-feature-hash', last_checked: 1_781_222_640, remediation: 'Run feature distribution audit and point-in-time sample check'},
  ],
  watchlist: [
    {monitor_id: 'MON-DATA-FB-001', category: 'DATA_FRESHNESS', subject_id: 'dataset-fallback-1', metric: 'freshness_hours', baseline: 6, current: 18, drift_score: 0.75, warn_threshold: 12, breach_threshold: 24, status: 'WATCH', owner: 'Data Platform', evidence_ref: 'fallback-data-hash', last_checked: 1_781_222_880, remediation: 'Refresh source snapshot and rerun data quality checks'},
    {monitor_id: 'MON-ALPHA-FB-001', category: 'ALPHA_DECAY', subject_id: 'alpha-fallback-1', metric: 'health_drift', baseline: 0.052, current: 0.039, drift_score: 0.25, warn_threshold: 0.18, breach_threshold: 0.32, status: 'WATCH', owner: 'Alpha Research', evidence_ref: 'fallback-exp-alpha', last_checked: 1_781_222_400, remediation: 'Review alpha decay, retrain window, or retire candidate'},
  ],
  monitoring_policy: {
    read_only: true,
    requires_owner: true,
    requires_thresholds: true,
    requires_evidence_ref: true,
    mutable_alerts: false,
    authorizes_live_trading: false,
  },
};

/** Drift score relative to its own breach threshold — a normalized 0..1 pressure gauge. */
function driftPressure(m: MonitoringDriftRecord): number {
  return Math.min(1, m.drift_score / Math.max(m.breach_threshold, 0.01));
}

const statusOrder: Record<string, number> = {BREACH: 0, WATCH: 1, OK: 2};
const statusZh: Record<string, string> = {
  OK: bi('正常', 'OK'), WATCH: bi('观察', 'WATCH'), BREACH: bi('破口', 'BREACH'),
};
const statusDonutTone: Record<string, Tone> = {OK: 'pos', WATCH: 'warn', BREACH: 'neg'};

function monitorColumns(): Column<MonitoringDriftRecord>[] {
  return [
    { key: 'monitor_id', header: bi('监控ID', 'Monitor'), mono: true,
      render: m => <span className="font-medium text-accent">{m.monitor_id}</span> },
    { key: 'category', header: bi('类别', 'Category'),
      render: m => <span className="text-muted">{m.category}</span> },
    { key: 'subject', header: bi('主体', 'Subject'), mono: true,
      render: m => <span className="text-muted">{m.subject_id}</span> },
    { key: 'metric', header: bi('指标', 'Metric'), mono: true,
      render: m => <span className="text-muted">{m.metric}</span> },
    { key: 'baseline', header: bi('基线', 'Baseline'), align: 'right', mono: true, sortable: true, sortAccessor: m => m.baseline,
      render: m => fixed(m.baseline, 3) },
    { key: 'current', header: bi('当前', 'Current'), align: 'right', mono: true, sortable: true, sortAccessor: m => m.current,
      render: m => fixed(m.current, 3) },
    { key: 'drift', header: bi('漂移压力', 'Drift Pressure'), width: '180px', sortable: true, sortAccessor: m => m.drift_score,
      render: m => (
        <div className="flex items-center gap-2">
          <ProgressBar value={driftPressure(m)} tone={statusTone(m.status)} />
          <span className="tnum w-10 text-right text-muted">{fixed(m.drift_score, 3)}</span>
        </div>
      ) },
    { key: 'thresholds', header: bi('警戒 / 破口', 'Warn / Breach'), align: 'right', mono: true,
      render: m => <span className="text-faint">{fixed(m.warn_threshold, 2)} / {fixed(m.breach_threshold, 2)}</span> },
    { key: 'status', header: bi('状态', 'Status'),
      render: m => <StatusChip value={m.status} /> },
    { key: 'owner', header: bi('负责人', 'Owner'),
      render: m => <span className="text-muted">{m.owner}</span> },
    { key: 'checked', header: bi('检查时间', 'Checked'), align: 'right', mono: true, sortable: true, sortAccessor: m => m.last_checked,
      render: m => <span className="text-faint">{stamp(m.last_checked)}</span> },
  ];
}

function PriorityRow({ m, tone }: { m: MonitoringDriftRecord; tone: Tone }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-line bg-elevated/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="mono truncate text-xs font-medium text-accent">{m.monitor_id}</span>
        <StatusChip value={m.status} />
      </div>
      {/* Compact card shows the category's raw `current` metric vs its breach threshold (coherent for
          time-based categories where both share units; the full ledger separates drift_score, current
          and thresholds into their own columns). The remediation line below names the real breach cause
          — for ALPHA_DECAY that is the degradation_monitor verdict (e.g. slippage / drawdown), which is
          why a row can read e.g. "live IC fine" yet still be BREACH. */}
      <div className="flex items-center justify-between gap-2 text-2xs text-faint">
        <span className="truncate">{m.category} · {m.metric}</span>
        <span className="tnum shrink-0">{fixed(m.current, 3)} / {fixed(m.breach_threshold, 2)}</span>
      </div>
      <ProgressBar value={driftPressure(m)} tone={tone} />
      <div className="truncate text-2xs text-muted">{m.owner} · {m.remediation}</div>
    </div>
  );
}

export default function MonitoringDrift() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<MonitoringDriftPayload>(
    s => fetchMonitoringDriftSnapshot(s));

  const monitoring = usingBackend ? payload! : fallbackMonitoring;
  const summary = monitoring.summary;
  const policy = monitoring.monitoring_policy;

  const breachRatio = summary.monitors > 0 ? summary.breach / summary.monitors : 0;

  const statusSegments = useMemo(() =>
    Object.entries(monitoring.status_counts)
      .sort((a, b) => (statusOrder[a[0]] ?? 9) - (statusOrder[b[0]] ?? 9))
      .map(([status, count]) => ({
        label: `${statusZh[status] ?? status} ${count}`,
        value: count,
        tone: statusDonutTone[status] ?? statusTone(status),
      })),
    [monitoring.status_counts],
  );

  const categoryBins = useMemo(() =>
    Object.entries(monitoring.category_counts)
      .sort((a, b) => b[1] - a[1])
      .map(([category, count]) => ({ label: category.replace(/_/g, ' '), count })),
    [monitoring.category_counts],
  );

  const sortedMonitors = useMemo(() =>
    [...monitoring.monitors].sort((a, b) =>
      (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9) || b.drift_score - a.drift_score),
    [monitoring.monitors],
  );

  const policyGates: Array<[string, boolean]> = [
    [bi('只读', 'READ_ONLY'), policy.read_only],
    [bi('需负责人', 'OWNER_REQUIRED'), policy.requires_owner],
    [bi('需阈值', 'THRESHOLDS_REQUIRED'), policy.requires_thresholds],
    [bi('需证据', 'EVIDENCE_REQUIRED'), policy.requires_evidence_ref],
    [bi('告警不可变', 'IMMUTABLE_ALERTS'), !policy.mutable_alerts],
    [bi('禁止实盘授权', 'NO_LIVE_AUTH'), !policy.authorizes_live_trading],
  ];

  return (
    <PageShell>
      <PageHeader title="Monitoring & Drift" titleZh="监控与漂移"
        subtitle={bi('只读漂移监控快照:告警不可变、不授权实盘;按破口/观察优先级排序',
          'Read-only drift surveillance snapshot — immutable alerts, no live authorization; ranked by breach / watch priority')}
        actions={
          <Chip
            label={summary.live_authorization_allowed ? bi('实盘授权:启用', 'LIVE AUTH: ON') : bi('实盘授权:禁用', 'LIVE AUTH: OFF')}
            tone={summary.live_authorization_allowed ? 'neg' : 'pos'} soft={false} />
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={monitoring.source} error={error} />

      <LoadingVeil active={apiState === 'loading'}>
      {/* Honest partial-real disclosure: the ALPHA_DECAY category's baseline + decay verdict are computed by
          the REAL degradation_monitor on real factor backtests; the live-drift side has no live/paper feed
          under L2_DRY_RUN_ONLY and is a placeholder. The other categories are synthetic baselines. */}
      <Callout tone="warn" summary={bi('诚实标注:ALPHA_DECAY 来自真引擎 · 其余类别为合成基线',
        'Honest note: ALPHA_DECAY is real · other categories are synthetic baselines')}>
        {bi('诚实标注:ALPHA_DECAY 的回测基线与衰退裁决来自真引擎(degradation_monitor,快照在场时);实时漂移在 L2_DRY_RUN_ONLY 下无实盘/纸面数据源,显示为占位。FEATURE_DRIFT / DATA_FRESHNESS / MODEL_RISK / REVIEWER_SLA 为合成基线,待相应持久层建成后升真。',
            'Honest note: ALPHA_DECAY baseline + decay verdict come from the real degradation_monitor (when the snapshot is present); live drift has no live/paper source under L2_DRY_RUN_ONLY and is shown as a placeholder. FEATURE_DRIFT / DATA_FRESHNESS / MODEL_RISK / REVIEWER_SLA are synthetic baselines pending their data layers.')}
      </Callout>

      <KpiStrip cols={7}>
        <StatTile label={bi('监控项', 'Monitors')} value={num(summary.monitors)} accent />
        <StatTile label={bi('正常', 'OK')} value={num(summary.ok)} tone="pos" />
        <StatTile label={bi('观察', 'Watch')} value={num(summary.watch)} tone="warn" />
        <StatTile label={bi('破口', 'Breach')} value={num(summary.breach)} tone="neg"
          sub={bi(`破口率 ${pct(breachRatio, 0)}`, `${pct(breachRatio, 0)} breach rate`)} />
        <StatTile label={bi('阻断监控', 'Blocking')} value={num(summary.blocking_monitors)} tone="neg" />
        <StatTile label={bi('类别', 'Categories')} value={num(summary.categories)} tone="info" />
        <StatTile label={bi('平均漂移', 'Avg Drift')} value={pct(summary.avg_drift_score)} tone="warn" />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('健康分布', 'Health')} title={bi('监控状态分布', 'Monitor Status Distribution')}>
          <div className="flex items-center gap-4">
            <Donut segments={statusSegments} size={148} />
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              {statusSegments.map(seg => (
                <div key={seg.label} className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2 text-xs text-muted">
                    <span className="h-2.5 w-2.5 rounded-sm" style={{ background: `var(--${seg.tone})` }} />
                    {seg.label.replace(/\s\d+$/, '')}
                  </span>
                  <span className="tnum text-xs font-semibold text-ink">{num(seg.value)}</span>
                </div>
              ))}
              <div className="mt-1 border-t border-line pt-2">
                <ProgressBar value={summary.breach} max={Math.max(summary.monitors, 1)} tone="neg" />
                <div className="mt-1 text-2xs text-faint">{bi('破口占比', 'Breach share')} · {pct(breachRatio, 0)}</div>
              </div>
            </div>
          </div>
        </Panel>

        <Panel className="xl:col-span-2" eyebrow={bi('类别', 'Categories')}
          title={bi('监控类别计数', 'Monitor Category Counts')}
          right={<Chip label={bi(`${categoryBins.length} 类`, `${categoryBins.length} cats`)} tone="info" />}>
          <Histogram bins={categoryBins} height={196} tone="info" />
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('优先级', 'Priority')} title={bi('破口监控', 'Top Breaches')}
          tone="neg"
          right={<Chip label={num(monitoring.top_breaches.length)} tone="neg" />}>
          {monitoring.top_breaches.length
            ? <div className="flex flex-col gap-2.5">
                {monitoring.top_breaches.map(m => <PriorityRow key={m.monitor_id} m={m} tone="neg" />)}
              </div>
            : <div className="py-6 text-center text-xs text-faint">{bi('无破口', 'No breaches')}</div>}
        </Panel>

        <Panel eyebrow={bi('优先级', 'Priority')} title={bi('观察清单', 'Watchlist')}
          tone="warn"
          right={<Chip label={num(monitoring.watchlist.length)} tone="warn" />}>
          {monitoring.watchlist.length
            ? <div className="flex flex-col gap-2.5">
                {monitoring.watchlist.map(m => <PriorityRow key={m.monitor_id} m={m} tone="warn" />)}
              </div>
            : <div className="py-6 text-center text-xs text-faint">{bi('观察清单为空', 'Watchlist empty')}</div>}
        </Panel>

        <Panel eyebrow={bi('治理', 'Governance')} title={bi('监控策略门', 'Monitoring Policy Gates')}>
          <div className="flex flex-col gap-2.5">
            {policyGates.map(([label, ok]) => (
              <div key={label} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-elevated/30 px-3 py-2">
                <span className="text-xs text-muted">{label}</span>
                <GateDot ok={ok} label={ok ? bi('通过', 'PASS') : bi('失败', 'FAIL')} />
              </div>
            ))}
            <div className="mt-1 rounded-lg border border-info/30 bg-info/5 px-3 py-2 text-2xs text-muted">
              {bi('模式', 'Mode')}: <span className="mono text-info">{monitoring.monitoring_mode}</span>
            </div>
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('全量明细', 'Full Ledger')} title={bi('全量监控明细', 'Full Monitor Ledger')}
        right={<Chip label={bi(`${sortedMonitors.length} 项`, `${sortedMonitors.length} monitors`)} tone="info" />}>
        <DataTable columns={monitorColumns()} rows={sortedMonitors} dense getKey={m => m.monitor_id}
          searchable={m => `${m.monitor_id} ${m.category} ${m.subject_id} ${m.metric} ${m.owner} ${m.status}`}
          searchPlaceholder={bi('搜索监控 ID / 类别 / 主体 / 负责人…', 'Search monitor / category / subject / owner…')} />
      </Panel>
      </LoadingVeil>
    </PageShell>
  );
}
