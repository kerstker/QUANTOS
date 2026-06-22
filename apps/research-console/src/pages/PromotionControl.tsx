import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchPromotionControlSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { PromotionControlPayload, PromotionGate, PromotionRecord } from '@/lib/types/research';

const fallbackPromotion: PromotionControlPayload = {
  source: 'FRONTEND_PROMOTION_CONTROL_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  promotion_mode: 'READ_ONLY_PROMOTION_CONTROL',
  current_level: 'L2_DRY_RUN_ONLY',
  target_stage: 'L2_DRY_RUN_REPLAY',
  phase3a_decision: 'NO_GO',
  summary: {
    candidates: 2,
    approved_for_dry_run: 1,
    blocked: 1,
    watch_gates: 2,
    failed_gates: 1,
    paper_replay_allowed: 1,
    broker_test_env_allowed: 0,
    live_release_allowed: 0,
    avg_readiness_score: 0.62,
  },
  decision_counts: {APPROVED_FOR_DRY_RUN_WITH_LIMITS: 1, BLOCKED: 1},
  stage_counts: {
    VALIDATION: {pass: 1, watch: 1, fail: 0},
    MODEL_RISK: {pass: 0, watch: 1, fail: 1},
    INCIDENT_RESPONSE: {pass: 1, watch: 0, fail: 1},
  },
  promotions: [
    {promotion_id: 'PROM-FB-001', alpha_id: 'alpha-fallback-1', expression: '(ts_mean :close 20)', current_stage: 'VALIDATING', requested_stage: 'L2_DRY_RUN_REPLAY', decision: 'APPROVED_FOR_DRY_RUN_WITH_LIMITS', decision_reason: 'Watch gates require owner review', readiness_score: 0.74, gates_passed: 6, gates_watch: 2, gates_failed: 0, gates_total: 9, blocking_gates: [], watch_gates: ['VALIDATION', 'MODEL_RISK'], evidence_refs: ['fallback-validation', 'fallback-ledger'], candidate_hash: 'fallback-prom-001', paper_replay_allowed: true, broker_test_env_allowed: false, live_release_allowed: false, next_action: 'Continue dry-run replay and collect additional evidence'},
    {promotion_id: 'PROM-FB-002', alpha_id: 'alpha-fallback-2', expression: '(zscore :returns 10)', current_stage: 'DECAYING', requested_stage: 'L2_DRY_RUN_REPLAY', decision: 'BLOCKED', decision_reason: '1 hard promotion gate failed', readiness_score: 0.5, gates_passed: 5, gates_watch: 0, gates_failed: 1, gates_total: 9, blocking_gates: ['INCIDENT_RESPONSE'], watch_gates: [], evidence_refs: ['fallback-incident'], candidate_hash: 'fallback-prom-002', paper_replay_allowed: false, broker_test_env_allowed: false, live_release_allowed: false, next_action: 'Close failed gates and attach reviewer evidence before further promotion'},
  ],
  gates: [
    {gate_id: 'PG-FB-001', alpha_id: 'alpha-fallback-1', stage: 'VALIDATION', status: 'WATCH', owner: 'Alpha Research', evidence_ref: 'fallback-validation', score: 0.67, reason: 'Needs more samples', blocks_paper_replay: false, blocks_broker_test_env: true, blocks_live_authorization: true},
    {gate_id: 'PG-FB-002', alpha_id: 'alpha-fallback-1', stage: 'MODEL_RISK', status: 'WATCH', owner: 'Model Governance', evidence_ref: 'fallback-model', score: 0.61, reason: 'Independent validation incomplete', blocks_paper_replay: false, blocks_broker_test_env: true, blocks_live_authorization: true},
    {gate_id: 'PG-FB-003', alpha_id: 'alpha-fallback-2', stage: 'INCIDENT_RESPONSE', status: 'FAIL', owner: 'Operations / Risk', evidence_ref: 'fallback-incident', score: 0, reason: 'High incident active', blocks_paper_replay: false, blocks_broker_test_env: true, blocks_live_authorization: true},
  ],
  release_policy: {
    read_only: true,
    requires_validation_approval: true,
    requires_paper_replay: true,
    requires_risk_approval: true,
    requires_model_risk_review: true,
    requires_no_blocking_incidents: true,
    requires_evidence_root_hash: true,
    requires_access_control: true,
    mutable_promotions: false,
    broker_test_env_enabled: false,
    live_release_enabled: false,
    authorizes_live_trading: false,
  },
  safety_boundary: {
    execution_boundary: 'LIVE_TRADING_DISALLOWED',
    phase3a_decision: 'NO_GO',
    forbidden_capabilities: ['No broker SDK imports', 'No live order APIs'],
  },
};

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

function statusClass(status: string) {
  if (status === 'FAIL' || status === 'BLOCKED') {
    return 'border-red-800 bg-red-950/30 text-red-300';
  }
  if (status === 'WATCH' || status.includes('LIMITS')) {
    return 'border-amber-800 bg-amber-950/30 text-amber-300';
  }
  return 'border-emerald-800 bg-emerald-950/30 text-emerald-300';
}

