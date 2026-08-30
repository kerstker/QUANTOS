import { useEffect, useMemo, useState, type ReactNode } from 'react';

import type { ResearchExperiment, ValidationReport } from '@/lib/types/research';
import { PageShell } from '@/ui/primitives';
import type { HourlyAlphaCandidate, HourlyAlphaSnapshot } from '@/workbench/hourly-alpha-contract';
import { useValidationEvidenceData } from '@/workbench/useValidationEvidenceData';

type Outcome = 'PASS' | 'BLOCK' | 'UNKNOWN' | 'UNAVAILABLE';

function short(value: string | null): string {
  if (value === null) return 'UNAVAILABLE';
  return value.length > 18 ? `${value.slice(0, 12)}…${value.slice(-4)}` : value;
}

function fixed(value: number, digits = 3): string {
  return Number.isFinite(value) ? value.toFixed(digits) : 'UNAVAILABLE';
}

function money(value: number): string {
  return Number.isFinite(value)
    ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value)
    : 'UNAVAILABLE';
}

function timestamp(seconds: number | null): string {
  if (seconds === null) return 'UNAVAILABLE';
  return new Date(seconds * 1_000).toISOString().replace('T', ' ').replace('.000Z', 'Z');
}

function outcomeTone(outcome: Outcome): string {
  if (outcome === 'PASS') return 'border-pos/35 bg-pos/10 text-pos';
  if (outcome === 'BLOCK') return 'border-neg/35 bg-neg/10 text-neg';
  if (outcome === 'UNKNOWN') return 'border-warn/35 bg-warn/10 text-warn';
  return 'border-line bg-inset text-muted';
}

