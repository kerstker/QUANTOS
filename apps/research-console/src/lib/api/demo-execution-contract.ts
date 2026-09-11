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

function openingValid(v: unknown, report: Record<string, unknown>, asOf: number): boolean {
  if (v === null) return true;
  const symbol = (s: unknown): s is string => typeof s === 'string' && /^[A-Z0-9_\u3400-\u9fff]{1,32}$/.test(s);
  if (!object(v) || v.schema !== 'DEMO_OPENING_ASSESSMENT_V1' || v.scope !== 'SINGLE_QUOTE_ACCOUNT_BOOK_TECHNICAL_PROJECTION' ||
      !['INPUTS_INCOMPLETE', 'DECLARED_INPUTS_READY'].includes(String(v.status)) || !hash(v.capture_hash) || !hash(v.account_ref_sha256) ||
      !count(v.opening_at_ms) || v.opening_at_ms > asOf || !count(report.observed_at_ms) || v.opening_at_ms > report.observed_at_ms ||
      v.quote_asset !== report.quote_asset || !decimal(v.initial_collateral) || !decimal(v.initial_position) ||
      !count(v.positions_count) || !count(v.other_quote_positions_count) || v.positions_count + v.other_quote_positions_count > 2000 ||
      v.basis_policy !== 'CALLER_DECLARED_EXACT_ENTRY_NOTIONAL' || v.source_verified !== false || v.atomic_snapshot_verified !== false ||
      v.submission_enabled !== false || v.venue_cash_reconciliation !== 'UNKNOWN' ||
      !Array.isArray(v.issues) || v.issues.length > 30 || !v.issues.every(code) ||
      !Array.isArray(v.missing_basis_symbols) || v.missing_basis_symbols.length > 2000 || !v.missing_basis_symbols.every(symbol) ||
      !Array.isArray(v.missing_contract_symbols) || v.missing_contract_symbols.length > 2001 || !v.missing_contract_symbols.every(symbol) ||
      new Set(v.missing_basis_symbols).size !== v.missing_basis_symbols.length || new Set(v.missing_contract_symbols).size !== v.missing_contract_symbols.length ||
      !Array.isArray(v.positions) || v.positions.length !== v.positions_count || !v.positions.every(p => object(p) && symbol(p.symbol) &&
        ['BOTH', 'LONG', 'SHORT'].includes(String(p.position_side)) && decimal(p.quantity) && Number(p.quantity) !== 0 &&
        (p.margin_mode === null || ['CROSSED', 'ISOLATED'].includes(String(p.margin_mode))) &&
        (p.entry_notional === null ? p.basis_source_sha256 === null : decimal(p.entry_notional) && Number(p.entry_notional) > 0 && hash(p.basis_source_sha256)) &&
        typeof p.contract_available === 'boolean')) return false;
  const basis = [...new Set(v.positions.filter(p => p.entry_notional === null).map(p => p.symbol))].sort();
  const contracts = v.positions.filter(p => !p.contract_available).map(p => p.symbol);
  const positionSymbols = v.positions.map(p => p.symbol);
  if (new Set(v.positions.map(p => `${p.symbol}:${p.position_side}`)).size !== v.positions_count ||
      JSON.stringify(basis) !== JSON.stringify([...v.missing_basis_symbols].sort()) ||
      contracts.some(s => !(v.missing_contract_symbols as string[]).includes(s)) ||
      v.missing_contract_symbols.some(s => !contracts.includes(s) && (s !== report.symbol || positionSymbols.includes(s)))) return false;
  if (v.status === 'INPUTS_INCOMPLETE') return v.issues.length > 0 && report.projection_status === 'UNRESOLVED' && report.projection_reason === 'OPENING_INPUTS_INCOMPLETE';
  return !v.issues.length && !v.missing_basis_symbols.length && !v.missing_contract_symbols.length;
}

