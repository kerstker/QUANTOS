import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BtcFactoryResults, BtcForwardResults } from '../BtcFactoryReport';
import type { BtcFactoryReport } from '@/lib/types/research';

const report: BtcFactoryReport = {
  fee_bps: '3.6', threshold_bps: 19.2, selection_updates: [],
  cases: [
    { policy: 'FACTORY', slip_bps: 5, stop: true, trial_id: 'a', status: 'PASSED', metrics: {
      net_pnl: -11, annual_pnl: { '2025': -10, '2026': -1 }, fills: 8, max_drawdown: .01,
      fees: '4', funding: '-2', reconciliation_residual: '0', halted_at: null, order_reasons: { REJECTED_CAPACITY: 2 },
    }, equity: [{ time: 1735689600000, equity: '1500', quantity: '0', mark: '100' }, { time: 1735776000000, equity: '1489', quantity: '0', mark: '100' }] },
    { policy: 'FACTORY', slip_bps: 10, stop: true, trial_id: 'b', status: 'FAILED', error: 'capacity-input-missing' },
  ],
};

describe('Factory economic and observation results', () => {
  it('changes cost scenario and retains failed cases without zero-filling', () => {
    render(<BtcFactoryResults report={report} />);
    expect(screen.getByText('-11.00')).toBeInTheDocument();
    expect(screen.getByText('REJECTED_CAPACITY: 2')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('单边滑点'), { target: { value: '10' } });
    expect(screen.getByText('计算失败')).toBeInTheDocument();
    expect(screen.getByText('capacity-input-missing')).toBeInTheDocument();
    expect(screen.queryByText('-11.00')).not.toBeInTheDocument();
  });
  it('never presents missing forward labels as zero error', () => {
    render(<BtcForwardResults report={{ status: 'FROZEN', reason: 'Waiting', observed_at_ms: 1735689600000,
      attempt: 1, authoritative: false, account_pnl_computed: false, models: [{ model: 'PRICE', candidate_id: 'a',
        frozen_at_ms: 1735689600000, decisions: 1, pending: 1, completed: 0, rmse_bps: null, account_pnl_computed: false }] }} />);
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('0.00')).not.toBeInTheDocument();
  });
});
