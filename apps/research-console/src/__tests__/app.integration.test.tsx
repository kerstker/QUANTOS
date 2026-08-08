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
    expect(screen.getByRole('dialog', { name: '治理抽屉' })).toHaveTextContent('只读');
    fireEvent.click(screen.getByRole('button', { name: '关闭治理抽屉' }));
    expect(screen.queryByRole('dialog', { name: '治理抽屉' })).toBeNull();
  });

  it('redirects the bare research route to Market Monitor', async () => {
    render(<MemoryRouter initialEntries={['/research']}><App /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: /市场监控/ })).toBeTruthy();
  });

  it('keeps removed blueprint routes on their honest legacy redirect', async () => {
    render(<MemoryRouter initialEntries={['/research/incident-response']}><App /></MemoryRouter>);
    expect(await screen.findByText('系统完整度审查')).toBeTruthy();
  });

  it('keeps legacy details reachable without making them top-level workspaces', async () => {
    render(<MemoryRouter initialEntries={['/research/command']}><App /></MemoryRouter>);
    expect(await screen.findByText(/一屏只回答四件事/)).toBeTruthy();
    expect(screen.getByRole('navigation', { name: 'object workspaces' })).toBeTruthy();
  });

  it('opens global search for legacy detail and drill-down pages', async () => {
    render(<MemoryRouter initialEntries={['/workbench/market']}><App /></MemoryRouter>);
    await screen.findByRole('heading', { name: /市场监控/ });
    fireEvent.click(screen.getByRole('button', { name: /Global search|全局搜索/ }));
    expect(await screen.findByRole('dialog', { name: '全局搜索' })).toBeTruthy();
    expect(screen.getByPlaceholderText(/Search pages|搜索页面/)).toBeTruthy();
  });
});