export function executionSnapshotValid(v: unknown, asOf: number): boolean {
  if (!object(v)) return false;
  if (v.status !== 'AVAILABLE') return ['NO_JOURNAL', 'CORRUPT_OR_UNREADABLE', 'DATA_BLOCKED'].includes(String(v.status)) && v.report === null;
  const r = v.report;
  if (!object(r) || r.schema !== 'DEMO_EXECUTION_REPLAY_V1' || r.environment !== 'BINANCE_USDM_DEMO' ||
      r.scope !== 'TECHNICAL_VALIDATION_ONLY' || r.source_verified !== false || r.authoritative !== false || r.submission_enabled !== false ||
      !['SYNTHETIC_TEST_FIXTURE', 'IMPORTED_UNVERIFIED'].includes(String(r.provenance)) ||
      !['USDT', 'USDC'].includes(String(r.quote_asset)) || r.symbol !== `BTC${r.quote_asset}` ||
      ![r.manifest_hash, r.journal_tail, r.replay_source_sha256, r.query_binding_hash].every(hash) ||
      typeof r.declared_source_commit !== 'string' || !/^[a-f0-9]{40}$/.test(r.declared_source_commit) ||
      ![r.journal_records, r.rejected_batches, r.trades_count, r.duplicate_messages].every(count) ||
      !(r.observed_at_ms === null || count(r.observed_at_ms) && r.observed_at_ms <= asOf) ||
      !decimal(r.filled_quantity) || !nullableDecimal(r.position_quantity) || !nullableDecimal(r.expected_end_position) ||
      !comparisons.includes(String(r.position_comparison)) || r.venue_cash_reconciliation !== 'UNKNOWN' || r.actual_paid_fees !== null ||
      !object(r.reported_commission_by_asset) || !Object.entries(r.reported_commission_by_asset).every(([k, n]) => /^[A-Z0-9]{1,20}$/.test(k) && decimal(n)) ||
      !Array.isArray(r.limitations) || !r.limitations.every(code) || !openingValid(r.opening, r, asOf) ||
      !Array.isArray(r.orders) || r.orders.length > 200 || !r.orders.every(o => object(o) && orderId(o.order_id) &&
        ['BUY', 'SELL'].includes(String(o.side)) && states.includes(String(o.state)) && decimal(o.quantity) && decimal(o.filled_quantity) &&
        (o.last_event_at_ms === null || count(o.last_event_at_ms) && o.last_event_at_ms <= asOf)) ||
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

export function orderRecoveryValid(v: unknown): boolean {
  if (!object(v)) return false;
  if (v.status !== 'AVAILABLE') return ['NO_SNAPSHOT', 'CORRUPT_OR_UNREADABLE'].includes(String(v.status)) && v.report === null;
  const r = v.report;
  const statuses = ['MATCHED_OBSERVATIONS', 'ACTION_REQUIRED', 'STALE', 'DATA_BLOCKED', 'IDENTITY_MISMATCH', 'JOURNAL_ADVANCED', 'NO_JOURNAL'];
  const rowStatuses = ['MATCHED_OBSERVATIONS', 'BACKFILL_REQUIRED', 'STATE_UPDATE_REQUIRED', 'QUERY_UNRESOLVED', 'RECEIPTS_MISSING',
    'IDENTITY_MISMATCH', 'OLDER_VENUE_OBSERVATION', 'TERMINAL_CONFLICT', 'QUANTITY_REGRESSION', 'STATE_CONFLICT'];
  if (!object(r) || r.schema !== 'DEMO_ORDER_RECOVERY_V1' || r.scope !== 'TECHNICAL_VALIDATION_ONLY' ||
      r.source_verified !== false || r.new_command_allowed !== false || !statuses.includes(String(r.status)) ||
      ![r.capture_hash, r.manifest_hash, r.journal_tail].every(hash) || !count(r.observed_at_ms) ||
      !['OPERATOR_DEMO_REST', 'SYNTHETIC_TEST_FIXTURE'].includes(String(r.provenance)) ||
      !Array.isArray(r.orders) || r.orders.length > 10 || !r.orders.every(o => object(o) && orderId(o.order_id) && venueId(o.venue_order_id) &&
        states.includes(String(o.receipt_state)) && (o.query_state === null || ['NEW', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH'].includes(String(o.query_state))) &&
        [o.receipt_quantity, o.query_quantity, o.quantity_gap].every(nullableDecimal) && rowStatuses.includes(String(o.status)) &&
        code(o.reason) && count(o.received_at_ms))) return false;
  if (r.status === 'MATCHED_OBSERVATIONS') return r.orders.length > 0 && r.orders.every(o => object(o) && o.status === 'MATCHED_OBSERVATIONS');
  return r.status === 'ACTION_REQUIRED' ? r.orders.length > 0 : r.orders.length === 0;
}
