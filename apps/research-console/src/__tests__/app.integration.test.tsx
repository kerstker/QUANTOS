import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from '@/lib/router';
import App from '@/App';
import { GROUPS } from '@/app/routes';

// App 级集成:完整外壳(AppShell + 注册表派生的导航 + lazy 路由 + 重定向)在 jsdom 里真渲染。
// 冒烟测试盖单页;这里盖「组装」——五入口操作面默认简洁、完整导航按需可达、旧路由语义重定向。
describe('App shell integration (registry-derived nav + redirects)', () => {
  // 断言用「页面独有内容」而非页名——页名同时出现在导航栏里,findByText 会撞多元素。
  it('lands on Command Center and exposes only five primary operations by default', async () => {
    render(<MemoryRouter initialEntries={['/']}><App /></MemoryRouter>);
    expect(await screen.findByText(/一屏只回答四件事/)).toBeTruthy();
    const primary = screen.getByRole('navigation', { name: 'primary operations' });
    expect(within(primary).getAllByRole('link')).toHaveLength(5);
    expect(screen.queryByRole('navigation', { name: 'lifecycle pipeline' })).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'section pages' })).toBeNull();

    const advanced = screen.getByRole('button', { name: /Advanced|高级工作台/ });
    expect(advanced.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(advanced);
    expect(advanced.getAttribute('aria-expanded')).toBe('true');
    for (const group of GROUPS) {
      expect(screen.getAllByText(group.zh).length, group.id).toBeGreaterThan(0);
    }
  });

  it('redirects a removed blueprint route to the honest completeness page', async () => {
    render(<MemoryRouter initialEntries={['/research/incident-response']}><App /></MemoryRouter>);
    expect(await screen.findByText('System Completeness Audit')).toBeTruthy();
  });

  it('redirects the bare /research route to the operator command center', async () => {
    render(<MemoryRouter initialEntries={['/research']}><App /></MemoryRouter>);
    expect(await screen.findByText(/一屏只回答四件事/)).toBeTruthy();
  });

  it('Command Center is still reachable at its own route', async () => {
    render(<MemoryRouter initialEntries={['/research/command']}><App /></MemoryRouter>);
    expect(await screen.findByText(/一屏只回答四件事/)).toBeTruthy();
  });
  it('opens the global route search instead of a decorative placeholder', async () => {
    render(<MemoryRouter initialEntries={['/research/getting-started']}><App /></MemoryRouter>);
    const search = screen.getByRole('button', { name: /Global search|全局搜索/ });
    fireEvent.click(search);
    expect(await screen.findByRole('dialog', { name: 'global search' })).toBeTruthy();
    expect(screen.getByPlaceholderText(/Search pages|搜索页面/)).toBeTruthy();
  });

  it('keeps the lifecycle rail compact and moves section pages behind disclosure', async () => {
    render(<MemoryRouter initialEntries={['/research/dry-run']}><App /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: /全自动影子纸面驾驶舱/ })).toBeTruthy();

    const advanced = screen.getByRole('button', { name: /Advanced|高级工作台/ });
    expect(advanced.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(advanced);

    const lifecycle = screen.getByRole('navigation', { name: 'lifecycle pipeline' });
    expect(lifecycle.querySelector('[data-layout="compact-grid"]')).toBeTruthy();
    expect(within(lifecycle).queryByText(/auto \d/i)).toBeNull();

    const section = screen.getByRole('navigation', { name: 'section pages' });
    const menu = within(section).getByRole('button', { name: /All pages|全部页面/ });
    expect(menu.getAttribute('aria-expanded')).toBe('false');
    expect(within(section).queryByRole('link', { name: /GoNoGo Evidence/ })).toBeNull();

    fireEvent.click(menu);
    expect(menu.getAttribute('aria-expanded')).toBe('true');
    expect(within(section).getByRole('link', { name: /GoNoGo Evidence/ })).toBeTruthy();
  });
});
