export interface DemoCashChainReport {
  schema:'DEMO_CASH_CHAIN_STATUS_V1';environment:'BINANCE_USDM_DEMO';scope:'TECHNICAL_VALIDATION_ONLY';
  phase:'MATCHED_REPORTED_INPUTS'|'DIFFERENCE_FOUND'|'UNRESOLVED';observed_at_ms:number;
  account_ref_sha256:string|null;symbol:'BTCUSDC'|'BTCUSDT'|null;quote_asset:'USDC'|'USDT'|null;
  batch_count:number;continuations_matched:number;issues:string[];ledger_state_hash:string|null;strategy_pnl:null;
  source_verified:false;coverage_verified:false;authoritative:false;submission_enabled:false;cash_reconciled:false;
  report_sha256:string;cash_capture_sha256:string;replay_source_sha256:string;projection_source_sha256:string;
  initial_equity:string|null;canonical_wallet:string|null;reported_wallet:string|null;wallet_residual:string|null;
  quantity:string|null;entry_notional:string|null;fee:string|null;funding:string|null;external_cash_flow:string|null;
  batches:Array<{manifest_hash:string;journal_tail:string;orders_count:number;trades_count:number;start_ms:number;end_ms:number}>;
}
export type DemoCashChainSnapshot={status:'AVAILABLE';report:DemoCashChainReport}|
  {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE';report:null};
