const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const integer=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0;
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
export function cancelStatusValid(v:unknown,asOf:number):boolean {
  if(!object(v))return false;
  if(['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status)))return v.report===null;
  const r=v.report;
  if(v.status!=='AVAILABLE'||!object(r)||!['DEMO_CANCEL_STATUS_V1','DEMO_GOLD_CANCEL_STATUS_V1'].includes(String(r.schema))||r.environment!=='BINANCE_USDM_DEMO'||r.scope!=='TECHNICAL_VALIDATION_ONLY'||
    !['RESERVED_AWAITING_ATTEMPTS','ORIGINAL_ID_QUERY_RECOVERY_REQUIRED'].includes(String(r.phase))||
    !integer(r.observed_at_ms)||!r.observed_at_ms||r.observed_at_ms>asOf||
    !['order_count','claimed_orders','response_orders','unresolved_responses'].every(k=>integer(r[k])&&Number(r[k])<=20)||
    !Array.isArray(r.orders)||!r.orders.length||r.orders.length!==r.order_count||
    !['source_verified','new_order_submission_enabled','economic_authority','cash_reconciled'].every(k=>r[k]===false)||
    !['grant_sha256','reservation_sha256','snapshot_sha256'].every(k=>hash(r[k])))return false;
  let claimed=0,responses=0,unknown=0;
  const clients=new Set(),venues=new Set(),symbols=new Set();
  for(const row of r.orders){
    if(!object(row)||!(r.schema==='DEMO_GOLD_CANCEL_STATUS_V1'?['XAUUSDT']:['BTCUSDC','BTCUSDT']).includes(String(row.symbol))||typeof row.client_order_id!=='string'||!/^QD[A-Za-z0-9_-]{8,34}$/.test(row.client_order_id)||
      clients.has(row.client_order_id)||typeof row.venue_order_id!=='string'||! /^[1-9][0-9]{0,18}$/.test(row.venue_order_id)||
      BigInt(row.venue_order_id)>9223372036854775807n||venues.has(row.venue_order_id)||
      !integer(row.last_observed_at_ms)||!row.last_observed_at_ms||row.last_observed_at_ms>r.observed_at_ms||
      !['claim_sha256','result_sha256','response_sha256'].every(k=>row[k]===null||hash(row[k])))return false;
    clients.add(row.client_order_id);venues.add(row.venue_order_id);symbols.add(row.symbol);
    if(row.status==='NOT_CLAIMED'){
      if(!['claim_sha256','result_sha256','response_sha256','venue_status','executed_quantity'].every(k=>row[k]===null))return false;
    }else if(['CLAIMED_RESPONSE_UNKNOWN','RESPONSE_OBSERVED'].includes(String(row.status))){
      if(!hash(row.claim_sha256))return false;
      claimed++;responses+=row.result_sha256!==null?1:0;
      if(row.status==='CLAIMED_RESPONSE_UNKNOWN'){
        unknown++;
        if(row.venue_status!==null||row.executed_quantity!==null||row.result_sha256===null&&row.response_sha256!==null)return false;
      }else if(!hash(row.result_sha256)||!hash(row.response_sha256)||
        !['NEW','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','EXPIRED_IN_MATCH'].includes(String(row.venue_status))||
        typeof row.executed_quantity!=='string'||!/^\d{1,24}(\.\d{1,24})?$/.test(row.executed_quantity)||
        row.venue_status==='NEW'&&/[1-9]/.test(row.executed_quantity)||
        ['FILLED','PARTIALLY_FILLED'].includes(String(row.venue_status))&&!/[1-9]/.test(row.executed_quantity))return false;
    }else return false;
  }
  return symbols.size===1&&claimed===r.claimed_orders&&responses===r.response_orders&&unknown===r.unresolved_responses&&
    r.phase===(claimed?'ORIGINAL_ID_QUERY_RECOVERY_REQUIRED':'RESERVED_AWAITING_ATTEMPTS');
}
