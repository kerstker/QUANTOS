import React, { useMemo } from 'react';
import { fetchSystemCompletenessSnapshot } from '@/lib/api/research-console-api';
import type {
  SystemCompletenessPayload,
  TopTierDomainAssessment,
  TopTierRequirement,
} from '@/lib/types/research';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  Chip,
  DataTable,
  KpiStrip,
  LoadingVeil,
  PageHeader,
  PageShell,
  Panel,
  ProvenanceRibbon,
  StatTile,
  StatusChip,
  type Column,
} from '@/ui/primitives';
import { num, statusTone } from '@/ui/format';

const TOP_TIER_EVIDENCE_ASSESSMENT = 'TOP_TIER_EVIDENCE_ASSESSMENT';

const unavailableAssessment: SystemCompletenessPayload = {
  source: 'FRONTEND_UNVERIFIED_FALLBACK',
  read_only: true,
  api_version: '0.1.0',
  data_provenance: 'UNVERIFIED',
  assessment: { schema_version: 1, verdict: 'UNVERIFIED' },
  summary: {
    total: 0,
    passed: 0,
    partial: 0,
    failed: 0,
    unverified: 0,
    manual_evidence_required: 0,
    red_lines_failed: 0,
  },
  domains: [],
  requirements: [],
  red_lines: [],
  safety_boundary: {
    read_only_console: true,
    execution_boundary: 'LIVE_TRADING_DISALLOWED',
    authorized_level: 'L2_DRY_RUN_ONLY',
    live_trading_authorized: false,
  },
  inventory: {
    domains: [],
    total_services_modules: 0,
    total_services_loc: 0,
    n_test_files: 0,
    n_test_functions: 0,
    n_console_pages: 0,
    n_console_endpoints: 0,
  },
};

function evidenceCell(requirement: TopTierRequirement) {
  if (!requirement.evidence.length) {
    return <span className="text-faint">No machine evidence</span>;
  }
  return (
    <div className="flex max-w-[420px] flex-col gap-1">
      {requirement.evidence.map(item => (
        <code key={item} className="break-all text-2xs text-info">{item}</code>
      ))}
    </div>
  );
}

