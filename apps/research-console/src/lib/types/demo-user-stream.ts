export interface DemoUserStreamRecord {
  sequence: number; kind: string; at_ms: number; record_hash: string;
  event_kind: string | null; event_ms: number | null; transaction_ms: number | null;
  reason: string | null; symbol: string | null; order_id: string | null; order_status: string | null;
  last_quantity: string | null; commission: string | null; commission_asset: string | null; account_reason: string | null;
}
export interface DemoUserStreamReport {
  schema: 'DEMO_USER_STREAM_V1'; environment: 'BINANCE_USDM_DEMO';
  account_ref_sha256: string; source_sha256: string; observed_at_ms: number;
  state: 'CONNECTING' | 'CONNECTED' | 'DISCONNECTED' | 'STOPPED' | 'STALE';
  records: number; head_sha256: string; sessions: number; connections: number; heartbeats: number;
  messages: number; unique_messages: number; gaps: number; event_counts: Record<string, number>;
  last_gap: {at_ms: number; reason: string; response_sha256?: string} | null;
  recent: DemoUserStreamRecord[]; integrity_scope: 'RECENT_100_RECORDS_AND_MANIFEST';
  coverage_verified: false; cash_reconciliation: 'UNKNOWN'; submission_enabled: false; authoritative: false;
}
export type DemoUserStreamSnapshot = {status: 'AVAILABLE'; report: DemoUserStreamReport} |
  {status: 'NO_JOURNAL' | 'DATA_BLOCKED' | 'CORRUPT_OR_UNREADABLE'; report: null};
