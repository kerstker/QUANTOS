import { useMemo } from 'react';
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
import { bi } from '@/lib/i18n/bilingual';

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

type BlockerLane = 'WORK NOW' | 'EXTERNAL' | 'AFTER L2';

const blockerLane: Record<string, { label: BlockerLane; order: number; tone: 'accent' | 'warn' | 'info' }> = {
  L2_SAFE_NOW: { label: 'WORK NOW', order: 0, tone: 'accent' },
  NEEDS_EXTERNAL: { label: 'EXTERNAL', order: 1, tone: 'warn' },
  POST_L2: { label: 'AFTER L2', order: 2, tone: 'info' },
};

function laneFor(requirement: TopTierRequirement) {
  return blockerLane[requirement.classification] ?? blockerLane.NEEDS_EXTERNAL;
}

function BlockerCard({ requirement, index }: { requirement: TopTierRequirement; index: number }) {
  const lane = laneFor(requirement);
  return (
    <article
      data-testid="prelive-blocker"
      className="grid gap-3 rounded-lg border border-line bg-inset/35 p-4 lg:grid-cols-[54px_minmax(220px,0.8fr)_minmax(280px,1.2fr)] lg:items-start"
    >
      <div className="ticker-num text-lg font-semibold text-faint">{String(index + 1).padStart(2, '0')}</div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Chip label={lane.label} tone={lane.tone} />
          <StatusChip value={requirement.status} />
          <span className="ticker-num text-2xs text-faint">
            {requirement.current_maturity} → {requirement.target_maturity}
          </span>
        </div>
        <div className="mt-2 break-words font-mono text-xs font-semibold text-ink">{requirement.id}</div>
        <div className="mt-1 text-2xs text-muted">{requirement.description}</div>
      </div>
      <div className="min-w-0 border-t border-line pt-3 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
        <div className="text-3xs font-semibold uppercase tracking-[0.12em] text-faint">Next controlled action</div>
        <p className="mt-1 text-xs leading-relaxed text-muted">{requirement.next_action}</p>
        <p className="mt-2 text-2xs leading-relaxed text-faint">{requirement.detail}</p>
      </div>
    </article>
  );
}

export default function SystemCompleteness() {
  const { payload, apiState, error, usingBackend } = useConsoleSnapshot<SystemCompletenessPayload>(
    fetchSystemCompletenessSnapshot,
  );
  const assessment = payload ?? unavailableAssessment;
  const tierA = useMemo(() => assessment.requirements.filter(item => item.tier === 'A'), [assessment.requirements]);
  const tierB = useMemo(() => assessment.requirements.filter(item => item.tier === 'B'), [assessment.requirements]);
  const blockers = useMemo(
    () => assessment.requirements
      .filter(item => item.status !== 'PASS')
      .sort((left, right) => {
        const laneDelta = laneFor(left).order - laneFor(right).order;
        return laneDelta || left.id.localeCompare(right.id);
      }),
    [assessment.requirements],
  );
  const blockerCounts = useMemo(() => ({
    workNow: blockers.filter(item => laneFor(item).label === 'WORK NOW').length,
    external: blockers.filter(item => laneFor(item).label === 'EXTERNAL').length,
    afterL2: blockers.filter(item => laneFor(item).label === 'AFTER L2').length,
  }), [blockers]);
  const verified = usingBackend && assessment.data_provenance === TOP_TIER_EVIDENCE_ASSESSMENT;

  return (
    <PageShell>
      <PageHeader
        title="System Completeness Audit"
        titleZh="系统完整度审查"
        subtitle={bi(
          '先看未完成项与下一受控动作；完整证据矩阵保留在高级审计区。没有主观完成百分比。',
          'Lead with unresolved requirements and their next controlled action. The complete machine-verifiable evidence matrix remains in Advanced audit.',
        )}
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

      <KpiStrip cols={4}>
        <StatTile label="System Verdict" value={assessment.assessment.verdict} tone={statusTone(assessment.assessment.verdict)} sub={`schema v${assessment.assessment.schema_version}`} />
        <StatTile label="Verified" value={`${num(assessment.summary.passed)}/${num(assessment.summary.total)}`} tone="pos" sub="machine evidence meets target" />
        <StatTile label="Open Requirements" value={num(blockers.length)} tone={blockers.length ? 'warn' : 'pos'} sub="ordered by action boundary" />
        <StatTile label="Red Lines Open" value={num(assessment.summary.red_lines_failed)} tone={assessment.summary.red_lines_failed ? 'neg' : 'pos'} sub="fail-closed top-tier verdict" />
      </KpiStrip>

      <Panel
        eyebrow="Decision Queue"
        title="Pre-live blocker queue"
        right={(
          <div className="flex flex-wrap gap-2">
            <Chip label={`${blockerCounts.workNow} work now`} tone="accent" />
            <Chip label={`${blockerCounts.external} external`} tone="warn" />
            <Chip label={`${blockerCounts.afterL2} after L2`} tone="info" />
          </div>
        )}
        bodyClass="p-4"
      >
        <div className="space-y-3">
          {blockers.map((requirement, index) => (
            <BlockerCard key={requirement.id} requirement={requirement} index={index} />
          ))}
          {!blockers.length && (
            <div className="rounded-lg border border-pos/30 bg-pos/5 px-4 py-6 text-center text-sm text-pos">
              No unresolved machine requirements.
            </div>
          )}
        </div>
      </Panel>

      <details className="group rounded-xl border border-line bg-panel/70">
        <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-ink marker:content-none">
          <span className="flex items-center justify-between gap-3">
            <span>Advanced evidence matrix</span>
            <span className="text-2xs font-normal text-faint group-open:hidden">22 requirements · expand</span>
            <span className="hidden text-2xs font-normal text-faint group-open:inline">collapse</span>
          </span>
        </summary>
        <div className="space-y-4 border-t border-line p-4">
          <Panel eyebrow="Evidence Domains" title="Domain Maturity"
            right={<Chip label={`${assessment.domains.length} domains`} tone="accent" />}>
            <DataTable columns={domainColumns} rows={assessment.domains} dense getKey={row => row.domain} empty="No verified domain assessment" />
          </Panel>

          <RequirementPanel title="Tier A — Institutional Research Grade" tier="Tier A" rows={tierA} />
          <RequirementPanel title="Tier B — Production Trading Grade" tier="Tier B" rows={tierB} />

          <Panel eyebrow="Fail-Closed" title="Red Lines"
            tone={assessment.red_lines.length ? 'neg' : 'pos'}
            right={<Chip label={`${assessment.red_lines.length} unresolved`} tone={assessment.red_lines.length ? 'neg' : 'pos'} />}>
            <DataTable columns={requirementColumns} rows={assessment.red_lines} dense
              getKey={row => `red-line-${row.id}`} empty="No unresolved red lines" />
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
        </div>
      </details>
      </LoadingVeil>
    </PageShell>
  );
}
