const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const count = (v: unknown): v is number => integer(v) && v <= 1000000;
const time = (v: unknown): v is number => integer(v) && v > 0;
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const text = (v: unknown) => typeof v === 'string' && /^[A-Z0-9_]{1,80}$/.test(v);
const symbol = (v: unknown) => typeof v === 'string' && /^[A-Z0-9_\u3400-\u9fff]{1,32}$/.test(v);
const decimal = (v: unknown) => typeof v === 'string' && /^-?\d{1,30}(\.\d{1,24})?$/.test(v);
const events = ['ACCOUNT_UPDATE','ORDER_TRADE_UPDATE','ACCOUNT_CONFIG_UPDATE','MARGIN_CALL','TRADE_LITE','listenKeyExpired'];
const kinds = ['SESSION_START','CONNECTED','HEARTBEAT','LEASE_RENEWED','MESSAGE','GAP','STOPPED'];
const gaps = ['UNCLEAN_RESTART','RESTART_WINDOW','CONNECT_FAILED','STREAM_DISCONNECTED','LEASE_EXPIRED','KEEPALIVE_FAILED',
  'MESSAGE_REJECTED','OUT_OF_ORDER_EVENT','CLOCK_REGRESSION','RECONNECT_BUDGET_EXHAUSTED'];
const stops = ['BUDGET_EXHAUSTED','SIGNAL','AUTHENTICATION_REJECTED','RATE_LIMITED','FAILED'];

export function userStreamValid(v: unknown, asOf: number): boolean {
  if (!object(v)) return false;
  if (['NO_JOURNAL','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report === null;
  const r = v.report;
  if (v.status !== 'AVAILABLE' || !object(r) || r.schema !== 'DEMO_USER_STREAM_V1' || r.environment !== 'BINANCE_USDM_DEMO' ||
      r.coverage_verified !== false || r.cash_reconciliation !== 'UNKNOWN' || r.submission_enabled !== false || r.authoritative !== false ||
      r.integrity_scope !== 'RECENT_100_RECORDS_AND_MANIFEST' || !['account_ref_sha256','source_sha256','head_sha256'].every(k => hash(r[k])) ||
      !time(r.observed_at_ms) || r.observed_at_ms > asOf ||
      !['CONNECTING','CONNECTED','DISCONNECTED','STOPPED','STALE'].includes(String(r.state)) ||
      (r.state === 'STALE') !== (asOf-r.observed_at_ms > 120000) ||
      !['records','sessions','connections','heartbeats','messages','unique_messages','gaps'].every(k => count(r[k])) ||
      !count(r.records) || !count(r.sessions) || !count(r.messages) || !count(r.unique_messages) || !count(r.gaps) ||
      r.records < 1 || r.sessions < 1 || r.unique_messages > r.messages ||
      !object(r.event_counts) || Object.entries(r.event_counts).some(([k,n]) => !events.includes(k) || !count(n)) ||
      Object.values(r.event_counts).reduce<number>((a,b) => a+Number(b),0) !== r.messages ||
      !Array.isArray(r.recent) || r.recent.length !== Math.min(50,r.records)) return false;
  if (r.last_gap === null ? r.gaps !== 0 : !object(r.last_gap) || !time(r.last_gap.at_ms) || r.last_gap.at_ms > asOf ||
      !gaps.includes(String(r.last_gap.reason)) || r.gaps === 0 ||
      r.last_gap.response_sha256 !== undefined && !hash(r.last_gap.response_sha256)) return false;
  const records = r.records;
  const recentLength = r.recent.length;
  return r.recent.every((row,i) => object(row) && row.sequence === records-recentLength+i+1 && hash(row.record_hash) &&
    kinds.includes(String(row.kind)) && time(row.at_ms) && row.at_ms <= asOf &&
    (i !== recentLength-1 || row.record_hash === r.head_sha256 && row.at_ms === r.observed_at_ms) &&
    (row.event_ms === null || time(row.event_ms)) && (row.transaction_ms === null || time(row.transaction_ms)) &&
    (row.kind === 'MESSAGE' ? events.includes(String(row.event_kind)) && time(row.event_ms) :
      row.event_kind === null && row.event_ms === null && row.transaction_ms === null) &&
    (row.kind === 'GAP' ? gaps.includes(String(row.reason)) : row.kind === 'STOPPED' ? stops.includes(String(row.reason)) : row.reason === null) &&
    (row.symbol === null || symbol(row.symbol)) && (row.order_id === null || typeof row.order_id === 'string' && /^\d{1,19}$/.test(row.order_id)) &&
    [row.order_status,row.commission_asset,row.account_reason].every(v => v === null || text(v)) &&
    [row.last_quantity,row.commission].every(v => v === null || decimal(v)));
}
