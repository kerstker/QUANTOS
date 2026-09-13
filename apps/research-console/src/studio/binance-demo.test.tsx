import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {DemoReadinessView} from './BinanceDemo';
import {parseDemoPayload} from '@/lib/api/binance-demo-api';
import type {BinanceDemoPayload, DemoReplayReport, DemoOrderRecoveryReport} from '@/lib/types/binance-demo';

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
    execution: {status: 'NO_JOURNAL', report: null},
    order_recovery: {status: 'NO_SNAPSHOT', report: null},
    private_checks: {status: 'NO_SNAPSHOT', report: null},
    user_stream: {status: 'NO_JOURNAL', report: null},
    cash_window: {status: 'NO_SNAPSHOT', report: null},
    execution_origin: {status: 'NO_SNAPSHOT', report: null},
    canonical_cash: {status: 'INPUTS_MISSING', report: null},
    cash_chain: {status:'NO_SNAPSHOT',report:null},
    oms_status: {status: 'NO_SNAPSHOT', report: null},
    cancellation_status: {status: 'NO_SNAPSHOT', report: null},
    resident_status: {status: 'NO_SNAPSHOT', report: null, last_result: null},
    executor_observation: {status:'NO_SNAPSHOT',report:null},
    issuer_status: {status:'NO_SNAPSHOT',report:null},
    backup_status: {status:'NO_SNAPSHOT',report:null},
    market_inputs:{BTCUSDC:{status:'NO_SNAPSHOT',report:null},BTCUSDT:{status:'NO_SNAPSHOT',report:null}},
    statement: {status: 'NO_SNAPSHOT', report: null}, account_baseline: {status: 'NO_SNAPSHOT', report: null},
  };
}

it('shows connection tests separately from missing actual fills, and rejects false submission evidence', () => {
  const data = payload();
  data.private_checks = {status: 'AVAILABLE', report: {
    schema_version: 'binance-demo-private-check.v1', environment: 'BINANCE_USDM_DEMO',
    observed_at_ms: 1800000000000, completed_at_ms: 1800000000001,
    connectivity_status: 'CHECKS_PASSED', steps: ['credentials', 'public', 'account', 'BTCUSDT_orders',
      'BTCUSDT_trades', 'BTCUSDC_orders', 'BTCUSDC_trades', 'listen_key', 'websocket'].map(name => ({
      name, status: 'PASSED', reason: 'CHECK_SUCCEEDED', rows: null,
    })), websocket_connections: 2, websocket_pongs: 2, websocket_events: 0,
    event_delivery_status: 'UNKNOWN', submission_status: 'BLOCKED',
    submission_reason: 'LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE', ledger_status: 'UNKNOWN',
    authoritative: false, source_sha256: 'a'.repeat(64),
  }};
  expect(parseDemoPayload(data).private_checks.status).toBe('AVAILABLE');
  const view = render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('账户与私有连接检查通过')).toBeInTheDocument();
  expect(screen.getByText(/未观察到私有事件/)).toBeInTheDocument();
  data.private_checks.status = 'STALE';
  view.rerender(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('历史检查结果')).toBeInTheDocument();
  expect(screen.queryByText('账户与私有连接检查通过')).not.toBeInTheDocument();
  const bad = {...data, private_checks: {status: 'AVAILABLE', report: {...data.private_checks.report, submission_status: 'PASSED'}}};
  expect(() => parseDemoPayload(bad)).toThrow();
});

it('separates public connectivity from account and order readiness without fake account totals', () => {
  const refresh = vi.fn();
  render(<DemoReadinessView data={payload()} loading={false} error={null} refresh={refresh}/>);
  expect(screen.getByText('公共接口可达')).toBeInTheDocument();
  expect(screen.getByText('账户尚未查询')).toBeInTheDocument();
  expect(screen.getByText('执行条件未满足')).toBeInTheDocument();
  expect(screen.getByRole('cell',{name:/BTCUSDC/})).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: /下单|开启交易/})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: '刷新快照'}));
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/不代表测试网订单已接通/)).toBeInTheDocument();
});

it('shows missing and corrupt execution journals without declaring the ledger reconciled', () => {
  const data = payload();
  const view = render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('尚无成交回执日志')).toBeInTheDocument();
  data.execution = {status: 'CORRUPT_OR_UNREADABLE', report: null};
  view.rerender(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('回执日志无法验证')).toBeInTheDocument();
  expect(screen.getByText('账本对账待验证')).toBeInTheDocument();
});

