import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from '@/lib/router';
import Studio from '@/App';
vi.mock('@/lib/api/research-console-api', () => ({
  fetchResearchRuns: vi.fn().mockResolvedValue({ source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, registry_available: true, runs: [] }),
  fetchResearchRun: vi.fn().mockResolvedValue({ run: null }),
  fetchTechnicalReplay: vi.fn().mockResolvedValue({ technical_replay: null }),
  fetchForwardDashboard: vi.fn().mockResolvedValue({observations:[],schedule:null,errors:[]}),
  fetchMarketDashboard: vi.fn().mockResolvedValue({candles:[],samples:[],symbols:['BTCUSDT'],status:'UNAVAILABLE'}),
  fetchConsoleModuleSnapshot: vi.fn().mockRejectedValue(new Error('offline')),
}));
vi.mock('@/lib/api/research-runner-api', () => ({
  fetchRunnerHealth: vi.fn().mockResolvedValue({ mode: 'RESEARCH_RUNNER', live_disallowed: true, kinds: ['btc_forward', 'btc_factory'], kind_policies: {} }),
  getOperator: () => 'researcher', getRunnerToken: () => '', setOperator: vi.fn(), setRunnerToken: vi.fn(), triggerRun: vi.fn(), cancelRun: vi.fn(),
}));
describe('Research Studio', () => {
  it('keeps gold replay parameters and the no-profit scope visible', async () => {
    render(<MemoryRouter initialEntries={['/studio/new?kind=gold_replay&attempt=7']}><Studio/></MemoryRouter>);
    expect(await screen.findByLabelText('实验编号')).toHaveValue(7);
    expect(screen.getByText(/此任务不模拟成交、不计算盈利/)).toBeInTheDocument();
    expect(screen.getByRole('button', {name:'提交研究任务'})).toBeDisabled();
  });
  it('opens a research workspace with explicit empty records and fifteen connected destinations', async () => {
    render(<MemoryRouter>
      <Studio />
    </MemoryRouter>);
    expect(screen.getByRole('heading', { name: '研究工作台' })).toBeInTheDocument();
    expect(await screen.findByText('还没有研究运行')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '主导航' }).querySelectorAll('a')).toHaveLength(15);
    expect(screen.getByRole('link', {name: '黄金账户'})).toHaveAttribute('href', '/studio/gold');
    expect(screen.getByRole('link', {name: 'DEMO 控制中心'})).toHaveAttribute('href', '/studio/demo');
    expect(screen.getByRole('link', {name: '打开 DEMO 控制中心'})).toHaveAttribute('href', '/studio/demo');
    expect(screen.getByRole('link', {name: '进入 DEMO 控制中心'})).toHaveAttribute('href', '/studio/demo');
    expect(screen.getByRole('link', { name: /Alpha 生命周期/ })).toHaveAttribute('href', '/studio/lifecycle');
    expect(screen.getByText('研究模式')).toHaveAttribute('title', 'L2_DRY_RUN_ONLY');
  });
  it('shows an actual missing run instead of an empty result chart', async () => {
    render(<MemoryRouter initialEntries={['/research/runs/missing']}>
      <Studio />
    </MemoryRouter>);
    expect(await screen.findByText('未找到这次运行')).toBeInTheDocument();
  });
  it('offers a research form and never an order or approval form', async () => {
    render(<MemoryRouter initialEntries={['/studio/new']}>
      <Studio />
    </MemoryRouter>);
    expect(await screen.findByLabelText('研究类型')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('研究类型'), { target: { value: 'btc_forward' } });
    expect(screen.getByLabelText('前向操作')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '提交研究任务' })).toBeDisabled();
    expect(screen.queryByText('下单')).not.toBeInTheDocument();
  });
  it('has a real not-found view and keyboard-accessible navigation', () => {
    render(<MemoryRouter initialEntries={['/missing']}>
      <Studio />
    </MemoryRouter>);
    expect(screen.getByRole('heading', { name: '页面不存在' })).toBeInTheDocument();
    expect(screen.getByText('跳到主要内容')).toHaveAttribute('href', '#studio-main');
  });
});

it('keeps the originating experiment and next forward action when continuing research', async () => {
 render(<MemoryRouter initialEntries={['/studio/new?kind=btc_forward&attempt=22&action=2']}><Studio/></MemoryRouter>);
 expect(await screen.findByLabelText('实验编号')).toHaveValue(22);
 expect(screen.getByLabelText('前向操作')).toHaveValue('2');
});
