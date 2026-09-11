export interface DemoSymbol {
  symbol: string; quote_asset: string; margin_asset: string; contract_type: 'PERPETUAL';
  status: 'TRADING'; tick_size: string; step_size: string; min_qty: string; min_notional: string;
}
export interface DemoPublic {
  status: 'READY' | 'FAILED'; reason: string; server_time_ms: number | null;
  round_trip_ms: number | null; clock_offset_ms: number | null; symbols: DemoSymbol[];
}
export interface DemoAccount {
  status: 'READY' | 'NOT_CHECKED' | 'FAILED' | 'SKIPPED'; reason: string; observed_at_ms: number | null;
  assets: Array<{asset: string; wallet_balance: string; available_balance: string; unrealized_profit: string}>;
  positions: Array<{symbol: string; position_side: 'BOTH' | 'LONG' | 'SHORT'; quantity: string; unrealized_profit: string}>;
}
export interface BinanceDemoPayload {
  source: string; read_only: true; api_version: string; data_provenance: 'DEMO_CONNECTIVITY_DIAGNOSTIC';
  snapshot_id: string | null; snapshot_ids: string[];
  snapshot_status: 'REAL' | 'STALE' | 'NO_SNAPSHOT' | 'DATA_BLOCKED' | 'CORRUPT_OR_UNREADABLE';
  data_quality: {status: string; provenance: string; reason: string};
  environment: 'BINANCE_USDM_DEMO'; rest_base: 'https://demo-fapi.binance.com';
  observed_at_ms: number | null; as_of_ms: number; age_ms: number | null; max_age_ms: number;
  public: DemoPublic | null; account: DemoAccount | null;
  submission_enabled: false; authoritative: false; execution_reason: 'LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE';
  order_stream_status: 'NOT_CHECKED'; ledger_reconciliation_status: 'NOT_CHECKED';
}