it('rejects unverified execution payloads that claim success without a report', () => {
  for (const execution of [{status: 'AVAILABLE', report: null}, {status: 'NO_JOURNAL', report: {}},
    {status: 'AVAILABLE', report: {source_verified: true}}]) {
    expect(() => parseDemoPayload({...payload(), execution})).toThrow('模拟盘数据契约不匹配');
  }
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

function replay(): DemoReplayReport {
  return {
    opening: null, schema: 'DEMO_EXECUTION_REPLAY_V1', environment: 'BINANCE_USDM_DEMO', scope: 'TECHNICAL_VALIDATION_ONLY',
    source_verified: false, authoritative: false, submission_enabled: false, provenance: 'SYNTHETIC_TEST_FIXTURE',
    manifest_hash: 'b'.repeat(64), journal_tail: 'c'.repeat(64), replay_source_sha256: 'd'.repeat(64), query_binding_hash: 'a'.repeat(64), declared_source_commit: 'a'.repeat(40),
    journal_records: 2, rejected_batches: 0, observed_at_ms: 131, symbol: 'BTCUSDC', quote_asset: 'USDC',
    projection_status: 'REPLAYED_TECHNICAL_ONLY', projection_reason: null, filled_quantity: '0.4', position_quantity: '0.4',
    expected_end_position: '1', position_comparison: 'WAITING_TERMINAL_ORDERS', venue_cash_reconciliation: 'UNKNOWN', actual_paid_fees: null,
    reconciliation: {initial_equity: '1000', ending_equity: '999.99208', price_pnl: '0', fee: '0.00792', spread: '0', impact: '0',
      funding: '0', liquidation_fee: '0', insurance_adjustment: '0', adl_adjustment: '0', attributed_change: '-0.00792', residual: '0'},
    ledger_state_hash: 'e'.repeat(64), trades_count: 1, duplicate_messages: 0, reported_commission_by_asset: {USDC: '0.00792'},
    orders: [{order_id: 'fixed-buy', side: 'BUY', quantity: '1', state: 'PARTIALLY_FILLED', filled_quantity: '0.4', last_event_at_ms: 121}],
    recent_trades: [{order_id: 'fixed-buy', venue_order_id: '9007199254740993', trade_id: '9007199254740995', quantity: '0.4', price: '99',
      event_ms: 121, transaction_ms: 121, venue_trade_ms: 121, received_at_ms: 131, liquidity_role: 'MAKER',
      reported_commission: '0.00792', commission_asset: 'USDC', response_sha256: 'f'.repeat(64)}],
    limitations: ['INITIAL_FLAT_ACCOUNT_DECLARED_ONLY', 'IMPORTED_SOURCE_UNVERIFIED'],
  };
}

function clockedReplay():DemoReplayReport {
  const r=replay();
  r.clock={schema:'BINANCE_USDM_CLOCK_BRACKET_V1',policy:'ENDPOINT_ENVELOPE_WITH_DECLARED_500MS_INTERIOR_DRIFT_V1',
    input_sha256:'a'.repeat(64),start_ms:100,end_ms:300,available_at_ms:320,offset_lower_ms:489,offset_upper_ms:1511,
    local_domain:'CAPTURE_PROCESS_EPOCH_MS',venue_domain:'BINANCE_USDM_SERVER_EPOCH_MS',quantization_ms:1,
    declared_interior_drift_ms:500,source_verified:false,interior_drift_verified:false,exact_latency_ms:null,
    causality_status:'COMPATIBILITY_ONLY_NOT_CAUSAL_CERTIFICATION'};
  for(const t of r.recent_trades){t.event_ms+=1000;t.transaction_ms+=1000;t.venue_trade_ms+=1000;}
  r.orders[0].last_event_at_ms=1121;
  return r;
}

it('shows separate raw clocks and uncertainty without inventing exact latency',()=>{
  const data={...payload(),execution:{status:'AVAILABLE' as const,report:clockedReplay()}};
  expect(parseDemoPayload(data).execution.status).toBe('AVAILABLE');
  render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('时间口径与不确定区间')).toBeInTheDocument();
  expect(screen.getByText(/489 至 1511 ms/)).toBeInTheDocument();
  expect(screen.getByText(/不提供精确延迟/)).toBeInTheDocument();
  expect(screen.getByText('9007199254740993')).toBeInTheDocument();
});

it('rejects future clock probes, claimed authority and incompatible raw events',()=>{
  for(const change of ['future','authority','latency','interval','event','missing'] as const){
    const data={...payload(),execution:{status:'AVAILABLE' as const,report:clockedReplay()}};
    const r=data.execution.report;
    if(change==='future')r.clock!.available_at_ms=data.as_of_ms+1;
    else if(change==='event')r.recent_trades[0].event_ms+=10000;
    else if(change==='missing')delete r.clock;
    else {
      const c=r.clock as unknown as Record<string,unknown>;
      if(change==='authority')c.interior_drift_verified=true;
      else if(change==='latency')c.exact_latency_ms=20;
      else c.offset_upper_ms=100000;
    }
    expect(()=>parseDemoPayload(data)).toThrow();
  }
});

it('preserves exact receipt IDs and amounts and labels partial imported fills as technical evidence', () => {
  const data = {...payload(), execution: {status: 'AVAILABLE' as const, report: replay()}};
  parseDemoPayload(data);
  const view = render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('合成测试数据')).toBeInTheDocument();
  expect(screen.getByText('等待订单终态')).toBeInTheDocument();
  expect(screen.getByText('部分成交')).toBeInTheDocument();
  expect(screen.getByText('9007199254740993')).toBeInTheDocument();
  expect(screen.getByText('9007199254740995')).toBeInTheDocument();
  expect(screen.getByText('0.00792 USDC')).toBeInTheDocument();
  expect(screen.queryByText('与声明持仓一致')).not.toBeInTheDocument();
  view.rerender(<DemoReadinessView data={data} loading={false} error="接口超时" refresh={vi.fn()}/>);
  expect(screen.getByText(/上次读取的历史回放/)).toBeInTheDocument();
});

