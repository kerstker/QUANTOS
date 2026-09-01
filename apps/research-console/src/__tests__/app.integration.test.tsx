import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import App from '@/App';
import { MemoryRouter } from '@/lib/router';

describe('Wave 3 app shell integration', () => {
  it('lands on Market Monitor with exactly five object workspaces', async () => {
    render(<MemoryRouter initialEntries={['/']}><App /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: /市场监控/ })).toBeTruthy();
    const primary = screen.getByRole('navigation', { name: 'object workspaces' });
    const links = within(primary).getAllByRole('link');
    expect(links).toHaveLength(5);
    expect(links.map(link => link.getAttribute('href'))).toEqual([
      '/workbench/market',
      '/workbench/alpha',
      '/workbench/validation',
      '/workbench/portfolio-risk',
      '/workbench/execution',
    ]);
    expect(screen.queryByRole('button', { name: /Advanced|高级工作台/ })).toBeNull();
  });

  it('keeps L2 and unverified identity visible when the real MFT API is unavailable', async () => {
    render(<MemoryRouter initialEntries={['/workbench/market']}><App /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: /市场监控/ })).toBeTruthy();
    const status = screen.getByRole('status', { name: '全局运行状态' });
    expect(status).toHaveTextContent('L2_DRY_RUN_ONLY');
    expect(status).toHaveTextContent('强身份未验证');
    expect(status).toHaveTextContent(/UNAVAILABLE|不可用/);
  });

  it('keeps the safety bar visible when switching to the light theme', async () => {
    document.documentElement.dataset.theme = 'dark';
    render(<MemoryRouter initialEntries={['/workbench/market']}><App /></MemoryRouter>);
    await screen.findByRole('heading', { name: /市场监控/ });
    fireEvent.click(screen.getByRole('button', { name: '切换浅色模式' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(screen.getByRole('status', { name: '全局运行状态' })).toHaveTextContent('L2_DRY_RUN_ONLY');
    document.documentElement.dataset.theme = 'dark';
  });

  it('opens governance, audit, health, and task surfaces as global drawers', async () => {
    render(<MemoryRouter initialEntries={['/workbench/market']}><App /></MemoryRouter>);
    await screen.findByRole('heading', { name: /市场监控/ });
    for (const label of ['治理', '审计', '健康', '任务']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole('button', { name: '治理' }));
    expect(screen.getByRole('dialog', { name: '治理抽屉' })).toHaveTextContent('GOVERNANCE_UNAVAILABLE');
    fireEvent.click(screen.getByRole('button', { name: '关闭治理抽屉' }));
    expect(screen.queryByRole('dialog', { name: '治理抽屉' })).toBeNull();
  });

  it('redirects the bare research route to Market Monitor', async () => {
    render(<MemoryRouter initialEntries={['/research']}><App /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: /市场监控/ })).toBeTruthy();
  });

  it('does not preserve an ambiguous stale blueprint redirect', () => {
    render(<MemoryRouter initialEntries={['/research/incident-response']}><App /></MemoryRouter>);
    expect(screen.queryByRole('heading')).toBeNull();
    expect(screen.getByRole('navigation', { name: 'object workspaces' })).toBeTruthy();
  });

  it('maps the old command overview bookmark to the read-only task drawer', async () => {
    render(<MemoryRouter initialEntries={['/research/command']}><App /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: /市场监控/ })).toBeTruthy();
    expect(screen.getByRole('dialog', { name: '任务抽屉' })).toHaveTextContent('TASKS_UNAVAILABLE');
    expect(screen.getByRole('navigation', { name: 'object workspaces' })).toBeTruthy();
  });

  it('keeps bounded evidence detail routes without promoting them to top-level workspaces', async () => {
    render(<MemoryRouter initialEntries={['/research/experiments']}><App /></MemoryRouter>);
    expect(await screen.findByRole('heading', { level: 1, name: /后端快照不可用/ })).toBeTruthy();
    expect(screen.getByText('PRODUCTION_FIXTURES_DISABLED')).toBeTruthy();
    const primary = screen.getByRole('navigation', { name: 'object workspaces' });
    expect(within(primary).getAllByRole('link')).toHaveLength(5);
  });

  it('opens global search for workspaces, bounded details, and four drawers', async () => {
    render(<MemoryRouter initialEntries={['/workbench/market']}><App /></MemoryRouter>);
    await screen.findByRole('heading', { name: /市场监控/ });
    fireEvent.click(screen.getByRole('button', { name: /Global search|全局搜索/ }));
    expect(await screen.findByRole('dialog', { name: '全局搜索' })).toBeTruthy();
    expect(screen.getByPlaceholderText(/Search pages|搜索页面/)).toBeTruthy();
    expect(screen.getAllByText('Alpha 控制面').length).toBeGreaterThan(1);
    expect(screen.getAllByText('治理').length).toBeGreaterThan(1);
    expect(screen.getByText('实验登记详情')).toBeTruthy();
    expect(screen.queryByText('Getting Started')).toBeNull();
  });
});
