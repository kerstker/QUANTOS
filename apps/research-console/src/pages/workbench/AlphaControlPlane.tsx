import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

import type {
  AlphaLifecycleEntry,
  AlphaLifecyclePayload,
  AlphaZooRow,
  AlphaZooPayload,
  AShareInstitutionalSearchPayload,
} from '@/lib/types/research';
import { PageShell } from '@/ui/primitives';
import type {
  HourlyAlphaCandidate,
  HourlyAlphaSnapshot,
} from '@/workbench/hourly-alpha-contract';
import {
  useAlphaControlPlaneData,
  type AlphaEnrichment,
} from '@/workbench/useAlphaControlPlaneData';

const PAGE_SIZE = 25;

function short(value: string | null): string {
  if (value === null) return 'UNAVAILABLE';
  return value.length > 18 ? `${value.slice(0, 12)}…${value.slice(-4)}` : value;
}

function decimal(value: number | null | undefined, digits = 3): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : 'UNAVAILABLE';
}

function timestamp(seconds: number | null): string {
  if (seconds === null) return 'UNAVAILABLE';
  return new Date(seconds * 1_000).toISOString().replace('T', ' ').replace('.000Z', 'Z');
}

function stateTone(state: string): string {
  if (state === 'VALIDATED') return 'border-pos/35 bg-pos/10 text-pos';
  if (state === 'BLOCKED') return 'border-neg/35 bg-neg/10 text-neg';
  return 'border-info/35 bg-info/10 text-info';
}

function AuthorityFootnote({ snapshot }: { snapshot: HourlyAlphaSnapshot }) {
  return (
    <div className="mt-4 grid gap-1 border-t border-line/70 pt-3 font-mono text-[10px] text-faint sm:grid-cols-2 xl:grid-cols-4">
      <span>{snapshot.data_provenance}</span>
      <span>snapshot {short(snapshot.snapshot_id)}</span>
      <span>as-of {timestamp(snapshot.as_of)}</span>
      <span>{snapshot.freshness.status} · age {snapshot.freshness.age_seconds ?? 'UNKNOWN'}s</span>
    </div>
  );
}

function Panel({ eyebrow, title, children }: {
  eyebrow: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-panel shadow-sm">
      <header className="border-b border-line px-4 py-3 sm:px-5">
        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-faint">{eyebrow}</p>
        <h2 className="mt-1 text-base font-semibold text-ink">{title}</h2>
      </header>
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

function AShareInstitutionalSearch({ research }: {
  research: AlphaEnrichment<AShareInstitutionalSearchPayload>;
}) {
  if (research.status !== 'AVAILABLE' || research.payload === null) {
    return (
      <section className="rounded-xl border border-warn/35 bg-warn/10 p-5" role="status">
        <p className="font-mono text-sm font-semibold text-warn">A股机构因子搜索 · {research.status}</p>
        <p className="mt-2 font-mono text-xs text-muted">{research.reason ?? 'SEARCH_EVIDENCE_UNAVAILABLE'}</p>
      </section>
    );
  }
  const payload = research.payload;
  const coverage = Object.entries(payload.surface_coverage);
  return (
    <section className="rounded-xl border border-warn/35 bg-panel shadow-sm" aria-label="A股机构因子搜索">
      <header className="border-b border-line px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-warn">A-share institutional surfaces</p>
            <h2 className="mt-1 text-base font-semibold text-ink">A股机构因子搜索</h2>
          </div>
          <span className="rounded border border-warn/35 bg-warn/10 px-2 py-1 font-mono text-[10px] text-warn">
            {payload.evidence_status} · READ ONLY
          </span>
        </div>
      </header>
      <div className="space-y-4 p-4 sm:p-5">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ['Stocks', payload.source_summary.instruments],
            ['Bars', payload.source_summary.rows],
            ['Candidates', payload.trial_accounting?.candidate_specs],
            ['Usefulness', payload.candidate_usefulness],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border border-line bg-inset/50 p-3">
              <p className="text-[10px] uppercase text-faint">{label}</p>
              <p className="mt-1 font-mono text-sm font-semibold text-ink">{value ?? 'UNAVAILABLE'}</p>
            </div>
          ))}
        </div>
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {payload.surface_results.map(row => (
            <div key={row.surface} className="rounded-lg border border-line bg-surface/50 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="font-mono text-xs font-semibold text-ink">{row.surface}</p>
                <span className={`font-mono text-[10px] ${row.candidate_usefulness === 'CONTINUE_RESEARCH' ? 'text-pos' : 'text-warn'}`}>
                  {row.candidate_usefulness}
                </span>
              </div>
              <p className="mt-2 font-mono text-[10px] text-muted">{row.candidate?.name ?? 'NO_CANDIDATE'}</p>
              <p className="mt-1 font-mono text-[10px] text-faint">
                confirmation SR {decimal(row.confirmation_excess_sharpe)} · adj p {decimal(row.bonferroni_adjusted_p)}
              </p>
            </div>
          ))}
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          {coverage.map(([name, item]) => (
            <div key={name} className="rounded-lg border border-line bg-inset/40 px-3 py-2">
              <p className="font-mono text-[10px] text-ink">{name} · {item.status}</p>
              <p className="mt-1 text-[10px] leading-4 text-faint">{item.reason}</p>
            </div>
          ))}
        </div>
        <p className="rounded-lg border border-info/30 bg-info/10 px-3 py-2 font-mono text-[10px] text-info">
          FORWARD: {payload.untouched_forward_observation.status ?? 'UNAVAILABLE'} · cutoff {payload.untouched_forward_observation.freeze_cutoff ?? 'UNKNOWN'} · minimum {payload.untouched_forward_observation.minimum_sessions ?? 'UNKNOWN'} sessions
        </p>
      </div>
    </section>
  );
}

