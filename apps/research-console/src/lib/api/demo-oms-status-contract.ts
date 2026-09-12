const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const integer=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0;
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const amount=(v:unknown)=>typeof v==='string'&&/^\d{1,24}(\.\d{1,24})?$/.test(v);
export function omsStatusValid(v:unknown,asOf:number):boolean {
  if(!object(v))return false;
  if(['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status)))return v.report===null;
  const r=v.report;
  if(v.status!=='AVAILABLE'||!object(r)||r.schema!=='DEMO_OMS_STATUS_V1'||r.environment!=='BINANCE_USDM_DEMO'||r.scope!=='TECHNICAL_VALIDATION_ONLY'||
    !['INPUTS_UNRESOLVED','AWAITING_PROJECTION','PROJECTION_INTERRUPTED','OMS_TERMINAL_PROJECTED'].includes(String(r.phase))||
    !integer(r.observed_at_ms)||!r.observed_at_ms||r.observed_at_ms>asOf||
    !integer(r.order_count)||!r.order_count||r.order_count>20||!integer(r.matched_orders)||r.matched_orders>r.order_count||
    !Array.isArray(r.orders)||r.orders.length!==r.matched_orders||
    !['source_verified','authoritative','submission_enabled','economic_authority','cash_reconciled'].every(k=>r[k]===false)||
    !['reservation_sha256','proposal_sha256','plan_sha256','import_sha256'].every(k=>hash(r[k]))||
    !['manifest_hash','journal_tail'].every(k=>r[k]===null||hash(r[k]))||
    !Array.isArray(r.issues)||r.issues.length>64||new Set(r.issues).size!==r.issues.length||
    !r.issues.every(i=>typeof i==='string'&&/^[A-Z_]{1,80}$/.test(i)))return false;
  if(!r.orders.every(o=>object(o)&&typeof o.client_order_id==='string'&&/^[.A-Z:/a-z0-9_-]{1,36}$/.test(o.client_order_id)&&
    typeof o.venue_order_id==='string'&&/^[1-9][0-9]{0,18}$/.test(o.venue_order_id)&&BigInt(o.venue_order_id)<=9223372036854775807n&&
    ['FILLED','CANCELED','EXPIRED'].includes(String(o.terminal_status))&&amount(o.filled_quantity)&&
    (o.terminal_status!=='FILLED'||/[1-9]/.test(String(o.filled_quantity)))&&
    (o.query_native_status==='EXPIRED_IN_MATCH'?'EXPIRED':o.query_native_status)===o.terminal_status&&
    integer(o.private_event_count)&&o.private_event_count>0&&o.private_event_count<=10000&&
    integer(o.last_received_at_ms)&&o.last_received_at_ms>0&&o.last_received_at_ms<=Number(r.observed_at_ms)&&hash(o.query_record_sha256)))return false;
  if(new Set(r.orders.map(o=>o.client_order_id)).size!==r.orders.length||new Set(r.orders.map(o=>o.venue_order_id)).size!==r.orders.length)return false;
  if(r.phase==='INPUTS_UNRESOLVED')return r.issues.length>0&&r.projected_orders===null;
  if(r.matched_orders!==r.order_count||!hash(r.manifest_hash)||!hash(r.journal_tail))return false;
  if(r.phase==='PROJECTION_INTERRUPTED')return r.projected_orders===null&&r.issues.length===1&&r.issues[0]==='OMS_PROJECTION_INTERRUPTED';
  return r.issues.length===0&&(r.phase==='OMS_TERMINAL_PROJECTED'?integer(r.projected_orders)&&r.projected_orders===r.order_count:r.projected_orders===null);
}
