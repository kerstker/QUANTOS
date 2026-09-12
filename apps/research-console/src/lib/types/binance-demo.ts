import type {DemoStatementSnapshot} from './demo-statement';
import type {DemoUserStreamSnapshot} from './demo-user-stream';
import type {DemoCashWindowSnapshot} from './demo-cash-window';
import type {DemoExecutionOriginSnapshot} from './demo-execution-origin';
import type {DemoCanonicalCashSnapshot} from './demo-canonical-cash';
import type {DemoOmsStatusSnapshot} from './demo-oms-status';
import type {DemoCancelStatusSnapshot} from './demo-cancel-status';
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
export interface DemoReplayReport {
  opening: DemoOpeningAssessment | null;
  schema: 'DEMO_EXECUTION_REPLAY_V1'; environment: 'BINANCE_USDM_DEMO'; scope: 'TECHNICAL_VALIDATION_ONLY';
  source_verified: false; authoritative: false; submission_enabled: false;
  provenance: 'SYNTHETIC_TEST_FIXTURE' | 'IMPORTED_UNVERIFIED';
  manifest_hash: string; declared_source_commit: string; replay_source_sha256: string; query_binding_hash: string;
  journal_tail: string; journal_records: number; rejected_batches: number; observed_at_ms: number | null;
  symbol: 'BTCUSDT' | 'BTCUSDC'; quote_asset: 'USDT' | 'USDC';
  projection_status: 'REPLAYED_TECHNICAL_ONLY' | 'UNRESOLVED'; projection_reason: string | null;
  filled_quantity: string; position_quantity: string | null; expected_end_position: string | null;
  position_comparison: 'UNKNOWN' | 'WAITING_TERMINAL_ORDERS' | 'DECLARED_POSITION_MATCH' | 'DECLARED_POSITION_MISMATCH';
  venue_cash_reconciliation: 'UNKNOWN'; actual_paid_fees: null;
  reconciliation: Record<string, string> | null; ledger_state_hash: string | null;
  trades_count: number; duplicate_messages: number; reported_commission_by_asset: Record<string, string>;
  orders: Array<{order_id: string; side: 'BUY' | 'SELL'; quantity: string; state: string; filled_quantity: string; last_event_at_ms: number | null}>;
  recent_trades: Array<{order_id: string; venue_order_id: string; trade_id: string; quantity: string; price: string;
    event_ms: number; transaction_ms: number; venue_trade_ms: number; received_at_ms: number;
    liquidity_role: 'MAKER' | 'TAKER'; reported_commission: string | null; commission_asset: string | null; response_sha256: string}>;
  limitations: string[];
}
export type DemoExecutionSnapshot = {status: 'AVAILABLE'; report: DemoReplayReport} |
  {status: 'NO_JOURNAL' | 'CORRUPT_OR_UNREADABLE' | 'DATA_BLOCKED'; report: null};
export interface DemoOrderRecoveryReport {
  schema: 'DEMO_ORDER_RECOVERY_V1'; scope: 'TECHNICAL_VALIDATION_ONLY'; source_verified: false; new_command_allowed: false;
  status: 'MATCHED_OBSERVATIONS' | 'ACTION_REQUIRED' | 'STALE' | 'DATA_BLOCKED' | 'IDENTITY_MISMATCH' | 'JOURNAL_ADVANCED' | 'NO_JOURNAL';
  capture_hash: string; manifest_hash: string; journal_tail: string; observed_at_ms: number;
  provenance: 'OPERATOR_DEMO_REST' | 'SYNTHETIC_TEST_FIXTURE';
  orders: Array<{order_id: string; venue_order_id: string; receipt_state: string; query_state: string | null;
    receipt_quantity: string | null; query_quantity: string | null; quantity_gap: string | null;
    status: 'MATCHED_OBSERVATIONS' | 'BACKFILL_REQUIRED' | 'STATE_UPDATE_REQUIRED' | 'QUERY_UNRESOLVED' | 'RECEIPTS_MISSING' |
      'IDENTITY_MISMATCH' | 'OLDER_VENUE_OBSERVATION' | 'TERMINAL_CONFLICT' | 'QUANTITY_REGRESSION' | 'STATE_CONFLICT';
    reason: string; received_at_ms: number}>;
}
export type DemoOrderRecoverySnapshot = {status: 'AVAILABLE'; report: DemoOrderRecoveryReport} |
  {status: 'NO_SNAPSHOT' | 'CORRUPT_OR_UNREADABLE'; report: null};
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
  execution: DemoExecutionSnapshot;
  order_recovery: DemoOrderRecoverySnapshot;
  private_checks: DemoPrivateCheckSnapshot;
  account_baseline: DemoAccountBaselineSnapshot;
  statement: DemoStatementSnapshot;
  user_stream: DemoUserStreamSnapshot;
  cash_window: DemoCashWindowSnapshot;
  execution_origin: DemoExecutionOriginSnapshot;
  canonical_cash: DemoCanonicalCashSnapshot;
  oms_status: DemoOmsStatusSnapshot;
  cancellation_status: DemoCancelStatusSnapshot;
}

