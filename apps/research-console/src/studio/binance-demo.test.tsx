import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {DemoReadinessView} from './BinanceDemo';
import {parseDemoPayload} from '@/lib/api/binance-demo-api';
import type {BinanceDemoPayload} from '@/lib/types/binance-demo';

afterEach(cleanup);
function payload(): BinanceDemoPayload {
  return {
    source: 'BACKEND_COMPUTED_SNAPSHOT', read_only: true, api_version: '0.1.0',
    data_provenance: 'DEMO_CONNECTIVITY_DIAGNOSTIC', snapshot_id: 'a'.repeat(64),
    snapshot_ids: ['a'.repeat(64)], snapshot_status: 'REAL',
    data_quality: {status: 'REAL', provenance: 'DEMO_CONNECTIVITY_DIAGNOSTIC', reason: 'OBSERVATION_AVAILABLE'},
    environment: 'BINANCE_USDM_DEMO', rest_base: 'https://demo-fapi.binance.com',
    observed_at_ms: 1800000000000, as_of_ms: 1800000001000, age_ms: 1000, max_age_ms: 120000,
    public: {status: 'READY', reason: 'PUBLIC_API_REACHABLE', server_time_ms: 1800000000000,
      round_trip_ms: 120, clock_offset_ms: -30, symbols: ['USDT', 'USDC'].map(quote => ({
        symbol: `BTC${quote}`, quote_asset: quote, margin_asset: quote, contract_type: 'PERPETUAL',
        status: 'TRADING', tick_size: '0.10', step_size: '0.001', min_qty: '0.001', min_notional: '100',
      }))},
    account: {status: 'NOT_CHECKED', reason: 'ACCOUNT_NOT_REQUESTED', observed_at_ms: null, assets: [], positions: []},
    submission_enabled: false, authoritative: false, execution_reason: 'LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE',
    order_stream_status: 'NOT_CHECKED', ledger_reconciliation_status: 'NOT_CHECKED',
  };
}

it('separates public connectivity from account and order readiness without fake account totals', () => {
  const refresh = vi.fn();
  render(<DemoReadinessView data={payload()} loading={false} error={null} refresh={refresh}/>);
  expect(screen.getByText('公共接口可达')).toBeInTheDocument();
  expect(screen.getByText('账户尚未查询')).toBeInTheDocument();
  expect(screen.getByText('执行条件未满足')).toBeInTheDocument();
  expect(screen.getByText('BTCUSDC')).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: /下单|开启交易/})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: '刷新快照'}));
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/不代表测试网订单已接通/)).toBeInTheDocument();
});

it('does not show old public success as current readiness after expiry or failed refresh', () => {
  const data = payload();
  data.snapshot_status = 'STALE';
  data.age_ms = 180000;
  const view = render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('快照已过期')).toBeInTheDocument();
  expect(screen.queryByText('公共接口可达')).not.toBeInTheDocument();
  view.rerender(<DemoReadinessView data={payload()} loading={false} error="读取超时" refresh={vi.fn()}/>);
  expect(screen.queryByText('公共接口可达')).not.toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('读取超时');
});

it('renders missing observations as unknown and balances separately by asset without rounding', () => {
  const data = payload();
  data.account = {status: 'READY', reason: 'ACCOUNT_READ_SUCCEEDED', observed_at_ms: data.observed_at_ms,
    assets: [{asset: 'USDT', wallet_balance: '123.12345678', available_balance: '100', unrealized_profit: '-0.25'},
      {asset: 'USDC', wallet_balance: '9.01', available_balance: '9.01', unrealized_profit: '0'}], positions: []};
  const view = render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('123.12345678')).toBeInTheDocument();
  expect(screen.getByText('账户查询成功')).toBeInTheDocument();
  expect(screen.getByText(/未返回持仓条目/)).toBeInTheDocument();
  view.rerender(<DemoReadinessView data={null} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('尚无接入检查快照')).toBeInTheDocument();
  expect(screen.queryByText('123.12345678')).not.toBeInTheDocument();
});

it('rejects broken safety markers, timestamps and nested account contracts on the wire', () => {
  expect(parseDemoPayload(payload()).public?.symbols).toHaveLength(2);
  for (const patch of [{submission_enabled: true}, {rest_base: 'https://fapi.binance.com'},
    {age_ms: -1}, {public: {status: 'READY'}}, {account: {status: 'READY', assets: []}}]) {
    expect(() => parseDemoPayload({...payload(), ...patch})).toThrow('模拟盘数据契约不匹配');
  }
});
