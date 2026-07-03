import React, { Suspense, lazy } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AppShell, type NavGroup } from '@/ui/AppShell';
import { PAGES, GROUPS, REDIRECTS } from '@/app/routes';

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

export default function App() {
  return (
    <AppShell nav={nav}>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<Navigate to="/research/command" replace />} />
          {REDIRECTS.map(r => (
            <Route key={r.from} path={r.from} element={<Navigate to={r.to} replace />} />
          ))}
          {LAZY.map(({ page, Component }) => (
            <Route key={page.path} path={page.path} element={<Component />} />
          ))}
        </Routes>
      </Suspense>
    </AppShell>
  );
}
