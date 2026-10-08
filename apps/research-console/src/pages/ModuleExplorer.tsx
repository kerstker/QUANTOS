import { useMemo, useState } from 'react';

import { fetchConsoleModuleSnapshot } from '@/lib/api/research-console-api';
import { PageShell } from '@/ui/primitives';
import { useConsoleSnapshot } from '@/ui/useConsoleSnapshot';
import {
  classifyConsoleModule,
  CONSOLE_MODULES,
  type ConsoleModuleState,
} from '@/workbench/module-registry';

type ModulePayload = Record<string, unknown>;

const GROUP_LABELS = {
  DATA: '数据',
  ALPHA: 'Alpha 研究',
  VALIDATION: '验证',
  PORTFOLIO_RISK: '组合与风险',
  EXECUTION: '执行与跟踪',
  GOVERNANCE_SYSTEM: '治理与系统',
} as const;

function stateTone(state: ConsoleModuleState): string {
  if (state === 'REAL') return 'border-pos/35 bg-pos/10 text-pos';
  if (state === 'TECHNICAL') return 'border-info/35 bg-info/10 text-info';
  if (state === 'DIAGNOSTIC') return 'border-warn/35 bg-warn/10 text-warn';
  if (state === 'SYNTHETIC') return 'border-line bg-inset text-muted';
  if (state === 'STALE') return 'border-warn/35 bg-warn/10 text-warn';
  if (state === 'CORRUPT_OR_UNREADABLE') return 'border-neg/45 bg-neg/10 text-neg';
  if (state === 'UNAVAILABLE') return 'border-neg/35 bg-neg/10 text-neg';
  return 'border-line bg-inset text-faint';
}

function scalar(payload: ModulePayload, key: string): string {
  const value = payload[key];
  if (value === null || value === undefined || value === '') return 'NOT_DECLARED';
  if (typeof value === 'object') return Array.isArray(value) ? `${value.length} rows` : 'object';
  return String(value);
}

function collectionSummary(payload: ModulePayload): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  for (const [key, value] of Object.entries(payload)) {
    if (Array.isArray(value)) rows.push([key, `${value.length} rows`]);
    else if (value && typeof value === 'object') rows.push([key, `${Object.keys(value).length} fields`]);
  }
  return rows.slice(0, 18);
}

