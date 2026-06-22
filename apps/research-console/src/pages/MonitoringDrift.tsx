import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchMonitoringDriftSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { MonitoringDriftPayload, MonitoringDriftRecord } from '@/lib/types/research';

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
    {monitor_id: 'MON-ALPHA-FB-001', category: 'ALPHA_DECAY', subject_id: 'alpha-fallback-1', metric: 'ic_drift', baseline: 0.052, current: 0.039, drift_score: 0.25, warn_threshold: 0.18, breach_threshold: 0.32, status: 'WATCH', owner: 'Alpha Research', evidence_ref: 'fallback-exp-alpha', last_checked: 1_781_222_400, remediation: 'Review alpha decay, retrain window, or retire candidate'},
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
    {monitor_id: 'MON-ALPHA-FB-001', category: 'ALPHA_DECAY', subject_id: 'alpha-fallback-1', metric: 'ic_drift', baseline: 0.052, current: 0.039, drift_score: 0.25, warn_threshold: 0.18, breach_threshold: 0.32, status: 'WATCH', owner: 'Alpha Research', evidence_ref: 'fallback-exp-alpha', last_checked: 1_781_222_400, remediation: 'Review alpha decay, retrain window, or retire candidate'},
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

function formatTime(seconds: number) {
  return new Date(seconds * 1000).toISOString().slice(0, 16).replace('T', ' ');
}

function numberText(value: number) {
  if (Math.abs(value) >= 10) {
    return value.toFixed(1);
  }
  return value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
}

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

function statusClass(status: string) {
  if (status === 'BREACH') {
    return 'border-red-800 bg-red-950/30 text-red-300';
  }
  if (status === 'WATCH') {
    return 'border-amber-800 bg-amber-950/30 text-amber-300';
  }
  return 'border-emerald-800 bg-emerald-950/30 text-emerald-300';
}

function statusTextClass(status: string) {
  if (status === 'BREACH') {
    return 'text-red-300';
  }
  if (status === 'WATCH') {
    return 'text-amber-300';
  }
  return 'text-emerald-300';
}

function driftBarClass(status: string) {
  if (status === 'BREACH') {
    return 'bg-red-600';
  }
  if (status === 'WATCH') {
    return 'bg-amber-500';
  }
  return 'bg-emerald-600';
}

