import { describe, it, expect } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import App from '@/App';
import { GROUPS } from '@/app/routes';

// App 级集成:完整外壳(AppShell + 注册表派生的导航 + lazy 路由 + 重定向)在 jsdom 里真渲染。
// 冒烟测试盖单页;这里盖「组装」——导航 7 组齐全、落地页重定向、删除页的旧路由落到语义就近目标。
describe('App shell integration (registry-derived nav + redirects)', () => {
  // 断言用「页面独有内容」而非页名——页名同时出现在导航栏里,findByText 会撞多元素。
  it('renders all nav groups and lands on Getting Started at /', async () => {
    render(<MemoryRouter initialEntries={['/']}><App /></MemoryRouter>);
    for (const group of GROUPS) {
      expect(screen.getAllByText(group.zh).length, group.id).toBeGreaterThan(0);
    }
    expect(await screen.findByText(/5 分钟最短路径/)).toBeTruthy();
  });

  it('redirects a removed blueprint route to the honest completeness page', async () => {
    render(<MemoryRouter initialEntries={['/research/incident-response']}><App /></MemoryRouter>);
    expect(await screen.findByText('System Completeness Audit')).toBeTruthy();
  });

  it('redirects the bare /research route to the Getting Started landing page', async () => {
    render(<MemoryRouter initialEntries={['/research']}><App /></MemoryRouter>);
    expect(await screen.findByText(/5 分钟最短路径/)).toBeTruthy();
  });

  it('Command Center is still reachable at its own route', async () => {
    render(<MemoryRouter initialEntries={['/research/command']}><App /></MemoryRouter>);
    expect(await screen.findByText(/研究全流程一张图/)).toBeTruthy();
  });
  it('opens the global route search instead of a decorative placeholder', async () => {
    render(<MemoryRouter initialEntries={['/research/getting-started']}><App /></MemoryRouter>);
    const search = screen.getByRole('button', { name: /Global search|全局搜索/ });
    fireEvent.click(search);
    expect(await screen.findByRole('dialog', { name: 'global search' })).toBeTruthy();
    expect(screen.getByPlaceholderText(/Search pages|搜索页面/)).toBeTruthy();
  });
});
