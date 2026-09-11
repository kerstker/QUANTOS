const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
const decimal = (v: unknown) => typeof v === 'string' && /^-?\d{1,30}(\.\d{1,24})?$/.test(v);
const symbol = (v: unknown) => typeof v === 'string' && /^[A-Z0-9_\u3400-\u9fff]{1,32}$/.test(v);
export function statementValid(v: unknown, asOf: number): boolean {
  if (!object(v)) return false;
  if (['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report === null;
  const r = v.report;
  if (!['AVAILABLE','STALE'].includes(String(v.status)) || !object(r) || r.schema !== 'BINANCE_DEMO_STATEMENT_V1' ||
      !['NOT_REQUESTED','CAPTURED','PARTIAL','FAILED'].includes(String(r.status)) ||
      !integer(r.start_ms) || !integer(r.end_ms) || !integer(r.observed_at_ms) || !integer(r.completed_at_ms) ||
      !(r.start_ms < r.end_ms && r.end_ms <= r.observed_at_ms && r.observed_at_ms <= r.completed_at_ms && r.completed_at_ms <= asOf) ||
      (v.status === 'STALE') !== (asOf-r.observed_at_ms > 120000) ||
      !['read_count','trade_count','income_count','failed_attempt_count'].every(k => integer(r[k])) ||
      r.history_complete !== false || r.venue_cash_reconciliation !== 'UNKNOWN' || r.strategy_pnl !== null || r.submission_enabled !== false ||
      !Array.isArray(r.symbols) || !r.symbols.length || r.symbols.length > 30 || !r.symbols.every(symbol) || new Set(r.symbols).size !== r.symbols.length ||
      !Array.isArray(r.assets) || !Array.isArray(r.terms) || !Array.isArray(r.gaps) ||
      !Array.isArray(r.unqueried_income_symbols) || !r.unqueried_income_symbols.every(symbol) ||
      !Array.isArray(r.income_breakdown) || !r.income_breakdown.every(b => object(b) && symbol(b.asset) && symbol(b.type) && decimal(b.amount))) return false;
  const symbols = r.symbols;
  return r.assets.every(a => object(a) && symbol(a.asset) && ['reported_commission','reported_realized_pnl','income_total','income_commission','fee_crosscheck_residual'].every(k => decimal(a[k]))) &&
    r.terms.length === symbols.length && new Set(r.terms.map(t => object(t) ? t.symbol : null)).size === symbols.length &&
    r.terms.every(t => object(t) && symbols.includes(t.symbol) && typeof t.exchange_rules === 'boolean' && typeof t.margin_brackets === 'boolean' &&
      (t.commission === null || object(t.commission) && t.commission.symbol === t.symbol && decimal(t.commission.makerCommissionRate) && decimal(t.commission.takerCommissionRate))) &&
    r.gaps.every(g => object(g) && typeof g.kind === 'string' && typeof g.reason === 'string' && typeof g.symbol === 'string' && integer(g.start_ms) && integer(g.end_ms));
}
