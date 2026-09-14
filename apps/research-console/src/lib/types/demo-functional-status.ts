export type DemoFunctionalPhase='OPENING_CASH_CAPTURE'|'OPEN_IOC'|'RECEIPT_RECOVERY'|'REDUCE_ONLY_IOC'|'CASH_RECONCILIATION'|
  'DEMO_CYCLE_COMPLETED'|'RECOVERY_REQUIRED'|'NO_TRADING_EVIDENCE'|'CASH_RECONCILIATION_REQUIRED'|'REDUCTION_INCOMPLETE'|'CYCLE_NOT_COMPLETED';
export interface DemoFunctionalReport {
  schema:'DEMO_FUNCTIONAL_STATUS_V1';environment:'BINANCE_USDM_DEMO';scope:'TECHNICAL_VALIDATION_ONLY';
  phase:DemoFunctionalPhase;observed_at_ms:number;source_commit:string;cycle_sha256:string;
  orders_attempted:number|null;final_quantity:string|null;fill_count:number|null;
  cash_status:'MATCHED_REPORTED_INPUTS'|'UNRESOLVED'|'DIFFERENCE_FOUND'|null;cash_reconciled:boolean;
  economic_authority:false;submission_enabled:false;
}
export type DemoFunctionalSnapshot={status:'AVAILABLE'|'STALE';report:DemoFunctionalReport}|
  {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE';report:null};
