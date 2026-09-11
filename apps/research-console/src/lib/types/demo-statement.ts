export interface DemoStatement {
  schema: 'BINANCE_DEMO_STATEMENT_V1'; status: 'NOT_REQUESTED' | 'CAPTURED' | 'PARTIAL' | 'FAILED';
  observed_at_ms: number; completed_at_ms: number; start_ms: number; end_ms: number;
  symbols: string[]; read_count: number; trade_count: number; income_count: number; failed_attempt_count: number;
  unqueried_income_symbols: string[]; income_breakdown: Array<{asset: string; type: string; amount: string}>;
  assets: Array<{asset: string; reported_commission: string; reported_realized_pnl: string;
    income_total: string; income_commission: string; fee_crosscheck_residual: string}>;
  gaps: Array<{kind: string; symbol: string; start_ms: number; end_ms: number; reason: string}>;
  terms: Array<{symbol: string; exchange_rules: boolean; margin_brackets: boolean;
    commission: {symbol: string; makerCommissionRate: string; takerCommissionRate: string} | null}>;
  history_complete: false; venue_cash_reconciliation: 'UNKNOWN'; strategy_pnl: null; submission_enabled: false;
}
export type DemoStatementSnapshot = {status: 'AVAILABLE' | 'STALE'; report: DemoStatement} |
  {status: 'NO_SNAPSHOT' | 'DATA_BLOCKED' | 'CORRUPT_OR_UNREADABLE'; report: null};
