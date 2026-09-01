import { useEffect, useMemo, useState } from 'react';

import { Link } from '@/lib/router';
import { fetchResearchRuns } from '@/lib/api/research-console-api';
import {
  fetchRunnerHealth,
  getOperator,
  getRunnerToken,
  runnerEnabled,
  setOperator,
  setRunnerToken,
  triggerRun,
  type RunnerHealth,
} from '@/lib/api/research-runner-api';
import type { ResearchRunsPayload } from '@/lib/types/research';
import { PageShell } from '@/ui/primitives';
import { useToast } from '@/ui/toast';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';

const PARAM_DEFAULTS: Readonly<Record<string, Record<string, unknown>>> = {
  data_refresh: { intraday: false },
};

type RunnerState =
  | { status: 'LOADING'; health: null; reason: null }
  | { status: 'AVAILABLE'; health: RunnerHealth; reason: null }
  | { status: 'UNAVAILABLE'; health: null; reason: string };

function stamp(seconds: number): string {
  return new Date(seconds * 1_000).toISOString().replace('T', ' ').replace('.000Z', 'Z');
}

function statusTone(status: string): string {
  if (status === 'done') return 'border-pos/35 bg-pos/10 text-pos';
  if (status === 'running' || status === 'queued') return 'border-info/35 bg-info/10 text-info';
  if (status === 'error' || status === 'cancelled') return 'border-neg/35 bg-neg/10 text-neg';
  return 'border-line bg-inset text-muted';
}

