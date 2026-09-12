import {executorComponents} from '../types/demo-executor-observation';
const object = (v:unknown):v is Record<string,unknown> => !!v && typeof v==='object' && !Array.isArray(v);
const exact = (v:Record<string,unknown>,keys:string) => Object.keys(v).sort().join(' ')===keys.split(' ').sort().join(' ');
const time = (v:unknown):v is number => typeof v==='number' && Number.isSafeInteger(v) && v>0 && v<=8640000000000000;
const hash = (v:unknown) => typeof v==='string' && /^[a-f0-9]{64}$/.test(v);
const phases:Record<string,string[]> = {
  private_stream:['CONNECTING','CONNECTED','DISCONNECTED','STOPPED','STALE'],
  origin_BTCUSDC:['OBSERVED_FLAT_QUOTE','UNRESOLVED'],origin_BTCUSDT:['OBSERVED_FLAT_QUOTE','UNRESOLVED'],
  resident:['NO_RESERVATIONS','INPUTS_UNRESOLVED','WAITING_QUERY_INTERVAL','RESIDENT_BUSY','QUERY_RECOVERY_REQUIRED','QUERY_OBSERVED_RECEIPTS_REQUIRED','OMS_TERMINAL_CASH_SEPARATE'],
  oms:['INPUTS_UNRESOLVED','AWAITING_PROJECTION','PROJECTION_INTERRUPTED','OMS_TERMINAL_PROJECTED'],
  cancellation:['RESERVED_AWAITING_ATTEMPTS','ORIGINAL_ID_QUERY_RECOVERY_REQUIRED'],
};
export function executorObservationValid(v:unknown,asOf:number):boolean {
  if (!object(v) || !exact(v,'status report')) return false;
  if (['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report===null;
  const r=v.report;
  if (!['AVAILABLE','STALE'].includes(String(v.status)) || !object(r) ||
    !exact(r,'schema environment scope phase observed_at_ms account_ref_sha256 source_commit components source_verified submission_enabled cash_reconciled economic_authority') ||
    r.schema!=='DEMO_EXECUTOR_OBSERVATION_V1' || r.environment!=='BINANCE_USDM_DEMO' || r.scope!=='TECHNICAL_VALIDATION_ONLY' ||
    !time(r.observed_at_ms) || r.observed_at_ms>asOf || (v.status==='STALE')!==(asOf-r.observed_at_ms>120000) || !hash(r.account_ref_sha256) ||
    typeof r.source_commit!=='string' || !/^[a-f0-9]{40}$/.test(r.source_commit) ||
    !['source_verified','submission_enabled','cash_reconciled','economic_authority'].every(k=>r[k]===false) ||
    !Array.isArray(r.components) || r.components.length!==executorComponents.length) return false;
  const observed=r.observed_at_ms;
  if (!r.components.every((row:unknown,index:number)=>{
    if (!object(row) || !exact(row,'name status phase observed_at_ms report_sha256 counts') || row.name!==executorComponents[index]) return false;
    if (row.status!=='AVAILABLE') return ['NO_SNAPSHOT','CORRUPT_OR_UNREADABLE'].includes(String(row.status)) &&
      ['phase','observed_at_ms','report_sha256','counts'].every(k=>row[k]===null);
    if (!time(row.observed_at_ms) || row.observed_at_ms>observed || !hash(row.report_sha256) || !phases[String(row.name)].includes(String(row.phase))) return false;
    if (row.name!=='private_stream') return row.counts===null;
    const c=row.counts;
    if (!object(c) || !exact(c,'records messages unique_messages gaps') || !Object.values(c).every(n=>typeof n==='number' && Number.isSafeInteger(n) && n>=0 && n<=200000)) return false;
    return Number(c.records)>=1 && Number(c.unique_messages)<=Number(c.messages) && Number(c.messages)+Number(c.gaps)<Number(c.records);
  })) return false;
  return r.phase===(r.components.every(row=>row.status==='AVAILABLE')?'RECORDS_OBSERVED':'PARTIAL_RECORDS');
}
