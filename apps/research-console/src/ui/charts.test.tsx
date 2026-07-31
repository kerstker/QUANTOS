import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { TickerTape } from './charts';


describe('TickerTape', () => {
  it('renders a single market status once without a distracting marquee', () => {
    render(<TickerTape items={[{ symbol: 'MEXC·BTCUSDT', price: 'offline' }]} />);

    expect(screen.getAllByText('MEXC·BTCUSDT')).toHaveLength(1);
    expect(screen.queryByRole('marquee')).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'market status' })).toBeInTheDocument();
  });

  it('keeps the continuous tape behavior for multiple market observations', () => {
    render(<TickerTape items={[
      { symbol: 'BTCUSDT', price: 60_000 },
      { symbol: 'ETHUSDT', price: 3_000 },
    ]} />);

    expect(screen.getByRole('marquee', { name: 'market tape' })).toBeInTheDocument();
    expect(screen.getAllByText('BTCUSDT')).toHaveLength(2);
    expect(screen.getAllByText('ETHUSDT')).toHaveLength(2);
  });
});
