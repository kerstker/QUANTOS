export interface DemoOmsStatusReport {
  schema:'DEMO_OMS_STATUS_V1';environment:'BINANCE_USDM_DEMO';scope:'TECHNICAL_VALIDATION_ONLY';
  phase:'INPUTS_UNRESOLVED'|'AWAITING_PROJECTION'|'PROJECTION_INTERRUPTED'|'OMS_TERMINAL_PROJECTED'|'OMS_ACCOUNT_RECONCILED';
  observed_at_ms:number;order_count:number;matched_orders:number;projected_orders:number|null;issues:string[];
  orders:Array<{client_order_id:string;venue_order_id:string;terminal_status:'FILLED'|'CANCELED'|'EXPIRED';
    filled_quantity:string;query_native_status:string;private_event_count:number;last_received_at_ms:number;query_record_sha256:string}>;
  reservation_sha256:string;proposal_sha256:string;plan_sha256:string;import_sha256:string;
  manifest_hash:string|null;journal_tail:string|null;
  source_verified:false;authoritative:false;submission_enabled:false;economic_authority:false;cash_reconciled:false;
}
export type DemoOmsStatusSnapshot={status:'AVAILABLE';report:DemoOmsStatusReport}|
  {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE';report:null};
