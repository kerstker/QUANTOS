import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MarketMonitor from '../MarketMonitor';
import { makeMftSnapshot } from '@/workbench/__tests__/mft-fixtures';

const sync = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));

vi.mock('@/workbench/useMftReadModel', () => ({
  useMftReadModel: () => sync.state,
}));

const HASH = 'a'.repeat(64);

function availableSnapshot() {
  return makeMftSnapshot({
    snapshot_id: HASH,
    snapshot_ids: [HASH],
    data_quality: {
      status: 'REAL',
      provenance: 'REAL_MFT_READ_MODEL',
      snapshot_ids: [HASH],
    },
    as_of_ms: 1_700_000_005_000,
    watermark: { sequence: 7, event_at_ms: 1_700_000_004_000, event_hash: HASH },
    cursor: `7:${HASH}`,
    freshness: {
      status: 'CURRENT', age_ms: 1_000,
      policy_id: 'mft-console-freshness', policy_version: '1', max_age_ms: 7_200_000,
    },
    market_gap: {
      status: 'CONTIGUOUS', venue: 'BINANCE', account: 'TESTNET-01',
      latest_closed_bar_ms: 1_700_000_000_000, missing_intervals: [],
    },
    scheduler: {
      status: 'LEADER', last_success_ms: 1_700_000_001_000,
      next_due_ms: 1_700_003_600_000,
    },
    positions: { status: 'RECONCILED', position_count: 2 },
    risk: {
      status: 'ALLOWED_TESTNET', kill_switch_latched: false, new_risk_allowed: true,
      day_pnl: 125.5, equity: 100_125.5, gross_exposure: 5_000, drawdown: 0.002,
    },
    reconciliation: { status: 'IN_SYNC', requires_manual_review: false },
    mandate_expiry: { status: 'VALID_TESTNET_ONLY', expires_at_ms: 1_800_000_000_000 },
  });
}

describe('MarketMonitor', () => {
  beforeEach(() => {
    sync.state = {
      phase: 'LIVE', snapshot: availableSnapshot(), reason: null,
      pages: 1, reloaded: false, transport: 'SSE',
    };
  });

  it('renders real market, scheduler, risk, alert, event, and rebalance state with provenance', () => {
    render(<MarketMonitor />);
    expect(screen.getByRole('heading', { name: '市场监控' })).toBeTruthy();
    expect(screen.getByText('CONTIGUOUS')).toBeTruthy();
    expect(screen.getByText('LEADER')).toBeTruthy();
    expect(screen.getByText('ALLOWED_TESTNET')).toBeTruthy();
    expect(screen.getByText('$125.50')).toBeTruthy();
    expect(screen.getByText(/authority sequence 7/i)).toBeTruthy();
    expect(screen.getByText(/next rebalance/i)).toBeTruthy();
    expect(screen.getByText('0 ACTIVE ALERTS')).toBeTruthy();
    expect(screen.getAllByText('MFT_READ_MODEL_SQLITE_AUTHORITY').length).toBeGreaterThan(1);
    expect(screen.getAllByText(/aaaaaaaaaaaa/).length).toBeGreaterThan(1);
    expect(screen.getAllByText(/CURRENT · age 1000ms/).length).toBeGreaterThan(1);
  });

  it('shows diagnostics only when the MFT authority is unavailable', () => {
    sync.state = {
      phase: 'UNAVAILABLE', snapshot: null, reason: 'STORE_MISSING',
      pages: 0, reloaded: false, transport: 'NONE',
    };
    render(<MarketMonitor />);
    expect(screen.getByText('MFT_UNAVAILABLE')).toBeTruthy();
    expect(screen.getByText(/STORE_MISSING/)).toBeTruthy();
    expect(screen.getByText('DIAGNOSTIC_ONLY')).toBeTruthy();
    expect(screen.queryByText('CONTIGUOUS')).toBeNull();
    expect(screen.queryByText('$125.50')).toBeNull();
    expect(screen.queryByText(/买入|卖出|BUY|SELL/i)).toBeNull();
  });

  it('does not render retained operational values while the snapshot is stale', () => {
    const snapshot = availableSnapshot();
    snapshot.freshness = { ...snapshot.freshness, status: 'STALE', age_ms: 9_000_000 };
    sync.state = {
      phase: 'STALE', snapshot, reason: 'FRESHNESS_EXCEEDED',
      pages: 1, reloaded: false, transport: 'SSE',
    };
    render(<MarketMonitor />);
    expect(screen.getByText('MFT_STALE')).toBeTruthy();
    expect(screen.getByText('DIAGNOSTIC_ONLY')).toBeTruthy();
    expect(screen.queryByText('CONTIGUOUS')).toBeNull();
    expect(screen.queryByText('LEADER')).toBeNull();
    expect(screen.queryByText('$125.50')).toBeNull();
  });

  it('fails closed when an operationally required section is unavailable', () => {
    const snapshot = availableSnapshot();
    snapshot.market_gap = { status: 'UNAVAILABLE' };
    sync.state = {
      phase: 'LIVE', snapshot, reason: null,
      pages: 1, reloaded: false, transport: 'SSE',
    };
    render(<MarketMonitor />);
    expect(screen.getByText('MFT_UNAVAILABLE')).toBeTruthy();
    expect(screen.getByText('REQUIRED_SECTION_UNAVAILABLE')).toBeTruthy();
    expect(screen.queryByText('LEADER')).toBeNull();
    expect(screen.queryByText('$125.50')).toBeNull();
  });
});