function RetainedResearchEvidence({ zoo, lifecycle }: {
  zoo: AlphaEnrichment<AlphaZooPayload>;
  lifecycle: AlphaEnrichment<AlphaLifecyclePayload>;
}) {
  const zooRows = zoo.status === 'AVAILABLE' && zoo.payload ? zoo.payload.rows : [];
  const lifecycleRows = lifecycle.status === 'AVAILABLE' && lifecycle.payload
    ? lifecycle.payload.alphas : [];
  const lifecycleById = new Map(lifecycleRows.map(row => [row.alpha_id, row]));
  if (zooRows.length === 0 && lifecycleRows.length === 0) {
    return (
      <section className="rounded-xl border border-warn/35 bg-warn/10 p-5" role="status">
        <p className="font-mono text-sm font-semibold text-warn">研究登记与生命周期数据不可用</p>
        <p className="mt-2 font-mono text-[10px] text-muted">
          zoo={zoo.reason ?? zoo.status} · lifecycle={lifecycle.reason ?? lifecycle.status}
        </p>
      </section>
    );
  }
  return (
    <section className="rounded-xl border border-warn/35 bg-panel shadow-sm" aria-label="可读诊断研究证据">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-warn">Visible diagnostic evidence</p>
          <h2 className="mt-1 text-base font-semibold text-ink">研究登记与生命周期（可读、不可晋级）</h2>
        </div>
        <span className="rounded border border-warn/35 bg-warn/10 px-2 py-1 font-mono text-[10px] text-warn">
          DIAGNOSTIC_ONLY · {zooRows.length} zoo · {lifecycleRows.length} lifecycle
        </span>
      </header>
      <div className="space-y-3 p-4 sm:p-5">
        <p className="text-xs leading-5 text-muted">
          这些行来自真实后端登记与留存 artifact。旧 V1 盈亏/Sharpe 不具备选择权威，但候选身份、家族、
          状态和失效原因仍应可见；前端不再把它们误报为传输故障。
        </p>
        <div className="max-h-[440px] overflow-auto rounded-lg border border-line">
          <table className="w-full min-w-[900px] text-left text-xs">
            <thead className="sticky top-0 bg-inset text-[10px] uppercase tracking-[0.1em] text-faint">
              <tr>
                <th className="px-3 py-2">Alpha</th><th className="px-3 py-2">Family</th>
                <th className="px-3 py-2">Registry status</th><th className="px-3 py-2">Lifecycle</th>
                <th className="px-3 py-2">Evidence boundary</th>
              </tr>
            </thead>
            <tbody>
              {zooRows.map(row => {
                const life = lifecycleById.get(row.alpha_id);
                return (
                  <tr key={row.alpha_id} className="border-t border-line/70 align-top">
                    <td className="px-3 py-2.5 font-mono font-semibold text-ink">{row.alpha_id}</td>
                    <td className="px-3 py-2.5 font-mono text-muted">{row.cluster_id || row.generator}</td>
                    <td className="px-3 py-2.5 font-mono text-muted">{row.status} · {row.verdict}</td>
                    <td className="px-3 py-2.5 font-mono text-muted">{life?.derived_stage ?? 'NO_LIFECYCLE_MATCH'}</td>
                    <td className="px-3 py-2.5 font-mono text-[10px] text-warn">
                      {lifecycle.payload?.evidence_status ?? zoo.payload?.evidence_status ?? 'DIAGNOSTIC_ONLY'}
                      {lifecycle.payload?.invalidation_code ? ` · ${lifecycle.payload.invalidation_code}` : ''}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="font-mono text-[10px] text-faint">
          zoo {zoo.payload?.data_provenance ?? zoo.reason} · lifecycle {lifecycle.payload?.data_provenance ?? lifecycle.reason}
        </p>
      </div>
    </section>
  );
}

function DiagnosticOnly({ snapshot, phase, reason, ashare, zoo, lifecycle }: {
  snapshot: HourlyAlphaSnapshot | null;
  phase: string;
  reason: string | null;
  ashare: AlphaEnrichment<AShareInstitutionalSearchPayload>;
  zoo: AlphaEnrichment<AlphaZooPayload>;
  lifecycle: AlphaEnrichment<AlphaLifecyclePayload>;
}) {
  const quarantined = phase === 'QUARANTINED';
  const technicalSearchAvailable = quarantined
    && ashare.status === 'AVAILABLE'
    && ashare.payload?.snapshot_present === true
    && ashare.payload.evidence_status === 'TECHNICAL_VALIDATION_ONLY';
  const status = phase === 'STALE'
    ? 'ALPHA_AUTHORITY_STALE'
    : technicalSearchAvailable
      ? 'ALPHA_TECHNICAL_SEARCH_AVAILABLE'
      : quarantined ? 'ALPHA_V1_AUTHORITY_QUARANTINED' : 'ALPHA_AUTHORITY_UNAVAILABLE';
  const tone = technicalSearchAvailable
    ? 'border-pos/35 bg-pos/10'
    : quarantined ? 'border-warn/35 bg-warn/10' : 'border-neg/35 bg-neg/10';
  const textTone = technicalSearchAvailable ? 'text-pos' : quarantined ? 'text-warn' : 'text-neg';
  return (
    <PageShell>
      <header>
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Alpha Control Plane</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">Alpha 控制平面</h1>
      </header>
      <section className={`rounded-xl border p-5 ${tone}`} role="status">
        <p className={`font-mono text-sm font-semibold ${textTone}`}>{status}</p>
        <p className="mt-2 font-mono text-xs text-muted">{reason ?? phase}</p>
        <p className={`mt-4 inline-flex rounded border px-2 py-1 font-mono text-[10px] ${technicalSearchAvailable ? 'border-pos/35 text-pos' : quarantined ? 'border-warn/35 text-warn' : 'border-neg/35 text-neg'}`}>
          {technicalSearchAvailable ? 'READ_ONLY_PIT_SEARCH' : quarantined ? 'EXPECTED_SAFE_ISOLATION' : 'DIAGNOSTIC_ONLY'}
        </p>
        {technicalSearchAvailable && (
          <p className="mt-2 flex flex-wrap gap-2 font-mono text-[10px] text-warn">
            <span>ALPHA_V1_AUTHORITY_QUARANTINED</span>
            <span>EXPECTED_SAFE_ISOLATION</span>
          </p>
        )}
        <p className="mt-3 max-w-3xl text-xs leading-5 text-muted">
          {technicalSearchAvailable
            ? 'A股 PIT 技术搜索链可用；候选仍处于技术验证与未触碰前向观察阶段。旧 V1 晋级账本继续安全隔离，不影响新的只读 Alpha 搜索。'
            : quarantined
            ? '旧 V1 候选工作流按发布策略隔离；这是安全状态，不是前端故障。当前研究结果应从新的技术验证/PIT 搜索权威进入。'
            : '候选状态、验证数量与前向结果均被隐藏。旧快照不会冒充当前权威状态，也不会回退到模拟数据。'}
        </p>
        {snapshot && (
          <dl className="mt-4 grid gap-2 sm:grid-cols-3">
            {(Object.entries(snapshot.stores) as [string, { status: string; reason?: string }][]).map(([name, store]) => (
              <div key={name} className="rounded-lg border border-line bg-surface/50 p-3">
                <dt className="font-mono text-[10px] uppercase text-faint">{name}</dt>
                <dd className="mt-1 font-mono text-xs text-ink">
                  {store.status}{store.reason ? ` · ${store.reason}` : ''}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </section>
      <AShareInstitutionalSearch research={ashare} />
      <RetainedResearchEvidence zoo={zoo} lifecycle={lifecycle} />
    </PageShell>
  );
}

function Funnel({ snapshot }: { snapshot: HourlyAlphaSnapshot }) {
  const funnel = snapshot.batch_funnel;
  if (funnel === null) return null;
  const metrics = [
    ['Generated', funnel.generated],
    ['Representatives', funnel.representatives],
    ['Validated', funnel.validated],
    ['Blocked', funnel.blocked],
    ['Forward decisions', funnel.forward_decisions],
    ['Realizations', funnel.forward_realizations],
  ] as const;
  return (
    <Panel eyebrow="Batch funnel" title="批次漏斗">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {metrics.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-line bg-inset/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-faint">{label}</p>
            <p className="mt-2 font-mono text-2xl font-semibold text-ink">{value}</p>
          </div>
        ))}
      </div>
      <AuthorityFootnote snapshot={snapshot} />
    </Panel>
  );
}

function EvidenceLinks({ candidate }: { candidate: HourlyAlphaCandidate }) {
  const links = [
    ['/workbench/validation', 'Validation evidence'],
    ['/research/experiments', 'Trial / experiment'],
    ['/research/alpha-graph', 'Lineage graph'],
    ['/research/alpha-lifecycle', 'Lifecycle detail'],
    ['/workbench/portfolio-risk', 'Portfolio / risk context'],
  ] as const;
  return (
    <div className="flex min-w-[150px] flex-col items-start gap-1">
      {links.map(([href, label]) => (
        <a key={href} href={`${href}?alpha_id=${encodeURIComponent(candidate.candidate_id)}`}
          className="text-[11px] text-accent underline-offset-2 hover:underline">
          {label}
        </a>
      ))}
    </div>
  );
}

function CandidateState({ state }: { state: string }) {
  const label = state === 'VALIDATED'
    ? 'VALIDATED · 研究验证（非资金批准）'
    : state;
  return (
    <span className={`inline-flex rounded-full border px-2 py-1 text-[10px] font-semibold ${stateTone(state)}`}>
      {label}
    </span>
  );
}

function CandidateBlotter({
  snapshot,
  zooRows,
  lifecycleRows,
  zooStatus,
  lifecycleStatus,
}: {
  snapshot: HourlyAlphaSnapshot;
  zooRows: AlphaZooRow[];
  lifecycleRows: AlphaLifecycleEntry[];
  zooStatus: string;
  lifecycleStatus: string;
}) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const tableRef = useRef<HTMLTableElement>(null);
  const zooById = useMemo(() => new Map(zooRows.map(row => [row.alpha_id, row])), [zooRows]);
  const lifecycleById = useMemo(
    () => new Map(lifecycleRows.map(row => [row.alpha_id, row])),
    [lifecycleRows],
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return snapshot.candidate_states;
    return snapshot.candidate_states.filter(candidate => {
      const zoo = zooById.get(candidate.candidate_id);
      const lifecycle = lifecycleById.get(candidate.candidate_id);
      return [candidate.candidate_id, candidate.state, candidate.trial_id,
        zoo?.name, zoo?.generator, zoo?.cluster_id, lifecycle?.derived_stage]
        .filter(Boolean).join(' ').toLowerCase().includes(needle);
    });
  }, [lifecycleById, query, snapshot.candidate_states, zooById]);
  useEffect(() => setPage(0), [query]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const rows = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  function targetRow(event: KeyboardEvent<HTMLTableRowElement>) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const elements = Array.from(
      tableRef.current?.querySelectorAll<HTMLElement>('[data-candidate-row]') ?? [],
    );
    const index = elements.indexOf(event.currentTarget);
    const next = event.key === 'ArrowDown' ? index + 1 : index - 1;
    if (next >= 0 && next < elements.length) {
      event.preventDefault();
      elements[next].focus();
    }
  }

  return (
    <Panel eyebrow="Candidate blotter" title="候选状态与证据摘要">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <label className="w-full max-w-sm text-[10px] uppercase tracking-[0.12em] text-faint">
          <span>筛选候选</span>
          <input type="search" aria-label="筛选候选" value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="candidate / state / family / lifecycle"
            className="mt-1.5 w-full rounded-md border border-line bg-inset px-3 py-2 text-xs normal-case text-ink placeholder:text-faint" />
        </label>
        <div className="font-mono text-[10px] text-faint">
          {filtered.length} rows · page {currentPage + 1}/{pageCount} · max {PAGE_SIZE}/page
        </div>
      </div>
      <div className="overflow-x-auto rounded-lg border border-line">
        <table ref={tableRef} className="w-full min-w-[1180px] border-collapse text-left text-xs">
          <thead className="bg-inset/80 text-[10px] uppercase tracking-[0.1em] text-faint">
            <tr>
              <th className="px-3 py-2">Candidate / trial</th>
              <th className="px-3 py-2">Authority state</th>
              <th className="px-3 py-2">Lineage / family</th>
              <th className="px-3 py-2">IC / OOS</th>
              <th className="px-3 py-2">Lifecycle</th>
              <th className="px-3 py-2">Evidence drill-down</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(candidate => {
              const zoo = zooById.get(candidate.candidate_id);
              const lifecycle = lifecycleById.get(candidate.candidate_id);
              return (
                <tr key={candidate.candidate_id} tabIndex={0} data-candidate-row
                  data-testid="candidate-row" onKeyDown={targetRow}
                  aria-label={`candidate ${candidate.candidate_id}`}
                  className="border-t border-line/70 align-top outline-none hover:bg-elevated/40 focus:bg-accent/10 focus:ring-2 focus:ring-inset focus:ring-accent">
                  <td className="px-3 py-3 font-mono">
                    <div className="font-semibold text-ink">{candidate.candidate_id}</div>
                    <div className="mt-1 text-[10px] text-faint">trial {short(candidate.trial_id)}</div>
                    <div className="mt-1 text-[10px] text-faint">artifact {short(candidate.artifact_commitment)}</div>
                  </td>
                  <td className="px-3 py-3"><CandidateState state={candidate.state} /></td>
                  <td className="px-3 py-3">
                    {zoo ? (
                      <>
                        <div className="font-semibold text-ink">{zoo.cluster_id || 'UNAVAILABLE'}</div>
                        <div className="mt-1 text-[10px] text-muted">{zoo.generator} · {zoo.name}</div>
                      </>
                    ) : <span className="font-mono text-faint">UNAVAILABLE · {zooStatus}</span>}
                  </td>
                  <td className="px-3 py-3 font-mono text-[11px]">
                    {zoo ? (
                      <>
                        <div className="text-ink">IC {decimal(zoo.ic)} · ICIR {decimal(zoo.icir)}</div>
                        <div className="mt-1 text-muted">
                          OOS IC {decimal(zoo.paper_ic)} · Paper SR {decimal(zoo.paper_sharpe)} · n={zoo.n_paper_periods ?? 'UNAVAILABLE'}
                        </div>
                      </>
                    ) : <span className="text-faint">UNAVAILABLE</span>}
                  </td>
                  <td className="px-3 py-3">
                    {lifecycle ? (
                      <>
                        <div className="font-mono font-semibold text-ink">{lifecycle.derived_stage}</div>
                        <div className="mt-1 text-[10px] text-muted">{lifecycle.lifecycle_state ?? 'UNKNOWN_LEGACY'}</div>
                      </>
                    ) : <span className="font-mono text-faint">UNAVAILABLE · {lifecycleStatus}</span>}
                  </td>
                  <td className="px-3 py-3"><EvidenceLinks candidate={candidate} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="border-t border-line p-8 text-center text-xs text-faint">NO_MATCHING_CANDIDATES</div>
        )}
      </div>
      <div className="mt-3 flex items-center justify-between gap-3">
        <button type="button" onClick={() => setPage(value => Math.max(0, value - 1))}
          disabled={currentPage === 0}
          className="rounded-md border border-line px-3 py-1.5 text-xs text-muted disabled:opacity-35">
          上一页
        </button>
        <span className="font-mono text-[10px] text-faint">Arrow ↑/↓ moves row focus</span>
        <button type="button" onClick={() => setPage(value => Math.min(pageCount - 1, value + 1))}
          disabled={currentPage >= pageCount - 1}
          className="rounded-md border border-line px-3 py-1.5 text-xs text-muted disabled:opacity-35">
          下一页
        </button>
      </div>
      <AuthorityFootnote snapshot={snapshot} />
    </Panel>
  );
}

export default function AlphaControlPlane() {
  const data = useAlphaControlPlaneData();
  const { authority } = data;
  const ashare = data.ashare ?? {
    status: 'UNAVAILABLE' as const, payload: null, reason: 'A_SHARE_SEARCH_NOT_CONFIGURED',
  };
  if ((authority.phase !== 'AVAILABLE' && authority.phase !== 'NO_SNAPSHOT')
    || authority.snapshot === null) {
    return (
      <DiagnosticOnly snapshot={authority.snapshot} phase={authority.phase} reason={authority.reason}
        ashare={ashare} zoo={data.zoo} lifecycle={data.lifecycle} />
    );
  }
  const snapshot = authority.snapshot;
  const zooRows = data.zoo.status === 'AVAILABLE' && data.zoo.payload ? data.zoo.payload.rows : [];
  const lifecycleRows = data.lifecycle.status === 'AVAILABLE' && data.lifecycle.payload
    ? data.lifecycle.payload.alphas : [];

  return (
    <PageShell>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Alpha Control Plane</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">Alpha 控制平面</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">
            Wave 1 候选权威、研究证据摘要与生命周期的只读聚合视图。候选状态只来自持久工作流 authority。
          </p>
        </div>
        <div className="space-y-1 text-right">
          <div className="rounded-lg border border-pos/35 bg-pos/10 px-3 py-2 font-mono text-xs font-semibold text-pos">
            {snapshot.data_provenance} · READ ONLY
          </div>
          <p className="font-mono text-[10px] text-warn">L2_DRY_RUN_ONLY · NO CAPITAL APPROVAL</p>
        </div>
      </header>

      <div className="rounded-lg border border-warn/35 bg-warn/10 px-4 py-3 text-xs leading-5 text-warn">
        <strong>语义边界：</strong> VALIDATED 仅表示研究验证通过；它不是资金批准、实盘 mandate、下单许可或风险授权。
      </div>

      <AShareInstitutionalSearch research={ashare} />

      <Funnel snapshot={snapshot} />

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel eyebrow="Authority health" title="工作流、Trial Ledger 与前向账本">
          <div className="grid gap-2 sm:grid-cols-3">
            {(Object.entries(snapshot.stores) as [string, { status: string; trials?: number }][]).map(([name, store]) => (
              <div key={name} className="rounded-lg border border-line bg-inset/50 p-3">
                <p className="font-mono text-[10px] uppercase text-faint">{name}</p>
                <p className="mt-1 font-mono text-sm font-semibold text-pos">{store.status}</p>
                {name === 'trials' && <p className="mt-1 text-[10px] text-muted">{store.trials ?? 0} trials</p>}
              </div>
            ))}
          </div>
          <AuthorityFootnote snapshot={snapshot} />
        </Panel>

        <Panel eyebrow="Leases & automatic tasks" title="租约与自动研究任务">
          <dl className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg border border-line bg-inset/50 p-3">
              <dt className="text-[10px] uppercase text-faint">Batch watermark</dt>
              <dd className="mt-1 font-mono text-xs text-ink">{short(snapshot.watermark?.batch_id ?? null)}</dd>
            </div>
            <div className="rounded-lg border border-line bg-inset/50 p-3">
              <dt className="text-[10px] uppercase text-faint">Last authority event</dt>
              <dd className="mt-1 font-mono text-xs text-ink">{timestamp(snapshot.watermark?.event_at ?? null)}</dd>
            </div>
            <div className="rounded-lg border border-warn/35 bg-warn/10 p-3">
              <dt className="text-[10px] uppercase text-faint">Lease owner / expiry</dt>
              <dd className="mt-1 font-mono text-xs text-warn">UNAVAILABLE · NOT_IN_V1_CONTRACT</dd>
            </div>
            <div className="rounded-lg border border-warn/35 bg-warn/10 p-3">
              <dt className="text-[10px] uppercase text-faint">Next automatic task</dt>
              <dd className="mt-1 font-mono text-xs text-warn">UNAVAILABLE · NOT_IN_V1_CONTRACT</dd>
            </div>
          </dl>
          <p className="mt-3 text-[10px] leading-4 text-faint">
            hourly-alpha-console.v1 未暴露租约明细或下一任务；界面不从候选状态反推或伪造调度状态。
          </p>
        </Panel>
      </div>

      <CandidateBlotter snapshot={snapshot} zooRows={zooRows} lifecycleRows={lifecycleRows}
        zooStatus={data.zoo.status} lifecycleStatus={data.lifecycle.status} />
    </PageShell>
  );
}
