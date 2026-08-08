import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from '@/lib/router';
import { PAGES, GROUPS, REDIRECTS } from '@/app/routes';
import { WORKBENCH_DATA_SOURCE_MANIFEST } from '@/workbench/data-source-manifest';
import { ConfirmDialogHost } from '@/ui/dialog';
import { ToastProvider } from '@/ui/toast';

// 注册表驱动的全量冒烟:遍历 src/app/routes.tsx 的 PAGES(路由/导航的唯一真源),
// 每一页异步加载并渲染——新增页面自动被覆盖,永远不会再出现「只冒烟 14/42」的地板缺口。
// 测试环境 VITE_MOCK_MODE 生效,页面渲染 mock fixtures,零网络;冒烟证明:页面能挂载、
// 产出非空 DOM、不抛异常。ConfirmDialogHost/ToastProvider mirror AppShell's real mount point
// (useConfirm/usePrompt/useToast throw outside them, same as in production).
describe('every registered console page renders in mock mode', () => {
  for (const page of PAGES) {
    it(`${page.en} (${page.path}) mounts and produces non-empty DOM`, async () => {
      const mod = await page.load();
      const Page = mod.default;
      const { container } = render(
        <MemoryRouter>
          <ToastProvider>
            <ConfirmDialogHost>
              <Page />
            </ConfirmDialogHost>
          </ToastProvider>
        </MemoryRouter>,
      );
      expect(container.firstChild).not.toBeNull();
      expect((container.textContent ?? '').trim().length).toBeGreaterThan(0);
    });
  }
});

// 注册表自洽:路径唯一、导航组引用存在、重定向目标可解析。
// 这些是以前 App.tsx 里 nav[] 与 <Routes> 两份手工清单会静默漂移的地方,现在机器钉死。
describe('route registry consistency', () => {
  it('exposes exactly five manifest-backed top-level workspaces', () => {
    const workspaces = PAGES.filter(page => page.group === 'workbench');
    expect(workspaces).toHaveLength(5);
    expect(workspaces.map(page => page.path)).toEqual(
      WORKBENCH_DATA_SOURCE_MANIFEST.map(item => item.route),
    );
    expect(PAGES.some(page => /Getting Started|Research Findings/i.test(page.en))).toBe(false);
    expect(PAGES.filter(page => page.group === null).length).toBeGreaterThan(0);
  });

  it('page paths are unique', () => {
    const paths = PAGES.map(p => p.path);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it('every nav page references a declared group and carries labels', () => {
    const groupIds = new Set(GROUPS.map(g => g.id));
    for (const page of PAGES) {
      if (page.group !== null) expect(groupIds.has(page.group), page.path).toBe(true);
      expect(page.zh.length, page.path).toBeGreaterThan(0);
      expect(page.en.length, page.path).toBeGreaterThan(0);
      expect(page.icon.length, page.path).toBeGreaterThan(0);
    }
  });

  it('keeps one command-palette group backed only by the five workspaces', () => {
    expect(GROUPS).toHaveLength(1);
    for (const group of GROUPS) {
      expect(PAGES.some(p => p.group === group.id), group.id).toBe(true);
    }
  });

  it('keeps only direct, semantically explicit legacy redirects', () => {
    const paths = new Set(PAGES.map(p => p.path));
    const froms = new Set(REDIRECTS.map(r => r.from));
    expect(new Set(REDIRECTS.map(r => r.from)).size).toBe(REDIRECTS.length);
    for (const r of REDIRECTS) {
      const target = new URL(r.to, 'http://quantos.local');
      expect(paths.has(target.pathname), `${r.from} -> ${r.to}`).toBe(true);
      expect(froms.has(target.pathname), `${r.from} -> ${r.to} chains into a redirect`).toBe(false);
      expect(paths.has(r.from), `${r.from} shadows a live page`).toBe(false);
      expect(target.pathname.startsWith('/workbench/'), r.to).toBe(true);
      const drawer = target.searchParams.get('drawer');
      if (drawer !== null) {
        expect(['governance', 'audit', 'health', 'tasks']).toContain(drawer);
      }
    }
  });
});
