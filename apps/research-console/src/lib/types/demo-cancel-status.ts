export interface DemoCancelStatusReport {
  schema:'DEMO_CANCEL_STATUS_V1';environment:'BINANCE_USDM_DEMO';scope:'TECHNICAL_VALIDATION_ONLY';
  phase:'RESERVED_AWAITING_ATTEMPTS'|'ORIGINAL_ID_QUERY_RECOVERY_REQUIRED';observed_at_ms:number;
  order_count:number;claimed_orders:number;response_orders:number;unresolved_responses:number;
  grant_sha256:string;reservation_sha256:string;snapshot_sha256:string;
  source_verified:false;new_order_submission_enabled:false;economic_authority:false;cash_reconciled:false;
  orders:Array<{symbol:'BTCUSDC'|'BTCUSDT';client_order_id:string;venue_order_id:string;
    status:'NOT_CLAIMED'|'CLAIMED_RESPONSE_UNKNOWN'|'RESPONSE_OBSERVED';claim_sha256:string|null;
    result_sha256:string|null;response_sha256:string|null;venue_status:string|null;executed_quantity:string|null;last_observed_at_ms:number}>;
}
export type DemoCancelStatusSnapshot={status:'AVAILABLE';report:DemoCancelStatusReport}|
  {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE';report:null};