it('retains raw fills but hides ledger quantities when commission is missing', () => {
  const report = replay();
  Object.assign(report, {projection_status: 'UNRESOLVED', projection_reason: 'COMMISSION_NOT_SUPPORTED', reconciliation: null,
    position_quantity: null, ledger_state_hash: null, position_comparison: 'UNKNOWN'});
  report.recent_trades[0].reported_commission = null;
  report.recent_trades[0].commission_asset = null;
  const data = {...payload(), execution: {status: 'AVAILABLE' as const, report}};
  parseDemoPayload(data);
  render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('回放未解决')).toBeInTheDocument();
  expect(screen.getByText('回放持仓 未知 BTC')).toBeInTheDocument();
  expect(screen.getByText('9007199254740995')).toBeInTheDocument();
  expect(screen.queryByText('0.00792 USDC')).not.toBeInTheDocument();
});

it('rejects rounded trade IDs, cash authority and unsupported projection claims', () => {
  for (const patch of [{source_verified: true}, {venue_cash_reconciliation: 'MATCH'}, {reconciliation: {}},
    {position_quantity: 0.4}, {observed_at_ms: 1900000000000}, {journal_tail: 'unbound'},
    {recent_trades: [{...replay().recent_trades[0], trade_id: 9007199254740996}]}]) {
    expect(() => parseDemoPayload({...payload(), execution: {status: 'AVAILABLE', report: {...replay(), ...patch}}})).toThrow();
  }
});

it('shows missing query observations separately from receipt recovery', () => {
  render(<DemoReadinessView data={payload()} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('尚无订单查询快照')).toBeInTheDocument();
});

function recovery(): DemoOrderRecoveryReport {
  return {schema: 'DEMO_ORDER_RECOVERY_V1', scope: 'TECHNICAL_VALIDATION_ONLY', source_verified: false, new_command_allowed: false,
    status: 'ACTION_REQUIRED', capture_hash: 'a'.repeat(64), manifest_hash: 'b'.repeat(64), journal_tail: 'c'.repeat(64),
    observed_at_ms: 200, provenance: 'SYNTHETIC_TEST_FIXTURE', orders: [{order_id: 'fixed-buy', venue_order_id: '9007199254740993',
      receipt_state: 'PARTIALLY_FILLED', query_state: 'FILLED', receipt_quantity: '0.4', query_quantity: '1', quantity_gap: '0.6',
      status: 'BACKFILL_REQUIRED', reason: 'ORDER_QUERY_SUCCEEDED', received_at_ms: 200}]};
}

