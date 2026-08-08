import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ExecutionOrders from '../ExecutionOrders';
import { makeMftSnapshot } from '@/workbench/__tests__/mft-fixtures';

const sync = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));

vi.mock('@/workbench/useMftReadModel', () => ({
  useMftReadModel: () => sync.state,
}));

const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);

function availableSnapshot() {
  return makeMftSnapshot({
    as_of_ms: 1_700_000_005_000,
    watermark: { sequence: 14, event_at_ms: 1_700_000_004_000, event_hash: HASH_A },
    cursor: `14:${HASH_A}`,
    snapshot_id: HASH_A,
    snapshot_ids: [HASH_A],
    data_quality: {
      status: 'REAL', provenance: 'REAL_MFT_READ_MODEL', snapshot_ids: [HASH_A],
    },
    market_gap: {
      status: 'CONTIGUOUS', venue: 'BINANCE_TESTNET', account: 'TESTNET-01',
      missing_intervals: [],
    },
    intent_funnel: { received: 4, validated: 3, risk_allowed: 2, blocked: 2, completed: 1 },
    oms: {
      status: 'REPLAYED',
      orders: {
        'cid-1': {
          client_order_id: 'cid-1', instrument_id: 'BTCUSDT', side: 'BUY',
          order_quantity: 1, cumulative_filled_qty: 1, canonical_state: 'FILLED',
        },
        'cid-2': {
          client_order_id: 'cid-2', instrument_id: 'ETHUSDT', side: 'SELL',
          order_quantity: 2, cumulative_filled_qty: 0, canonical_state: 'UNKNOWN',
          recovery_reason: 'ACK_UNKNOWN',
        },
        'cid-3': {
          client_order_id: 'cid-3', instrument_id: 'SOLUSDT', side: 'BUY',
          order_quantity: 5, cumulative_filled_qty: 2, canonical_state: 'CANCELLED',
          recovery_reason: 'LEDGER_FAILURE',
        },
      },
    },
    fills: {
      count: 2,
      items: [
        { fill_id: 'fill-1', client_order_id: 'cid-1', instrument: 'BTCUSDT', quantity: 1, price: 60_000 },
        { fill_id: 'fill-2', client_order_id: 'cid-3', instrument: 'SOLUSDT', quantity: 2, price: 150 },
      ],
    },
    positions: { status: 'RECONCILED', items: { BTCUSDT: 1, SOLUSDT: 2 } },
    risk: { status: 'ALLOWED_TESTNET', kill_switch_latched: false },
    reconciliation: { status: 'DIVERGED', requires_manual_review: true, residual: 0.001 },
    tca: {
      status: 'COMPLETE', report_id: 'tca-1', fill_ratio: 0.75,
      slippage_bps: 4.2, microstructure_scope: 'TESTNET_MICROSTRUCTURE_NOT_PRODUCTION',
    },
    mandate_expiry: { status: 'VALID_TESTNET_ONLY', expires_at_ms: 1_800_000_000_000 },
  });
}

function event(sequence: number, eventId: string, section: 'oms' | 'fills') {
  return {
    sequence, event_id: eventId, section, event_at_ms: 1_700_000_000_000 + sequence,
    payload: { status: section === 'oms' ? 'REPLAYED' : 'COMPLETE' },
    payload_hash_input: '{}', payload_hash: HASH_B, prev_hash: HASH_A, event_hash: HASH_A,
  };
}

function liveState() {
  return {
    phase: 'LIVE', snapshot: availableSnapshot(), reason: null,
    pages: 1, reloaded: false, transport: 'SSE',
    events: [event(13, 'fills.changed', 'fills'), event(12, 'oms.changed', 'oms')],
    eventHistory: 'SESSION_DELTA',
  };
}

describe('ExecutionOrders', () => {
  beforeEach(() => { sync.state = liveState(); });

  it('renders only MFT environment, funnel, OMS, fills, positions, TCA and controls', () => {
    render(<ExecutionOrders />);
    expect(screen.getByRole('heading', { name: '执行与订单' })).toBeTruthy();
    expect(screen.getByText('L2_DRY_RUN_ONLY')).toBeTruthy();
    expect(screen.getByText('BINANCE_TESTNET · TESTNET-01')).toBeTruthy();
    expect(screen.getByText(/received 4/)).toBeTruthy();
    expect(screen.getByText('cid-1')).toBeTruthy();
    expect(screen.getByText(/fill-1/)).toBeTruthy();
    expect(screen.getAllByText('BTCUSDT').length).toBeGreaterThan(0);
    expect(screen.getByText('75.00%')).toBeTruthy();
    expect(screen.getByText('4.20 bps')).toBeTruthy();
    expect(screen.getByText('DIVERGED')).toBeTruthy();
    expect(screen.getByText('VALID_TESTNET_ONLY')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /submit|cancel|reset|approve|下单|撤单|重置|批准/i })).toBeNull();
  });

  it('orders captured OMS/fill events by authority sequence', () => {
    render(<ExecutionOrders />);
    const rows = within(screen.getByTestId('authority-event-tape')).getAllByTestId('authority-event');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toMatch(/12.*oms\.changed/);
    expect(rows[1].textContent).toMatch(/13.*fills\.changed/);
    expect(screen.getByText(/current authority sequence 14/i)).toBeTruthy();
  });

  it('shows pending review and explicit recovery guidance for unknown ack, partial-cancel, and ledger failure', () => {
    render(<ExecutionOrders />);
    const queue = screen.getByTestId('pending-review-queue');
    expect(within(queue).getByText(/cid-2/)).toBeTruthy();
    expect(within(queue).getByText(/cid-3/)).toBeTruthy();
    expect(screen.getByText(/ACK_UNKNOWN.*QUERY_VENUE_BY_CLIENT_ORDER_ID/)).toBeTruthy();
    expect(screen.getByText(/PARTIAL_CANCEL_RACE.*RECONCILE_CUMULATIVE_FILL/)).toBeTruthy();
    expect(screen.getByText(/LEDGER_FAILURE.*NEVER_RESUBMIT_BLIND/)).toBeTruthy();
  });

  it('is diagnostic-only and hides retained execution values when stale', () => {
    sync.state = { ...liveState(), phase: 'STALE', reason: 'FRESHNESS_EXCEEDED' };
    render(<ExecutionOrders />);
    expect(screen.getByText('MFT_STALE')).toBeTruthy();
    expect(screen.getByText(/REFETCH_FULL_SNAPSHOT/)).toBeTruthy();
    expect(screen.queryByText('cid-1')).toBeNull();
    expect(screen.queryByText('fill-1')).toBeNull();
  });

  it('requires resnapshot on a cursor gap and never shows retained orders', () => {
    sync.state = { ...liveState(), phase: 'RESNAPSHOT_REQUIRED', reason: 'CURSOR_GAP' };
    render(<ExecutionOrders />);
    expect(screen.getByText('MFT_RESNAPSHOT_REQUIRED')).toBeTruthy();
    expect(screen.getByText(/CURSOR_GAP.*REFETCH_FULL_SNAPSHOT/)).toBeTruthy();
    expect(screen.queryByText('cid-1')).toBeNull();
  });

  it('does not manufacture event history from a full snapshot', () => {
    sync.state = { ...liveState(), events: [], eventHistory: 'RESET_BY_RESNAPSHOT' };
    render(<ExecutionOrders />);
    expect(screen.getByText(/NO_SESSION_DELTA_EVENTS/)).toBeTruthy();
    expect(screen.getByText(/snapshot rows are current state, not historical events/i)).toBeTruthy();
  });
});
