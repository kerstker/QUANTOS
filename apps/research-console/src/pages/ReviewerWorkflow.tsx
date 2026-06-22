import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchReviewerWorkflowSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ReviewerWorkflowPayload, ReviewerWorkflowTask } from '@/lib/types/research';

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

function formatTime(seconds: number) {
  return new Date(seconds * 1000).toISOString().slice(0, 16).replace('T', ' ');
}

function stateClass(state: string) {
  if (state === 'OVERDUE' || state === 'BLOCKED') {
    return 'text-red-300';
  }
  if (state === 'PENDING_REVIEW' || state === 'QUEUED' || state === 'PENDING') {
    return 'text-amber-300';
  }
  return 'text-emerald-300';
}

function severityClass(severity: string) {
  if (severity === 'CRITICAL') {
    return 'border-red-800 bg-red-950/30 text-red-300';
  }
  if (severity === 'HIGH') {
    return 'border-orange-800 bg-orange-950/30 text-orange-300';
  }
  return 'border-slate-700 bg-slate-950/60 text-slate-300';
}

function TaskCard({task}: {task: ReviewerWorkflowTask}) {
  return (
    <div className={`rounded border p-3 text-xs ${severityClass(task.severity)}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-bold text-slate-100">{task.task_id} / {task.title}</div>
          <div className="mt-1 break-words text-slate-500">{task.evidence_ref}</div>
        </div>
        <span className={stateClass(task.state)}>{task.state}</span>
      </div>
      <div className="mt-3 grid gap-1 text-slate-400 md:grid-cols-2">
        <div>{bi('复核人', 'Reviewer')}: {task.assigned_reviewer}</div>
        <div>{bi('角色', 'Role')}: {task.role}</div>
        <div>{bi('SLA', 'SLA')}: {task.age_hours}/{task.sla_hours}h</div>
        <div>{bi('阻断', 'Blocking')}: {task.blocks_live_authorization ? 'YES' : 'NO'}</div>
      </div>
    </div>
  );
}

function WorkloadBoard({workflow}: {workflow: ReviewerWorkflowPayload}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('复核负载', 'Reviewer Workload')}</h2>
      <div className="space-y-3 text-xs">
        {workflow.workload.map(item => (
          <div key={item.reviewer_id}>
            <div className="mb-1 flex justify-between gap-2">
              <span className="text-slate-300">{item.name} / {item.role}</span>
              <span className="text-cyan-300">{Math.round(item.utilization * 100)}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded bg-slate-800">
              <div className="h-full bg-cyan-700" style={{width: `${Math.min(100, Math.round(item.utilization * 100))}%`}} />
            </div>
            <div className="mt-1 text-slate-500">{item.open_tasks}/{item.capacity} open / blocking {item.required_open_blocking}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function GateBoard({workflow}: {workflow: ReviewerWorkflowPayload}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('审批门禁', 'Approval Gates')}</h2>
      <div className="space-y-3 text-xs">
        {workflow.approval_gates.map(gate => (
          <div key={gate.gate_id} className="rounded border border-slate-800 bg-slate-950/60 p-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-bold text-slate-200">{gate.name}</div>
                <div className="mt-1 text-slate-500">{gate.gate_id}</div>
              </div>
              <span className={stateClass(gate.status)}>{gate.status}</span>
            </div>
            <div className="mt-2 text-slate-400">{bi('负责人角色', 'Owner role')}: {gate.owner_role}</div>
            <div className="mt-1 break-words text-slate-500">{gate.required_tasks.join(', ')}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PolicyBoard({workflow}: {workflow: ReviewerWorkflowPayload}) {
  const policies: Array<[string, boolean]> = [
    ['READ_ONLY', workflow.workflow_policy.read_only],
    ['INDEPENDENT_REVIEWER', workflow.workflow_policy.requires_independent_reviewer],
    ['BLOCKING_TASK_CLOSURE', workflow.workflow_policy.requires_blocking_task_closure],
    ['PHASE3A_GO_REQUIRED', workflow.workflow_policy.requires_phase3a_go],
    ['SIGNED_ATTESTATION', workflow.workflow_policy.requires_signed_attestation],
    ['IMMUTABLE_TASKS', !workflow.workflow_policy.mutable_tasks],
    ['NO_LIVE_AUTHORIZATION', !workflow.workflow_policy.authorizes_live_trading],
  ];
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('工作流策略', 'Workflow Policy')}</h2>
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

export default function ReviewerWorkflow() {
  const [payload, setPayload] = useState<ReviewerWorkflowPayload | null>(null);
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
    fetchReviewerWorkflowSnapshot(controller.signal)
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
  const workflow = usingBackend && payload ? payload : fallbackWorkflow;
  const blockingTasks = useMemo(() => workflow.tasks.filter(task => task.blocks_live_authorization), [workflow.tasks]);

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('复核工作流', 'Reviewer Workflow')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision="L2_DRY_RUN_ONLY" />
              <DecisionBadge decision={workflow.workflow_mode} />
              <DecisionBadge decision={workflow.summary.live_authorization_allowed ? 'APPROVED' : 'NO_GO'} />
            </div>
          </div>
          <div className="text-xs text-slate-500 lg:text-right">
            <div>{bi('工作流', 'Workflow')}: {workflow.workflow_id}</div>
            <div>{bi('Phase 3A', 'Phase 3A')}: {workflow.summary.phase3a_decision}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={workflow.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('任务', 'Tasks')} value={workflow.summary.tasks} />
        <MetricCard label={bi('阻断任务', 'Blocking')} value={workflow.summary.blocking_tasks} />
        <MetricCard label={bi('逾期', 'Overdue')} value={workflow.summary.overdue_tasks} />
        <MetricCard label={bi('复核人', 'Reviewers')} value={workflow.summary.reviewers} />
        <MetricCard label={bi('门禁', 'Gates')} value={workflow.summary.approval_gates} />
        <MetricCard label={bi('阻断门禁', 'Blocked')} value={workflow.summary.blocked_gates} />
        <MetricCard label={bi('独立验证', 'Validation')} value={workflow.summary.independent_validation_complete ? 'DONE' : 'OPEN'} />
        <MetricCard label={bi('实盘授权', 'Live Auth')} value={workflow.summary.live_authorization_allowed ? 'YES' : 'NO'} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('阻断任务', 'Blocking Tasks')}</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {blockingTasks.map(task => <TaskCard key={task.task_id} task={task} />)}
          </div>
        </div>
        <div className="space-y-4">
          <WorkloadBoard workflow={workflow} />
          <GateBoard workflow={workflow} />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <PolicyBoard workflow={workflow} />
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('证据链接', 'Evidence Links')}</h2>
          <div className="grid gap-3 md:grid-cols-3">
            {workflow.evidence_links.map(link => (
              <div key={link.source} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs">
                <div className="font-bold text-slate-200">{link.source}</div>
                <div className="mt-2 break-words text-slate-500">{link.ref}</div>
                <div className="mt-2 text-cyan-300">{link.records} records / {link.blocking} blocking</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-auto">
        <table className="w-full min-w-[1160px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('任务', 'Task')}</th>
              <th className="p-2 text-left">{bi('来源', 'Source')}</th>
              <th className="p-2 text-left">{bi('复核人', 'Reviewer')}</th>
              <th className="p-2 text-left">{bi('严重度', 'Severity')}</th>
              <th className="p-2 text-left">{bi('状态', 'State')}</th>
              <th className="p-2 text-right">Age</th>
              <th className="p-2 text-right">SLA</th>
              <th className="p-2 text-left">{bi('创建', 'Created')}</th>
              <th className="p-2 text-left">Hash</th>
              <th className="p-2 text-left">{bi('阻断', 'Blocks')}</th>
            </tr>
          </thead>
          <tbody>
            {workflow.tasks.map(task => (
              <tr key={task.task_id} className="border-b border-slate-800/50">
                <td className="p-2">
                  <div className="font-bold text-slate-200">{task.task_id}</div>
                  <div className="text-slate-500">{task.title}</div>
                </td>
                <td className="p-2">
                  <div className="text-cyan-300">{task.source_area}</div>
                  <div className="break-words text-slate-500">{task.evidence_ref}</div>
                </td>
                <td className="p-2 text-slate-300">{task.assigned_reviewer}</td>
                <td className="p-2 text-orange-300">{task.severity}</td>
                <td className={`p-2 ${stateClass(task.state)}`}>{task.state}</td>
                <td className="p-2 text-right text-slate-300">{task.age_hours}h</td>
                <td className="p-2 text-right text-slate-300">{task.sla_hours}h</td>
                <td className="p-2 text-slate-400">{formatTime(task.created_at)}</td>
                <td className="p-2 font-mono text-slate-500">{task.task_hash}</td>
                <td className={task.blocks_live_authorization ? 'p-2 text-red-300' : 'p-2 text-emerald-300'}>{task.blocks_live_authorization ? 'YES' : 'NO'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