it('displays missing fills without changing receipt accounting or enabling a retry', () => {
  const data: BinanceDemoPayload = {...payload(), execution: {status: 'AVAILABLE', report: replay()},
    order_recovery: {status: 'AVAILABLE', report: recovery()}};
  parseDemoPayload(data);
  render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('缺少逐笔成交回执')).toBeInTheDocument();
  expect(screen.getByText('0.6')).toBeInTheDocument();
  expect(screen.getByText('0.00792 USDC')).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: /重试订单|重新下单/})).not.toBeInTheDocument();
});

it('hides stale comparisons and never turns an unknown query into a zero fill', () => {
  const report = recovery();
  Object.assign(report.orders[0], {status: 'QUERY_UNRESOLVED', reason: 'ORDER_NOT_FOUND_UNCONFIRMED', query_state: null, query_quantity: null, quantity_gap: null});
  const data: BinanceDemoPayload = {...payload(), order_recovery: {status: 'AVAILABLE', report}};
  parseDemoPayload(data);
  const view = render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText(/不能据此确认订单不存在/)).toBeInTheDocument();
  data.order_recovery = {status: 'AVAILABLE', report: {...report, status: 'STALE', orders: []}};
  parseDemoPayload(data);
  view.rerender(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('查询快照已过期')).toBeInTheDocument();
  expect(screen.queryByRole('region', {name: '订单恢复比较表'})).not.toBeInTheDocument();
});

it('rejects recovery authority, rounded quantities and false matching claims on the wire', () => {
  for (const patch of [{new_command_allowed: true}, {source_verified: true}, {status: 'MATCHED_OBSERVATIONS'},
    {orders: [{...recovery().orders[0], query_quantity: 1}]}, {status: 'STALE'}]) {
    expect(() => parseDemoPayload({...payload(), order_recovery: {status: 'AVAILABLE', report: {...recovery(), ...patch}}})).toThrow();
  }
});

function baseline(): import('@/lib/types/binance-demo').DemoAccountBaselineReport {
  return {
    schema: 'DEMO_ACCOUNT_BASELINE_V1', environment: 'BINANCE_USDM_DEMO', capture_hash: 'b'.repeat(64), source_sha256: 'c'.repeat(64),
    observed_at_ms: 1800000000000, completed_at_ms: 1800000000100, inventory_status: 'STABLE_OBSERVATIONS', capture_reason: 'CAPTURE_COMPLETE',
    issues: ['ENTRY_BASIS_UNVERIFIED', 'REST_OBSERVATIONS_NOT_ATOMIC'], nonzero_positions: 1, flat_account: false,
    account_config: {canTrade: true, dualSidePosition: false, multiAssetsMargin: false},
    assets: [{asset: 'USDT', observed_wallet: '1000', observed_cross_wallet: '1000', observed_unrealized: '20',
      observed_margin_balance: '1020', observed_available: '990', account_identity_residual: '0'}],
    positions: [{symbol: 'BTCUSDT', position_side: 'BOTH', margin_asset: 'USDT', quantity: '2', observed_entry_price: '100.001',
      observed_mark_price: '110', observed_unrealized: '20', estimated_entry_notional: '200.002', basis_pnl_residual: '-0.002',
      isolated_wallet: '0', margin_mode: 'CROSSED', leverage: 20, ownership: 'PREEXISTING_ACCOUNT_POSITION'}], open_orders: [],
    comparisons: ['wallet', 'positions', 'account_config', 'symbol_config'].map(kind => ({kind, status: 'MATCHED_OBSERVATIONS'})),
    atomic_snapshot_verified: false, new_strategy_pnl: null, ledger_reconciliation: 'UNKNOWN', submission_enabled: false, authoritative: false,
    read_intervals: ['config_before', 'account_before', 'symbols_before', 'positions', 'orders', 'symbols_after', 'account_after', 'config_after']
      .map((kind, i) => ({kind, started_at_ms: 1800000000000 + i * 10, received_at_ms: 1800000000001 + i * 10, response_sha256: 'd'.repeat(64)})),
  };
}

