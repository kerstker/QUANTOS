import React from 'react';
import { fetchReviewerWorkflowSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ReviewerWorkflowPayload, ReviewerWorkflowTask } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, Chip, GateDot, ProgressBar, KeyValue, type Column,
} from '@/ui/primitives';
import { Donut, Histogram } from '@/ui/charts';
import { num, pct, signed, shortHash, stamp, statusTone, deltaTone, type Tone } from '@/ui/format';

const fallbackWorkflow: ReviewerWorkflowPayload = {
  source: 'FRONTEND_REVIEWER_WORKFLOW_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  workflow_mode: 'READ_ONLY_REVIEWER_WORKFLOW',
  workflow_id: 'reviewer-workflow-fallback',
  summary: {
    tasks: 3,
    blocking_tasks: 2,
    overdue_tasks: 1,
    reviewers: 2,
    approval_gates: 2,
    blocked_gates: 2,
    independent_validation_complete: false,
    phase3a_decision: 'NO_GO',
    live_authorization_allowed: false,
  },
  reviewers: [
    {reviewer_id: 'rev-risk-001', name: 'Risk Reviewer', role: 'Independent Risk', required: true, capacity: 7, open_tasks: 3},
    {reviewer_id: 'rev-model-001', name: 'Model Validator', role: 'Model Validation', required: true, capacity: 6, open_tasks: 4},
  ],
  tasks: [
    {task_id: 'RW-001', title: 'Validate model risk issue register', evidence_ref: 'MRM-I01', source_area: 'MODEL_RISK_REVIEW', assigned_reviewer_id: 'rev-model-001', assigned_reviewer: 'Model Validator', role: 'Model Validation', severity: 'HIGH', gate_type: 'BLOCKING', state: 'OVERDUE', age_hours: 42, sla_hours: 36, due_in_hours: -6, blocks_live_authorization: true, requires_independent_signoff: true, task_hash: 'fallback-rw-001', created_at: 1_781_222_400},
    {task_id: 'RW-002', title: 'Challenge stress scenario calibration', evidence_ref: 'EV-SCENARIO-STRESS-001', source_area: 'SCENARIO_STRESS', assigned_reviewer_id: 'rev-risk-001', assigned_reviewer: 'Risk Reviewer', role: 'Independent Risk', severity: 'HIGH', gate_type: 'BLOCKING', state: 'PENDING_REVIEW', age_hours: 22, sla_hours: 36, due_in_hours: 14, blocks_live_authorization: true, requires_independent_signoff: true, task_hash: 'fallback-rw-002', created_at: 1_781_223_000},
    {task_id: 'RW-003', title: 'Verify evidence ledger hash chain', evidence_ref: 'fallback-root', source_area: 'EVIDENCE_LEDGER', assigned_reviewer_id: 'rev-risk-001', assigned_reviewer: 'Risk Reviewer', role: 'Independent Risk', severity: 'MEDIUM', gate_type: 'NON_BLOCKING', state: 'QUEUED', age_hours: 9, sla_hours: 72, due_in_hours: 63, blocks_live_authorization: false, requires_independent_signoff: true, task_hash: 'fallback-rw-003', created_at: 1_781_223_600},
  ],
  approval_gates: [
    {gate_id: 'GATE-INDEPENDENT-VALIDATION', name: 'Independent validation complete', status: 'BLOCKED', required_tasks: ['RW-001'], owner_role: 'Model Validation'},
    {gate_id: 'GATE-STRESS-CHALLENGE', name: 'Stress challenge complete', status: 'BLOCKED', required_tasks: ['RW-002'], owner_role: 'Independent Risk'},
  ],
  workload: [
    {reviewer_id: 'rev-risk-001', name: 'Risk Reviewer', role: 'Independent Risk', required: true, capacity: 7, open_tasks: 3, utilization: 0.43, required_open_blocking: 1},
    {reviewer_id: 'rev-model-001', name: 'Model Validator', role: 'Model Validation', required: true, capacity: 6, open_tasks: 4, utilization: 0.67, required_open_blocking: 1},
  ],
  evidence_links: [
    {source: 'Evidence Ledger', ref: 'fallback-root', records: 3, blocking: 1},
    {source: 'Model Risk Review', ref: 'READ_ONLY_MODEL_RISK_REVIEW', records: 3, blocking: 1},
  ],
  workflow_policy: {
    read_only: true,
    requires_independent_reviewer: true,
    requires_blocking_task_closure: true,
    requires_phase3a_go: true,
    requires_signed_attestation: true,
    mutable_tasks: false,
    authorizes_live_trading: false,
  },
};