export interface DemoPrivateCheckReport {
  schema_version: 'binance-demo-private-check.v1'; environment: 'BINANCE_USDM_DEMO';
  observed_at_ms: number; completed_at_ms: number; source_sha256: string;
  connectivity_status: 'NOT_REQUESTED' | 'CHECKS_PASSED' | 'FAILED';
  steps: Array<{name: string; status: 'PASSED' | 'FAILED'; reason: string; rows: number | null}>;
  websocket_connections: number; websocket_pongs: number; websocket_events: number;
  event_delivery_status: 'UNKNOWN' | 'EVENTS_OBSERVED';
  submission_status: 'BLOCKED'; submission_reason: 'LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE';
  ledger_status: 'UNKNOWN'; authoritative: false;
}
export type DemoPrivateCheckSnapshot = {status: 'AVAILABLE' | 'STALE'; report: DemoPrivateCheckReport} |
  {status: 'NO_SNAPSHOT' | 'DATA_BLOCKED' | 'CORRUPT_OR_UNREADABLE'; report: null};

export interface DemoAccountBaselineReport {
  schema: 'DEMO_ACCOUNT_BASELINE_V1'; environment: 'BINANCE_USDM_DEMO'; capture_hash: string; source_sha256: string;
  observed_at_ms: number; completed_at_ms: number;
  inventory_status: 'STABLE_OBSERVATIONS' | 'CHANGED_DURING_CAPTURE' | 'CAPTURE_FAILED' | 'NOT_REQUESTED' | 'STALE';
  capture_reason: string; issues: string[]; nonzero_positions: number | null; flat_account: boolean | null;
  account_config: {canTrade: boolean; dualSidePosition: boolean; multiAssetsMargin: boolean} | null;
  assets: Array<{asset: string; observed_wallet: string; observed_cross_wallet: string; observed_unrealized: string;
    observed_margin_balance: string; observed_available: string; account_identity_residual: string}>;
  positions: Array<{symbol: string; position_side: 'BOTH' | 'LONG' | 'SHORT'; margin_asset: string; quantity: string;
    observed_entry_price: string; observed_mark_price: string; observed_unrealized: string; estimated_entry_notional: string;
    basis_pnl_residual: string; isolated_wallet: string; margin_mode: 'CROSSED' | 'ISOLATED' | null; leverage: number | null;
    ownership: 'PREEXISTING_ACCOUNT_POSITION'}>;
  open_orders: Array<{symbol: string; orderId: string; clientOrderId: string; positionSide: 'BOTH' | 'LONG' | 'SHORT';
    side: 'BUY' | 'SELL'; status: 'NEW' | 'PARTIALLY_FILLED' | 'PENDING_CANCEL'; origQty: string; executedQty: string}>;
  comparisons: Array<{kind: string; status: 'MATCHED_OBSERVATIONS' | 'DIFFERENT'}>;
  atomic_snapshot_verified: false; new_strategy_pnl: null; ledger_reconciliation: 'UNKNOWN'; submission_enabled: false; authoritative: false;
  read_intervals: Array<{kind: string; started_at_ms: number; received_at_ms: number; response_sha256: string}>;
}
export type DemoAccountBaselineSnapshot = {status: 'AVAILABLE'; report: DemoAccountBaselineReport} |
  {status: 'NO_SNAPSHOT' | 'DATA_BLOCKED' | 'CORRUPT_OR_UNREADABLE'; report: null};

export interface DemoOpeningAssessment {
  schema: 'DEMO_OPENING_ASSESSMENT_V1'; scope: 'SINGLE_QUOTE_ACCOUNT_BOOK_TECHNICAL_PROJECTION';
  status: 'INPUTS_INCOMPLETE' | 'DECLARED_INPUTS_READY'; capture_hash: string; account_ref_sha256: string;
  opening_at_ms: number; quote_asset: 'USDT' | 'USDC'; initial_collateral: string; initial_position: string;
  positions_count: number; other_quote_positions_count: number;
  missing_basis_symbols: string[]; missing_contract_symbols: string[]; issues: string[];
  basis_policy: 'CALLER_DECLARED_EXACT_ENTRY_NOTIONAL'; source_verified: false;
  atomic_snapshot_verified: false; submission_enabled: false; venue_cash_reconciliation: 'UNKNOWN';
  positions: Array<{symbol: string; position_side: 'BOTH' | 'LONG' | 'SHORT'; quantity: string; margin_mode: 'CROSSED' | 'ISOLATED' | null;
    entry_notional: string | null; basis_source_sha256: string | null; contract_available: boolean}>;
}