function statusTextClass(status: string) {
  if (status === 'FAIL' || status === 'BLOCKED') {
    return 'text-red-300';
  }
  if (status === 'WATCH' || status.includes('LIMITS')) {
    return 'text-amber-300';
  }
  return 'text-emerald-300';
}

function PromotionCard({record}: {record: PromotionRecord}) {
  return (
    <div className={`rounded border p-3 text-xs ${statusClass(record.decision)}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-bold text-slate-100">{record.promotion_id} / {record.alpha_id}</div>
          <div className="mt-1 break-words text-slate-500">{record.expression}</div>
        </div>
        <div className="flex-shrink-0 text-right">
          <div className="font-bold">{pct(record.readiness_score)}</div>
          <div className={statusTextClass(record.decision)}>{record.decision}</div>
        </div>
      </div>
      <div className="mt-3 grid gap-1 text-slate-400 md:grid-cols-2">
        <div>{bi('\u5f53\u524d', 'Current')}: {record.current_stage}</div>
        <div>{bi('\u76ee\u6807', 'Target')}: {record.requested_stage}</div>
        <div>{bi('\u901a\u8fc7', 'Pass')}: {record.gates_passed}/{record.gates_total}</div>
        <div>{bi('\u89c2\u5bdf', 'Watch')}: {record.gates_watch} / {bi('\u5931\u8d25', 'Fail')}: {record.gates_failed}</div>
      </div>
      <div className="mt-3 text-slate-300">{record.decision_reason}</div>
      <div className="mt-2 break-words text-slate-500">{record.next_action}</div>
      <div className="mt-3 flex flex-wrap gap-1">
        <span className={record.paper_replay_allowed ? 'rounded bg-emerald-950/40 px-2 py-0.5 text-emerald-300' : 'rounded bg-red-950/40 px-2 py-0.5 text-red-300'}>Paper {record.paper_replay_allowed ? 'YES' : 'NO'}</span>
        <span className="rounded bg-red-950/40 px-2 py-0.5 text-red-300">Broker Test NO</span>
        <span className="rounded bg-red-950/40 px-2 py-0.5 text-red-300">Live NO</span>
      </div>
    </div>
  );
}

function StageBoard({promotion}: {promotion: PromotionControlPayload}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u95e8\u7981\u9636\u6bb5', 'Gate Stages')}</h2>
      <div className="space-y-2 text-xs">
        {Object.entries(promotion.stage_counts).map(([stage, counts]) => {
          const total = counts.pass + counts.watch + counts.fail || 1;
          return (
            <div key={stage} className="rounded bg-slate-800 p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="font-bold text-slate-200">{stage}</span>
                <span className="text-slate-400">{counts.pass}/{total}</span>
              </div>
              <div className="grid grid-cols-3 gap-1">
                <div className="h-2 rounded bg-emerald-700" style={{opacity: Math.max(0.2, counts.pass / total)}} />
                <div className="h-2 rounded bg-amber-600" style={{opacity: Math.max(0.2, counts.watch / total)}} />
                <div className="h-2 rounded bg-red-700" style={{opacity: Math.max(0.2, counts.fail / total)}} />
              </div>
              <div className="mt-2 text-slate-500">PASS {counts.pass} / WATCH {counts.watch} / FAIL {counts.fail}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PolicyBoard({promotion}: {promotion: PromotionControlPayload}) {
  const rows: Array<[string, boolean]> = [
    ['READ_ONLY', promotion.release_policy.read_only],
    ['VALIDATION_REQUIRED', promotion.release_policy.requires_validation_approval],
    ['PAPER_REPLAY_REQUIRED', promotion.release_policy.requires_paper_replay],
    ['RISK_APPROVAL_REQUIRED', promotion.release_policy.requires_risk_approval],
    ['MODEL_RISK_REQUIRED', promotion.release_policy.requires_model_risk_review],
    ['NO_BLOCKING_INCIDENTS', promotion.release_policy.requires_no_blocking_incidents],
    ['IMMUTABLE_PROMOTIONS', !promotion.release_policy.mutable_promotions],
    ['BROKER_TEST_DISABLED', !promotion.release_policy.broker_test_env_enabled],
    ['LIVE_RELEASE_DISABLED', !promotion.release_policy.live_release_enabled],
    ['NO_LIVE_AUTHORIZATION', !promotion.release_policy.authorizes_live_trading],
  ];
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u53d1\u5e03\u7b56\u7565', 'Release Policy')}</h2>
      <div className="grid gap-2 text-xs md:grid-cols-2">
        {rows.map(([label, passed]) => (
          <div key={label} className="flex items-center justify-between gap-2 rounded bg-slate-800 px-3 py-2">
            <span className="text-slate-300">{label}</span>
            <span className={passed ? 'text-emerald-300' : 'text-red-300'}>{passed ? 'PASS' : 'FAIL'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function GateTable({gates}: {gates: PromotionGate[]}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900">
      <div className="border-b border-slate-800 p-4">
        <h2 className="text-sm uppercase text-slate-400">{bi('\u5168\u91cf\u95e8\u7981\u77e9\u9635', 'Full Gate Matrix')}</h2>
      </div>
      <div className="overflow-auto">
        <table className="w-full min-w-[1180px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('\u95e8\u7981', 'Gate')}</th>
              <th className="p-2 text-left">Alpha</th>
              <th className="p-2 text-left">{bi('\u9636\u6bb5', 'Stage')}</th>
              <th className="p-2 text-left">{bi('\u72b6\u6001', 'Status')}</th>
              <th className="p-2 text-left">{bi('\u8d1f\u8d23\u4eba', 'Owner')}</th>
              <th className="p-2 text-right">{bi('\u5206\u6570', 'Score')}</th>
              <th className="p-2 text-left">{bi('\u8bc1\u636e', 'Evidence')}</th>
              <th className="p-2 text-left">{bi('\u539f\u56e0', 'Reason')}</th>
            </tr>
          </thead>
          <tbody>
            {gates.map(gate => (
              <tr key={gate.gate_id} className="border-b border-slate-800/50">
                <td className="p-2 font-bold text-cyan-300">{gate.gate_id}</td>
                <td className="p-2 text-slate-300">{gate.alpha_id}</td>
                <td className="p-2 text-slate-400">{gate.stage}</td>
                <td className={`p-2 font-bold ${statusTextClass(gate.status)}`}>{gate.status}</td>
                <td className="p-2 text-slate-400">{gate.owner}</td>
                <td className="p-2 text-right text-slate-300">{pct(gate.score)}</td>
                <td className="p-2 text-slate-500">{gate.evidence_ref}</td>
                <td className="p-2 text-slate-500">{gate.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function PromotionControl() {
  const [payload, setPayload] = useState<PromotionControlPayload | null>(null);
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
    fetchPromotionControlSnapshot(controller.signal)
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
  const promotion = usingBackend && payload ? payload : fallbackPromotion;
  const priorityPromotions = useMemo(
    () => [...promotion.promotions].sort((a, b) => b.gates_failed - a.gates_failed || b.gates_watch - a.gates_watch || a.readiness_score - b.readiness_score),
    [promotion.promotions],
  );

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('\u7814\u7a76\u664b\u7ea7\u63a7\u5236', 'Promotion Control')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision={promotion.current_level} />
              <DecisionBadge decision={promotion.promotion_mode} />
              <DecisionBadge decision={promotion.phase3a_decision} />
            </div>
          </div>
          <div className="text-xs text-slate-500 lg:text-right">
            <div>{bi('\u76ee\u6807\u9636\u6bb5', 'Target')}: {promotion.target_stage}</div>
            <div>{bi('\u6267\u884c\u8fb9\u754c', 'Boundary')}: {promotion.safety_boundary.execution_boundary}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={promotion.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('\u5019\u9009', 'Candidates')} value={promotion.summary.candidates} />
        <MetricCard label={bi('\u5e72\u8dd1\u901a\u8fc7', 'Dry-run OK')} value={promotion.summary.approved_for_dry_run} />
        <MetricCard label={bi('\u963b\u65ad', 'Blocked')} value={promotion.summary.blocked} />
        <MetricCard label={bi('\u89c2\u5bdf\u95e8', 'Watch Gates')} value={promotion.summary.watch_gates} />
        <MetricCard label={bi('\u5931\u8d25\u95e8', 'Failed Gates')} value={promotion.summary.failed_gates} />
        <MetricCard label={bi('\u7eb8\u9762\u56de\u653e', 'Paper Replay')} value={promotion.summary.paper_replay_allowed} />
        <MetricCard label={bi('\u5e73\u5747\u51c6\u5907\u5ea6', 'Avg Ready')} value={pct(promotion.summary.avg_readiness_score)} />
        <MetricCard label={bi('\u5b9e\u76d8\u53d1\u5e03', 'Live Release')} value={promotion.summary.live_release_allowed} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u5019\u9009\u664b\u7ea7', 'Candidate Promotions')}</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {priorityPromotions.map(record => <PromotionCard key={record.promotion_id} record={record} />)}
          </div>
        </div>
        <div className="space-y-4">
          <StageBoard promotion={promotion} />
          <PolicyBoard promotion={promotion} />
        </div>
      </div>

      <div className="rounded-lg border border-red-900/50 bg-red-950/20 p-4 text-xs">
        <h2 className="mb-3 text-sm uppercase text-red-300">{bi('\u5b89\u5168\u8fb9\u754c', 'Safety Boundary')}</h2>
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {promotion.safety_boundary.forbidden_capabilities.map(capability => (
            <div key={capability} className="rounded border border-red-900/40 bg-slate-950/60 px-3 py-2 text-red-200">{capability}</div>
          ))}
        </div>
      </div>

      <GateTable gates={promotion.gates} />
    </div>
  );
}
