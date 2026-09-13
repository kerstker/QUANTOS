export interface DemoIssuerBudget {
  purpose:'submission'|'cancellation';
  reserved_grants:number; signed_grants:number; reserved_orders:number;
  max_grants:number; max_orders:number;
  reserved_notional:string|null; max_notional:string|null; remaining_notional:string|null;
}
export interface DemoIssuerReport {
  schema:'DEMO_ISSUER_STATUS_V1'; environment:'BINANCE_USDM_DEMO'; scope:'TECHNICAL_VALIDATION_ONLY';
  phase:'POLICY_INACTIVE'|'LIMIT_REACHED'|'PENDING_SIGNATURE'|'POLICY_OBSERVED';
  observed_at_ms:number; source_commit:string; account_ref_sha256:string;
  policy_id:string; policy_sha256:string; symbol:'BTCUSDC'|'BTCUSDT';
  valid_from_ms:number; valid_until_ms:number; key_status:'ACTIVE'|'REVOKED'|'MISSING'; key_material_status:'NOT_CHECKED';
  records:number; last_record_at_ms:number|null; journal_sha256:string|null; budgets:DemoIssuerBudget[];
  source_verified:false; submission_enabled:false; cash_reconciled:false; economic_authority:false;
}
export type DemoIssuerSnapshot = {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE';report:null} |
  {status:'AVAILABLE'|'STALE';report:DemoIssuerReport};
