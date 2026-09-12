export interface DemoCashWindowReport {
  schema: 'DEMO_CASH_WINDOW_V1'; environment: 'BINANCE_USDM_DEMO';
  status: 'MATCHED_REPORTED_WINDOW' | 'DIFFERENCE_FOUND' | 'UNRESOLVED';
  capture_status: 'NOT_REQUESTED' | 'CAPTURED' | 'PARTIAL' | 'FAILED';
  observed_at_ms: number; completed_at_ms: number; account_ref_sha256: string | null; source_sha256: string;
  observe_seconds: number; read_count: number; income_count: number; duplicate_rows: number;
  endpoint_ambiguous_rows: number; sweeps_match: boolean;
  bounds: {query_start_ms: number; query_end_ms: number; inner_start_ms: number; inner_end_ms: number} | null;
  assets: Array<{asset: string; opening_wallet: string | null; closing_wallet: string | null;
    wallet_change: string | null; reported_flow: string | null; residual: string | null;
    transfers: string | null; commissions: string | null; funding: string | null; realized_pnl: string | null;
    breakdown: Array<{type: string; amount: string}>}>;
  issues: string[];
  failures: Array<{kind: string; sweep: number; page: number; requested_at_ms: number; received_at_ms: number; reason: string}>;
  recent_flows: Array<{asset: string; type: string; amount: string; time_ms: number; transaction_id: string; symbol: string}>;
  ledger_reconciliation: 'NOT_EVALUATED'; strategy_pnl: null; history_complete: false;
  source_verified: false; submission_enabled: false; authoritative: false;
}
export type DemoCashWindowSnapshot = {status: 'AVAILABLE'; report: DemoCashWindowReport} |
  {status: 'NO_SNAPSHOT' | 'DATA_BLOCKED' | 'CORRUPT_OR_UNREADABLE'; report: null};
