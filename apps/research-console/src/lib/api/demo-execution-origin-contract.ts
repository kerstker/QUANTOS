const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const code = (v: unknown) => typeof v === 'string' && /^[A-Z_]{1,80}$/.test(v);
export function executionOriginValid(v: unknown, asOf: number): boolean {
  if (!object(v)) return false;
  if (['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report === null;
  const r=v.report;
  if (v.status !== 'AVAILABLE' || !object(r) || !['DEMO_EXECUTION_ORIGIN_V1','DEMO_EXECUTION_ORIGIN_V2','DEMO_EXECUTION_ORIGIN_V3'].includes(String(r.schema)) || r.environment !== 'BINANCE_USDM_DEMO' ||
    !['OBSERVED_FLAT_QUOTE','UNRESOLVED'].includes(String(r.status)) || !['BTCUSDT','BTCUSDC'].includes(String(r.symbol)) ||
    r.symbol !== `BTC${r.quote_asset}` || !integer(r.observed_at_ms) || !r.observed_at_ms || !integer(r.completed_at_ms) ||
    r.completed_at_ms < r.observed_at_ms || r.completed_at_ms > asOf || r.completed_at_ms-r.observed_at_ms > 120000 ||
    !hash(r.source_sha256) || !(r.account_ref_sha256 === null || hash(r.account_ref_sha256)) ||
    !(r.wallet === null || typeof r.wallet === 'string' && /^-?\d{1,30}(\.\d{1,24})?$/.test(r.wallet)) ||
    !['other_quote_positions','quote_positions','ordinary_orders','algo_orders'].every(k => r[k] === null || integer(r[k]) && Number(r[k]) <= 2000) ||
    !integer(r.read_count) || r.read_count > 16 || !Array.isArray(r.issues) || r.issues.length > 32 || !r.issues.every(code) ||
    new Set(r.issues).size !== r.issues.length || !Array.isArray(r.failures) || r.failures.length > 1 ||
    r.settlement_rounding !== 'UNVERIFIED' || r.source_verified !== false || r.atomic_snapshot_verified !== false ||
    r.submission_enabled !== false || r.strategy_pnl !== null) return false;
  if (!r.failures.every(f => object(f) && typeof f.kind === 'string' && /^[a-z_]{1,32}$/.test(f.kind) && code(f.reason) &&
    integer(f.requested_at_ms) && integer(f.received_at_ms) && f.requested_at_ms >= Number(r.observed_at_ms) &&
    f.received_at_ms >= f.requested_at_ms && f.received_at_ms <= Number(r.completed_at_ms) &&
    (f.response_sha256 === null || hash(f.response_sha256)) && (f.server_time_ms === null || integer(f.server_time_ms) &&
      f.kind.startsWith('clock') && hash(f.response_sha256)))) return false;
  if ((r.status === 'UNRESOLVED') !== (r.issues.length > 0)) return false;
  if (asOf-r.observed_at_ms > 30000 && !r.issues.includes('ORIGIN_STALE')) return false;
  return r.status !== 'OBSERVED_FLAT_QUOTE' || r.read_count === 16 && r.failures.length === 0 &&
    hash(r.account_ref_sha256) && r.wallet !== null && r.quote_positions === 0 && r.ordinary_orders === 0 && r.algo_orders === 0;
}
