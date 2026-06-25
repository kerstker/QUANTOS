import React, { useMemo } from 'react';
import { fetchPromotionControlSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { PromotionControlPayload, PromotionGate, PromotionRecord } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, GateDot, ProgressBar, Chip, KeyValue, type Column,
} from '@/ui/primitives';
import { RadialGauge, Histogram, Donut } from '@/ui/charts';
import { num, pct, shortHash, statusTone, type Tone } from '@/ui/format';

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

function GateBar({ counts }: { counts: { pass: number; watch: number; fail: number } }) {
  const total = counts.pass + counts.watch + counts.fail || 1;
  const seg: Array<[Tone, number]> = [['pos', counts.pass], ['warn', counts.watch], ['neg', counts.fail]];
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-inset">
      {seg.map(([tone, v]) => v > 0 && (
        <div key={tone} style={{ width: `${(v / total) * 100}%`, background: `var(--${tone})` }} />
      ))}
    </div>
  );
}

export default function PromotionControl() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<PromotionControlPayload>(
    s => fetchPromotionControlSnapshot(s));

  const promotion = usingBackend ? payload! : fallbackPromotion;
  const { summary, release_policy: policy, safety_boundary: boundary } = promotion;

  const priorityPromotions = useMemo(
    () => [...promotion.promotions].sort(
      (a, b) => b.gates_failed - a.gates_failed || b.gates_watch - a.gates_watch || a.readiness_score - b.readiness_score),
    [promotion.promotions],
  );

  // decision_counts -> Histogram bins
  const decisionBins = useMemo(
    () => Object.entries(promotion.decision_counts)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ label: k.replace(/_/g, ' ').slice(0, 14), count: v })),
    [promotion.decision_counts],
  );

  // stage roll-up across all stage_counts -> Donut composition (pass/watch/fail)
  const stageRollup = useMemo(() => {
    let pass = 0, watch = 0, fail = 0;
    for (const c of Object.values(promotion.stage_counts)) { pass += c.pass; watch += c.watch; fail += c.fail; }
    return [
      { label: bi('通过', 'Pass'), value: pass, tone: 'pos' as Tone },
      { label: bi('观察', 'Watch'), value: watch, tone: 'warn' as Tone },
      { label: bi('失败', 'Fail'), value: fail, tone: 'neg' as Tone },
    ];
  }, [promotion.stage_counts]);

  const policyRows: Array<[string, boolean]> = [
    [bi('只读', 'READ_ONLY'), policy.read_only],
    [bi('验证批准', 'VALIDATION_APPROVAL'), policy.requires_validation_approval],
    [bi('纸面重放', 'PAPER_REPLAY'), policy.requires_paper_replay],
    [bi('风险批准', 'RISK_APPROVAL'), policy.requires_risk_approval],
    [bi('模型风险评审', 'MODEL_RISK_REVIEW'), policy.requires_model_risk_review],
    [bi('无阻断事件', 'NO_BLOCKING_INCIDENTS'), policy.requires_no_blocking_incidents],
    [bi('证据根哈希', 'EVIDENCE_ROOT_HASH'), policy.requires_evidence_root_hash],
    [bi('访问控制', 'ACCESS_CONTROL'), policy.requires_access_control],
    [bi('晋级不可变', 'IMMUTABLE_PROMOTIONS'), !policy.mutable_promotions],
    [bi('券商测试禁用', 'BROKER_TEST_DISABLED'), !policy.broker_test_env_enabled],
    [bi('实盘发布禁用', 'LIVE_RELEASE_DISABLED'), !policy.live_release_enabled],
    [bi('无实盘授权', 'NO_LIVE_AUTHORIZATION'), !policy.authorizes_live_trading],
  ];

  const promoCols: Column<PromotionRecord>[] = [
    { key: 'id', header: bi('晋级 / Alpha', 'Promotion / Alpha'), render: r => (
      <div className="min-w-0">
        <div className="mono text-info">{r.promotion_id}</div>
        <div className="truncate text-2xs text-faint">{r.alpha_id} · <span className="mono">{r.expression}</span></div>
      </div>
    ) },
    { key: 'stage', header: bi('阶段', 'Stage'), render: r => (
      <div className="flex flex-col gap-0.5">
        <Chip label={r.current_stage} tone={statusTone(r.current_stage)} />
        <span className="text-2xs text-faint">→ {r.requested_stage}</span>
      </div>
    ) },
    { key: 'decision', header: bi('裁决', 'Decision'), render: r => (
      <div className="flex flex-col gap-0.5">
        <StatusChip value={r.decision} />
        <span className="max-w-[18rem] truncate text-2xs text-faint">{r.decision_reason}</span>
      </div>
    ) },
    { key: 'ready', header: bi('准备度', 'Readiness'), align: 'center', width: '92px', render: r => (
      <div className="flex justify-center"><RadialGauge value={r.readiness_score} max={1} size={76} /></div>
    ) },
    { key: 'gates', header: bi('门禁 P/W/F', 'Gates P/W/F'), width: '150px', render: r => (
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-1.5 text-2xs tnum">
          <span className="text-pos">{r.gates_passed}</span><span className="text-faint">/</span>
          <span className="text-warn">{r.gates_watch}</span><span className="text-faint">/</span>
          <span className="text-neg">{r.gates_failed}</span>
          <span className="text-faint">· {bi('共', 'of')} {r.gates_total}</span>
        </div>
        <GateBar counts={{ pass: r.gates_passed, watch: r.gates_watch, fail: r.gates_failed }} />
        {r.blocking_gates.length > 0 && (
          <span className="text-2xs text-neg">⛔ {r.blocking_gates.join(', ')}</span>
        )}
      </div>
    ) },
    { key: 'paper', header: bi('纸面回放', 'Paper'), align: 'center', render: r => <div className="flex justify-center"><GateDot ok={r.paper_replay_allowed} /></div> },
    { key: 'broker', header: bi('券商沙箱', 'Broker'), align: 'center', render: r => <div className="flex justify-center"><GateDot ok={r.broker_test_env_allowed} /></div> },
    { key: 'live', header: bi('实盘', 'Live'), align: 'center', render: r => <div className="flex justify-center"><GateDot ok={r.live_release_allowed} /></div> },
    { key: 'next', header: bi('下一步', 'Next Action'), render: r => <span className="text-2xs text-muted">{r.next_action}</span> },
  ];

  const gateCols: Column<PromotionGate>[] = [
    { key: 'gate', header: bi('门禁', 'Gate'), mono: true, render: g => <span className="text-info">{g.gate_id}</span> },
    { key: 'alpha', header: 'Alpha', mono: true, render: g => <span className="text-muted">{g.alpha_id}</span> },
    { key: 'stage', header: bi('阶段', 'Stage'), render: g => <span className="text-muted">{g.stage}</span> },
    { key: 'status', header: bi('状态', 'Status'), render: g => <StatusChip value={g.status} /> },
    { key: 'owner', header: bi('负责人', 'Owner'), render: g => <span className="text-muted">{g.owner}</span> },
    { key: 'score', header: bi('分数', 'Score'), width: '120px', render: g => (
      <div className="flex items-center gap-2">
        <ProgressBar value={g.score} tone={statusTone(g.status)} />
        <span className="tnum w-9 text-right text-muted">{pct(g.score, 0)}</span>
      </div>
    ) },
    { key: 'paper', header: bi('阻纸面', 'Blk Paper'), align: 'center', render: g => <div className="flex justify-center"><GateDot ok={!g.blocks_paper_replay} /></div> },
    { key: 'broker', header: bi('阻沙箱', 'Blk Broker'), align: 'center', render: g => <div className="flex justify-center"><GateDot ok={!g.blocks_broker_test_env} /></div> },
    { key: 'live', header: bi('阻实盘', 'Blk Live'), align: 'center', render: g => <div className="flex justify-center"><GateDot ok={!g.blocks_live_authorization} /></div> },
    { key: 'evidence', header: bi('证据', 'Evidence'), mono: true, render: g => <span className="text-faint">{shortHash(g.evidence_ref, 14)}</span> },
    { key: 'reason', header: bi('原因', 'Reason'), render: g => <span className="text-2xs text-muted">{g.reason}</span> },
  ];

  return (
    <PageShell>
      <PageHeader title="Promotion Control" titleZh="研究晋级控制"
        subtitle={bi('只读晋级门禁矩阵 · 实盘发布永久受阻(NO_GO)', 'Read-only promotion gate matrix · live release permanently blocked (NO_GO)')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Chip label={bi('只读', 'READ_ONLY')} tone="info" />
            <Chip label={promotion.current_level} tone={statusTone(promotion.current_level)} />
            <StatusChip value={promotion.phase3a_decision} />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={promotion.source} error={error} />

      <KpiStrip cols={8}>
        <StatTile label={bi('候选', 'Candidates')} value={num(summary.candidates)} accent />
        <StatTile label={bi('干跑通过', 'Dry-run OK')} value={num(summary.approved_for_dry_run)} tone="pos" />
        <StatTile label={bi('阻断', 'Blocked')} value={num(summary.blocked)} tone="neg" />
        <StatTile label={bi('观察门', 'Watch Gates')} value={num(summary.watch_gates)} tone="warn" />
        <StatTile label={bi('失败门', 'Failed Gates')} value={num(summary.failed_gates)} tone="neg" />
        <StatTile label={bi('纸面回放', 'Paper Replay')} value={num(summary.paper_replay_allowed)} tone="info" />
        <StatTile label={bi('实盘发布', 'Live Release')} value={num(summary.live_release_allowed)} tone="neg"
          sub={bi('永久受阻', 'permanently blocked')} />
        <StatTile label={bi('平均准备度', 'Avg Readiness')} value={pct(summary.avg_readiness_score, 0)} tone="accent" />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('综合', 'Aggregate')} title={bi('阶段门禁构成', 'Gate Stage Composition')}>
          <div className="flex flex-col items-center gap-3">
            <Donut size={150} segments={stageRollup} />
            <div className="flex flex-wrap justify-center gap-x-4 gap-y-1.5">
              {stageRollup.map(s => (
                <div key={String(s.label)} className="flex items-center gap-1.5 text-2xs">
                  <span className="h-2 w-2 rounded-full" style={{ background: `var(--${s.tone})` }} />
                  <span className="text-muted">{s.label}</span>
                  <span className="tnum text-ink">{s.value}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel className="xl:col-span-2" eyebrow={bi('裁决', 'Decisions')} title={bi('晋级裁决分布', 'Promotion Decision Counts')}>
          <Histogram height={200} tone="info"
            bins={decisionBins.length ? decisionBins : [{ label: bi('无', 'none'), count: 0 }]} />
        </Panel>
      </div>

      <Panel eyebrow={bi('候选晋级', 'Candidate Promotions')} title={bi('晋级请求与门禁就绪度', 'Promotion Requests & Gate Readiness')}
        right={<Chip label={bi(`${promotion.promotions.length} 项`, `${promotion.promotions.length} requests`)} tone="info" />}>
        <DataTable columns={promoCols} rows={priorityPromotions} getKey={r => r.promotion_id}
          empty={bi('无晋级请求', 'No promotion requests')} />
      </Panel>

      <Panel eyebrow={bi('门禁矩阵', 'Gate Matrix')} title={bi('全量晋级门禁', 'Full Promotion Gate Matrix')}
        right={<Chip label={bi(`${promotion.gates.length} 门`, `${promotion.gates.length} gates`)} tone="muted" />}>
        <DataTable columns={gateCols} rows={promotion.gates} getKey={g => g.gate_id}
          empty={bi('无门禁记录', 'No gate records')} />
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('发布策略', 'Release Policy')} title={bi('晋级与发布前置条件', 'Promotion & Release Preconditions')}>
          <div className="grid gap-2 sm:grid-cols-2">
            {policyRows.map(([label, ok]) => (
              <div key={label} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-inset/40 px-3 py-2">
                <span className="text-2xs text-muted">{label}</span>
                <GateDot ok={ok} label={ok ? bi('就绪', 'OK') : bi('未满足', 'FAIL')} />
              </div>
            ))}
          </div>
        </Panel>

        <Panel eyebrow={bi('执行边界', 'Execution Boundary')} title={bi('安全边界', 'Safety Boundary')} tone="neg">
          <div className="flex flex-col gap-3">
            <KeyValue cols={1} items={[
              { k: bi('执行边界', 'Execution Boundary'), v: <span className="text-neg">{boundary.execution_boundary}</span> },
              { k: bi('Phase 3A 裁决', 'Phase 3A Decision'), v: <span className="text-neg">{boundary.phase3a_decision}</span> },
              { k: bi('目标阶段', 'Target Stage'), v: promotion.target_stage },
              { k: bi('晋级模式', 'Promotion Mode'), v: promotion.promotion_mode },
            ]} />
            <div className="border-t border-line pt-3">
              <div className="mb-2 text-2xs uppercase tracking-wide text-faint">{bi('禁止能力', 'Forbidden Capabilities')}</div>
              <div className="flex flex-col gap-1.5">
                {boundary.forbidden_capabilities.map(cap => (
                  <div key={cap} className="flex items-center gap-2 text-2xs text-neg">
                    <GateDot ok={false} /><span>{cap}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Panel>
      </div>
    </PageShell>
  );
}
