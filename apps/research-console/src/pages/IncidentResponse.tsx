import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchIncidentResponseSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { IncidentResponseIncident, IncidentResponsePayload } from '@/lib/types/research';

const fallbackIncidentResponse: IncidentResponsePayload = {
  source: 'FRONTEND_INCIDENT_RESPONSE_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  incident_mode: 'READ_ONLY_INCIDENT_RESPONSE',
  summary: {
    incidents: 3,
    open_incidents: 3,
    critical: 1,
    high: 1,
    medium: 1,
    sla_breaches: 1,
    escalated: 2,
    avg_age_minutes: 126,
    external_alerts_sent: 0,
    live_authorization_allowed: false,
  },
  severity_counts: {CRITICAL: 1, HIGH: 1, MEDIUM: 1},
  state_counts: {ESCALATED: 1, SLA_BREACHED: 1, WATCH_REVIEW: 1},
  routing_counts: {MODEL_RISK_AGING: 1, FEATURE_DRIFT: 1, REVIEWER_SLA: 1},
  incidents: [
    {incident_id: 'INC-FB-001', source_monitor_id: 'MON-MRM-FB-001', category: 'MODEL_RISK_AGING', subject_id: 'MRM-FB-001', severity: 'CRITICAL', state: 'ESCALATED', trigger_status: 'BREACH', trigger_metric: 'issue_age_hours', drift_score: 0.7, owner: 'Model Governance', incident_commander: 'Chief Risk Officer', queue: 'Model Governance', opened_at: 1_781_222_400, due_at: 1_781_236_800, age_minutes: 180, sla_minutes: 240, due_in_minutes: 60, sla_breached: false, escalation_level: 'L3', route_id: 'ROUTE-MODEL-RISK', runbook_id: 'RB-MODEL-RISK', evidence_ref: 'fallback-model-risk', evidence_packet_hash: 'fallback-incident-001', containment_action: 'Block candidate promotion and require reviewer evidence before any phase advancement', next_action: 'Escalate model risk issue owner', external_alert_sent: false, blocks_live_authorization: true},
    {incident_id: 'INC-FB-002', source_monitor_id: 'MON-FEATURE-FB-001', category: 'FEATURE_DRIFT', subject_id: 'feature-fallback-1', severity: 'HIGH', state: 'SLA_BREACHED', trigger_status: 'BREACH', trigger_metric: 'quality_score_delta', drift_score: 0.041, owner: 'Research Platform', incident_commander: 'Data Platform Lead', queue: 'Research Platform Data Desk', opened_at: 1_781_222_700, due_at: 1_781_229_900, age_minutes: 150, sla_minutes: 120, due_in_minutes: -30, sla_breached: true, escalation_level: 'L2', route_id: 'ROUTE-FEATURE-DRIFT', runbook_id: 'RB-FEATURE-DRIFT', evidence_ref: 'fallback-feature-hash', evidence_packet_hash: 'fallback-incident-002', containment_action: 'Freeze affected feature materialization', next_action: 'Run feature distribution audit', external_alert_sent: false, blocks_live_authorization: true},
    {incident_id: 'INC-FB-003', source_monitor_id: 'MON-SLA-FB-001', category: 'REVIEWER_SLA', subject_id: 'RW-FB-001', severity: 'MEDIUM', state: 'WATCH_REVIEW', trigger_status: 'WATCH', trigger_metric: 'overdue_hours', drift_score: 0, owner: 'Compliance', incident_commander: 'Compliance', queue: 'Reviewer Coordination', opened_at: 1_781_223_000, due_at: 1_781_226_600, age_minutes: 48, sla_minutes: 60, due_in_minutes: 12, sla_breached: false, escalation_level: 'L1', route_id: 'ROUTE-REVIEWER-SLA', runbook_id: 'RB-REVIEWER-SLA', evidence_ref: 'fallback-reviewer', evidence_packet_hash: 'fallback-incident-003', containment_action: 'Keep Phase 3A authorization blocked', next_action: 'Track blocking task closure', external_alert_sent: false, blocks_live_authorization: true},
  ],
  routing_rules: [
    {route_id: 'ROUTE-MODEL-RISK', category: 'MODEL_RISK_AGING', queue: 'Model Governance', primary_owner: 'Model Governance', escalation_owner: 'Chief Risk Officer', sla_minutes: 240, escalation_after_minutes: 180, runbook_id: 'RB-MODEL-RISK', notification_mode: 'READ_ONLY_NO_EXTERNAL_SEND'},
    {route_id: 'ROUTE-FEATURE-DRIFT', category: 'FEATURE_DRIFT', queue: 'Research Platform Data Desk', primary_owner: 'Research Platform', escalation_owner: 'Data Platform Lead', sla_minutes: 120, escalation_after_minutes: 90, runbook_id: 'RB-FEATURE-DRIFT', notification_mode: 'READ_ONLY_NO_EXTERNAL_SEND'},
    {route_id: 'ROUTE-REVIEWER-SLA', category: 'REVIEWER_SLA', queue: 'Reviewer Coordination', primary_owner: 'Compliance', escalation_owner: 'Model Governance / Compliance', sla_minutes: 60, escalation_after_minutes: 45, runbook_id: 'RB-REVIEWER-SLA', notification_mode: 'READ_ONLY_NO_EXTERNAL_SEND'},
  ],
  runbooks: [
    {runbook_id: 'RB-MODEL-RISK', category: 'MODEL_RISK_AGING', title: 'MODEL_RISK_AGING response playbook', objective: 'Contain model governance drift', steps: ['Escalate independent validation owner', 'Confirm linked model limitations', 'Update issue register remediation plan'], requires_postmortem: true, mutable_runbook: false},
    {runbook_id: 'RB-FEATURE-DRIFT', category: 'FEATURE_DRIFT', title: 'FEATURE_DRIFT response playbook', objective: 'Contain feature quality drift', steps: ['Freeze affected feature materialization', 'Run point-in-time distribution audit', 'Attach feature quality evidence'], requires_postmortem: false, mutable_runbook: false},
    {runbook_id: 'RB-REVIEWER-SLA', category: 'REVIEWER_SLA', title: 'REVIEWER_SLA response playbook', objective: 'Contain reviewer workflow drift', steps: ['Escalate overdue reviewer assignment', 'Confirm blocking gate ownership', 'Keep Phase 3A authorization blocked'], requires_postmortem: true, mutable_runbook: false},
  ],
  timeline: [
    {event_id: 'ITE-FB-001', incident_id: 'INC-FB-001', timestamp: 1_781_222_400, event_type: 'DETECTED', actor: 'Monitoring & Drift', detail: 'MODEL_RISK_AGING BREACH trigger', evidence_ref: 'fallback-model-risk'},
    {event_id: 'ITE-FB-002', incident_id: 'INC-FB-001', timestamp: 1_781_222_460, event_type: 'ROUTED', actor: 'Read-only incident router', detail: 'Routed to Model Governance', evidence_ref: 'fallback-incident-001'},
    {event_id: 'ITE-FB-003', incident_id: 'INC-FB-002', timestamp: 1_781_222_760, event_type: 'SLA_EVALUATED', actor: 'Incident Response', detail: 'SLA breached=true; escalation=L2', evidence_ref: 'fallback-incident-002'},
  ],
  incident_policy: {
    read_only: true,
    requires_evidence_ref: true,
    requires_sla: true,
    requires_owner: true,
    requires_postmortem_for_critical: true,
    external_notifications_enabled: false,
    mutable_incidents: false,
    authorizes_live_trading: false,
  },
};

