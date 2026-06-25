import React from 'react';
import { fetchReleaseCommitteeSnapshot } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ReleaseCommitteePayload } from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  PageShell, PageHeader, ProvenanceRibbon, Panel, KpiStrip, StatTile, DataTable,
  StatusChip, Chip, GateDot, ProgressBar, KeyValue, type Column,
} from '@/ui/primitives';
import { Donut, RadialGauge } from '@/ui/charts';
import { num, pct, shortHash, statusTone, type Tone } from '@/ui/format';

const fallbackCommittee: ReleaseCommitteePayload = {
  source: 'FRONTEND_RELEASE_COMMITTEE_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  committee_mode: 'READ_ONLY_RELEASE_COMMITTEE',
  meeting_id: 'RC-FALLBACK-READONLY',
  current_level: 'L2_DRY_RUN_ONLY',
  phase3a_decision: 'NO_GO',
  committee_decision: 'NO_GO',
  summary: {
    agenda_items: 2,
    no_go_items: 2,
    dry_run_only_items: 0,
    required_seats: 5,
    quorum_required: 5,
    quorum_present: 0,
    quorum_met: false,
    votes: 6,
    reject_votes: 6,
    abstain_votes: 0,
    signed_votes: 0,
    blocking_attestations: 2,
    open_blocking_tasks: 2,
    broker_test_env_allowed: 0,
    live_release_allowed: 0,
  },
  seats: [
    {seat_id: 'RC-RISK', role: 'Independent Risk', required: true, member: 'Risk Reviewer', domain: 'Risk', vote_weight: 1},
    {seat_id: 'RC-MODEL', role: 'Model Validation', required: true, member: 'Model Validator', domain: 'Model Risk', vote_weight: 1},
    {seat_id: 'RC-COMPLIANCE', role: 'Compliance', required: true, member: 'Compliance Reviewer', domain: 'Compliance', vote_weight: 1},
  ],
  agenda: [
    {agenda_id: 'RC-AGENDA-FB-001', promotion_id: 'PROM-FB-001', alpha_id: 'alpha-fallback-1', requested_stage: 'L2_DRY_RUN_REPLAY', recommendation: 'NO_GO', release_decision: 'BLOCKED', readiness_score: 0.51, gate_failures: 2, watch_gates: 1, blocking_attestations: 2, open_blocking_tasks: 2, evidence_package_hash: 'fallback-committee-001', reason: 'Blocking attestations remain open', next_action: 'Close blocker attestations and reviewer tasks before committee can vote GO', broker_test_env_allowed: false, live_release_allowed: false},
    {agenda_id: 'RC-AGENDA-FB-002', promotion_id: 'PROM-FB-002', alpha_id: 'alpha-fallback-2', requested_stage: 'L2_DRY_RUN_REPLAY', recommendation: 'NO_GO', release_decision: 'BLOCKED', readiness_score: 0.42, gate_failures: 3, watch_gates: 0, blocking_attestations: 2, open_blocking_tasks: 2, evidence_package_hash: 'fallback-committee-002', reason: 'Promotion gates failed', next_action: 'Close failed gates and attach reviewer evidence', broker_test_env_allowed: false, live_release_allowed: false},
  ],
  votes: [
    {vote_id: 'RC-VOTE-FB-001', agenda_id: 'RC-AGENDA-FB-001', seat_id: 'RC-RISK', role: 'Independent Risk', vote: 'REJECT', rationale: 'Blocking gates prevent release', signed: false, signature_hash: null, vote_weight: 1},
    {vote_id: 'RC-VOTE-FB-002', agenda_id: 'RC-AGENDA-FB-001', seat_id: 'RC-MODEL', role: 'Model Validation', vote: 'REJECT', rationale: 'Validation evidence incomplete', signed: false, signature_hash: null, vote_weight: 1},
    {vote_id: 'RC-VOTE-FB-003', agenda_id: 'RC-AGENDA-FB-002', seat_id: 'RC-COMPLIANCE', role: 'Compliance', vote: 'REJECT', rationale: 'Phase 3A NO_GO remains active', signed: false, signature_hash: null, vote_weight: 1},
  ],
  required_attestations: [
    {attestation_id: 'ATT-VALIDATION', name: 'Independent validation evidence complete', owner_role: 'Model Validation', status: 'MISSING', evidence_ref: 'MRM-I01', blocks_release: true},
    {attestation_id: 'ATT-COMPLIANCE', name: 'Phase 3A blockers closed', owner_role: 'Compliance', status: 'BLOCKED', evidence_ref: 'EV-PHASE3A-001', blocks_release: true},
  ],
  blockers: [
    {blocker_id: 'RC-BLOCK-FB-001', source: 'ATTESTATION', ref: 'ATT-VALIDATION', owner: 'Model Validation', reason: 'Independent validation evidence complete', blocks_release: true},
    {blocker_id: 'RC-BLOCK-FB-002', source: 'REVIEWER_WORKFLOW', ref: 'RW-004', owner: 'Compliance Reviewer', reason: 'Confirm Phase 3A governance NO_GO blockers', blocks_release: true},
  ],
  committee_policy: {
    read_only: true,
    requires_quorum: true,
    requires_required_seat_signatures: true,
    requires_signed_attestations: true,
    requires_promotion_go: true,
    requires_phase3a_go: true,
    mutable_votes: false,
    external_signing_enabled: false,
    broker_test_env_enabled: false,
    live_release_enabled: false,
    authorizes_live_trading: false,
  },
};

