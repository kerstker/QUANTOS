import { useLocation } from '@/lib/router';
import { WORKBENCH_DATA_SOURCE_MANIFEST } from '@/workbench/data-source-manifest';

export default function WorkbenchScaffold() {
  const { pathname } = useLocation();
  const entry = WORKBENCH_DATA_SOURCE_MANIFEST.find(item => item.route === pathname)
    ?? WORKBENCH_DATA_SOURCE_MANIFEST[0];

  return (
    <section className="mx-auto max-w-7xl">
      <div className="rounded-2xl border border-line bg-panel p-5 shadow-sm sm:p-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-3xs font-semibold uppercase tracking-[0.18em] text-accent">{entry.en}</p>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">{entry.zh}</h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-muted">{entry.purpose}</p>
          </div>
          <div className="rounded-lg border border-warn/35 bg-warn/10 px-3 py-2 text-xs font-semibold text-warn">
            L2_DRY_RUN_ONLY · READ ONLY
          </div>
        </div>

        <div className="mt-7 grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
          <article className="rounded-xl border border-line bg-inset/35 p-5">
            <p className="text-3xs font-semibold uppercase tracking-[0.15em] text-faint">CONTENT SLICE</p>
            <h2 className="mt-2 text-lg font-semibold text-ink">UNAVAILABLE</h2>
            <p className="mt-2 text-sm leading-6 text-muted">
              该对象工作区的数据视图将在后续 Wave 3 切片中接入。这里不显示合成数字，也不启用隐式 mock。
            </p>
          </article>
          <article className="rounded-xl border border-line bg-inset/35 p-5">
            <p className="text-3xs font-semibold uppercase tracking-[0.15em] text-faint">AUTHORITATIVE SOURCES</p>
            <ul className="mt-3 space-y-2 text-xs text-muted">
              {entry.authoritativeEndpoints.map(endpoint => (
                <li key={endpoint} className="break-all rounded border border-line/70 bg-surface/60 px-2.5 py-2 font-mono">
                  {endpoint}
                </li>
              ))}
            </ul>
          </article>
        </div>
      </div>
    </section>
  );
}