function MonitorCard({monitor}: {monitor: MonitoringDriftRecord}) {
  const barWidth = Math.min(100, Math.max(3, Math.round((monitor.drift_score / Math.max(monitor.breach_threshold, 0.01)) * 100)));
  return (
    <div className={`rounded border p-3 text-xs ${statusClass(monitor.status)}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-bold text-slate-100">{monitor.monitor_id}</div>
          <div className="mt-1 break-words text-slate-500">{monitor.subject_id}</div>
        </div>
        <span className="flex-shrink-0 font-bold">{monitor.status}</span>
      </div>
      <div className="mt-3 grid gap-1 text-slate-400 md:grid-cols-2">
        <div>{bi('\u7c7b\u522b', 'Category')}: {monitor.category}</div>
        <div>{bi('\u6307\u6807', 'Metric')}: {monitor.metric}</div>
        <div>{bi('\u57fa\u7ebf', 'Baseline')}: {numberText(monitor.baseline)}</div>
        <div>{bi('\u5f53\u524d', 'Current')}: {numberText(monitor.current)}</div>
        <div>{bi('\u8d1f\u8d23\u4eba', 'Owner')}: {monitor.owner}</div>
        <div>{bi('\u68c0\u67e5', 'Checked')}: {formatTime(monitor.last_checked)}</div>
      </div>
      <div className="mt-3">
        <div className="mb-1 flex justify-between gap-2 text-slate-400">
          <span>drift {numberText(monitor.drift_score)}</span>
          <span>warn {numberText(monitor.warn_threshold)} / breach {numberText(monitor.breach_threshold)}</span>
        </div>
        <div className="h-2 overflow-hidden rounded bg-slate-900">
          <div className={`h-full ${driftBarClass(monitor.status)}`} style={{width: `${barWidth}%`}} />
        </div>
      </div>
      <div className="mt-3 break-words text-slate-300">{monitor.remediation}</div>
      <div className="mt-2 break-words text-slate-500">{bi('\u8bc1\u636e', 'Evidence')}: {monitor.evidence_ref}</div>
    </div>
  );
}

function StatusBoard({monitoring}: {monitoring: MonitoringDriftPayload}) {
  const statuses = ['OK', 'WATCH', 'BREACH'];
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u72b6\u6001\u5206\u5e03', 'Status Distribution')}</h2>
      <div className="space-y-3 text-xs">
        {statuses.map(status => {
          const count = monitoring.status_counts[status] ?? 0;
          const width = monitoring.summary.monitors > 0 ? Math.round((count / monitoring.summary.monitors) * 100) : 0;
          return (
            <div key={status}>
              <div className="mb-1 flex justify-between gap-2">
                <span className={statusTextClass(status)}>{status}</span>
                <span className="text-slate-400">{count} / {width}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded bg-slate-800">
                <div className={`h-full ${driftBarClass(status)}`} style={{width: `${Math.max(3, width)}%`}} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CategoryBoard({monitoring}: {monitoring: MonitoringDriftPayload}) {
  const categories = Object.entries(monitoring.category_counts).sort((a, b) => b[1] - a[1]);
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u76d1\u63a7\u7c7b\u522b', 'Monitor Categories')}</h2>
      <div className="grid gap-2 text-xs sm:grid-cols-2">
        {categories.map(([category, count]) => (
          <div key={category} className="rounded bg-slate-800 px-3 py-2">
            <div className="font-bold text-slate-200">{category}</div>
            <div className="mt-1 text-cyan-300">{count} {bi('\u6761', 'records')}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PolicyBoard({monitoring}: {monitoring: MonitoringDriftPayload}) {
  const policies: Array<[string, boolean]> = [
    ['READ_ONLY', monitoring.monitoring_policy.read_only],
    ['OWNER_REQUIRED', monitoring.monitoring_policy.requires_owner],
    ['THRESHOLDS_REQUIRED', monitoring.monitoring_policy.requires_thresholds],
    ['EVIDENCE_REF_REQUIRED', monitoring.monitoring_policy.requires_evidence_ref],
    ['IMMUTABLE_ALERTS', !monitoring.monitoring_policy.mutable_alerts],
    ['NO_LIVE_AUTHORIZATION', !monitoring.monitoring_policy.authorizes_live_trading],
  ];
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u76d1\u63a7\u7b56\u7565', 'Monitoring Policy')}</h2>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        {policies.map(([label, passed]) => (
          <div key={label} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
            <span className="text-slate-300">{label}</span>
            <span className={passed ? 'text-emerald-300' : 'text-red-300'}>{passed ? 'PASS' : 'FAIL'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CalculationLens() {
  const rows = [
    ['ALPHA_DECAY', 'abs(current_ic - baseline_ic) / max(abs(baseline_ic), 0.01)'],
    ['FEATURE_DRIFT', 'max(0, baseline_quality - current_quality)'],
    ['DATA_FRESHNESS', 'freshness_hours / 24'],
    ['MODEL_RISK_AGING', 'issue_age_hours / 120'],
    ['REVIEWER_SLA', 'overdue_hours / sla_hours'],
  ];
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u8ba1\u7b97\u900f\u660e\u5ea6', 'Calculation Lens')}</h2>
      <div className="space-y-2 text-xs">
        {rows.map(([category, formula]) => (
          <div key={category} className="rounded border border-slate-800 bg-slate-950/60 p-3">
            <div className="font-bold text-cyan-300">{category}</div>
            <div className="mt-1 break-words text-slate-400">{formula}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function MonitoringDrift() {
  const [payload, setPayload] = useState<MonitoringDriftPayload | null>(null);
  const [apiState, setApiState] = useState<ConsoleApiState>('disabled');
  const [apiError, setApiError] = useState<string | null>(null);
  const apiEnabled = isResearchConsoleApiEnabled();

  useEffect(() => {
    if (!apiEnabled) {
      setPayload(null);
      setApiState('disabled');
      setApiError(null);
      return;
    }

    const controller = new AbortController();
    setApiState('loading');
    setApiError(null);
    fetchMonitoringDriftSnapshot(controller.signal)
      .then(nextPayload => {
        setPayload(nextPayload);
        setApiState('connected');
      })
      .catch(error => {
        if (controller.signal.aborted) {
          return;
        }
        setPayload(null);
        setApiState('error');
        setApiError(error instanceof Error ? error.message : String(error));
      });

    return () => controller.abort();
  }, [apiEnabled]);

  const usingBackend = apiState === 'connected' && payload !== null;
  const monitoring = usingBackend && payload ? payload : fallbackMonitoring;
  const priorityMonitors = useMemo(
    () => [...monitoring.top_breaches, ...monitoring.watchlist].slice(0, 8),
    [monitoring.top_breaches, monitoring.watchlist],
  );
  const breachRatio = monitoring.summary.monitors > 0 ? monitoring.summary.breach / monitoring.summary.monitors : 0;

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('\u76d1\u63a7\u4e0e\u6f02\u79fb', 'Monitoring & Drift')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={monitoring.monitoring_mode} />
              <DecisionBadge decision={monitoring.summary.live_authorization_allowed ? 'APPROVED' : 'NO_GO'} />
            </div>
          </div>
          <div className="text-xs text-slate-500 lg:text-right">
            <div>{bi('\u6a21\u5f0f', 'Mode')}: {monitoring.monitoring_mode}</div>
            <div>{bi('\u5b9e\u76d8\u6388\u6743', 'Live authorization')}: {monitoring.summary.live_authorization_allowed ? 'enabled' : 'disabled'}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={monitoring.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('\u76d1\u63a7\u9879', 'Monitors')} value={monitoring.summary.monitors} />
        <MetricCard label="OK" value={monitoring.summary.ok} />
        <MetricCard label="WATCH" value={monitoring.summary.watch} />
        <MetricCard label="BREACH" value={monitoring.summary.breach} />
        <MetricCard label={bi('\u7c7b\u522b', 'Categories')} value={monitoring.summary.categories} />
        <MetricCard label={bi('\u963b\u65ad', 'Blocking')} value={monitoring.summary.blocking_monitors} />
        <MetricCard label={bi('\u5e73\u5747\u6f02\u79fb', 'Avg Drift')} value={pct(monitoring.summary.avg_drift_score)} />
        <MetricCard label={bi('\u7834\u53e3\u7387', 'Breach Rate')} value={pct(breachRatio)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u4f18\u5148\u5904\u7406', 'Priority Monitors')}</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {priorityMonitors.map(monitor => <MonitorCard key={monitor.monitor_id} monitor={monitor} />)}
          </div>
        </div>
        <div className="space-y-4">
          <StatusBoard monitoring={monitoring} />
          <CategoryBoard monitoring={monitoring} />
          <PolicyBoard monitoring={monitoring} />
          <CalculationLens />
        </div>
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900">
        <div className="border-b border-slate-800 p-4">
          <h2 className="text-sm uppercase text-slate-400">{bi('\u5168\u91cf\u76d1\u63a7\u660e\u7ec6', 'Full Monitor Ledger')}</h2>
        </div>
        <div className="overflow-auto">
          <table className="w-full min-w-[1100px] text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase">
                <th className="p-2 text-left">{bi('\u76d1\u63a7ID', 'Monitor ID')}</th>
                <th className="p-2 text-left">{bi('\u7c7b\u522b', 'Category')}</th>
                <th className="p-2 text-left">{bi('\u4e3b\u4f53', 'Subject')}</th>
                <th className="p-2 text-left">{bi('\u6307\u6807', 'Metric')}</th>
                <th className="p-2 text-right">{bi('\u57fa\u7ebf', 'Baseline')}</th>
                <th className="p-2 text-right">{bi('\u5f53\u524d', 'Current')}</th>
                <th className="p-2 text-right">Drift</th>
                <th className="p-2 text-right">Warn</th>
                <th className="p-2 text-right">Breach</th>
                <th className="p-2 text-left">{bi('\u72b6\u6001', 'Status')}</th>
                <th className="p-2 text-left">{bi('\u8d1f\u8d23\u4eba', 'Owner')}</th>
                <th className="p-2 text-left">{bi('\u8bc1\u636e', 'Evidence')}</th>
              </tr>
            </thead>
            <tbody>
              {monitoring.monitors.map(monitor => (
                <tr key={monitor.monitor_id} className="border-b border-slate-800/50">
                  <td className="p-2 font-bold text-cyan-300">{monitor.monitor_id}</td>
                  <td className="p-2 text-slate-300">{monitor.category}</td>
                  <td className="p-2 text-slate-400">{monitor.subject_id}</td>
                  <td className="p-2 text-slate-400">{monitor.metric}</td>
                  <td className="p-2 text-right text-slate-300">{numberText(monitor.baseline)}</td>
                  <td className="p-2 text-right text-slate-300">{numberText(monitor.current)}</td>
                  <td className="p-2 text-right text-slate-300">{numberText(monitor.drift_score)}</td>
                  <td className="p-2 text-right text-slate-500">{numberText(monitor.warn_threshold)}</td>
                  <td className="p-2 text-right text-slate-500">{numberText(monitor.breach_threshold)}</td>
                  <td className={`p-2 font-bold ${statusTextClass(monitor.status)}`}>{monitor.status}</td>
                  <td className="p-2 text-slate-400">{monitor.owner}</td>
                  <td className="p-2 text-slate-500">{monitor.evidence_ref}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