const decisionTone = (v: string): Tone => statusTone(v);

type Seat = ReleaseCommitteePayload['seats'][number];
type Agenda = ReleaseCommitteePayload['agenda'][number];
type Vote = ReleaseCommitteePayload['votes'][number];

const POLICY_KEYS: ReadonlyArray<{ k: keyof ReleaseCommitteePayload['committee_policy']; label: string; invert?: boolean }> = [
  { k: 'read_only', label: bi('只读', 'READ_ONLY') },
  { k: 'requires_quorum', label: bi('需法定人数', 'QUORUM_REQUIRED') },
  { k: 'requires_required_seat_signatures', label: bi('需席位签名', 'SEAT_SIGNATURES') },
  { k: 'requires_signed_attestations', label: bi('需签署证明', 'SIGNED_ATTEST') },
  { k: 'requires_promotion_go', label: bi('需晋升GO', 'PROMOTION_GO') },
  { k: 'requires_phase3a_go', label: bi('需Phase3A GO', 'PHASE3A_GO') },
  { k: 'mutable_votes', label: bi('投票不可变', 'IMMUTABLE_VOTES'), invert: true },
  { k: 'external_signing_enabled', label: bi('外部签名禁用', 'EXT_SIGN_OFF'), invert: true },
  { k: 'broker_test_env_enabled', label: bi('券商测试禁用', 'BROKER_TEST_OFF'), invert: true },
  { k: 'live_release_enabled', label: bi('实盘发布禁用', 'LIVE_RELEASE_OFF'), invert: true },
  { k: 'authorizes_live_trading', label: bi('无实盘授权', 'NO_LIVE_AUTH'), invert: true },
];

