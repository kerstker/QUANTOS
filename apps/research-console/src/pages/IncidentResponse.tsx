import React, { useMemo } from 'react';
import { fetchIncidentResponseSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { IncidentResponseIncident, IncidentResponsePayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, Chip, GateDot, ProgressBar, KeyValue, type Column,
} from '@/ui/primitives';
import { Donut, Histogram } from '@/ui/charts';
import { num, pct, pctPoints, stamp, shortHash, statusTone, type Tone } from '@/ui/format';

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

const SEVERITY_RANK: Record<string, number> = {CRITICAL: 3, HIGH: 2, MEDIUM: 1};
const SEVERITY_TONE: Record<string, Tone> = {CRITICAL: 'neg', HIGH: 'warn', MEDIUM: 'info'};

const eventTypeZh: Record<string, string> = {
  DETECTED: bi('检测', 'Detected'),
  ROUTED: bi('已路由', 'Routed'),
  SLA_EVALUATED: bi('SLA评估', 'SLA Eval'),
  ESCALATED: bi('已升级', 'Escalated'),
};

function severityTone(s: string): Tone {
  return SEVERITY_TONE[s] ?? statusTone(s);
}

export default function IncidentResponse() {
  const { payload, apiState, error, usingBackend } =
    useConsoleSnapshot<IncidentResponsePayload>(s => fetchIncidentResponseSnapshot(s));

  const data = usingBackend ? payload! : fallbackIncidentResponse;
  const { summary, incident_policy } = data;

  const priorityIncidents = useMemo(
    () => [...data.incidents].sort((a, b) =>
      (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0)
      || Number(b.sla_breached) - Number(a.sla_breached)
      || b.drift_score - a.drift_score),
    [data.incidents],
  );

  const slaBreachRate = summary.incidents > 0 ? summary.sla_breaches / summary.incidents : 0;
  const escalatedRate = summary.incidents > 0 ? summary.escalated / summary.incidents : 0;

  const severityDonut = Object.entries(data.severity_counts)
    .sort((a, b) => (SEVERITY_RANK[b[0]] ?? 0) - (SEVERITY_RANK[a[0]] ?? 0))
    .map(([k, v]) => ({ label: k, value: v, tone: severityTone(k) }));

  const stateDonut = Object.entries(data.state_counts)
    .map(([k, v]) => ({ label: k, value: v, tone: statusTone(k) }));

  const routingBins = Object.entries(data.routing_counts)
    .map(([k, v]) => ({ label: k.replace(/_/g, ' ').slice(0, 12), count: v }));

  const policies: Array<[React.ReactNode, boolean]> = [
    [bi('只读', 'READ_ONLY'), incident_policy.read_only],
    [bi('需证据引用', 'EVIDENCE_REF'), incident_policy.requires_evidence_ref],
    [bi('需SLA', 'SLA_REQUIRED'), incident_policy.requires_sla],
    [bi('需负责人', 'OWNER_REQUIRED'), incident_policy.requires_owner],
    [bi('关键事件复盘', 'CRIT_POSTMORTEM'), incident_policy.requires_postmortem_for_critical],
    [bi('禁外部通知', 'NO_EXTERNAL_NOTIFY'), !incident_policy.external_notifications_enabled],
    [bi('不可变事件', 'IMMUTABLE'), !incident_policy.mutable_incidents],
    [bi('禁实盘授权', 'NO_LIVE_AUTH'), !incident_policy.authorizes_live_trading],
  ];

  const incidentCols: Column<IncidentResponseIncident>[] = [
    { key: 'id', header: bi('事件', 'Incident'), mono: true,
      render: r => <span className="font-semibold text-accent">{r.incident_id}</span> },
    { key: 'cat', header: bi('类别', 'Category'),
      render: r => <span className="text-muted">{r.category}</span> },
    { key: 'subj', header: bi('主体', 'Subject'), mono: true,
      render: r => <span className="text-muted">{r.subject_id}</span> },
    { key: 'sev', header: bi('严重度', 'Severity'),
      render: r => <Chip label={r.severity} tone={severityTone(r.severity)} /> },
    { key: 'state', header: bi('状态', 'State'),
      render: r => <StatusChip value={r.state} /> },
    { key: 'drift', header: bi('漂移', 'Drift'), align: 'right', mono: true,
      render: r => <span className={r.drift_score >= 0.5 ? 'text-neg' : 'text-ink'}>{r.drift_score > 0 ? r.drift_score.toFixed(3) : '—'}</span> },
    { key: 'sla', header: 'SLA', width: '160px',
      render: r => {
        const frac = Math.min(1, r.age_minutes / Math.max(r.sla_minutes, 1));
        return (
          <div className="flex items-center gap-2">
            <ProgressBar value={frac} tone={r.sla_breached ? 'neg' : frac >= 0.8 ? 'warn' : 'accent'} />
            <span className="tnum w-16 shrink-0 text-right text-2xs text-muted">{r.age_minutes}/{r.sla_minutes}m</span>
          </div>
        );
      } },
    { key: 'due', header: bi('剩余', 'Due In'), align: 'right', mono: true,
      render: r => <span className={r.due_in_minutes < 0 ? 'text-neg' : 'text-muted'}>{r.due_in_minutes >= 0 ? `${r.due_in_minutes}m` : `${r.due_in_minutes}m`}</span> },
    { key: 'breach', header: bi('破口', 'SLA'), align: 'center',
      render: r => <GateDot ok={!r.sla_breached} /> },
    { key: 'esc', header: bi('升级', 'Level'), align: 'center', mono: true,
      render: r => <span className="text-muted">{r.escalation_level}</span> },
    { key: 'blocks', header: bi('阻断实盘', 'Blocks Live'), align: 'center',
      render: r => <GateDot ok={r.blocks_live_authorization} /> },
    { key: 'evid', header: bi('证据', 'Evidence'), mono: true,
      render: r => <span className="text-faint">{shortHash(r.evidence_packet_hash)}</span> },
  ];

  const routingCols: Column<typeof data.routing_rules[number]>[] = [
    { key: 'cat', header: bi('类别', 'Category'),
      render: r => <span className="font-semibold text-accent">{r.category}</span> },
    { key: 'queue', header: bi('队列', 'Queue'), render: r => <span className="text-ink">{r.queue}</span> },
    { key: 'owner', header: bi('负责人', 'Owner'), render: r => <span className="text-muted">{r.primary_owner}</span> },
    { key: 'esc', header: bi('升级负责', 'Escalation'), render: r => <span className="text-muted">{r.escalation_owner}</span> },
    { key: 'sla', header: 'SLA', align: 'right', mono: true, render: r => `${r.sla_minutes}m` },
    { key: 'after', header: bi('升级阈值', 'Esc. After'), align: 'right', mono: true, render: r => `${r.escalation_after_minutes}m` },
    { key: 'notify', header: bi('通知', 'Notify'),
      render: r => <Chip label={r.notification_mode} tone="info" /> },
  ];

  const timelineCols: Column<typeof data.timeline[number]>[] = [
    { key: 'time', header: bi('时间', 'Time'), mono: true,
      render: e => <span className="text-muted">{stamp(e.timestamp)}</span> },
    { key: 'inc', header: bi('事件', 'Incident'), mono: true,
      render: e => <span className="font-semibold text-accent">{e.incident_id}</span> },
    { key: 'type', header: bi('类型', 'Type'),
      render: e => <Chip label={eventTypeZh[e.event_type] ?? e.event_type} tone={statusTone(e.event_type)} /> },
    { key: 'actor', header: bi('执行者', 'Actor'), render: e => <span className="text-muted">{e.actor}</span> },
    { key: 'detail', header: bi('说明', 'Detail'), render: e => <span className="text-faint">{e.detail}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Incident Response" titleZh="告警事件响应"
        subtitle={bi('只读事件路由、SLA 监控与处置剧本快照 · 无外部通知 · 不授权实盘',
          'Read-only incident routing, SLA tracking & runbook snapshot · no external send · never authorizes live')}
        actions={
          <div className="flex items-center gap-2">
            <Chip label={data.incident_mode} tone="info" />
            <Chip label={summary.live_authorization_allowed ? 'LIVE_AUTH' : 'NO_LIVE_AUTH'}
              tone={summary.live_authorization_allowed ? 'neg' : 'pos'} />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={data.source} error={error} />
      {/* Honesty: this subsystem is NOT built — needs the Phase 14 identity/RBAC layer. */}
      <div className="rounded-lg border border-warn/30 bg-warn/5 px-3 py-1.5 text-2xs text-warn">
        蓝图 / BLUEPRINT:本页为合成演示,对应的「事件响应持久层」子系统尚未实现(需 Phase 14 身份/RBAC 层);L2 下维持诚实展示,不伪造审批/权限。 · synthetic demo — the incident-response persistence is not built yet.
      </div>

      <KpiStrip cols={8}>
        <StatTile label={bi('事件总数', 'Incidents')} value={num(summary.incidents)} accent
          sub={bi(`${num(summary.open_incidents)} 开放`, `${num(summary.open_incidents)} open`)} />
        <StatTile label={bi('关键', 'Critical')} value={num(summary.critical)} tone="neg" />
        <StatTile label={bi('高', 'High')} value={num(summary.high)} tone="warn" />
        <StatTile label={bi('中', 'Medium')} value={num(summary.medium)} tone="info" />
        <StatTile label={bi('SLA 破口', 'SLA Breaches')} value={num(summary.sla_breaches)} tone="neg"
          sub={pct(slaBreachRate, 0)} />
        <StatTile label={bi('已升级', 'Escalated')} value={num(summary.escalated)} tone="warn"
          sub={pct(escalatedRate, 0)} />
        <StatTile label={bi('平均年龄', 'Avg Age')} value={num(summary.avg_age_minutes)} unit="m" />
        <StatTile label={bi('外部告警', 'External Alerts')} value={num(summary.external_alerts_sent)} tone="pos"
          sub={bi('诚实只读', 'read-only')} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('分布', 'Distribution')} title={bi('按严重度', 'By Severity')}
          right={<Chip label={bi(`${data.incidents.length} 事件`, `${data.incidents.length} inc`)} tone="muted" />}>
          <div className="flex items-center gap-4">
            <Donut segments={severityDonut} size={132} />
            <div className="flex flex-1 flex-col gap-2">
              {severityDonut.map(s => (
                <div key={s.label} className="flex items-center justify-between gap-2">
                  <Chip label={s.label} tone={s.tone} />
                  <span className="tnum text-xs text-muted">{num(s.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('生命周期', 'Lifecycle')} title={bi('按状态', 'By State')}>
          <div className="flex items-center gap-4">
            <Donut segments={stateDonut} size={132} />
            <div className="flex flex-1 flex-col gap-2">
              {stateDonut.map(s => (
                <div key={s.label} className="flex items-center justify-between gap-2">
                  <StatusChip value={s.label} />
                  <span className="tnum text-xs text-muted">{num(s.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('合规护栏', 'Guardrails')} title={bi('响应策略', 'Response Policy')}>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {policies.map(([label, passed], i) => (
              <div key={i} className="flex items-center justify-between gap-2 rounded-md border border-line/60 bg-inset/40 px-2.5 py-1.5">
                <span className="text-2xs text-muted">{label}</span>
                <GateDot ok={passed} label={passed ? 'PASS' : 'FAIL'} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('优先处置', 'Triage')} title={bi('优先事件队列', 'Priority Incident Queue')}>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {priorityIncidents.slice(0, 4).map(inc => {
              const frac = Math.min(1, inc.age_minutes / Math.max(inc.sla_minutes, 1));
              return (
                <div key={inc.incident_id} className="flex flex-col gap-2 rounded-lg border border-line bg-inset/30 p-3"
                  style={{ borderColor: `color-mix(in srgb, var(--${severityTone(inc.severity)}) 35%, var(--line))` }}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="mono text-xs font-semibold text-accent">{inc.incident_id}</div>
                      <div className="truncate text-2xs text-faint">{inc.category} · {inc.subject_id}</div>
                    </div>
                    <Chip label={inc.severity} tone={severityTone(inc.severity)} />
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusChip value={inc.state} />
                    <span className="text-2xs text-faint">{inc.escalation_level}</span>
                  </div>
                  <div>
                    <div className="mb-1 flex items-center justify-between text-2xs">
                      <span className="text-faint">SLA {inc.age_minutes}/{inc.sla_minutes}m</span>
                      <span className={inc.sla_breached ? 'text-neg' : 'text-muted'}>
                        {inc.sla_breached ? bi('已破口', 'BREACHED') : bi(`剩 ${inc.due_in_minutes}m`, `${inc.due_in_minutes}m left`)}
                      </span>
                    </div>
                    <ProgressBar value={frac} tone={inc.sla_breached ? 'neg' : frac >= 0.8 ? 'warn' : 'accent'} />
                  </div>
                  <div className="truncate text-2xs text-muted">{bi('下一步', 'Next')}: {inc.next_action}</div>
                  <div className="flex items-center justify-between gap-2 text-2xs">
                    <span className="text-faint">{bi('指挥', 'IC')}: {inc.incident_commander}</span>
                    <GateDot ok={inc.blocks_live_authorization} label={bi('阻断实盘', 'blocks live')} />
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>

        <Panel eyebrow={bi('路由命中', 'Routing')} title={bi('类别分布', 'Category Counts')}>
          <Histogram bins={routingBins} height={200} tone="info" />
        </Panel>
      </div>

      <Panel eyebrow={bi('全量台账', 'Ledger')} title={bi('事件台账', 'Incident Ledger')}
        right={<Chip label={bi(`${data.incidents.length} 条`, `${data.incidents.length} rows`)} tone="muted" />}>
        <DataTable columns={incidentCols} rows={data.incidents} getKey={r => r.incident_id} dense />
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel eyebrow={bi('SLA 路由', 'Routing')} title={bi('路由规则', 'Routing Rules')}>
          <DataTable columns={routingCols} rows={data.routing_rules} getKey={r => r.route_id} dense />
        </Panel>

        <Panel eyebrow={bi('审计', 'Audit')} title={bi('事件时间线', 'Incident Timeline')}>
          <DataTable columns={timelineCols} rows={data.timeline} getKey={e => e.event_id} dense
            empty={bi('暂无时间线事件', 'No timeline events')} />
        </Panel>
      </div>

      <Panel eyebrow={bi('处置剧本', 'Playbooks')} title={bi('响应剧本', 'Response Runbooks')}
        right={<Chip label={bi(`${data.runbooks.length} 剧本`, `${data.runbooks.length} runbooks`)} tone="info" />}>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.runbooks.map(rb => (
            <div key={rb.runbook_id} className="flex flex-col gap-2 rounded-lg border border-line bg-inset/30 p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="mono text-xs font-semibold text-accent">{rb.runbook_id}</div>
                  <div className="truncate text-2xs text-faint">{rb.category}</div>
                </div>
                <Chip label={rb.mutable_runbook ? bi('可变', 'MUTABLE') : bi('封存', 'SEALED')}
                  tone={rb.mutable_runbook ? 'neg' : 'pos'} />
              </div>
              <div className="text-2xs text-muted">{rb.objective}</div>
              <ol className="flex flex-col gap-1.5 text-2xs text-muted">
                {rb.steps.map((step, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mono shrink-0 text-faint">{i + 1}.</span>
                    <span className="min-w-0">{step}</span>
                  </li>
                ))}
              </ol>
              <div className="mt-auto flex items-center justify-between border-t border-line/60 pt-2 text-2xs">
                <span className="text-faint">{bi('事后复盘', 'Postmortem')}</span>
                <GateDot ok={rb.requires_postmortem} label={rb.requires_postmortem ? bi('要求', 'required') : bi('非必需', 'optional')} />
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel eyebrow={bi('快照元数据', 'Snapshot Meta')} title={bi('事件响应模式', 'Incident Response Mode')}>
        <KeyValue cols={4} items={[
          { k: bi('模式', 'Mode'), v: data.incident_mode },
          { k: bi('API 版本', 'API Version'), v: data.api_version },
          { k: bi('外部通知', 'External Notify'), v: incident_policy.external_notifications_enabled ? bi('启用', 'enabled') : bi('禁用', 'disabled') },
          { k: bi('SLA 破口率', 'SLA Breach Rate'), v: pctPoints(slaBreachRate * 100, 1) },
          { k: bi('实盘授权', 'Live Auth'), v: summary.live_authorization_allowed ? bi('允许', 'allowed') : bi('禁止', 'blocked') },
          { k: bi('可变事件', 'Mutable'), v: incident_policy.mutable_incidents ? 'true' : 'false' },
          { k: bi('只读', 'Read-only'), v: data.read_only ? 'true' : 'false' },
          { k: bi('数据源', 'Source'), v: shortHash(data.source, 18) },
        ]} />
      </Panel>
    </PageShell>
  );
}
