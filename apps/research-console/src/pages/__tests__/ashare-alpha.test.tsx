import { render, screen } from '@testing-library/react';
import { MemoryRouter } from '@/lib/router';
import { describe, expect, it, vi } from 'vitest';
import AShareAlpha from '@/pages/AShareAlpha';
import type { AShareAlphaPayload } from '@/lib/types/research';

/**
 * AShareAlpha gating tests. The registry-driven smoke test already proves the page
 * mounts in mock mode; these pin the two pieces of page-level gating logic:
 *  1) 诚实局限默认展开(项目的诚实姿态)+ mock 模式下「演示数据」标记可见;
 *  2) runner 操作区在 runner 未配置时按钮禁用并给出原因(后端真实快照也不放行)。
 * Test env has no VITE_RUNNER_URL, so runnerEnabled() === false and no network happens.
 */

const mockRealPayload: AShareAlphaPayload = {
  source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true,
  provenance: 'REAL_ASHARE_ALPHA_SNAPSHOT', generated_at: '2026-07-21T00:00:00Z',
  data_freshness: { stocks: 4873, bars: 2284160, last_date: 20260718, sources: { wind: 3412, ifind: 1461 } },
  universe: { file_count: 5120, used: 4873 },
  factors: [
    { name: 'rev_5d', train_ic: 0.061, train_t: 5.8, test_ic: 0.054, test_t: 4.6, kept: true },
    { name: 'mom_20d', train_ic: 0.021, train_t: 1.9, test_ic: 0.012, test_t: 1.1, kept: false },
  ],
  combo: {
    champion: 'icir_weighted_h1', weights: { rev_5d: 1 },
    h1_test_ic: 0.068, h1_test_t: 5.9, h5_test_ic: 0.052, h5_test_t: 4.4, hit_rate: 0.58,
  },
  basket: {
    n: 50, aum_cny: 5_000_000, cost_bps: 25,
    test: { total_return: 0.612, sharpe: 1.83, max_dd: 0.214, avg_turnover: 0.31 },
    bench_test: { total_return: 0.221, sharpe: 0.72, max_dd: 0.286 },
    equity_test: [1, 1.2, 1.5], bench_equity_test: [1, 1.05, 1.1],
  },
  variants: [
    { name: 'icir_weighted_h1', train_sharpe: 2.41, test_return: 0.612, test_sharpe: 1.83, test_mdd: 0.214, turnover: 0.31, is_champion: true },
  ],
  dry_run: {
    run_id: 'a3f9c1e2b7d4480a9e5f1c2d3b4a5f60', total_return: 0.034, sharpe: 1.42,
    max_drawdown: 0.041, n_approved: 148, n_rejected: 0, residual: 7.2e-9,
  },
  paper_track: {
    alpha_id: 'ashare_microcap_icir_h1', n_weeks: 14, cum_return: 0.087, sharpe: 1.96,
    weekly_ic_mean: 0.049, weekly_ic_t: 3.7, kind: 'BACKFILL',
  },
  cost_grid: [{ cost_bps: 25, total_return: 0.612, sharpe: 1.83 }],
  turnover_caps: [{ cap: 0.3, train_sharpe: 2.41, test_return: 0.612, test_sharpe: 1.83 }],
  holdings_latest: [{ code: '002647.SZ', weight: 0.0241 }],
  honest_limitations: ['局限甲:微盘流动性只在成本网格内近似。', '局限乙:paper-track 为 BACKFILL。'],
};

// Mutable snapshot state for the mocked hook (system-completeness.test.tsx pattern,
// via vi.hoisted so the factory can see it before module evaluation).
const h = vi.hoisted(() => ({ mode: 'mock' as 'mock' | 'real' }));
vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: () => (h.mode === 'real'
    ? { usingBackend: true, apiState: 'connected', error: null, payload: mockRealPayload, snapshotState: 'REAL' }
    : { usingBackend: false, apiState: 'disabled', error: null, payload: null }),
}));

describe('AShareAlpha workbench gating', () => {
  it('mock mode: honest limitations expanded by default, demo-data marker visible, runner buttons disabled with reason', () => {
    h.mode = 'mock';
    render(<MemoryRouter><AShareAlpha /></MemoryRouter>);
    // 诚实局限默认展开(mock payload 里的条目直接出现在 DOM,不需要点开 Callout)
    expect(screen.getByText(/微盘流动性/)).toBeInTheDocument();
    expect(screen.getByText(/paper-track 当前为 BACKFILL/)).toBeInTheDocument();
    // 演示数据标记(mock 模式下 ProvenanceRibbon extra chip)
    expect(screen.getByText(/DEMO DATA/)).toBeInTheDocument();
    // runner 未配置 → 两个按钮都禁用并显示原因
    expect(screen.getByRole('button', { name: /重跑 A股Alpha 研究/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /推进 paper-track/ })).toBeDisabled();
    expect(screen.getByText(/VITE_RUNNER_URL not configured/)).toBeInTheDocument();
  });

  it('backend REAL snapshot: real provenance headline + factor table; runner still gated without runner config', () => {
    h.mode = 'real';
    render(<MemoryRouter><AShareAlpha /></MemoryRouter>);
    // REAL 快照 → 真实研究结果标题,而非合成警告
    expect(screen.getByText(/真实研究结果/)).toBeInTheDocument();
    // 因子表 + 冠军变体渲染(rev_5d 出现在因子表与复合权重条两处)
    expect(screen.getAllByText('rev_5d').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/CHAMPION/).length).toBeGreaterThan(0);
    // 后端 payload 的诚实局限同样默认展开
    expect(screen.getByText(/局限甲/)).toBeInTheDocument();
    // runner 未配置 → 即使后端连接,按钮仍禁用
    expect(screen.getByRole('button', { name: /重跑 A股Alpha 研究/ })).toBeDisabled();
  });
});