export default function ReleaseCommittee() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<ReleaseCommitteePayload>(
    s => fetchReleaseCommitteeSnapshot(s));

  const committee = usingBackend ? payload! : fallbackCommittee;
  const s = committee.summary;
  const decisionT = decisionTone(committee.committee_decision);

  const quorumFrac = s.quorum_required ? s.quorum_present / s.quorum_required : 0;
  const signedFrac = s.votes ? s.signed_votes / s.votes : 0;

  const seatCols: Column<Seat>[] = [
    { key: 'seat', header: bi('席位', 'Seat'), render: x => <span className="mono font-medium text-accent">{x.seat_id}</span> },
    { key: 'role', header: bi('角色', 'Role'), render: x => <span className="text-ink">{x.role}</span> },
    { key: 'member', header: bi('成员', 'Member'), render: x => <span className="text-muted">{x.member}</span> },
    { key: 'domain', header: bi('领域', 'Domain'), render: x => <span className="text-muted">{x.domain}</span> },
    { key: 'req', header: bi('类别', 'Class'), render: x => <Chip label={x.required ? bi('必需', 'REQUIRED') : bi('观察', 'OBSERVER')} tone={x.required ? 'warn' : 'muted'} /> },
    { key: 'w', header: bi('权重', 'Weight'), align: 'right', mono: true, render: x => num(x.vote_weight) },
  ];

  const agendaCols: Column<Agenda>[] = [
    { key: 'agenda', header: bi('议程', 'Agenda'), render: a => (
      <div className="flex flex-col">
        <span className="mono font-medium text-accent">{a.agenda_id}</span>
        <span className="text-2xs text-faint">{a.alpha_id} · {a.requested_stage}</span>
      </div>
    ) },
    { key: 'rec', header: bi('建议', 'Recommendation'), render: a => <StatusChip value={a.recommendation} /> },
    { key: 'dec', header: bi('发布决策', 'Release'), render: a => <StatusChip value={a.release_decision} /> },
    { key: 'ready', header: bi('准备度', 'Readiness'), width: '150px', render: a => (
      <div className="flex items-center gap-2">
        <ProgressBar value={a.readiness_score} tone={a.readiness_score >= 0.75 ? 'pos' : a.readiness_score >= 0.5 ? 'warn' : 'neg'} />
        <span className="tnum w-10 text-right text-muted">{pct(a.readiness_score, 0)}</span>
      </div>
    ) },
    { key: 'gates', header: bi('门 失败/观察', 'Gate F/W'), align: 'right', mono: true, render: a => (
      <span><span className="text-neg">{num(a.gate_failures)}</span><span className="text-faint"> / </span><span className="text-warn">{num(a.watch_gates)}</span></span>
    ) },
    { key: 'att', header: bi('阻断证明', 'Block Attest'), align: 'right', mono: true, render: a => <span className={a.blocking_attestations > 0 ? 'text-neg' : 'text-pos'}>{num(a.blocking_attestations)}</span> },
    { key: 'tasks', header: bi('阻断任务', 'Open Tasks'), align: 'right', mono: true, render: a => <span className={a.open_blocking_tasks > 0 ? 'text-neg' : 'text-pos'}>{num(a.open_blocking_tasks)}</span> },
    { key: 'broker', header: bi('券商测试', 'Broker'), align: 'center', render: a => <GateDot ok={!a.broker_test_env_allowed} /> },
    { key: 'live', header: bi('实盘', 'Live'), align: 'center', render: a => <GateDot ok={!a.live_release_allowed} /> },
    { key: 'hash', header: bi('证据包', 'Evidence'), mono: true, render: a => <span className="text-faint">{shortHash(a.evidence_package_hash, 12)}</span> },
  ];

  const voteCols: Column<Vote>[] = [
    { key: 'vote', header: bi('投票', 'Vote'), render: v => <span className="mono text-accent">{v.vote_id}</span> },
    { key: 'agenda', header: bi('议程', 'Agenda'), mono: true, render: v => <span className="text-muted">{v.agenda_id}</span> },
    { key: 'seat', header: bi('席位', 'Seat'), render: v => <span className="text-ink">{v.role}</span> },
    { key: 'result', header: bi('结果', 'Result'), render: v => <StatusChip value={v.vote} /> },
    { key: 'weight', header: bi('权重', 'Weight'), align: 'right', mono: true, render: v => num(v.vote_weight) },
    { key: 'signed', header: bi('已签', 'Signed'), align: 'center', render: v => <GateDot ok={v.signed} /> },
    { key: 'sig', header: bi('签名哈希', 'Signature'), mono: true, render: v => <span className="text-faint">{v.signature_hash ? shortHash(v.signature_hash, 12) : bi('未签', 'unsigned')}</span> },
    { key: 'why', header: bi('理由', 'Rationale'), render: v => <span className="text-muted">{v.rationale}</span> },
  ];

  // Vote tally for the donut (deterministic counts off the votes array).
  const reject = committee.votes.filter(v => decisionTone(v.vote) === 'neg').length;
  const abstain = committee.votes.filter(v => /ABSTAIN|HOLD/i.test(v.vote)).length;
  const approve = committee.votes.length - reject - abstain;
  const tally: Array<{ label: string; value: number; tone: Tone }> = ([
    { label: bi('批准', 'Approve'), value: approve, tone: 'pos' as Tone },
    { label: bi('弃权', 'Abstain'), value: abstain, tone: 'warn' as Tone },
    { label: bi('拒绝', 'Reject'), value: reject, tone: 'neg' as Tone },
  ]).filter(t => t.value > 0);

  return (
    <PageShell>
      <PageHeader title="Release Committee" titleZh="发布委员会"
        subtitle={bi('只读发布委员会快照 · 法定人数、席位投票、阻断证明与发布门 — 实盘发布永久受阻',
          'Read-only release-committee snapshot · quorum, seat votes, blocking attestations & release gates — live release permanently blocked')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Chip label={committee.meeting_id} tone="muted" />
            <Chip label={committee.committee_mode} tone="info" />
            <StatusChip value={committee.current_level} />
            <Chip label={bi(`委员会决策 ${committee.committee_decision}`, `DECISION ${committee.committee_decision}`)} tone={decisionT} soft={false} />
          </div>
        } />
      <ProvenanceRibbon usingBackend={usingBackend} apiState={apiState} source={committee.source} error={error} />

      <KpiStrip cols={8}>
        <StatTile label={bi('议程项', 'Agenda Items')} value={num(s.agenda_items)} accent />
        <StatTile label="NO_GO" value={num(s.no_go_items)} tone="neg" />
        <StatTile label={bi('法定人数', 'Quorum')} value={`${num(s.quorum_present)}/${num(s.quorum_required)}`}
          tone={s.quorum_met ? 'pos' : 'neg'} sub={s.quorum_met ? bi('已满足', 'met') : bi('未满足', 'not met')} />
        <StatTile label={bi('签署票', 'Signed Votes')} value={`${num(s.signed_votes)}/${num(s.votes)}`} tone="warn" />
        <StatTile label={bi('拒绝票', 'Reject Votes')} value={num(s.reject_votes)} tone="neg" />
        <StatTile label={bi('阻断证明', 'Block Attest')} value={num(s.blocking_attestations)} tone="neg" />
        <StatTile label={bi('券商测试', 'Broker Test')} value={num(s.broker_test_env_allowed)} tone={s.broker_test_env_allowed ? 'neg' : 'pos'} sub={bi('允许', 'allowed')} />
        <StatTile label={bi('实盘发布', 'Live Release')} value={num(s.live_release_allowed)} tone={s.live_release_allowed ? 'neg' : 'pos'} sub={bi('允许', 'allowed')} />
      </KpiStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel eyebrow={bi('裁决', 'Verdict')} title={bi('委员会决策', 'Committee Decision')}
          tone={decisionT} className="flex flex-col">
          <div className="flex flex-col items-center gap-3 py-2">
            <div className="text-center">
              <div className={`tnum text-3xl font-extrabold ${decisionT === 'neg' ? 'text-neg' : decisionT === 'pos' ? 'text-pos' : 'text-warn'}`}>
                {committee.committee_decision}
              </div>
              <div className="mt-1 text-2xs uppercase tracking-wider text-faint">
                {bi('Phase 3A', 'Phase 3A')} · {committee.phase3a_decision}
              </div>
            </div>
            <div className="flex w-full items-center justify-around gap-2 border-t border-line pt-3">
              <div className="flex flex-col items-center gap-1">
                <RadialGauge value={quorumFrac} max={1} size={104} label={bi('法定', 'Quorum')} tone={s.quorum_met ? 'pos' : 'neg'} />
                <GateDot ok={s.quorum_met} label={s.quorum_met ? bi('已达法定', 'QUORUM MET') : bi('未达法定', 'NO QUORUM')} />
              </div>
              <div className="flex flex-col items-center gap-1">
                <RadialGauge value={signedFrac} max={1} size={104} label={bi('签署', 'Signed')} tone="warn" />
                <span className="text-2xs text-faint">{num(s.signed_votes)} / {num(s.votes)} {bi('已签', 'signed')}</span>
              </div>
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('投票分布', 'Vote Tally')} title={bi('议席投票结果', 'Seat Vote Outcome')}
          className="flex flex-col">
          <div className="flex items-center gap-5">
            <Donut size={132} segments={tally} />
            <div className="flex flex-col gap-2">
              {tally.map(t => (
                <div key={t.label} className="flex items-center justify-between gap-4 text-xs">
                  <span className="flex items-center gap-2 text-muted">
                    <span className="h-2.5 w-2.5 rounded-sm" style={{ background: `var(--${t.tone})` }} />
                    {t.label}
                  </span>
                  <span className="tnum font-semibold text-ink">{num(t.value)}</span>
                </div>
              ))}
              <div className="mt-1 border-t border-line pt-2 text-2xs text-faint">
                {bi('弃权', 'Abstain')}: <span className="tnum">{num(s.abstain_votes)}</span>
              </div>
            </div>
          </div>
        </Panel>

        <Panel eyebrow={bi('治理', 'Governance')} title={bi('委员会策略', 'Committee Policy')}
          right={<GateDot ok={!committee.committee_policy.authorizes_live_trading} label={bi('无实盘授权', 'NO LIVE AUTH')} />}>
          <div className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
            {POLICY_KEYS.map(p => {
              const raw = committee.committee_policy[p.k];
              const ok = p.invert ? !raw : raw;
              return (
                <div key={String(p.k)} className="flex items-center justify-between gap-2 rounded-md border border-line bg-inset/40 px-2.5 py-1.5">
                  <span className="truncate text-2xs text-muted">{p.label}</span>
                  <GateDot ok={ok} label={ok ? 'PASS' : 'FAIL'} />
                </div>
              );
            })}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" eyebrow={bi('席位', 'Seats')} title={bi('委员会席位与法定人数', 'Committee Seats & Quorum')}
          right={<Chip label={bi(`${committee.seats.length} 席`, `${committee.seats.length} seats`)} tone="info" />}>
          <DataTable columns={seatCols} rows={committee.seats} getKey={x => x.seat_id} dense />
        </Panel>

        <Panel eyebrow={bi('门禁', 'Gating')} title={bi('必需证明', 'Required Attestations')}
          right={<Chip label={bi(`${committee.required_attestations.filter(a => a.blocks_release).length} 阻断`, `${committee.required_attestations.filter(a => a.blocks_release).length} blocking`)} tone="neg" />}>
          <div className="flex flex-col gap-2.5">
            {committee.required_attestations.map(att => {
              const ok = statusTone(att.status) === 'pos';
              return (
                <div key={att.attestation_id} className="rounded-md border border-line bg-inset/40 p-2.5">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-ink">{att.name}</div>
                      <div className="text-2xs text-faint">{att.owner_role} · <span className="mono">{att.evidence_ref}</span></div>
                    </div>
                    <GateDot ok={ok} />
                  </div>
                  <div className="mt-1.5 flex items-center justify-between gap-2">
                    <StatusChip value={att.status} />
                    {att.blocks_release && <Chip label={bi('阻断发布', 'BLOCKS RELEASE')} tone="neg" />}
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>
      </div>

      <Panel eyebrow={bi('议程', 'Agenda')} title={bi('委员会议程 · 晋升请求', 'Committee Agenda · Promotion Requests')}
        right={<Chip label={bi(`${committee.summary.no_go_items} 项 NO_GO`, `${committee.summary.no_go_items} NO_GO`)} tone="neg" />}>
        <DataTable columns={agendaCols} rows={committee.agenda} getKey={a => a.agenda_id} dense />
      </Panel>

      {committee.blockers.length > 0 && (
        <Panel eyebrow={bi('阻断', 'Blockers')} title={bi('发布阻断项', 'Release Blockers')} tone="neg"
          right={<Chip label={bi(`${committee.summary.open_blocking_tasks} 个开放任务`, `${committee.summary.open_blocking_tasks} open tasks`)} tone="neg" />}>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {committee.blockers.map(b => (
              <div key={b.blocker_id} className="rounded-lg border border-neg/30 bg-neg/5 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="mono text-2xs font-semibold text-neg">{b.blocker_id}</span>
                  <Chip label={b.source} tone="neg" />
                </div>
                <KeyValue cols={2} items={[
                  { k: bi('引用', 'Ref'), v: b.ref },
                  { k: bi('责任人', 'Owner'), v: b.owner },
                ]} />
                <p className="mt-2 text-xs text-ink">{b.reason}</p>
                {b.blocks_release && <div className="mt-2"><GateDot ok={false} label={bi('阻断发布', 'BLOCKS RELEASE')} /></div>}
              </div>
            ))}
          </div>
        </Panel>
      )}

      <Panel eyebrow={bi('投票', 'Votes')} title={bi('投票状态 · 不可变记录', 'Vote State · Immutable Record')}
        right={<Chip label={bi(`签署 ${committee.summary.signed_votes}/${committee.summary.votes}`, `${committee.summary.signed_votes}/${committee.summary.votes} signed`)} tone="warn" />}>
        <DataTable columns={voteCols} rows={committee.votes} getKey={v => v.vote_id} dense />
      </Panel>
    </PageShell>
  );
}
