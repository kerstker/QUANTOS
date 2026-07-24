import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import {
  EquityCurveChart, StatusBanner, AsOfStamp, EndpointFootnote, StateView,
  ConfirmActionButton, MandateGateChip,
} from './workbench';
import { DataTable, type Column } from './primitives';
import { ConfirmDialogHost } from './dialog';

/* Workbench kit component tests (docs/FRONTEND_WORKBENCH_REDESIGN.md §4).
   Test env runs in mock mode (VITE_MOCK_MODE != 'false'), so self-fetching
   components degrade to their honest offline posture — that IS the contract
   under test here (no network, no fabricated state). */

describe('EquityCurveChart', () => {
  const pts = (base: number) => Array.from({ length: 10 }, (_, i) => ({ t: 1000 + i * 60, equity: base + i * 0.01 }));

  it('renders one svg path per series with a legend', () => {
    const { container } = render(
      <EquityCurveChart series={[
        { label: 'RAW', points: pts(1.0) },
        { label: 'VT', points: pts(0.9), dashed: true },
      ]} />,
    );
    expect(container.querySelectorAll('svg path')).toHaveLength(2);
    expect(screen.getByText('RAW')).toBeInTheDocument();
    expect(screen.getByText('VT')).toBeInTheDocument();
  });

  it('shows an honest empty state when no series has two points', () => {
    render(<EquityCurveChart series={[{ label: 'RAW', points: [{ equity: 1 }] }]} />);
    expect(screen.getByText(/not enough curve points/)).toBeInTheDocument();
  });
});

describe('StatusBanner', () => {
  it('degrades to an UNKNOWN / fail-closed posture when the backend is offline (mock mode)', () => {
    render(<StatusBanner />);
    expect(screen.getByRole('status', { name: 'safety status' })).toBeInTheDocument();
    expect(screen.getByText('KILL_UNKNOWN')).toBeInTheDocument();
    expect(screen.getByText('LIVE_DISALLOWED')).toBeInTheDocument();
  });
});

describe('AsOfStamp', () => {
  it('marks recent data FRESH', () => {
    render(<AsOfStamp ts={1_700_000_000} ageHours={2} freshWithinHours={72} />);
    expect(screen.getByText('FRESH')).toBeInTheDocument();
  });
  it('marks old data STALE', () => {
    render(<AsOfStamp ts={1_700_000_000} ageHours={200} freshWithinHours={72} />);
    expect(screen.getByText('STALE')).toBeInTheDocument();
  });
  it('is honest when the timestamp is unknown', () => {
    render(<AsOfStamp ts={null} ageHours={null} />);
    expect(screen.getByText('AS-OF ?')).toBeInTheDocument();
  });
});

describe('EndpointFootnote', () => {
  it('renders the source endpoint path for audit traceability', () => {
    render(<EndpointFootnote path="/api/v1/console/mexc-market" note="read-only" />);
    expect(screen.getByText('/api/v1/console/mexc-market')).toBeInTheDocument();
    expect(screen.getByText(/read-only/)).toBeInTheDocument();
  });
});

describe('StateView', () => {
  it('renders children only in the ready state', () => {
    const { rerender } = render(<StateView state="loading"><span>DATA</span></StateView>);
    expect(screen.queryByText('DATA')).not.toBeInTheDocument();
    rerender(<StateView state="error" error="boom"><span>DATA</span></StateView>);
    expect(screen.getByText(/boom/)).toBeInTheDocument();
    rerender(<StateView state="empty" empty="nothing here"><span>DATA</span></StateView>);
    expect(screen.getByText('nothing here')).toBeInTheDocument();
    rerender(<StateView state="ready"><span>DATA</span></StateView>);
    expect(screen.getByText('DATA')).toBeInTheDocument();
  });
});

describe('ConfirmActionButton', () => {
  it('fires onConfirm only after the dialog is confirmed', async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialogHost>
        <ConfirmActionButton label="Run" title="Run the thing?" message="It mutates." onConfirm={onConfirm} />
      </ConfirmDialogHost>,
    );
    fireEvent.click(screen.getByText('Run'));
    expect(onConfirm).not.toHaveBeenCalled();
    // cancel path: no fire
    fireEvent.click(screen.getByText(/取消/));
    await waitFor(() => expect(screen.queryByText('Run the thing?')).not.toBeInTheDocument());
    expect(onConfirm).not.toHaveBeenCalled();
    // confirm path: fires exactly once
    fireEvent.click(screen.getByText('Run'));
    fireEvent.click(screen.getByText(/确认/));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  });
});

describe('MandateGateChip', () => {
  it('renders the mandate state as a read-only chip', () => {
    const { rerender } = render(<MandateGateChip state="SIGNED" />);
    expect(screen.getByText('MANDATE_SIGNED')).toBeInTheDocument();
    rerender(<MandateGateChip state="SIGNED" expiresAt={100} now={200} />);
    expect(screen.getByText('MANDATE_EXPIRED')).toBeInTheDocument();
    rerender(<MandateGateChip state="UNKNOWN" />);
    expect(screen.getByText('MANDATE_UNKNOWN')).toBeInTheDocument();
  });
});

describe('DataTable sticky header + compact density', () => {
  const cols: Column<{ a: number }>[] = [
    { key: 'a', header: 'A', render: r => r.a },
  ];
  const rows = [{ a: 1 }, { a: 2 }];

  it('applies sticky thead + scroll window when stickyHeader is set', () => {
    const { container } = render(
      <DataTable rows={rows} columns={cols} stickyHeader maxHeight={240} />,
    );
    const thead = container.querySelector('thead');
    expect(thead?.className).toContain('sticky');
    expect(thead?.className).toContain('top-0');
    const scroller = container.querySelector('div.overflow-auto');
    expect(scroller).not.toBeNull();
    expect((scroller as HTMLElement).style.maxHeight).toBe('240px');
  });

  it('uses compact padding and the density-compact class in compact density', () => {
    const { container } = render(
      <DataTable rows={rows} columns={cols} density="compact" />,
    );
    expect(container.querySelector('.density-compact')).not.toBeNull();
    const td = container.querySelector('td');
    expect(td?.className).toContain('px-2');
    expect(td?.className).toContain('py-1');
  });

  it('keeps default rendering untouched when the new props are absent', () => {
    const { container } = render(<DataTable rows={rows} columns={cols} />);
    expect(container.querySelector('thead.sticky')).toBeNull();
    expect(container.querySelector('.density-compact')).toBeNull();
  });
});
