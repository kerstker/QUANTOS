import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MarketMonitor from '../MarketMonitor';
import { makeMftSnapshot } from '@/workbench/__tests__/mft-fixtures';

const sync = vi.hoisted(() => ({
  state: {} as Record<string, unknown>,
  catalog: {} as Record<string, unknown>,
}));

vi.mock('@/workbench/useMftReadModel', () => ({
  useMftReadModel: () => sync.state,
}));

vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: () => sync.catalog,
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
    sync.catalog = {
      apiState: 'connected', usingBackend: true, error: null,
      payload: {
        source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
        data_provenance: 'REAL_PIT_DATASETS',
        binance_realtime: {
          verdict: 'PASS', checked_at_ms: 1_700_000_005_000, universe_size: 14,
          stream: {
            status_present: true, effective_streaming: true, reported_streaming: true,
            fresh: true, age_ms: 1611, lag_ms: 1,
            coverage: { requested: 14, complete: 14 },
            connections: { public: true, market: true }, reconnects: {},
            event_counts: { bookTicker: 12_245_151, kline_1m: 265_423 },
            rejected_frames: 0, chain_valid: true, routes: {}, source_path: null,
          },
          rest_snapshot: {
            status_present: true, fresh: true, age_ms: 140_887, verdict: 'PASS',
            coverage: { requested: 14, complete: 14, failed: 0 }, chain_valid: true,
            errors: 0, fields: [], source_path: null,
          },
          feature_projection: {
            status_present: true, fresh: true, age_ms: 10_000, status: 'PASS',
            feature_count: 8, instrument_count: 14, retained_value_count: 100,
            new_value_count: 8, eligible_source_snapshot_count: 1,
            skipped_existing_snapshot_count: 0, materialization_elapsed_ms: 100,
            bounded_drift_read: true, drift_loaded_observation_count: 100,
            drift_history_limit_per_series: 500, drift_status: 'BLOCK',
            drift_status_counts: { BLOCK: 1 }, drift_series_count: 8,
            batch_chain_valid: true, calibration: 'EMPIRICALLY_CALIBRATED_PIT_V1',
            calibration_id: 'cal-1', warn_ks: 0.1, block_ks: 0.2,
            calibration_window_count: 10, load_bearing: true, source_path: null,
          },
          safety: {
            public_market_data_only: true, credentials: false, private_stream: false,
            broker_connectivity: false, order_submission: false, live_trading: false,
          },
        },
      },
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
    expect(screen.getByText('REAL_PUBLIC_MARKET_MONITORING')).toBeTruthy();
    expect(screen.getByText('12,510,574')).toBeTruthy();
    expect(screen.getByText('EXECUTION AUTHORITY UNAVAILABLE')).toBeTruthy();
    expect(screen.queryByText('CONTIGUOUS')).toBeNull();
    expect(screen.queryByText('$125.50')).toBeNull();
    expect(screen.queryByText(/买入|卖出|BUY|SELL/i)).toBeNull();
  });

  it('distinguishes the planned absence of an MFT producer from a frontend outage', () => {
    sync.state = {
      phase: 'UNAVAILABLE',
      snapshot: {
        ...availableSnapshot(), status: 'BLOCKED', snapshot_status: 'UNAVAILABLE',
        availability_mode: 'NOT_PROVISIONED', producer_status: 'ABSENT_IN_L2_MVP',
        reason: 'STORE_MISSING',
      },
      reason: 'STORE_MISSING', pages: 0, reloaded: false, transport: 'NONE',
    };
    render(<MarketMonitor />);
    expect(screen.getByText('MFT_VIEW_NOT_PROVISIONED')).toBeTruthy();
    expect(screen.getByText('EXPECTED_L2_CONFIGURATION')).toBeTruthy();
    expect(screen.getByText('REAL_PUBLIC_MARKET_MONITORING')).toBeTruthy();
    expect(screen.queryByText('MFT_UNAVAILABLE')).toBeNull();
  });

  it('does not claim public monitoring when its safety contract is not exact', () => {
    sync.state = {
      phase: 'UNAVAILABLE', snapshot: null, reason: 'STORE_MISSING',
      pages: 0, reloaded: false, transport: 'NONE',
    };
    const catalog = sync.catalog.payload as Record<string, any>;
    catalog.binance_realtime.safety.order_submission = true;

    render(<MarketMonitor />);

    expect(screen.queryByText('REAL_PUBLIC_MARKET_MONITORING')).toBeNull();
    expect(screen.getByText(/公开市场数据权威同样不可用/)).toBeTruthy();
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
