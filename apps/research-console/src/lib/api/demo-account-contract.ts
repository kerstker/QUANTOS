/** Validate saved account observations; no credentials or execution mutations. */
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const symbol = (v: unknown) => typeof v === 'string' && /^[A-Z0-9_\u3400-\u9fff]{1,32}$/.test(v);
const asset = (v: unknown) => typeof v === 'string' && /^[A-Z0-9_]{1,32}$/.test(v);
const decimal = (v: unknown) => typeof v === 'string' && /^-?\d{1,100}(\.\d{1,100})?$/.test(v);
const positive = (v: unknown) => decimal(v) && Number(v) > 0;
const config = (v: unknown) => v === null || object(v) && ['canTrade', 'dualSidePosition', 'multiAssetsMargin'].every(k => typeof v[k] === 'boolean');
const side = (v: unknown) => ['BOTH', 'LONG', 'SHORT'].includes(String(v));
const unique = (rows: Record<string, unknown>[], keys: string[]) => new Set(rows.map(r => keys.map(k => r[k]).join(':'))).size === rows.length;
const reads = ['config_before', 'account_before', 'symbols_before', 'positions', 'orders', 'symbols_after', 'account_after', 'config_after'];
const comparisons = ['wallet', 'positions', 'account_config', 'symbol_config'];
const reasons = ['ACCOUNT_NOT_REQUESTED', 'CAPTURE_COMPLETE', 'REQUEST_FAILED', 'RESPONSE_INVALID', 'REQUEST_TIMEOUT', 'CLOCK_UNRELIABLE',
  'AUTHENTICATION_REJECTED', 'RATE_LIMITED', 'CREDENTIAL_FILE_INVALID', 'CREDENTIAL_FILE_PERMISSIONS', 'REDIRECT_REFUSED', 'RESPONSE_TOO_LARGE'];

