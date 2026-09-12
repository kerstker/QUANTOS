const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const decimal = (v: unknown): v is string => typeof v === 'string' && /^-?\d{1,35}(\.\d{1,24})?$/.test(v);
const amount = (v: unknown) => v === null || decimal(v);
const code = (v: unknown) => typeof v === 'string' && /^[A-Z0-9_]{1,80}$/.test(v);
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const list = (v: unknown, max: number, check: (x: unknown) => boolean): v is unknown[] =>
  Array.isArray(v) && v.length <= max && v.every(check);

export function cashWindowValid(v: unknown, asOf: number): boolean {
  if (!object(v)) return false;
  if (['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report === null;
  const r = v.report;
  if (v.status !== 'AVAILABLE' || !object(r) || r.schema !== 'DEMO_CASH_WINDOW_V1' || r.environment !== 'BINANCE_USDM_DEMO' ||
      !['MATCHED_REPORTED_WINDOW','DIFFERENCE_FOUND','UNRESOLVED'].includes(String(r.status)) ||
      !['NOT_REQUESTED','CAPTURED','PARTIAL','FAILED'].includes(String(r.capture_status)) ||
      !integer(r.observed_at_ms) || r.observed_at_ms <= 5000 || !integer(r.completed_at_ms) ||
      r.completed_at_ms < r.observed_at_ms || r.completed_at_ms > asOf ||
      !integer(r.observe_seconds) || r.observe_seconds < 30 || r.observe_seconds > 3600 ||
      r.completed_at_ms-r.observed_at_ms > r.observe_seconds*1000+240000 ||
      !(r.account_ref_sha256 === null || hash(r.account_ref_sha256)) || !hash(r.source_sha256) ||
      !integer(r.read_count) || r.read_count > 14 || !integer(r.income_count) || r.income_count > 5000 ||
      !integer(r.duplicate_rows) || r.duplicate_rows > 10000 || !integer(r.endpoint_ambiguous_rows) ||
      r.endpoint_ambiguous_rows > r.income_count || typeof r.sweeps_match !== 'boolean' ||
      r.ledger_reconciliation !== 'NOT_EVALUATED' || r.strategy_pnl !== null || r.history_complete !== false ||
      r.source_verified !== false || r.authoritative !== false || r.submission_enabled !== false ||
      !list(r.issues,20,code) || !list(r.assets,6000,a => object(a) && code(a.asset) &&
        ['opening_wallet','closing_wallet','wallet_change','reported_flow','residual','transfers','commissions','funding','realized_pnl'].every(k => amount(a[k])) &&
        list(a.breakdown,5000,b => object(b) && code(b.type) && decimal(b.amount))) ||
      !list(r.failures,1,f => object(f) && ['clock_before','wallet_before','wallet_after','clock_after','income','credentials','worker'].includes(String(f.kind)) && integer(f.sweep) && f.sweep <= 2 &&
        integer(f.page) && f.page <= 5 && integer(f.requested_at_ms) && integer(f.received_at_ms) &&
        f.requested_at_ms >= Number(r.observed_at_ms) && f.received_at_ms >= f.requested_at_ms && f.received_at_ms <= Number(r.completed_at_ms) && code(f.reason)) ||
      !list(r.recent_flows,50,f => object(f) && code(f.asset) && code(f.type) && decimal(f.amount) && integer(f.time_ms) &&
        typeof f.symbol === 'string' && /^[A-Z0-9_\u3400-\u9fff]{0,32}$/.test(f.symbol) &&
        typeof f.transaction_id === 'string' && /^\d{1,19}$/.test(f.transaction_id) && BigInt(f.transaction_id) <= 9223372036854775807n)) return false;
  const bounds = r.bounds;
  if (bounds !== null && (!object(bounds) || !['query_start_ms','query_end_ms','inner_start_ms','inner_end_ms'].every(k => integer(bounds[k])) ||
      Number(bounds.query_start_ms) >= Number(bounds.inner_start_ms) || Number(bounds.inner_start_ms) >= Number(bounds.inner_end_ms) ||
      Number(bounds.inner_end_ms) >= Number(bounds.query_end_ms) || Number(bounds.query_end_ms) > r.completed_at_ms)) return false;
  if (r.recent_flows.length !== Math.min(50,r.income_count) ||
      new Set(r.assets.map(a => (a as Record<string,unknown>).asset)).size !== r.assets.length) return false;
  if (r.status !== 'UNRESOLVED') {
    if (r.capture_status !== 'CAPTURED' || !r.sweeps_match || r.issues.length || r.failures.length || !bounds ||
        !r.assets.length || r.endpoint_ambiguous_rows !== 0 || !hash(r.account_ref_sha256)) return false;
    const zero = (x: unknown) => decimal(x) && /^-?0+(\.0+)?$/.test(x);
    if (!r.assets.every(a => object(a) && ['opening_wallet','closing_wallet','wallet_change','reported_flow','residual',
      'transfers','commissions','funding','realized_pnl'].every(k=>decimal(a[k])))) return false;
    if ((r.status === 'MATCHED_REPORTED_WINDOW') !== r.assets.every(a => object(a) && zero(a.residual))) return false;
  } else if (!r.issues.length || !r.assets.every(a => object(a) && a.residual === null)) return false;
  return true;
}
