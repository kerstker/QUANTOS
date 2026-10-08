const object=(v:unknown):v is Record<string,unknown>=>!!v && typeof v==='object' && !Array.isArray(v);
const integer=(v:unknown):v is number=>typeof v==='number' && Number.isSafeInteger(v);
export const activeFunctionalPhases=['OPENING_CASH_CAPTURE','OPEN_IOC','RECEIPT_RECOVERY','REDUCE_ONLY_IOC','CASH_RECONCILIATION'];
const terminal=['DEMO_CYCLE_COMPLETED','RECOVERY_REQUIRED','NO_TRADING_EVIDENCE','CASH_RECONCILIATION_REQUIRED','REDUCTION_INCOMPLETE','CYCLE_NOT_COMPLETED','REDUCTION_AUTHORIZATION_REQUIRED','EVIDENCE_REPAIR_REQUIRED','MANUAL_ACCOUNT_RECONCILED'];
export function functionalStatusValid(v:unknown,asOf:number):boolean {
  if (!object(v) || Object.keys(v).sort().join(' ')!=='report status') return false;
  if (['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report===null;
  const r=v.report;
  if (!['AVAILABLE','STALE'].includes(String(v.status)) || !object(r) ||
    Object.keys(r).sort().join(' ')!=='schema environment scope phase observed_at_ms source_commit cycle_sha256 orders_attempted final_quantity fill_count cash_status cash_reconciled economic_authority submission_enabled'.split(' ').sort().join(' ') ||
    r.schema!=='DEMO_FUNCTIONAL_STATUS_V1' || r.environment!=='BINANCE_USDM_DEMO' || r.scope!=='TECHNICAL_VALIDATION_ONLY' ||
    ![...activeFunctionalPhases,...terminal].includes(String(r.phase)) || r.economic_authority!==false || r.submission_enabled!==false ||
    !integer(r.observed_at_ms) || r.observed_at_ms<=0 || r.observed_at_ms>asOf ||
    (v.status==='STALE')!==(activeFunctionalPhases.includes(String(r.phase)) && asOf-r.observed_at_ms>120000) ||
    typeof r.source_commit!=='string' || !/^[a-f0-9]{40}$/.test(r.source_commit) ||
    typeof r.cycle_sha256!=='string' || !/^[a-f0-9]{64}$/.test(r.cycle_sha256) ||
    !(r.orders_attempted===null || integer(r.orders_attempted) && r.orders_attempted>=0 && r.orders_attempted<=3) ||
    !(r.fill_count===null || integer(r.fill_count) && r.fill_count>=0 && r.fill_count<=10000) ||
    !(r.final_quantity===null || typeof r.final_quantity==='string' && /^\d{1,20}(\.\d{1,18})?$/.test(r.final_quantity)) ||
    ![null,'MATCHED_REPORTED_INPUTS','MATCHED_REPORTED_ACCOUNT','UNRESOLVED','DIFFERENCE_FOUND'].includes(r.cash_status as string|null)) return false;
  const done=r.phase==='DEMO_CYCLE_COMPLETED';
  const manual=r.phase==='MANUAL_ACCOUNT_RECONCILED';
  if (r.cash_reconciled!==(done || manual)) return false;
  if(manual) return r.orders_attempted===1 && integer(r.fill_count) && r.fill_count>=2 &&
    typeof r.final_quantity==='string' && /^0+(\.0+)?$/.test(r.final_quantity) && r.cash_status==='MATCHED_REPORTED_ACCOUNT';
  return !done || integer(r.orders_attempted) && r.orders_attempted>=2 && integer(r.fill_count) && r.fill_count>=2 &&
    typeof r.final_quantity==='string' && /^0+(\.0+)?$/.test(r.final_quantity) && r.cash_status==='MATCHED_REPORTED_INPUTS';
}
