export interface DemoTargetConfiguration {
  symbol: 'BTCUSDT' | 'BTCUSDC'; marginType: 'CROSSED' | 'ISOLATED'; leverage: number;
  isAutoAddMargin: boolean; maxNotionalValue: string;
}
export interface DemoExecutionOriginReport {
  schema: 'DEMO_EXECUTION_ORIGIN_V1' | 'DEMO_EXECUTION_ORIGIN_V2' | 'DEMO_EXECUTION_ORIGIN_V3' | 'DEMO_EXECUTION_ORIGIN_V4'; environment: 'BINANCE_USDM_DEMO';
  status: 'OBSERVED_FLAT_QUOTE' | 'UNRESOLVED'; symbol: 'BTCUSDT' | 'BTCUSDC'; quote_asset: 'USDT' | 'USDC';
  wallet: string | null; other_quote_positions: number | null; quote_positions: number | null;
  ordinary_orders: number | null; algo_orders: number | null; account_ref_sha256: string | null;
  observed_at_ms: number; completed_at_ms: number; source_sha256: string; read_count: number; issues: string[];
  failures: Array<{kind: string; requested_at_ms: number; received_at_ms: number; reason: string;
    response_sha256: string | null; server_time_ms: number | null}>;
  settlement_rounding: 'UNVERIFIED'; source_verified: false; atomic_snapshot_verified: false;
  submission_enabled: false; strategy_pnl: null;
  target_configuration?: {status: 'MATCHED_OBSERVATIONS' | 'DIFFERENT_OBSERVATIONS' | 'INCOMPLETE';
    before: DemoTargetConfiguration | null; after: DemoTargetConfiguration | null};
}
export type DemoExecutionOriginSnapshot = {status: 'AVAILABLE'; report: DemoExecutionOriginReport} |
  {status: 'NO_SNAPSHOT' | 'DATA_BLOCKED' | 'CORRUPT_OR_UNREADABLE'; report: null};
