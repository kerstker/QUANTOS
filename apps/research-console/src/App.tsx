import { Suspense, lazy } from 'react';
import { Routes, Route, Navigate } from '@/lib/router';
import { AppShell, type NavGroup } from '@/ui/AppShell';
import { PAGES, GROUPS, REDIRECTS } from '@/app/routes';
import { ErrorBoundary } from '@/ui/primitives';

// 路由、导航、冒烟测试的唯一真源是 src/app/routes.tsx 的页面注册表(2026-07-03 信息架构重构)。
// 这里只做派生:每页 route-level 代码分割(React.lazy),首屏 chunk 保持最小。
const LAZY = PAGES.map(page => ({ page, Component: lazy(page.load) }));

const nav: NavGroup[] = GROUPS.map(group => ({
  zh: group.zh,
  en: group.en,
  items: LAZY.filter(({ page }) => page.group === group.id)
    .map(({ page }) => ({ to: page.path, zh: page.zh, en: page.en, icon: page.icon })),
}));

function RouteFallback() {
  return (
    <div className="flex h-64 items-center justify-center text-xs text-faint">
      <span className="animate-pulse">载入中 / loading…</span>
    </div>
  );
}

function RouteErrorFallback({ page }: { page: (typeof PAGES)[number] }) {
  return (
    <div className="mx-auto mt-10 max-w-xl rounded-xl border border-neg/30 bg-surface p-6">
      <div className="mb-2 text-2xs font-semibold uppercase tracking-[0.14em] text-neg">ROUTE_ERROR</div>
      <h2 className="text-lg font-semibold text-ink">{page.zh} <span className="text-muted">{page.en}</span></h2>
      <p className="mt-2 text-sm text-faint">页面加载失败。其它工作区仍可用；请重试或返回全局搜索。 / This route failed to render. Other workspaces remain available.</p>
      <button type="button" onClick={() => window.location.reload()}
        className="mt-4 rounded-md border border-line px-3 py-1.5 text-2xs text-muted hover:bg-inset">
        重试 / Retry
      </button>
    </div>
  );
}

export default function App() {
  return (
    <AppShell nav={nav}>
      <ErrorBoundary>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<Navigate to="/workbench/market" replace />} />
            {REDIRECTS.map(r => (
              <Route key={r.from} path={r.from} element={<Navigate to={r.to} replace />} />
            ))}
            {LAZY.map(({ page, Component }) => (
              <Route key={page.path} path={page.path}
                element={<ErrorBoundary fallback={<RouteErrorFallback page={page} />}><Component /></ErrorBoundary>} />
            ))}
          </Routes>
        </Suspense>
      </ErrorBoundary>
    </AppShell>
  );
}
