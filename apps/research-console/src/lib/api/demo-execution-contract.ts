const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const decimal = (v: unknown) => typeof v === 'string' && /^-?\d{1,40}(\.\d{1,40})?$/.test(v);
const nullableDecimal = (v: unknown) => v === null || decimal(v);
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const code = (v: unknown) => typeof v === 'string' && /^[A-Z0-9_]{1,100}$/.test(v);
const orderId = (v: unknown) => typeof v === 'string' && /^[A-Za-z0-9_.-]{1,64}$/.test(v);
const venueId = (v: unknown) => typeof v === 'string' && /^\d{1,19}$/.test(v);
const states = ['UNKNOWN', 'ACK', 'PARTIALLY_FILLED', 'FILLED', 'CANCELLED', 'EXPIRED'];
const comparisons = ['UNKNOWN', 'WAITING_TERMINAL_ORDERS', 'DECLARED_POSITION_MATCH', 'DECLARED_POSITION_MISMATCH'];
const reconciliationKeys = ['initial_equity', 'ending_equity', 'price_pnl', 'fee', 'spread', 'impact', 'funding',
  'liquidation_fee', 'insurance_adjustment', 'adl_adjustment', 'attributed_change', 'residual'];

export function executionSnapshotValid(v: unknown, asOf: number): boolean {
  if (!object(v)) return false;
  if (v.status !== 'AVAILABLE') return ['NO_JOURNAL', 'CORRUPT_OR_UNREADABLE', 'DATA_BLOCKED'].includes(String(v.status)) && v.report === null;
  const r = v.report;
  if (!object(r) || r.schema !== 'DEMO_EXECUTION_REPLAY_V1' || r.environment !== 'BINANCE_USDM_DEMO' ||
      r.scope !== 'TECHNICAL_VALIDATION_ONLY' || r.source_verified !== false || r.authoritative !== false || r.submission_enabled !== false ||
      !['SYNTHETIC_TEST_FIXTURE', 'IMPORTED_UNVERIFIED'].includes(String(r.provenance)) ||
      !['USDT', 'USDC'].includes(String(r.quote_asset)) || r.symbol !== `BTC${r.quote_asset}` ||
      ![r.manifest_hash, r.journal_tail, r.replay_source_sha256].every(hash) ||
      typeof r.declared_source_commit !== 'string' || !/^[a-f0-9]{40}$/.test(r.declared_source_commit) ||
      ![r.journal_records, r.rejected_batches, r.trades_count, r.duplicate_messages].every(count) ||
      !(r.observed_at_ms === null || count(r.observed_at_ms) && r.observed_at_ms <= asOf) ||
      !decimal(r.filled_quantity) || !nullableDecimal(r.position_quantity) || !nullableDecimal(r.expected_end_position) ||
      !comparisons.includes(String(r.position_comparison)) || r.venue_cash_reconciliation !== 'UNKNOWN' || r.actual_paid_fees !== null ||
      !object(r.reported_commission_by_asset) || !Object.entries(r.reported_commission_by_asset).every(([k, n]) => /^[A-Z0-9]{1,20}$/.test(k) && decimal(n)) ||
      !Array.isArray(r.limitations) || !r.limitations.every(code) ||
      !Array.isArray(r.orders) || r.orders.length > 200 || !r.orders.every(o => object(o) && orderId(o.order_id) &&
        ['BUY', 'SELL'].includes(String(o.side)) && states.includes(String(o.state)) && decimal(o.quantity) && decimal(o.filled_quantity)) ||
      !Array.isArray(r.recent_trades) || r.recent_trades.length > 50 || !r.recent_trades.every(t => object(t) &&
        orderId(t.order_id) && venueId(t.venue_order_id) && venueId(t.trade_id) && decimal(t.quantity) && decimal(t.price) &&
        ['MAKER', 'TAKER'].includes(String(t.liquidity_role)) && nullableDecimal(t.reported_commission) &&
        (t.commission_asset === null || typeof t.commission_asset === 'string' && /^[A-Z0-9]{1,20}$/.test(t.commission_asset)) &&
        hash(t.response_sha256) && count(t.venue_trade_ms) && count(t.transaction_ms) && count(t.event_ms) && count(t.received_at_ms) &&
        t.venue_trade_ms <= t.transaction_ms && t.transaction_ms <= t.event_ms && t.event_ms <= t.received_at_ms && t.received_at_ms <= asOf)) return false;
  if (r.projection_status === 'UNRESOLVED') return code(r.projection_reason) && r.reconciliation === null &&
    r.ledger_state_hash === null && r.position_quantity === null && r.position_comparison === 'UNKNOWN';
  return r.projection_status === 'REPLAYED_TECHNICAL_ONLY' && r.projection_reason === null &&
    hash(r.ledger_state_hash) && decimal(r.position_quantity) && object(r.reconciliation) &&
    reconciliationKeys.every(k => decimal((r.reconciliation as Record<string, unknown>)[k]));
}