it('shows inherited basis and its residual without treating opening unrealized PnL as new returns', () => {
  const data = payload(); data.account_baseline = {status: 'AVAILABLE', report: baseline()};
  expect(parseDemoPayload(data).account_baseline.report?.nonzero_positions).toBe(1);
  const view = render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('账户起点')).toBeInTheDocument();
  expect(screen.getByText('采集前后数值一致')).toBeInTheDocument();
  expect(screen.getByText('100.001')).toBeInTheDocument();
  expect(screen.getByText('-0.002')).toBeInTheDocument();
  expect(screen.getByText(/不计入本次策略收益/)).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: /下单|确认持仓|接管/})).not.toBeInTheDocument();
  data.account_baseline.report!.inventory_status = 'STALE';
  view.rerender(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByText('历史账户起点')).toBeInTheDocument();
  expect(screen.queryByText('采集前后数值一致')).not.toBeInTheDocument();
});

it('rejects incomplete or falsely authoritative account baselines, and retains changed observations', () => {
  const data = payload(); data.account_baseline = {status: 'AVAILABLE', report: baseline()};
  const report = baseline();
  for (const patch of [{new_strategy_pnl: '20'}, {atomic_snapshot_verified: true}, {ledger_reconciliation: 'PASSED'},
    {completed_at_ms: data.as_of_ms + 1}, {read_intervals: []}, {nonzero_positions: 0}, {flat_account: true},
    {assets: [{...report.assets[0], observed_wallet: 1000}]}, {positions: report.positions.concat(report.positions)},
    {inventory_status: 'STALE'}, {comparisons: report.comparisons.slice(0, 1)}]) {
    expect(() => parseDemoPayload({...data, statement: {status: 'NO_SNAPSHOT', report: null}, account_baseline: {status: 'AVAILABLE', report: {...report, ...patch}}})).toThrow();
  }
  report.inventory_status = 'CHANGED_DURING_CAPTURE'; report.flat_account = null;
  report.comparisons[0].status = 'DIFFERENT'; report.issues.push('ACCOUNT_CHANGED_DURING_CAPTURE');
  data.account_baseline.report = report;
  expect(parseDemoPayload(data).account_baseline.report?.inventory_status).toBe('CHANGED_DURING_CAPTURE');
});

it('shows inherited opening inputs and missing exact basis without claiming a flat account or profit', () => {
  const r = replay();
  r.opening = {schema: 'DEMO_OPENING_ASSESSMENT_V1', scope: 'SINGLE_QUOTE_ACCOUNT_BOOK_TECHNICAL_PROJECTION',
    status: 'INPUTS_INCOMPLETE', capture_hash: 'e'.repeat(64), account_ref_sha256: 'f'.repeat(64),
    opening_at_ms: 90, quote_asset: 'USDC', initial_collateral: '1000', initial_position: '2',
    positions_count: 1, other_quote_positions_count: 0, missing_basis_symbols: ['BTCUSDC'], missing_contract_symbols: [],
    issues: ['EXACT_ENTRY_BASIS_MISSING'], basis_policy: 'CALLER_DECLARED_EXACT_ENTRY_NOTIONAL', source_verified: false,
    atomic_snapshot_verified: false, submission_enabled: false, venue_cash_reconciliation: 'UNKNOWN',
    positions: [{symbol: 'BTCUSDC', position_side: 'BOTH', quantity: '2', margin_mode: 'CROSSED', entry_notional: null, basis_source_sha256: null, contract_available: true}],
  };
  r.projection_status = 'UNRESOLVED'; r.projection_reason = 'OPENING_INPUTS_INCOMPLETE'; r.reconciliation = null;
  r.position_quantity = null; r.ledger_state_hash = null; r.position_comparison = 'UNKNOWN';
  const data = {...payload(), execution: {status: 'AVAILABLE' as const, report: r}};
  expect(parseDemoPayload(data).execution.report?.opening?.initial_position).toBe('2');
  render(<DemoReadinessView data={data} loading={false} error={null} refresh={vi.fn()}/>);
  expect(screen.getByRole('heading', {name: '回放起点与缺项'})).toBeInTheDocument();
  expect(screen.getByText(/精确成本尚缺 1 个合约/)).toBeInTheDocument();
  expect(screen.queryByText(/初始空仓、抵押金/)).not.toBeInTheDocument();
  for (const patch of [{source_verified: true}, {quote_asset: 'USDT'}, {opening_at_ms: data.as_of_ms + 1},
    {positions_count: 0}, {missing_basis_symbols: []}, {status: 'DECLARED_INPUTS_READY'},
    {positions: [{...r.opening.positions[0], entry_notional: '200'}]}]) {
    expect(() => parseDemoPayload({...data, execution: {status: 'AVAILABLE', report: {...r, opening: {...r.opening, ...patch}}}})).toThrow();
  }
});