export default function ModuleExplorer() {
  const [selectedId, setSelectedId] = useState(CONSOLE_MODULES[0].id);
  const [filter, setFilter] = useState('');
  const selected = CONSOLE_MODULES.find(item => item.id === selectedId) ?? CONSOLE_MODULES[0];
  const result = useConsoleSnapshot<ModulePayload>(
    signal => fetchConsoleModuleSnapshot<ModulePayload>(
      selected.endpoint,
      CONSOLE_MODULES,
      signal,
    ),
    [selected.endpoint],
  );
  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return CONSOLE_MODULES;
    return CONSOLE_MODULES.filter(item => (
      `${item.label} ${item.id} ${item.group} ${item.endpoint}`.toLowerCase().includes(needle)
    ));
  }, [filter]);
  const payload = result.usingBackend ? result.payload : null;
  const state: ConsoleModuleState = payload ? classifyConsoleModule(payload) : 'UNAVAILABLE';

  return (
    <PageShell>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Console module explorer</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">后端模块浏览器</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">
            当前只读 Console API 的全部固定 GET 模块。真实、技术诊断、陈旧、损坏、占位和不可用状态
            分开显示；可读的诊断数据不会再被误报成网络故障。
          </p>
        </div>
        <span className="rounded border border-line bg-inset px-3 py-2 font-mono text-xs text-muted">
          {CONSOLE_MODULES.length} MODULES · READ ONLY
        </span>
      </header>

      <div className="grid min-h-[680px] gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <aside className="rounded-xl border border-line bg-panel p-3 shadow-sm">
          <label className="block text-[10px] uppercase tracking-[0.12em] text-faint">
            筛选模块
            <input
              value={filter}
              onChange={event => setFilter(event.target.value)}
              placeholder="名称 / endpoint / group"
              className="mt-2 w-full rounded-md border border-line bg-inset px-3 py-2 text-xs normal-case text-ink placeholder:text-faint"
            />
          </label>
          <div className="mt-3 max-h-[72vh] space-y-3 overflow-y-auto pr-1">
            {(Object.keys(GROUP_LABELS) as Array<keyof typeof GROUP_LABELS>).map(group => {
              const items = visible.filter(item => item.group === group);
              if (!items.length) return null;
              return (
                <section key={group}>
                  <p className="px-2 py-1 text-[9px] font-semibold uppercase tracking-[0.14em] text-faint">
                    {GROUP_LABELS[group]} · {items.length}
                  </p>
                  <div className="space-y-1">
                    {items.map(item => (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => setSelectedId(item.id)}
                        className={`w-full rounded-md border px-3 py-2 text-left ${item.id === selected.id
                          ? 'border-accent/45 bg-accent/10'
                          : 'border-transparent hover:border-line hover:bg-inset/60'}`}
                      >
                        <span className="block text-xs font-semibold text-ink">{item.label}</span>
                        <span className="mt-1 block truncate font-mono text-[9px] text-faint">{item.endpoint}</span>
                      </button>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        </aside>

        <section className="min-w-0 rounded-xl border border-line bg-panel shadow-sm">
          <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line p-4 sm:p-5">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-[0.14em] text-faint">{selected.group}</p>
              <h2 className="mt-1 text-lg font-semibold text-ink">{selected.label}</h2>
              <p className="mt-1 break-all font-mono text-[10px] text-muted">GET {selected.endpoint}</p>
            </div>
            <span className={`rounded border px-2.5 py-1 font-mono text-[10px] font-semibold ${stateTone(state)}`}>
              {result.apiState === 'loading' ? 'LOADING' : state}
            </span>
          </header>

          <div className="space-y-4 p-4 sm:p-5">
            {!payload ? (
              <div className="rounded-lg border border-neg/35 bg-neg/10 p-4" role="status">
                <p className="font-mono text-xs font-semibold text-neg">MODULE_TRANSPORT_UNAVAILABLE</p>
                <p className="mt-2 break-all font-mono text-[10px] text-muted">
                  {result.apiState === 'loading' ? 'LOADING' : result.error ?? result.apiState}
                </p>
              </div>
            ) : (
              <>
                <div className={`rounded-lg border p-4 ${stateTone(state)}`} role="status">
                  <p className="font-mono text-sm font-semibold">{state}</p>
                  <p className="mt-2 text-xs leading-5 opacity-85">
                    {state === 'DIAGNOSTIC'
                      ? '后端数据可读，但不可作为候选晋级、资金授权或下单依据。'
                      : state === 'TECHNICAL'
                        ? '技术研究结果可读；仍需前向观察与正式验证。'
                        : state === 'SYNTHETIC'
                          ? '该模块仍是确定性蓝图/占位，不冒充真实生产数据。'
                          : state === 'STALE'
                            ? '端点连通且快照可读，但新鲜度已超限；当前内容不可作为最新状态。'
                            : state === 'CORRUPT_OR_UNREADABLE'
                              ? '端点连通，但后端存储损坏或无法解析；必须修复数据源后再使用。'
                          : state === 'UNAVAILABLE'
                            ? '端点连通，但其后端 authority/store/snapshot 当前未配置或无数据。'
                            : '后端快照已通过只读响应契约。'}
                  </p>
                </div>

                <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  {['data_provenance', 'snapshot_status', 'evidence_status', 'invalidation_code'].map(key => (
                    <div key={key} className="rounded-lg border border-line bg-inset/50 p-3">
                      <dt className="text-[9px] uppercase tracking-[0.1em] text-faint">{key}</dt>
                      <dd className="mt-1 break-all font-mono text-[10px] text-ink">{scalar(payload, key)}</dd>
                    </div>
                  ))}
                </dl>

                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {collectionSummary(payload).map(([key, value]) => (
                    <div key={key} className="flex items-center justify-between gap-3 rounded border border-line bg-inset/40 px-3 py-2 text-xs">
                      <span className="truncate font-mono text-muted">{key}</span>
                      <span className="shrink-0 font-mono text-ink">{value}</span>
                    </div>
                  ))}
                </div>

                <details className="rounded-lg border border-line bg-inset/35" open>
                  <summary className="cursor-pointer px-4 py-3 text-xs font-semibold text-ink">完整后端 JSON</summary>
                  <pre className="max-h-[55vh] overflow-auto border-t border-line p-4 text-[10px] leading-5 text-muted">
                    {JSON.stringify(payload, null, 2)}
                  </pre>
                </details>
              </>
            )}
          </div>
        </section>
      </div>
    </PageShell>
  );
}
