import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMftSnapshot } from '@/workbench/__tests__/mft-fixtures';
import { GlobalDrawerHost } from '@/workbench/GlobalDrawerHost';
import { GlobalStatusBar } from '@/workbench/GlobalStatusBar';

const sync = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));

vi.mock('@/workbench/useMftReadModel', () => ({
  useMftReadModel: () => sync.state,
}));

describe('GlobalStatusBar', () => {
  beforeEach(() => {
    sync.state = {
      phase: 'LIVE',
      snapshot: makeMftSnapshot({
        market_gap: { status: 'CONTIGUOUS', venue: 'BINANCE', account: 'TESTNET-01' },
        risk: { status: 'PASS', kill_switch_latched: false },
        reconciliation: { status: 'RECONCILED' },
        mandate_expiry: { status: 'VALID_TESTNET_ONLY', expires_at_ms: 9_999_999_999_999 },
      }),
      reason: null,
      pages: 1,
      reloaded: false,
      transport: 'SSE',
    };
  });

  it('keeps environment, source timing, safety, mandate, and identity visible', () => {
    render(<GlobalStatusBar />);
    const bar = screen.getByRole('status', { name: '全局运行状态' });
    expect(bar).toHaveTextContent('L2_DRY_RUN_ONLY');
    expect(bar).toHaveTextContent('BINANCE');
    expect(bar).toHaveTextContent('TESTNET-01');
    expect(bar).toHaveTextContent('CURRENT');
    expect(bar).toHaveTextContent('watermark');
    expect(bar).toHaveTextContent('熔断正常');
    expect(bar).toHaveTextContent('VALID_TESTNET_ONLY');
    expect(bar).toHaveTextContent('强身份未验证');
  });

  it('does not fabricate operational values when the authority is unavailable', () => {
    sync.state = {
      phase: 'UNAVAILABLE', snapshot: null, reason: 'STORE_MISSING', pages: 0,
      reloaded: false, transport: 'NONE',
    };
    render(<GlobalStatusBar />);
    const bar = screen.getByRole('status', { name: '全局运行状态' });
    expect(bar).toHaveTextContent('UNAVAILABLE');
    expect(bar).toHaveTextContent('UNKNOWN');
    expect(bar).not.toHaveTextContent('BINANCE');
  });
});

describe('GlobalDrawerHost', () => {
  it('is keyboard reachable and closes with Escape', () => {
    render(<GlobalDrawerHost />);
    fireEvent.click(screen.getByRole('button', { name: '健康' }));
    expect(screen.getByRole('dialog', { name: '健康抽屉' })).toHaveTextContent('UNAVAILABLE');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: '健康抽屉' })).toBeNull();
  });
});