export default function ResearchRuns() {
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState('');
  const [operatorTag, setOperatorTag] = useState(getOperator());
  const [token, setToken] = useState(getRunnerToken());
  const [runner, setRunner] = useState<RunnerState>(() => (
    runnerEnabled()
      ? { status: 'LOADING', health: null, reason: null }
      : { status: 'UNAVAILABLE', health: null, reason: 'VITE_RUNNER_URL_NOT_CONFIGURED' }
  ));
  const registry = useConsoleSnapshot<ResearchRunsPayload>(
    signal => fetchResearchRuns(signal),
    [reload],
  );
  const toast = useToast();

  useEffect(() => {
    if (!runnerEnabled()) return;
    const controller = new AbortController();
    setRunner({ status: 'LOADING', health: null, reason: null });
    fetchRunnerHealth(controller.signal)
      .then(health => setRunner({ status: 'AVAILABLE', health, reason: null }))
      .catch(error => {
        if (!controller.signal.aborted) {
          setRunner({
            status: 'UNAVAILABLE',
            health: null,
            reason: error instanceof Error ? error.message : String(error),
          });
        }
      });
    return () => controller.abort();
  }, [reload]);

  const rows = useMemo(() => {
    const values = registry.payload?.runs ?? [];
    const needle = query.trim().toLowerCase();
    if (!needle) return values;
    return values.filter(row => (
      `${row.run_id} ${row.kind} ${row.title} ${row.status} ${row.verdict}`.toLowerCase().includes(needle)
    ));
  }, [query, registry.payload]);
  // Polling is a registry lifecycle concern, not a table-visibility concern.
  // A search filter may hide the active row, but it must never stop progress
  // refresh for that queued/running backend job.
  const active = (registry.payload?.runs ?? []).some(
    row => row.status === 'queued' || row.status === 'running',
  );

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setReload(value => value + 1), 3_000);
    return () => window.clearInterval(timer);
  }, [active]);

  async function run(kind: string) {
    try {
      const result = await triggerRun(kind, PARAM_DEFAULTS[kind] ?? {});
      toast.success(`研究任务已排队：${result.kind} · ${result.run_id}`);
      setReload(value => value + 1);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  const kinds = runner.health?.kinds ?? [];
  const canTrigger = runner.status === 'AVAILABLE' && token.trim().length > 0;

  return (
    <PageShell>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Guarded research runner</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">研究运行与受控触发</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">
            8400 只读运行登记与 8420 研究 runner 分离。这里只允许后端白名单中的搜索、回测和数据任务；
            不接券商、不触碰凭证、不提交订单。
          </p>
        </div>
        <div className={`rounded border px-3 py-2 font-mono text-xs font-semibold ${
          runner.status === 'AVAILABLE'
            ? 'border-pos/35 bg-pos/10 text-pos'
            : 'border-warn/35 bg-warn/10 text-warn'
        }`}>
          RUNNER {runner.status}
        </div>
      </header>

      <section className="rounded-xl border border-line bg-panel shadow-sm">
        <header className="border-b border-line px-4 py-3 sm:px-5">
          <p className="text-[10px] uppercase tracking-[0.15em] text-faint">8420 controlled surface</p>
          <h2 className="mt-1 text-base font-semibold text-ink">受控 Alpha 搜索 / 回测入口</h2>
        </header>
        <div className="space-y-4 p-4 sm:p-5">
          {runner.status !== 'AVAILABLE' ? (
            <div className="rounded-lg border border-warn/35 bg-warn/10 p-3" role="status">
              <p className="font-mono text-xs font-semibold text-warn">RESEARCH_RUNNER_UNAVAILABLE</p>
              <p className="mt-1 break-all font-mono text-[10px] text-muted">{runner.reason ?? 'HEALTH_CHECK_IN_PROGRESS'}</p>
            </div>
          ) : (
            <div className="rounded-lg border border-pos/35 bg-pos/10 p-3">
              <p className="font-mono text-xs font-semibold text-pos">
                {runner.health.mode} · live_disallowed={String(runner.health.live_disallowed)}
              </p>
              <p className="mt-1 font-mono text-[10px] text-muted">{kinds.length} runnable kinds from /health</p>
            </div>
          )}

          <div className="grid gap-3 lg:grid-cols-2">
            <label className="text-[10px] uppercase tracking-[0.1em] text-faint">
              操作者标签（审计标签，不是身份认证）
              <input
                value={operatorTag}
                onChange={event => {
                  setOperatorTag(event.target.value);
                  setOperator(event.target.value);
                }}
                className="mt-1.5 w-full rounded-md border border-line bg-inset px-3 py-2 text-xs normal-case text-ink"
              />
            </label>
            <label className="text-[10px] uppercase tracking-[0.1em] text-faint">
              Runner token（只保存在当前标签页）
              <input
                type="password"
                value={token}
                onChange={event => {
                  setToken(event.target.value);
                  setRunnerToken(event.target.value);
                }}
                placeholder="key/research_runner_token.txt"
                className="mt-1.5 w-full rounded-md border border-line bg-inset px-3 py-2 text-xs normal-case text-ink placeholder:text-faint"
              />
            </label>
          </div>

          <div className="flex flex-wrap gap-2">
            {kinds.map(kind => {
              const policy = runner.health?.kind_policies?.[kind];
              return (
                <button
                  key={kind}
                  type="button"
                  onClick={() => run(kind)}
                  disabled={!canTrigger}
                  title={policy ? `${policy.purpose} · ${policy.evidence_authority ?? 'NO_SELECTION_AUTHORITY'}` : kind}
                  className="rounded-md border border-accent/45 px-3 py-2 text-left text-xs text-accent hover:bg-accent/10 disabled:cursor-not-allowed disabled:border-line disabled:text-faint"
                >
                  <span className="block font-mono font-semibold">▷ {kind}</span>
                  <span className="mt-1 block text-[9px] opacity-75">{policy?.purpose ?? 'RESEARCH_ONLY'}</span>
                </button>
              );
            })}
            {runner.status === 'AVAILABLE' && kinds.length === 0 && (
              <p className="font-mono text-xs text-warn">NO_RUNNABLE_KIND_ADVERTISED</p>
            )}
          </div>
          {runner.health?.quarantined_kinds && Object.keys(runner.health.quarantined_kinds).length > 0 && (
            <details className="rounded-lg border border-warn/30 bg-warn/5 px-3 py-2">
              <summary className="cursor-pointer font-mono text-[10px] text-warn">已隔离旧运行类型</summary>
              <div className="mt-2 grid gap-1 sm:grid-cols-2">
                {Object.entries(runner.health.quarantined_kinds).map(([kind, reason]) => (
                  <p key={kind} className="font-mono text-[9px] text-muted">{kind} · {reason}</p>
                ))}
              </div>
            </details>
          )}
        </div>
      </section>

      <section className="rounded-xl border border-line bg-panel shadow-sm">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
          <div>
            <p className="text-[10px] uppercase tracking-[0.15em] text-faint">8400 registry</p>
            <h2 className="mt-1 text-base font-semibold text-ink">研究运行登记</h2>
          </div>
          <span className="font-mono text-[10px] text-warn">
            {registry.payload?.evidence_status ?? registry.apiState} · {registry.payload?.invalidation_code ?? 'CURRENT_CONTRACT'}
          </span>
        </header>
        <div className="p-4 sm:p-5">
          {!registry.usingBackend || !registry.payload ? (
            <p className="rounded border border-neg/35 bg-neg/10 p-3 font-mono text-xs text-neg">
              REGISTRY_UNAVAILABLE · {registry.error ?? registry.apiState}
            </p>
          ) : (
            <>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <input
                  value={query}
                  onChange={event => setQuery(event.target.value)}
                  placeholder="筛选 run id / kind / verdict"
                  className="w-full max-w-sm rounded-md border border-line bg-inset px-3 py-2 text-xs text-ink placeholder:text-faint"
                />
                <span className="font-mono text-[10px] text-faint">{rows.length} runs</span>
              </div>
              <div className="overflow-x-auto rounded-lg border border-line">
                <table className="w-full min-w-[980px] text-left text-xs">
                  <thead className="bg-inset/70 text-[10px] uppercase tracking-[0.1em] text-faint">
                    <tr>
                      <th className="px-3 py-2">Run</th><th className="px-3 py-2">Kind</th>
                      <th className="px-3 py-2">Status</th><th className="px-3 py-2">Verdict</th>
                      <th className="px-3 py-2">Headline</th><th className="px-3 py-2">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(row => (
                      <tr key={row.run_id} className="border-t border-line/70 align-top">
                        <td className="px-3 py-3">
                          <Link className="font-mono font-semibold text-accent hover:underline" to={`/research/runs/${row.run_id}`}>
                            {row.title}
                          </Link>
                          <p className="mt-1 font-mono text-[9px] text-faint">{row.run_id}</p>
                        </td>
                        <td className="px-3 py-3 font-mono text-muted">{row.kind}</td>
                        <td className="px-3 py-3"><span className={`rounded border px-2 py-1 font-mono text-[9px] ${statusTone(row.status)}`}>{row.status}</span></td>
                        <td className="max-w-[260px] px-3 py-3 text-muted">{row.verdict}</td>
                        <td className="px-3 py-3 font-mono text-[10px] text-ink">
                          {row.headline.label ?? 'NO_HEADLINE'} · {row.headline.value ?? 'UNAVAILABLE'}
                        </td>
                        <td className="px-3 py-3 font-mono text-[10px] text-faint">{stamp(row.created_ts)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </section>
    </PageShell>
  );
}
