import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { LiveTradingPayload } from '@/lib/types/research';
import LiveTrading from '@/pages/LiveTrading';

const state = vi.hoisted(() => ({
  payload: {
    source: 'BACKEND_COMPUTED_SNAPSHOT',
    read_only: true,
    api_version: '0.1.0',
    data_provenance: 'REAL_LIVE_TRADING_SNAPSHOT',
    snapshot_present: true,
    verdict: 'L2_FORWARD_EVIDENCE_BLOCKED',
    forward_track: {
      present: true,
      evidence_state: 'DATA_BLOCKED',
      attested_realizations: 0,
      attested_pending: 0,
      legacy_unattested_decisions: 2,
      evidence_gate_weeks: 8,
      evidence_gate_eligible: false,
      data_admission: { status: 'BLOCKED' },
      actions_allowed: false,
    },
    execution_stack: { present: false },
    honesty_note: 'Read-only L2 diagnostic.',
  } as LiveTradingPayload,
}));

vi.mock('@/ui/useConsoleSnapshot', () => ({
  useConsoleSnapshot: () => ({
    payload: state.payload,
    apiState: 'connected',
    error: null,
    usingBackend: true,
  }),
}));

describe('L2 forward evidence workflow boundary', () => {
  it('renders blocked attested evidence and never offers an action', () => {
    render(<LiveTrading />);

    expect(screen.getByText(/认证前向.*0.*8/)).toBeInTheDocument();
    expect(screen.getByText(/历史未认证.*2/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /启动|执行|交易/ })).not.toBeInTheDocument();
  });
});