export function accountBaselineValid(v: unknown, asOf: number): boolean {
  if (!object(v)) return false;
  if (['NO_SNAPSHOT', 'DATA_BLOCKED', 'CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report === null;
  const r = v.report;
  if (v.status !== 'AVAILABLE' || !object(r) || r.schema !== 'DEMO_ACCOUNT_BASELINE_V1' || r.environment !== 'BINANCE_USDM_DEMO' ||
      !hash(r.capture_hash) || !hash(r.source_sha256) || !integer(r.observed_at_ms) || !integer(r.completed_at_ms) ||
      r.completed_at_ms < r.observed_at_ms || r.completed_at_ms > asOf || r.completed_at_ms - r.observed_at_ms > 120000 ||
      !['STABLE_OBSERVATIONS', 'CHANGED_DURING_CAPTURE', 'CAPTURE_FAILED', 'NOT_REQUESTED', 'STALE'].includes(String(r.inventory_status)) ||
      (r.inventory_status === 'STALE') !== (asOf - r.observed_at_ms > 120000) || !reasons.includes(String(r.capture_reason)) ||
      r.atomic_snapshot_verified !== false || r.authoritative !== false || r.submission_enabled !== false ||
      r.new_strategy_pnl !== null || r.ledger_reconciliation !== 'UNKNOWN' ||
      !(r.nonzero_positions === null || integer(r.nonzero_positions) && r.nonzero_positions <= 2000) ||
      !(r.flat_account === null || typeof r.flat_account === 'boolean') ||
      !config(r.account_config) ||
      !Array.isArray(r.issues) || r.issues.length > 30 || !r.issues.every(i => typeof i === 'string' && /^[A-Z_]{1,80}$/.test(i)) ||
      !Array.isArray(r.assets) || r.assets.length > 500 || !r.assets.every(a => object(a) && asset(a.asset) &&
        ['observed_wallet', 'observed_cross_wallet', 'observed_unrealized', 'observed_margin_balance', 'observed_available', 'account_identity_residual'].every(k => decimal(a[k]))) ||
      !unique(r.assets, ['asset']) || !Array.isArray(r.positions) || r.positions.length > 2000 || !r.positions.every(p => object(p) &&
        symbol(p.symbol) && side(p.position_side) && asset(p.margin_asset) && p.ownership === 'PREEXISTING_ACCOUNT_POSITION' &&
        ['quantity', 'observed_unrealized', 'basis_pnl_residual', 'isolated_wallet'].every(k => decimal(p[k])) && Number(p.quantity) !== 0 &&
        ['observed_entry_price', 'observed_mark_price', 'estimated_entry_notional'].every(k => positive(p[k])) && Number(p.isolated_wallet) >= 0 &&
        (p.margin_mode === null || ['CROSSED', 'ISOLATED'].includes(String(p.margin_mode))) &&
        (p.leverage === null || integer(p.leverage) && p.leverage >= 1 && p.leverage <= 1000)) || !unique(r.positions, ['symbol', 'position_side']) ||
      !Array.isArray(r.open_orders) || r.open_orders.length > 2000 || !r.open_orders.every(o => object(o) && symbol(o.symbol) &&
        typeof o.orderId === 'string' && /^[1-9]\d{0,18}$/.test(o.orderId) && typeof o.clientOrderId === 'string' && /^[.A-Z:/a-z0-9_-]{1,36}$/.test(o.clientOrderId) &&
        side(o.positionSide) && ['BUY', 'SELL'].includes(String(o.side)) && ['NEW', 'PARTIALLY_FILLED', 'PENDING_CANCEL'].includes(String(o.status)) &&
        positive(o.origQty) && decimal(o.executedQty) && Number(o.executedQty) >= 0 && Number(o.executedQty) <= Number(o.origQty)) || !unique(r.open_orders, ['symbol', 'orderId']) ||
      !Array.isArray(r.read_intervals) || r.read_intervals.length > 8 || !r.read_intervals.every((i, index, all) => object(i) && i.kind === reads[index] &&
        hash(i.response_sha256) && integer(i.started_at_ms) && integer(i.received_at_ms) && i.started_at_ms <= i.received_at_ms &&
        i.received_at_ms <= Number(r.completed_at_ms) && i.started_at_ms >= (index ? Number(all[index - 1].received_at_ms) : Number(r.observed_at_ms))) ||
      !Array.isArray(r.comparisons) || r.comparisons.length > 4 || !r.comparisons.every((c, i) => object(c) && c.kind === comparisons[i] &&
        ['MATCHED_OBSERVATIONS', 'DIFFERENT'].includes(String(c.status)))) return false;
  const complete = r.capture_reason === 'CAPTURE_COMPLETE';
  const stable = complete && r.comparisons.length === 4 && r.comparisons.every(c => c.status === 'MATCHED_OBSERVATIONS');
  if (complete) {
    if (r.read_intervals.length !== 8 || r.comparisons.length !== 4 || r.nonzero_positions === null || r.account_config === null ||
        !r.issues.includes('REST_OBSERVATIONS_NOT_ATOMIC') || (r.positions.length > 0 && !r.issues.includes('ENTRY_BASIS_UNVERIFIED')) ||
        (stable && r.nonzero_positions !== r.positions.length) || r.flat_account !== (stable ? r.nonzero_positions === 0 : null)) return false;
    return r.inventory_status === 'STALE' || r.inventory_status === (stable ? 'STABLE_OBSERVATIONS' : 'CHANGED_DURING_CAPTURE');
  }
  if (r.assets.length || r.positions.length || r.open_orders.length || r.comparisons.length || r.nonzero_positions !== null || r.flat_account !== null) return false;
  if (r.capture_reason === 'ACCOUNT_NOT_REQUESTED') return !r.read_intervals.length && ['NOT_REQUESTED', 'STALE'].includes(String(r.inventory_status));
  return ['CAPTURE_FAILED', 'STALE'].includes(String(r.inventory_status));
}
