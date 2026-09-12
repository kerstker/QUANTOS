export interface DemoCanonicalCashReport {
  schema: 'DEMO_CANONICAL_CASH_COMPARISON_V1'; environment: 'BINANCE_USDM_DEMO'; scope: 'TECHNICAL_VALIDATION_ONLY';
  status: 'MATCHED_REPORTED_INPUTS' | 'DIFFERENCE_FOUND' | 'UNRESOLVED';
  account_ref_sha256: string; symbol: 'BTCUSDT' | 'BTCUSDC'; quote_asset: 'USDT' | 'USDC';
  manifest_hash: string; journal_tail: string; cash_capture_sha256: string;
  replay_source_sha256: string; projection_source_sha256: string;
  cash_input_status: 'MATCHED_REPORTED_WINDOW' | 'DIFFERENCE_FOUND' | 'UNRESOLVED';
  cash_input_duplicates: number; observed_at_ms: number; issues: string[];
  canonical_wallet: string | null; reported_wallet: string | null; wallet_residual: string | null;
  fee_comparison: 'UNKNOWN' | 'MATCHED_REPORTED_TRADES' | 'NO_REPORTED_TRADES';
  realized_pnl_comparison: 'UNKNOWN' | 'MATCHED_REPORTED_TOTAL' | 'DIFFERENCE_FOUND';
  reconciliation: Record<string, string> | null; ledger_state_hash: string | null;
  source_verified: false; coverage_verified: false; authoritative: false; submission_enabled: false;
  strategy_pnl: null; limitations: string[];
}
export type DemoCanonicalCashSnapshot = {status: 'AVAILABLE'; report: DemoCanonicalCashReport} |
  {status: 'INPUTS_MISSING' | 'DATA_BLOCKED' | 'CORRUPT_OR_UNREADABLE'; report: null};