function formatTime(seconds: number) {
  return new Date(seconds * 1000).toISOString().slice(0, 16).replace('T', ' ');
}

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

function severityClass(severity: string) {
  if (severity === 'CRITICAL') {
    return 'border-red-800 bg-red-950/30 text-red-300';
  }
  if (severity === 'HIGH') {
    return 'border-orange-800 bg-orange-950/30 text-orange-300';
  }
  return 'border-amber-800 bg-amber-950/30 text-amber-300';
}

function stateTextClass(state: string) {
  if (state === 'ESCALATED' || state === 'SLA_BREACHED') {
    return 'text-red-300';
  }
  if (state === 'TRIAGE' || state === 'WATCH_REVIEW') {
    return 'text-amber-300';
  }
  return 'text-emerald-300';
}

function IncidentCard({incident}: {incident: IncidentResponseIncident}) {
  const elapsedPct = Math.min(100, Math.max(3, Math.round((incident.age_minutes / Math.max(incident.sla_minutes, 1)) * 100)));
  return (
    <div className={`rounded border p-3 text-xs ${severityClass(incident.severity)}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-bold text-slate-100">{incident.incident_id} / {incident.category}</div>
          <div className="mt-1 break-words text-slate-500">{incident.source_monitor_id} {'->'} {incident.subject_id}</div>
        </div>
        <div className="flex-shrink-0 text-right">
          <div className="font-bold">{incident.severity}</div>
          <div className={stateTextClass(incident.state)}>{incident.state}</div>
        </div>
      </div>
      <div className="mt-3 grid gap-1 text-slate-400 md:grid-cols-2">
        <div>{bi('\u961f\u5217', 'Queue')}: {incident.queue}</div>
        <div>{bi('\u8d1f\u8d23\u4eba', 'Owner')}: {incident.owner}</div>
        <div>{bi('\u6307\u6325', 'Commander')}: {incident.incident_commander}</div>
        <div>{bi('\u5347\u7ea7', 'Escalation')}: {incident.escalation_level}</div>
      </div>
      <div className="mt-3">
        <div className="mb-1 flex justify-between gap-2 text-slate-400">
          <span>SLA {incident.age_minutes}/{incident.sla_minutes}m</span>
          <span className={incident.sla_breached ? 'text-red-300' : 'text-emerald-300'}>{incident.sla_breached ? 'BREACHED' : `${incident.due_in_minutes}m left`}</span>
        </div>
        <div className="h-2 overflow-hidden rounded bg-slate-900">
          <div className={incident.sla_breached ? 'h-full bg-red-600' : 'h-full bg-cyan-600'} style={{width: `${elapsedPct}%`}} />
        </div>
      </div>
      <div className="mt-3 break-words text-slate-300">{incident.next_action}</div>
      <div className="mt-2 break-words text-slate-500">{bi('\u8bc1\u636e', 'Evidence')}: {incident.evidence_ref}</div>
    </div>
  );
}

function CountBoard({title, counts}: {title: string; counts: Record<string, number>}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{title}</h2>
      <div className="grid gap-2 text-xs sm:grid-cols-2">
        {Object.entries(counts).map(([key, value]) => (
          <div key={key} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
            <span className="break-words text-slate-300">{key}</span>
            <span className="text-cyan-300">{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PolicyBoard({incidentResponse}: {incidentResponse: IncidentResponsePayload}) {
  const policies: Array<[string, boolean]> = [
    ['READ_ONLY', incidentResponse.incident_policy.read_only],
    ['EVIDENCE_REF_REQUIRED', incidentResponse.incident_policy.requires_evidence_ref],
    ['SLA_REQUIRED', incidentResponse.incident_policy.requires_sla],
    ['OWNER_REQUIRED', incidentResponse.incident_policy.requires_owner],
    ['CRITICAL_POSTMORTEM_REQUIRED', incidentResponse.incident_policy.requires_postmortem_for_critical],
    ['EXTERNAL_NOTIFICATIONS_DISABLED', !incidentResponse.incident_policy.external_notifications_enabled],
    ['IMMUTABLE_INCIDENTS', !incidentResponse.incident_policy.mutable_incidents],
    ['NO_LIVE_AUTHORIZATION', !incidentResponse.incident_policy.authorizes_live_trading],
  ];
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u54cd\u5e94\u7b56\u7565', 'Response Policy')}</h2>
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

function RunbookBoard({incidentResponse}: {incidentResponse: IncidentResponsePayload}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u5904\u7f6e\u5267\u672c', 'Runbooks')}</h2>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {incidentResponse.runbooks.map(runbook => (
          <div key={runbook.runbook_id} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs">
            <div className="font-bold text-cyan-300">{runbook.runbook_id}</div>
            <div className="mt-1 text-slate-300">{runbook.category}</div>
            <div className="mt-2 text-slate-500">{runbook.objective}</div>
            <ol className="mt-3 list-decimal space-y-1 pl-4 text-slate-400">
              {runbook.steps.slice(0, 4).map(step => <li key={step}>{step}</li>)}
            </ol>
            <div className="mt-3 text-slate-500">{bi('\u4e8b\u540e\u590d\u76d8', 'Postmortem')}: {runbook.requires_postmortem ? 'required' : 'not required'}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function IncidentResponse() {
  const [payload, setPayload] = useState<IncidentResponsePayload | null>(null);
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
    fetchIncidentResponseSnapshot(controller.signal)
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
  const incidentResponse = usingBackend && payload ? payload : fallbackIncidentResponse;
  const priorityIncidents = useMemo(
    () => [...incidentResponse.incidents].sort((a, b) => {
      const severityRank: Record<string, number> = {CRITICAL: 3, HIGH: 2, MEDIUM: 1};
      return (severityRank[b.severity] ?? 0) - (severityRank[a.severity] ?? 0) || Number(b.sla_breached) - Number(a.sla_breached);
    }).slice(0, 6),
    [incidentResponse.incidents],
  );
  const slaBreachRate = incidentResponse.summary.incidents > 0 ? incidentResponse.summary.sla_breaches / incidentResponse.summary.incidents : 0;

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('\u544a\u8b66\u4e8b\u4ef6\u54cd\u5e94', 'Incident Response')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={incidentResponse.incident_mode} />
              <DecisionBadge decision={incidentResponse.summary.live_authorization_allowed ? 'APPROVED' : 'NO_GO'} />
            </div>
          </div>
          <div className="text-xs text-slate-500 lg:text-right">
            <div>{bi('\u6a21\u5f0f', 'Mode')}: {incidentResponse.incident_mode}</div>
            <div>{bi('\u5916\u90e8\u901a\u77e5', 'External alerts')}: {incidentResponse.incident_policy.external_notifications_enabled ? 'enabled' : 'disabled'}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={incidentResponse.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('\u4e8b\u4ef6', 'Incidents')} value={incidentResponse.summary.incidents} />
        <MetricCard label="CRITICAL" value={incidentResponse.summary.critical} />
        <MetricCard label="HIGH" value={incidentResponse.summary.high} />
        <MetricCard label="MEDIUM" value={incidentResponse.summary.medium} />
        <MetricCard label={bi('SLA\u7834\u53e3', 'SLA Breach')} value={incidentResponse.summary.sla_breaches} />
        <MetricCard label={bi('\u5df2\u5347\u7ea7', 'Escalated')} value={incidentResponse.summary.escalated} />
        <MetricCard label={bi('\u5e73\u5747\u5e74\u9f84', 'Avg Age')} value={incidentResponse.summary.avg_age_minutes} unit="m" />
        <MetricCard label={bi('\u7834\u53e3\u7387', 'Breach Rate')} value={pct(slaBreachRate)} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u4f18\u5148\u4e8b\u4ef6', 'Priority Incidents')}</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {priorityIncidents.map(incident => <IncidentCard key={incident.incident_id} incident={incident} />)}
          </div>
        </div>
        <div className="space-y-4">
          <CountBoard title={bi('\u4e25\u91cd\u5ea6', 'Severity')} counts={incidentResponse.severity_counts} />
          <CountBoard title={bi('\u72b6\u6001', 'State')} counts={incidentResponse.state_counts} />
          <PolicyBoard incidentResponse={incidentResponse} />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="rounded-lg border border-slate-800 bg-slate-900">
          <div className="border-b border-slate-800 p-4">
            <h2 className="text-sm uppercase text-slate-400">{bi('\u8def\u7531\u89c4\u5219', 'Routing Rules')}</h2>
          </div>
          <div className="overflow-auto">
            <table className="w-full min-w-[760px] text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 uppercase">
                  <th className="p-2 text-left">{bi('\u7c7b\u522b', 'Category')}</th>
                  <th className="p-2 text-left">{bi('\u961f\u5217', 'Queue')}</th>
                  <th className="p-2 text-left">{bi('\u8d1f\u8d23\u4eba', 'Owner')}</th>
                  <th className="p-2 text-right">SLA</th>
                  <th className="p-2 text-left">{bi('\u901a\u77e5', 'Notify')}</th>
                </tr>
              </thead>
              <tbody>
                {incidentResponse.routing_rules.map(rule => (
                  <tr key={rule.route_id} className="border-b border-slate-800/50">
                    <td className="p-2 font-bold text-cyan-300">{rule.category}</td>
                    <td className="p-2 text-slate-300">{rule.queue}</td>
                    <td className="p-2 text-slate-400">{rule.primary_owner}</td>
                    <td className="p-2 text-right text-slate-300">{rule.sla_minutes}m</td>
                    <td className="p-2 text-slate-500">{rule.notification_mode}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900">
          <div className="border-b border-slate-800 p-4">
            <h2 className="text-sm uppercase text-slate-400">{bi('\u65f6\u95f4\u7ebf', 'Timeline')}</h2>
          </div>
          <div className="max-h-[420px] overflow-auto">
            <table className="w-full min-w-[820px] text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 uppercase">
                  <th className="p-2 text-left">{bi('\u65f6\u95f4', 'Time')}</th>
                  <th className="p-2 text-left">{bi('\u4e8b\u4ef6', 'Incident')}</th>
                  <th className="p-2 text-left">{bi('\u7c7b\u578b', 'Type')}</th>
                  <th className="p-2 text-left">{bi('\u6267\u884c\u8005', 'Actor')}</th>
                  <th className="p-2 text-left">{bi('\u8bf4\u660e', 'Detail')}</th>
                </tr>
              </thead>
              <tbody>
                {incidentResponse.timeline.slice(0, 24).map(event => (
                  <tr key={event.event_id} className="border-b border-slate-800/50">
                    <td className="p-2 text-slate-400">{formatTime(event.timestamp)}</td>
                    <td className="p-2 font-bold text-cyan-300">{event.incident_id}</td>
                    <td className="p-2 text-slate-300">{event.event_type}</td>
                    <td className="p-2 text-slate-400">{event.actor}</td>
                    <td className="p-2 text-slate-500">{event.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <RunbookBoard incidentResponse={incidentResponse} />

      <div className="rounded-lg border border-slate-800 bg-slate-900">
        <div className="border-b border-slate-800 p-4">
          <h2 className="text-sm uppercase text-slate-400">{bi('\u5168\u91cf\u4e8b\u4ef6\u53f0\u8d26', 'Full Incident Ledger')}</h2>
        </div>
        <div className="overflow-auto">
          <table className="w-full min-w-[1180px] text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400 uppercase">
                <th className="p-2 text-left">{bi('\u4e8b\u4ef6ID', 'Incident')}</th>
                <th className="p-2 text-left">{bi('\u76d1\u63a7', 'Monitor')}</th>
                <th className="p-2 text-left">{bi('\u4e3b\u4f53', 'Subject')}</th>
                <th className="p-2 text-left">{bi('\u4e25\u91cd\u5ea6', 'Severity')}</th>
                <th className="p-2 text-left">{bi('\u72b6\u6001', 'State')}</th>
                <th className="p-2 text-left">{bi('\u8def\u7531', 'Route')}</th>
                <th className="p-2 text-right">Age</th>
                <th className="p-2 text-right">SLA</th>
                <th className="p-2 text-left">{bi('\u963b\u65ad', 'Blocks')}</th>
                <th className="p-2 text-left">{bi('\u8bc1\u636e', 'Evidence')}</th>
              </tr>
            </thead>
            <tbody>
              {incidentResponse.incidents.map(incident => (
                <tr key={incident.incident_id} className="border-b border-slate-800/50">
                  <td className="p-2 font-bold text-cyan-300">{incident.incident_id}</td>
                  <td className="p-2 text-slate-300">{incident.source_monitor_id}</td>
                  <td className="p-2 text-slate-400">{incident.subject_id}</td>
                  <td className="p-2 text-slate-300">{incident.severity}</td>
                  <td className={`p-2 font-bold ${stateTextClass(incident.state)}`}>{incident.state}</td>
                  <td className="p-2 text-slate-500">{incident.route_id}</td>
                  <td className="p-2 text-right text-slate-300">{incident.age_minutes}m</td>
                  <td className={incident.sla_breached ? 'p-2 text-right text-red-300' : 'p-2 text-right text-slate-300'}>{incident.sla_minutes}m</td>
                  <td className={incident.blocks_live_authorization ? 'p-2 text-red-300' : 'p-2 text-emerald-300'}>{incident.blocks_live_authorization ? 'YES' : 'NO'}</td>
                  <td className="p-2 text-slate-500">{incident.evidence_packet_hash}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