const severityTone: Record<string, Tone> = {
  CRITICAL: 'neg', HIGH: 'warn', MEDIUM: 'info', LOW: 'muted',
};

/** Due window: overdue (<0) is negative, tightening (<sla margin) warns. */
function dueTone(hours: number): Tone {
  if (hours < 0) return 'neg';
  if (hours <= 24) return 'warn';
  return 'pos';
}

function utilTone(u: number): Tone {
  if (u >= 0.85) return 'neg';
  if (u >= 0.6) return 'warn';
  return 'accent';
}

export default function ReviewerWorkflow() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<ReviewerWorkflowPayload>(
    s => fetchReviewerWorkflowSnapshot(s));

  const wf = usingBackend ? payload! : fallbackWorkflow;
  const sum = wf.summary;
  const liveOk = sum.live_authorization_allowed;

  // Severity distribution histogram (deterministic order, only present buckets).
  const sevOrder = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];
  const sevCounts = sevOrder
    .map(s => ({ label: s, count: wf.tasks.filter(t => t.severity === s).length }))
    .filter(b => b.count > 0);

  // Gate status composition donut.
  const gateStatuses = Array.from(new Set(wf.approval_gates.map(g => g.status)));
  const gateSegments = gateStatuses.map(st => ({
    label: st,
    value: wf.approval_gates.filter(g => g.status === st).length,
    tone: statusTone(st),
  }));

  const taskCols: Column<ReviewerWorkflowTask>[] = [
    { key: 'task', header: bi('任务', 'Task'), render: t => (
      <div className="min-w-0">
        <div className="mono text-2xs text-faint">{t.task_id}</div>
        <div className="truncate font-medium text-ink">{t.title}</div>
      </div>
    ) },
    { key: 'source', header: bi('来源 / 证据', 'Source / Evidence'), render: t => (
      <div className="min-w-0">
        <Chip label={t.source_area} tone="info" />
        <div className="mt-0.5 mono truncate text-2xs text-faint">{t.evidence_ref}</div>
      </div>
    ) },
    { key: 'reviewer', header: bi('复核人', 'Reviewer'), render: t => (
      <div className="min-w-0">
        <div className="truncate text-ink">{t.assigned_reviewer}</div>
        <div className="truncate text-2xs text-muted">{t.role}</div>
      </div>
    ) },
    { key: 'sev', header: bi('严重度', 'Severity'), render: t => <Chip label={t.severity} tone={severityTone[t.severity] ?? 'muted'} /> },
    { key: 'gate', header: bi('门类', 'Gate'), render: t => <StatusChip value={t.gate_type} /> },
    { key: 'state', header: bi('状态', 'State'), render: t => <StatusChip value={t.state} /> },
    { key: 'sla', header: bi('SLA', 'SLA'), align: 'right', width: '150px', render: t => (
      <div className="flex items-center justify-end gap-2">
        <span className="tnum text-2xs text-muted">{num(t.age_hours)}/{num(t.sla_hours)}h</span>
        <ProgressBar value={t.age_hours} max={t.sla_hours} tone={t.age_hours >= t.sla_hours ? 'neg' : 'accent'} />
      </div>
    ) },
    { key: 'due', header: bi('到期', 'Due'), align: 'right', render: t => (
      <span className="tnum font-semibold" style={{ color: `var(--${dueTone(t.due_in_hours)})` }}>
        {signed(t.due_in_hours, 0)}h
      </span>
    ) },
    { key: 'signoff', header: bi('独立签核', 'Signoff'), align: 'center', render: t => <GateDot ok={t.requires_independent_signoff} /> },
    { key: 'blocks', header: bi('阻断实盘', 'Blocks Live'), align: 'center', render: t => (
      <div className="flex items-center justify-center gap-1.5">
        <GateDot ok={!t.blocks_live_authorization} />
        <span className="text-2xs text-muted">{t.blocks_live_authorization ? bi('阻断', 'BLOCK') : bi('放行', 'CLEAR')}</span>
      </div>
    ) },
    { key: 'created', header: bi('创建', 'Created'), align: 'right', mono: true, render: t => stamp(t.created_at) },
    { key: 'hash', header: 'Hash', mono: true, render: t => shortHash(t.task_hash) },
  ];

  const policyRows: Array<[string, boolean]> = [
    [bi('只读', 'READ_ONLY'), wf.workflow_policy.read_only],
    [bi('独立复核人', 'INDEPENDENT_REVIEWER'), wf.workflow_policy.requires_independent_reviewer],
    [bi('阻断任务清零', 'BLOCKING_TASK_CLOSURE'), wf.workflow_policy.requires_blocking_task_closure],
    [bi('需 3A 放行', 'PHASE3A_GO_REQUIRED'), wf.workflow_policy.requires_phase3a_go],
    [bi('签名背书', 'SIGNED_ATTESTATION'), wf.workflow_policy.requires_signed_attestation],
    [bi('任务不可变', 'IMMUTABLE_TASKS'), !wf.workflow_policy.mutable_tasks],
    [bi('无实盘授权', 'NO_LIVE_AUTHORIZATION'), !wf.workflow_policy.authorizes_live_trading],
  ];

  return (
    <PageShell>
      <PageHeader title="Reviewer Workflow" titleZh="复核工作流"
        subtitle={bi('独立复核任务、审批门禁与负载——只读;阻断任务未清零前实盘授权不可达',
          'Independent reviewer tasks, approval gates & workload — read-only; live authorization unreachable until blocking tasks clear')}
        actions={
          <div className="flex items-center gap-2">
            <Chip label={`Phase 3A · ${sum.phase3a_decision}`} tone={statusTone(sum.phase3a_decision)} />
            <Chip label={liveOk ? bi('实盘授权 可', 'LIVE AUTH OK') : bi('实盘授权 受阻', 'LIVE AUTH BLOCKED')}
              tone={liveOk ? 'pos' : 'neg'} soft={false} />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={wf.source} error={error} />

      <KpiStrip cols={8}>
        <StatTile label={bi('任务', 'Tasks')} value={num(sum.tasks)} accent />
        <StatTile label={bi('阻断任务', 'Blocking')} value={num(sum.blocking_tasks)} tone={sum.blocking_tasks > 0 ? 'neg' : 'pos'} />
        <StatTile label={bi('逾期', 'Overdue')} value={num(sum.overdue_tasks)} tone={sum.overdue_tasks > 0 ? 'warn' : 'pos'} />
        <StatTile label={bi('复核人', 'Reviewers')} value={num(sum.reviewers)} />
        <StatTile label={bi('门禁', 'Gates')} value={num(sum.approval_gates)} tone="info" />
        <StatTile label={bi('阻断门禁', 'Blocked Gates')} value={num(sum.blocked_gates)} tone={sum.blocked_gates > 0 ? 'neg' : 'pos'} />
        <StatTile label={bi('独立验证', 'Indep. Valid.')} value={sum.independent_validation_complete ? bi('完成', 'DONE') : bi('未完成', 'OPEN')}
          tone={sum.independent_validation_complete ? 'pos' : 'warn'} />
        <StatTile label={bi('实盘授权', 'Live Auth')} value={liveOk ? bi('允许', 'YES') : bi('拒绝', 'NO')}
          tone={liveOk ? 'pos' : 'neg'} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('容量', 'Capacity')} title={bi('复核负载', 'Reviewer Workload')}
          right={<Chip label={bi(`${wf.workload.length} 复核人`, `${wf.workload.length} reviewers`)} tone="info" />}>
          <div className="flex flex-col gap-3.5">
            {wf.workload.map(w => (
              <div key={w.reviewer_id} className="flex flex-col gap-1.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-xs font-medium text-ink">{w.name}</span>
                    <span className="truncate text-2xs text-muted">{w.role}</span>
                    {w.required && <Chip label={bi('必需', 'REQUIRED')} tone="special" />}
                  </div>
                  <span className="tnum shrink-0 text-xs font-semibold" style={{ color: `var(--${utilTone(w.utilization)})` }}>
                    {pct(w.utilization, 0)}
                  </span>
                </div>
                <ProgressBar value={w.utilization} tone={utilTone(w.utilization)} height={8} />
                <div className="flex items-center justify-between text-2xs text-faint">
                  <span className="tnum">{num(w.open_tasks)}/{num(w.capacity)} {bi('开放', 'open')}</span>
                  <span className="tnum">{bi('开放阻断', 'open blocking')}: <span className={w.required_open_blocking > 0 ? 'text-neg' : 'text-pos'}>{num(w.required_open_blocking)}</span></span>
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <div className="flex flex-col gap-4">
          <Panel eyebrow={bi('门禁构成', 'Gates')} title={bi('门禁状态分布', 'Gate Status Mix')}>
            <div className="flex items-center justify-center gap-6">
              <Donut size={132} segments={gateSegments} />
              <div className="flex flex-col gap-1.5 text-2xs">
                {gateSegments.map(s => (
                  <div key={s.label} className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full" style={{ background: `var(--${s.tone})` }} />
                    <span className="text-muted">{s.label}</span>
                    <span className="tnum ml-auto text-ink">{s.value}</span>
                  </div>
                ))}
              </div>
            </div>
          </Panel>
          <Panel eyebrow={bi('分布', 'Distribution')} title={bi('任务严重度', 'Task Severity')}>
            <Histogram height={132} tone="warn" bins={sevCounts} />
          </Panel>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('审批', 'Approval')} title={bi('审批门禁', 'Approval Gates')}>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {wf.approval_gates.map(g => (
              <div key={g.gate_id} className="flex flex-col gap-2 rounded-xl border border-line bg-inset/40 p-3"
                style={{ borderColor: `color-mix(in srgb, var(--${statusTone(g.status)}) 35%, var(--line))` }}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-xs font-semibold text-ink">{g.name}</div>
                    <div className="mono mt-0.5 truncate text-2xs text-faint">{g.gate_id}</div>
                  </div>
                  <StatusChip value={g.status} />
                </div>
                <div className="flex items-center gap-2 text-2xs text-muted">
                  <GateDot ok={statusTone(g.status) === 'pos'} />
                  <span>{bi('负责角色', 'Owner')}: {g.owner_role}</span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {g.required_tasks.map(rt => <Chip key={rt} label={rt} tone="muted" />)}
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel eyebrow={bi('不可变约束', 'Invariants')} title={bi('工作流策略', 'Workflow Policy')}>
          <div className="flex flex-col gap-2">
            {policyRows.map(([label, ok]) => (
              <div key={label} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset/30 px-3 py-2">
                <span className="text-2xs font-medium text-muted">{label}</span>
                <div className="flex items-center gap-2">
                  <GateDot ok={ok} />
                  <span className="tnum text-2xs font-semibold" style={{ color: `var(--${ok ? 'pos' : 'neg'})` }}>
                    {ok ? 'PASS' : 'FAIL'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('复核队列', 'Review Queue')} title={bi('复核任务', 'Reviewer Tasks')}
        right={
          <div className="flex items-center gap-2">
            <Chip label={bi(`${sum.blocking_tasks} 阻断`, `${sum.blocking_tasks} blocking`)} tone={sum.blocking_tasks > 0 ? 'neg' : 'pos'} />
            <Chip label={bi(`${sum.overdue_tasks} 逾期`, `${sum.overdue_tasks} overdue`)} tone={sum.overdue_tasks > 0 ? 'warn' : 'muted'} />
          </div>
        }>
        <DataTable columns={taskCols} rows={wf.tasks} getKey={t => t.task_id} dense />
      </Panel>

      <Panel eyebrow={bi('证据来源', 'Evidence')} title={bi('证据链接', 'Evidence Links')}>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {wf.evidence_links.map(link => (
            <div key={link.source} className="flex flex-col gap-2 rounded-xl border border-line bg-inset/40 p-3.5">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-semibold text-ink">{link.source}</span>
                <GateDot ok={link.blocking === 0} />
              </div>
              <div className="mono truncate text-2xs text-faint">{link.ref}</div>
              <KeyValue cols={2} items={[
                { k: bi('记录', 'Records'), v: num(link.records) },
                { k: bi('阻断', 'Blocking'), v: <span style={{ color: `var(--${deltaTone(-link.blocking)})` }}>{num(link.blocking)}</span> },
              ]} />
            </div>
          ))}
        </div>
      </Panel>
    </PageShell>
  );
}
