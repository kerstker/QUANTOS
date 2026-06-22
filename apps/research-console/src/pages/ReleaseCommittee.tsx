import React, { useEffect, useMemo, useState } from 'react';
import { DataSourceHeader, type ConsoleApiState } from '@/components/data-source';
import { DecisionBadge, MetricCard } from '@/components/status-badges';
import { fetchReleaseCommitteeSnapshot, isResearchConsoleApiEnabled } from '@/lib/api/research-console-api';
import { bi } from '@/lib/i18n/bilingual';
import type { ReleaseCommitteePayload } from '@/lib/types/research';

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

function pct(value: number) {
  return `${Math.round(value * 100)}%`;
}

function decisionClass(value: string) {
  if (value === 'NO_GO' || value === 'BLOCKED' || value === 'REJECT') {
    return 'text-red-300';
  }
  if (value === 'ABSTAIN' || value === 'HOLD' || value === 'DRY_RUN_ONLY') {
    return 'text-amber-300';
  }
  return 'text-emerald-300';
}

function PolicyBoard({committee}: {committee: ReleaseCommitteePayload}) {
  const rows: Array<[string, boolean]> = [
    ['READ_ONLY', committee.committee_policy.read_only],
    ['QUORUM_REQUIRED', committee.committee_policy.requires_quorum],
    ['SIGNATURES_REQUIRED', committee.committee_policy.requires_required_seat_signatures],
    ['ATTESTATIONS_REQUIRED', committee.committee_policy.requires_signed_attestations],
    ['PROMOTION_GO_REQUIRED', committee.committee_policy.requires_promotion_go],
    ['PHASE3A_GO_REQUIRED', committee.committee_policy.requires_phase3a_go],
    ['IMMUTABLE_VOTES', !committee.committee_policy.mutable_votes],
    ['EXTERNAL_SIGNING_DISABLED', !committee.committee_policy.external_signing_enabled],
    ['BROKER_TEST_DISABLED', !committee.committee_policy.broker_test_env_enabled],
    ['LIVE_RELEASE_DISABLED', !committee.committee_policy.live_release_enabled],
    ['NO_LIVE_AUTHORIZATION', !committee.committee_policy.authorizes_live_trading],
  ];
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u59d4\u5458\u4f1a\u7b56\u7565', 'Committee Policy')}</h2>
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

function SeatBoard({committee}: {committee: ReleaseCommitteePayload}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-3 text-sm uppercase text-slate-400">{bi('\u5e2d\u4f4d\u4e0eQuorum', 'Seats & Quorum')}</h2>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {committee.seats.map(seat => (
          <div key={seat.seat_id} className="rounded border border-slate-800 bg-slate-950/60 p-3 text-xs">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-bold text-cyan-300">{seat.seat_id}</div>
                <div className="mt-1 text-slate-200">{seat.role}</div>
              </div>
              <span className={seat.required ? 'text-red-300' : 'text-slate-500'}>{seat.required ? 'REQUIRED' : 'OBSERVER'}</span>
            </div>
            <div className="mt-2 text-slate-400">{seat.member}</div>
            <div className="mt-1 text-slate-500">{seat.domain} / weight {seat.vote_weight}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AgendaBoard({committee}: {committee: ReleaseCommitteePayload}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900">
      <div className="border-b border-slate-800 p-4">
        <h2 className="text-sm uppercase text-slate-400">{bi('\u4f1a\u8bae\u8bae\u7a0b', 'Committee Agenda')}</h2>
      </div>
      <div className="overflow-auto">
        <table className="w-full min-w-[1080px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('\u8bae\u7a0b', 'Agenda')}</th>
              <th className="p-2 text-left">Alpha</th>
              <th className="p-2 text-left">{bi('\u5efa\u8bae', 'Recommendation')}</th>
              <th className="p-2 text-left">{bi('\u51b3\u7b56', 'Decision')}</th>
              <th className="p-2 text-right">{bi('\u51c6\u5907\u5ea6', 'Readiness')}</th>
              <th className="p-2 text-right">{bi('\u5931\u8d25\u95e8', 'Gate Fail')}</th>
              <th className="p-2 text-right">{bi('\u963b\u65ad\u8bc1\u660e', 'Attest')}</th>
              <th className="p-2 text-left">{bi('\u8bc1\u636e\u5305', 'Evidence')}</th>
            </tr>
          </thead>
          <tbody>
            {committee.agenda.map(item => (
              <tr key={item.agenda_id} className="border-b border-slate-800/50">
                <td className="p-2 font-bold text-cyan-300">{item.agenda_id}</td>
                <td className="p-2 text-slate-300">{item.alpha_id}</td>
                <td className={`p-2 font-bold ${decisionClass(item.recommendation)}`}>{item.recommendation}</td>
                <td className={`p-2 font-bold ${decisionClass(item.release_decision)}`}>{item.release_decision}</td>
                <td className="p-2 text-right text-slate-300">{pct(item.readiness_score)}</td>
                <td className="p-2 text-right text-red-300">{item.gate_failures}</td>
                <td className="p-2 text-right text-red-300">{item.blocking_attestations}</td>
                <td className="p-2 text-slate-500">{item.evidence_package_hash}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function VotesBoard({committee}: {committee: ReleaseCommitteePayload}) {
  const sampleVotes = committee.votes.slice(0, 18);
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900">
      <div className="border-b border-slate-800 p-4">
        <h2 className="text-sm uppercase text-slate-400">{bi('\u6295\u7968\u72b6\u6001', 'Vote State')}</h2>
      </div>
      <div className="overflow-auto">
        <table className="w-full min-w-[900px] text-xs">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400 uppercase">
              <th className="p-2 text-left">{bi('\u6295\u7968', 'Vote')}</th>
              <th className="p-2 text-left">{bi('\u8bae\u7a0b', 'Agenda')}</th>
              <th className="p-2 text-left">{bi('\u5e2d\u4f4d', 'Seat')}</th>
              <th className="p-2 text-left">{bi('\u7ed3\u679c', 'Result')}</th>
              <th className="p-2 text-left">{bi('\u7b7e\u7f72', 'Signed')}</th>
              <th className="p-2 text-left">{bi('\u7406\u7531', 'Rationale')}</th>
            </tr>
          </thead>
          <tbody>
            {sampleVotes.map(vote => (
              <tr key={vote.vote_id} className="border-b border-slate-800/50">
                <td className="p-2 font-bold text-cyan-300">{vote.vote_id}</td>
                <td className="p-2 text-slate-300">{vote.agenda_id}</td>
                <td className="p-2 text-slate-400">{vote.role}</td>
                <td className={`p-2 font-bold ${decisionClass(vote.vote)}`}>{vote.vote}</td>
                <td className={vote.signed ? 'p-2 text-emerald-300' : 'p-2 text-red-300'}>{vote.signed ? 'YES' : 'NO'}</td>
                <td className="p-2 text-slate-500">{vote.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function ReleaseCommittee() {
  const [payload, setPayload] = useState<ReleaseCommitteePayload | null>(null);
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
    fetchReleaseCommitteeSnapshot(controller.signal)
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
  const committee = usingBackend && payload ? payload : fallbackCommittee;
  const topBlockers = useMemo(() => committee.blockers.slice(0, 8), [committee.blockers]);

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-lg font-bold text-slate-100">{bi('\u53d1\u5e03\u59d4\u5458\u4f1a', 'Release Committee')}</h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <DecisionBadge decision="READ_ONLY" />
              <DecisionBadge decision={committee.current_level} />
              <DecisionBadge decision={committee.committee_mode} />
              <DecisionBadge decision={committee.committee_decision} />
            </div>
          </div>
          <div className="text-xs text-slate-500 lg:text-right">
            <div>{bi('\u4f1a\u8bae', 'Meeting')}: {committee.meeting_id}</div>
            <div>{bi('Phase 3A', 'Phase 3A')}: {committee.phase3a_decision}</div>
          </div>
        </div>
        <DataSourceHeader usingBackend={usingBackend} apiState={apiState} source={committee.source} error={apiError} />
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <MetricCard label={bi('\u8bae\u7a0b', 'Agenda')} value={committee.summary.agenda_items} />
        <MetricCard label="NO_GO" value={committee.summary.no_go_items} />
        <MetricCard label={bi('Quorum', 'Quorum')} value={`${committee.summary.quorum_present}/${committee.summary.quorum_required}`} />
        <MetricCard label={bi('\u7b7e\u7f72\u7968', 'Signed Votes')} value={committee.summary.signed_votes} />
        <MetricCard label={bi('\u62d2\u7edd\u7968', 'Reject Votes')} value={committee.summary.reject_votes} />
        <MetricCard label={bi('\u963b\u65ad\u8bc1\u660e', 'Block Attest')} value={committee.summary.blocking_attestations} />
        <MetricCard label={bi('\u5238\u5546\u6d4b\u8bd5', 'Broker Test')} value={committee.summary.broker_test_env_allowed} />
        <MetricCard label={bi('\u5b9e\u76d8\u53d1\u5e03', 'Live Release')} value={committee.summary.live_release_allowed} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <SeatBoard committee={committee} />
        <PolicyBoard committee={committee} />
      </div>

      <div className="rounded-lg border border-red-900/50 bg-red-950/20 p-4">
        <h2 className="mb-3 text-sm uppercase text-red-300">{bi('\u963b\u65ad\u9879', 'Release Blockers')}</h2>
        <div className="grid gap-2 text-xs md:grid-cols-2 xl:grid-cols-4">
          {topBlockers.map(blocker => (
            <div key={blocker.blocker_id} className="rounded border border-red-900/40 bg-slate-950/60 p-3">
              <div className="font-bold text-red-200">{blocker.blocker_id} / {blocker.source}</div>
              <div className="mt-1 text-slate-300">{blocker.ref}</div>
              <div className="mt-1 text-slate-500">{blocker.owner}</div>
              <div className="mt-2 text-red-100">{blocker.reason}</div>
            </div>
          ))}
        </div>
      </div>

      <AgendaBoard committee={committee} />
      <VotesBoard committee={committee} />
    </div>
  );
}