const requirementColumns: Column<TopTierRequirement>[] = [
  {
    key: 'id',
    header: 'Requirement',
    width: '210px',
    mono: true,
    render: requirement => (
      <div className="flex flex-col gap-1">
        <span>{requirement.id}</span>
        <span className="font-sans text-2xs text-faint">{requirement.domain}</span>
      </div>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    width: '155px',
    render: requirement => <StatusChip value={requirement.status} />,
  },
  {
    key: 'maturity',
    header: 'Maturity',
    width: '130px',
    mono: true,
    render: requirement => `${requirement.current_maturity} → ${requirement.target_maturity}`,
  },
  {
    key: 'classification',
    header: 'Boundary',
    width: '145px',
    render: requirement => <Chip label={requirement.classification} tone={statusTone(requirement.classification)} />,
  },
  {
    key: 'evidence',
    header: 'Evidence',
    render: evidenceCell,
  },
  {
    key: 'action',
    header: 'Detail / Next Action',
    render: requirement => (
      <div className="min-w-[260px] max-w-[460px]">
        <div className="text-muted">{requirement.detail}</div>
        <div className="mt-1 text-2xs text-faint">Next: {requirement.next_action}</div>
      </div>
    ),
  },
];

const domainColumns: Column<TopTierDomainAssessment>[] = [
  { key: 'domain', header: 'Domain', mono: true, render: domain => domain.domain },
  { key: 'maturity', header: 'Current Maturity', mono: true, render: domain => domain.current_maturity },
  { key: 'requirements', header: 'Requirements', align: 'right', render: domain => num(domain.requirements) },
  { key: 'passed', header: 'Passed', align: 'right', render: domain => num(domain.passed) },
  {
    key: 'red-lines',
    header: 'Red Lines Open',
    align: 'right',
    render: domain => <span className={domain.red_lines_failed ? 'text-neg' : 'text-pos'}>{num(domain.red_lines_failed)}</span>,
  },
];

function RequirementPanel({ title, tier, rows }: { title: string; tier: string; rows: TopTierRequirement[] }) {
  return (
    <Panel
      eyebrow={tier}
      title={title}
      right={<Chip label={`${rows.length} requirements`} tone="info" />}
    >
      <DataTable
        columns={requirementColumns}
        rows={rows}
        dense
        getKey={row => row.id}
        empty="No requirements in this tier"
      />
    </Panel>
  );
}

export default function SystemCompleteness() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<SystemCompletenessPayload>(
    fetchSystemCompletenessSnapshot,
  );
  const assessment = payload ?? unavailableAssessment;
  const tierA = useMemo(() => assessment.requirements.filter(item => item.tier === 'A'), [assessment.requirements]);
  const tierB = useMemo(() => assessment.requirements.filter(item => item.tier === 'B'), [assessment.requirements]);
  const verified = usingBackend && assessment.data_provenance === TOP_TIER_EVIDENCE_ASSESSMENT;

  return (
    <PageShell>
      <PageHeader
        title="System Completeness Audit"
        titleZh="系统完整度审查"
        subtitle="Machine-verifiable Tier A research-grade and Tier B production-grade evidence. No subjective completion percentages."
        actions={(
          <>
            <StatusChip value={assessment.assessment.verdict} />
            <Chip label={assessment.safety_boundary.authorized_level} tone="info" />
            <Chip label="READ_ONLY" tone="info" />
          </>
        )}
      />

      <ProvenanceRibbon
        usingBackend={usingBackend}
        apiState={apiState}
        source={assessment.data_provenance ?? assessment.source}
        error={error}
        evidenceData={verified}
      />

      <LoadingVeil active={apiState === 'loading'}>
      {!verified && (
        <Panel tone="warn" title="Assessment unavailable" eyebrow="UNVERIFIED">
          <p className="text-sm text-warn">assessment artifact unavailable</p>
          <p className="mt-2 text-xs text-muted">Start the read-only console API or regenerate the governed assessment artifact. No fallback score is inferred.</p>
        </Panel>
      )}

      <KpiStrip cols={6}>
        <StatTile label="System Verdict" value={assessment.assessment.verdict} tone={statusTone(assessment.assessment.verdict)} sub={`schema v${assessment.assessment.schema_version}`} />
        <StatTile label="Requirements" value={num(assessment.summary.total)} sub="Tier A + Tier B" />
        <StatTile label="Passed" value={num(assessment.summary.passed)} tone="pos" sub="machine evidence meets target" />
        <StatTile label="Partial" value={num(assessment.summary.partial)} tone="warn" sub="evidence exists below target" />
        <StatTile label="Manual Evidence" value={num(assessment.summary.manual_evidence_required)} tone="warn" sub="external proof required" />
        <StatTile label="Red Lines Open" value={num(assessment.summary.red_lines_failed)} tone={assessment.summary.red_lines_failed ? 'neg' : 'pos'} sub="fail-closed top-tier verdict" />
      </KpiStrip>

      <Panel
        eyebrow="Evidence Domains"
        title="Domain Maturity"
        right={<Chip label={`${assessment.domains.length} domains`} tone="accent" />}
      >
        <DataTable columns={domainColumns} rows={assessment.domains} dense getKey={row => row.domain} empty="No verified domain assessment" />
      </Panel>

      <RequirementPanel title="Tier A — Institutional Research Grade" tier="Tier A" rows={tierA} />
      <RequirementPanel title="Tier B — Production Trading Grade" tier="Tier B" rows={tierB} />

      <Panel
        eyebrow="Fail-Closed"
        title="Red Lines"
        tone={assessment.red_lines.length ? 'neg' : 'pos'}
        right={<Chip label={`${assessment.red_lines.length} unresolved`} tone={assessment.red_lines.length ? 'neg' : 'pos'} />}
      >
        <DataTable
          columns={requirementColumns}
          rows={assessment.red_lines}
          dense
          getKey={row => `red-line-${row.id}`}
          empty="No unresolved red lines"
        />
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel eyebrow="Repository Evidence" title="Live Inventory">
          <KpiStrip cols={3}>
            <StatTile label="Service Modules" value={num(assessment.inventory.total_services_modules)} />
            <StatTile label="Service LOC" value={num(assessment.inventory.total_services_loc)} />
            <StatTile label="Test Functions" value={num(assessment.inventory.n_test_functions)} />
            <StatTile label="Test Files" value={num(assessment.inventory.n_test_files)} />
            <StatTile label="Console Pages" value={num(assessment.inventory.n_console_pages)} />
            <StatTile label="Console Endpoints" value={num(assessment.inventory.n_console_endpoints)} />
          </KpiStrip>
        </Panel>

        <Panel eyebrow="Non-Negotiable" title="L2 Safety Boundary">
          <div className="flex flex-wrap gap-2">
            <Chip label={assessment.safety_boundary.execution_boundary} tone="neg" />
            <Chip label={assessment.safety_boundary.authorized_level} tone="info" />
            <Chip label={assessment.safety_boundary.read_only_console ? 'READ_ONLY_CONSOLE' : 'CONSOLE_MUTATION_ENABLED'} tone={assessment.safety_boundary.read_only_console ? 'pos' : 'neg'} />
            <Chip label={assessment.safety_boundary.live_trading_authorized ? 'LIVE_AUTHORIZED' : 'LIVE_NOT_AUTHORIZED'} tone={assessment.safety_boundary.live_trading_authorized ? 'neg' : 'pos'} />
          </div>
          <p className="mt-4 text-xs leading-relaxed text-muted">
            Tier B requirements may be assessed and planned, but this console does not authorize live trading, broker connectivity, credential access, or AI-directed order submission.
          </p>
        </Panel>
      </div>
      </LoadingVeil>
    </PageShell>
  );
}