function OutcomeBadge({ outcome, label }: { outcome: Outcome; label?: string }) {
  const icon = outcome === 'PASS' ? '✓' : outcome === 'BLOCK' ? '✕' : '?';
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[10px] font-semibold ${outcomeTone(outcome)}`}>
      <span aria-hidden>{icon}</span>{label ?? outcome}
    </span>
  );
}

function Panel({ eyebrow, title, right, children }: {
  eyebrow: string;
  title: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-panel shadow-sm">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-faint">{eyebrow}</p>
          <h2 className="mt-1 text-base font-semibold text-ink">{title}</h2>
        </div>
        {right}
      </header>
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

function DiagnosticOnly({ phase, reason }: { phase: string; reason: string | null }) {
  const quarantined = phase === 'QUARANTINED';
  const label = phase === 'STALE'
    ? 'VALIDATION_AUTHORITY_STALE'
    : quarantined ? 'VALIDATION_V1_AUTHORITY_QUARANTINED' : 'VALIDATION_AUTHORITY_UNAVAILABLE';
  return (
    <PageShell>
      <header>
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Validation Evidence</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">验证证据</h1>
      </header>
      <section className={`rounded-xl border p-5 ${quarantined ? 'border-warn/35 bg-warn/10' : 'border-neg/35 bg-neg/10'}`} role="status">
        <p className={`font-mono text-sm font-semibold ${quarantined ? 'text-warn' : 'text-neg'}`}>{label}</p>
        <p className="mt-2 font-mono text-xs text-muted">{reason ?? phase}</p>
        <p className={`mt-4 inline-flex rounded border px-2 py-1 font-mono text-[10px] ${quarantined ? 'border-warn/35 text-warn' : 'border-neg/35 text-neg'}`}>
          {quarantined ? 'EXPECTED_SAFE_ISOLATION' : 'DIAGNOSTIC_ONLY'}
        </p>
        <p className="mt-3 text-xs leading-5 text-muted">
          {quarantined
            ? '旧 V1 验证权威已按策略隔离；保留结果不跨越当前权威边界。新的 PIT 技术验证会在独立证据面展示。'
            : '权威状态不可用或陈旧时，不显示保留的 PASS、指标、artifact 或前向结果，也不回退到旧页面模拟数据。'}
        </p>
      </section>
    </PageShell>
  );
}

function candidateOutcome(candidate: HourlyAlphaCandidate): Outcome {
  if (candidate.state === 'VALIDATED') return 'PASS';
  if (candidate.state === 'BLOCKED') return 'BLOCK';
  return 'UNKNOWN';
}

const GATES: ReadonlyArray<[string, keyof ValidationReport]> = [
  ['LEAKAGE', 'leakage_pass'],
  ['WALK_FORWARD', 'walk_forward_pass'],
  ['MULTIPLE_TESTING', 'multiple_testing_pass'],
  ['COST', 'costs_pass'],
  ['CAPACITY', 'capacity_pass'],
  ['DECAY', 'decay_pass'],
];

function FalsificationMatrix({ report }: { report: ValidationReport | null }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
      {GATES.map(([name, key]) => {
        const outcome: Outcome = report === null ? 'UNAVAILABLE' : report[key] === true ? 'PASS' : 'BLOCK';
        return (
          <div key={name} className={`rounded-lg border p-3 ${outcomeTone(outcome)}`}>
            <p className="font-mono text-xs font-semibold">{name} · {outcome}</p>
            <p className="mt-1 text-[10px] opacity-80">
              {report === null ? 'NO_EXACT_MATCH' : `aux run ${report.run_id}`}
            </p>
          </div>
        );
      })}
    </div>
  );
}

function Binding({ candidate, snapshot }: {
  candidate: HourlyAlphaCandidate;
  snapshot: HourlyAlphaSnapshot;
}) {
  const rows = [
    ['Candidate', candidate.candidate_id],
    ['Trial input identity', candidate.trial_id ?? 'UNAVAILABLE'],
    ['Artifact commitment', candidate.artifact_commitment ?? 'UNAVAILABLE'],
    ['Read schema', snapshot.schema_version],
    ['Snapshot', short(snapshot.snapshot_id)],
    ['As-of / freshness', `${timestamp(snapshot.as_of)} · ${snapshot.freshness.status} · age ${snapshot.freshness.age_seconds ?? 'UNKNOWN'}s`],
  ];
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="min-w-0 rounded-lg border border-line bg-inset/50 p-3">
          <dt className="text-[10px] uppercase tracking-[0.1em] text-faint">{label}</dt>
          <dd className="mt-1 break-all font-mono text-xs text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function DagSummary() {
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      {['CHEAP', 'OOS', 'REALISM'].map(stage => (
        <div key={stage} className="rounded-lg border border-line bg-inset/50 p-3">
          <p className="font-mono text-xs font-semibold text-muted">{stage} · UNAVAILABLE_IN_V1</p>
          <p className="mt-1 text-[10px] leading-4 text-faint">
            stage receipt hash/version/implementation_id 未由 hourly-alpha-console.v1 暴露。
          </p>
        </div>
      ))}
    </div>
  );
}

function ExperimentSummary({ experiment }: { experiment: ResearchExperiment | null }) {
  if (experiment === null) {
    return <p className="font-mono text-xs text-faint">UNAVAILABLE · NO_EXACT_MATCH</p>;
  }
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-lg border border-line bg-inset/50 p-3">
          <p className="text-[10px] uppercase text-faint">Backtest</p>
          <p className="mt-1 font-mono text-xs text-ink">Sharpe {fixed(experiment.metrics.sharpe)} · IC {fixed(experiment.metrics.ic)}</p>
        </div>
        <div className="rounded-lg border border-line bg-inset/50 p-3">
          <p className="text-[10px] uppercase text-faint">Cost assumption</p>
          <p className="mt-1 font-mono text-xs text-ink">{experiment.config.cost_bps} bps</p>
        </div>
        <div className="rounded-lg border border-line bg-inset/50 p-3">
          <p className="text-[10px] uppercase text-faint">Capacity estimate</p>
          <p className="mt-1 font-mono text-xs text-ink">{money(experiment.metrics.capacity_estimate)}</p>
        </div>
        <div className="rounded-lg border border-line bg-inset/50 p-3">
          <p className="text-[10px] uppercase text-faint">Dataset</p>
          <p className="mt-1 break-all font-mono text-xs text-ink">{experiment.config.data_snapshot_id}</p>
        </div>
      </div>
      <dl className="grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg border border-line bg-inset/50 p-3">
          <dt className="text-[10px] uppercase text-faint">Config hash</dt>
          <dd className="mt-1 break-all font-mono text-xs text-ink">{experiment.config.config_hash}</dd>
        </div>
        <div className="rounded-lg border border-line bg-inset/50 p-3">
          <dt className="text-[10px] uppercase text-faint">Reproducibility hash</dt>
          <dd className="mt-1 break-all font-mono text-xs text-ink">{experiment.reproducibility_hash}</dd>
        </div>
      </dl>
      <div className="space-y-1">
        {experiment.artifacts.map(artifact => (
          <div key={artifact.artifact_id} className="flex flex-wrap justify-between gap-2 rounded border border-line px-3 py-2 font-mono text-[10px]">
            <span className="text-muted">{artifact.kind} · {artifact.status}</span>
            <span className="break-all text-ink">{artifact.content_hash}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const FAILURE_POLICY = [
  ['MALFORMED', 'BLOCK', 'client contract / DAG payload schema'],
  ['N_MISMATCH', 'BLOCK', 'DAG OOS recomputation'],
  ['SELF_REPORTED_POSITIVE', 'BLOCK', 'DAG ignores caller-observed score'],
  ['ORDERED_DECAY', 'BLOCK', 'DAG OOS/decay evidence'],
  ['STALE', 'UNAVAILABLE', 'authority freshness'],
  ['MISSING', 'UNAVAILABLE', 'authority/store/evidence'],
] as const;

export default function ValidationEvidence() {
  const data = useValidationEvidenceData();
  const { authority } = data;
  const [selectedId, setSelectedId] = useState('');
  const candidates = useMemo(
    () => authority.snapshot?.candidate_states ?? [],
    [authority.snapshot],
  );
  useEffect(() => {
    if (!candidates.some(candidate => candidate.candidate_id === selectedId)) {
      setSelectedId(candidates[0]?.candidate_id ?? '');
    }
  }, [candidates, selectedId]);

  if ((authority.phase !== 'AVAILABLE' && authority.phase !== 'NO_SNAPSHOT')
    || authority.snapshot === null) {
    return <DiagnosticOnly phase={authority.phase} reason={authority.reason} />;
  }
  const snapshot = authority.snapshot;
  const candidate = candidates.find(item => item.candidate_id === selectedId) ?? candidates[0] ?? null;
  const report = candidate && data.validation.status === 'AVAILABLE' && data.validation.payload
    ? data.validation.payload.reports.find(item => item.alpha_id === candidate.candidate_id) ?? null
    : null;
  const experiment = candidate && data.experiments.status === 'AVAILABLE' && data.experiments.payload
    ? data.experiments.payload.experiments.find(item => item.alpha_id === candidate.candidate_id) ?? null
    : null;
  const funnel = snapshot.batch_funnel;

  return (
    <PageShell>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Validation Evidence</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">验证证据</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">
            Trial Ledger 权威绑定、分层 DAG 可见性、反证摘要、回测现实主义与 Shadow/Paper 前向差距。
          </p>
        </div>
        <div className="rounded-lg border border-pos/35 bg-pos/10 px-3 py-2 font-mono text-xs font-semibold text-pos">
          REAL_HOURLY_ALPHA_AUTHORITIES · READ ONLY
        </div>
      </header>

      <div className="rounded-lg border border-warn/35 bg-warn/10 px-4 py-3 text-xs leading-5 text-warn">
        PASS 仅表示 Wave 1 研究验证的终态；辅助 validation/experiment 数据不改变 authority，也不构成资金、风险或实盘批准。
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <Panel eyebrow="Trial Ledger authority" title="权威健康与批次前向差距">
          <div className="grid gap-2 sm:grid-cols-3">
            <div className="rounded-lg border border-line bg-inset/50 p-3">
              <p className="text-[10px] uppercase text-faint">Trial Ledger</p>
              <p className="mt-1 font-mono text-sm font-semibold text-pos">{snapshot.stores.trials.status}</p>
              <p className="mt-1 text-[10px] text-muted">{snapshot.stores.trials.trials ?? 0} trials</p>
            </div>
            <div className="rounded-lg border border-line bg-inset/50 p-3">
              <p className="text-[10px] uppercase text-faint">Artifact-bound</p>
              <p className="mt-1 font-mono text-sm font-semibold text-ink">
                {candidates.filter(item => item.trial_id && item.artifact_commitment).length}/{candidates.length}
              </p>
            </div>
            <div className="rounded-lg border border-line bg-inset/50 p-3">
              <p className="text-[10px] uppercase text-faint">Shadow / Paper gap</p>
              <p className="mt-1 font-mono text-xs font-semibold text-ink">
                {funnel ? `${funnel.forward_decisions} decisions · ${funnel.forward_realizations} realizations · gap ${Math.max(0, funnel.forward_decisions - funnel.forward_realizations)}` : 'UNAVAILABLE'}
              </p>
            </div>
          </div>
          <p className="mt-3 font-mono text-[10px] text-faint">
            {snapshot.schema_version} · snapshot {short(snapshot.snapshot_id)} · {snapshot.freshness.status}
          </p>
        </Panel>

        <Panel eyebrow="Auxiliary source admission" title="辅助证据可用性">
          <div className="space-y-2 font-mono text-xs">
            <div className="rounded-lg border border-line bg-inset/50 p-3">
              <span className="text-faint">validation · </span>
              <span className={data.validation.status === 'AVAILABLE' ? 'text-pos' : 'text-warn'}>
                {data.validation.status} · {data.validation.reason ?? data.validation.payload?.data_provenance}
              </span>
            </div>
            <div className="rounded-lg border border-line bg-inset/50 p-3">
              <span className="text-faint">experiments · </span>
              <span className={data.experiments.status === 'AVAILABLE' ? 'text-pos' : 'text-warn'}>
                {data.experiments.status} · {data.experiments.reason ?? data.experiments.payload?.data_provenance}
              </span>
            </div>
          </div>
        </Panel>
      </div>

      {candidate === null ? (
        <Panel eyebrow="Candidate evidence" title="NO_VALIDATION_RUN">
          <p className="font-mono text-xs text-faint">Authority stores are available, but no candidate batch has run.</p>
        </Panel>
      ) : (
        <>
          <Panel eyebrow="Candidate evidence" title="候选证据绑定"
            right={(
              <label className="text-[10px] uppercase text-faint">
                选择候选
                <select aria-label="选择候选" value={candidate.candidate_id}
                  onChange={event => setSelectedId(event.target.value)}
                  className="ml-2 max-w-[260px] rounded border border-line bg-inset px-2 py-1.5 font-mono text-xs normal-case text-ink">
                  {candidates.map(item => <option key={item.candidate_id} value={item.candidate_id}>{item.candidate_id}</option>)}
                </select>
              </label>
            )}>
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <OutcomeBadge outcome={candidateOutcome(candidate)}
                label={`${candidateOutcome(candidate)} · RESEARCH VALIDATION`} />
              <span className="font-mono text-xs text-muted">authority state {candidate.state}</span>
            </div>
            <Binding candidate={candidate} snapshot={snapshot} />
          </Panel>

          <Panel eyebrow="Hierarchical validation DAG" title="分层 DAG 阶段摘要"
            right={<OutcomeBadge outcome="UNAVAILABLE" label="RECEIPTS NOT EXPOSED" />}>
            <DagSummary />
          </Panel>

          <Panel eyebrow="Auxiliary falsification matrix" title="六维反证矩阵"
            right={report ? <OutcomeBadge outcome="UNKNOWN" label="AUXILIARY · NOT DAG AUTHORITY" /> : undefined}>
            <FalsificationMatrix report={report} />
            {report && (
              <div className="mt-3 rounded-lg border border-line bg-inset/50 p-3 font-mono text-xs text-muted">
                OOS Sharpe {fixed(report.oos_sharpe)} · OOS IC {fixed(report.oos_ic)} · Net Sharpe {fixed(report.net_sharpe)}
                {report.reasons.length > 0 && <span> · {report.reasons.join(', ')}</span>}
              </div>
            )}
            {!report && <p className="mt-3 font-mono text-xs text-faint">UNAVAILABLE · NO_EXACT_MATCH</p>}
          </Panel>

          <Panel eyebrow="Backtest realism & immutable artifacts" title="回测、成本、容量与复现绑定">
            <ExperimentSummary experiment={experiment} />
          </Panel>
        </>
      )}

      <Panel eyebrow="Fail-closed taxonomy" title="错误安全分类（策略，不是当前观测）">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {FAILURE_POLICY.map(([condition, outcome, source]) => (
            <div key={condition} className={`rounded-lg border p-3 ${outcomeTone(outcome as Outcome)}`}>
              <p className="font-mono text-xs font-semibold">{condition} · {outcome}</p>
              <p className="mt-1 text-[10px] opacity-80">{source}</p>
            </div>
          ))}
        </div>
      </Panel>

      <Panel eyebrow="Evidence drill-down" title="只读详情入口">
        <div className="flex flex-wrap gap-2">
          {[
            ['/research/experiments', 'Experiment registry'],
            ['/research/alpha-lifecycle', 'Lifecycle / forward detail'],
            ['/research/backtest-results', 'Backtest detail'],
            ['/workbench/alpha', 'Alpha authority'],
            ['/workbench/portfolio-risk', 'Portfolio / risk context'],
            ['/workbench/execution', 'Forward execution reality'],
          ].map(([href, label]) => (
            <a key={href} href={candidate ? `${href}?alpha_id=${encodeURIComponent(candidate.candidate_id)}` : href}
              className="rounded-md border border-line px-3 py-2 text-xs text-accent hover:bg-inset">
              {label}
            </a>
          ))}
        </div>
      </Panel>
    </PageShell>
  );
}
