const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const exact=(v:Record<string,unknown>,keys:string)=>Object.keys(v).sort().join(' ')===keys.split(' ').sort().join(' ');
const integer=(v:unknown,max=8640000000000000):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0&&v<=max;
const safety='source_verified submission_enabled economic_authority cross_namespace_atomic production_restore_performed';
const names=['research','executor','issuer','controller'];
const summaryFields='schema namespace observed_at_ms status recovery_point_at_ms last_verified_at_ms rpo_seconds source_capture_age_ms capture_slots_remaining archive_bytes_reverified automatic_history_deletion verified_cycles pending_cycles failed_cycles interrupted_cycles seal_failures '+safety;
function summaryValid(r:unknown,namespace:string,at:number):boolean {
  if (!object(r)||!exact(r,summaryFields)||r.schema!=='DEMO_CHECKPOINT_CYCLE_STATUS_V1'||r.namespace!==namespace||
    !(safety+' archive_bytes_reverified automatic_history_deletion').split(' ').every(k=>r[k]===false)||
    !integer(r.observed_at_ms)||r.observed_at_ms<=0||r.observed_at_ms>at||
    !integer(r.rpo_seconds,7*86400)||r.rpo_seconds<60||!integer(r.seal_failures,4096)) return false;
  const counts=['verified_cycles','pending_cycles','failed_cycles','interrupted_cycles','capture_slots_remaining'];
  if (!counts.every(k=>integer(r[k],512))||counts.reduce((n,k)=>n+Number(r[k]),0)!==512) return false;
  if (r.verified_cycles===0) return r.status==='NO_VERIFIED_BACKUP'&&r.recovery_point_at_ms===null&&
    r.last_verified_at_ms===null&&r.source_capture_age_ms===null;
  if (!integer(r.recovery_point_at_ms)||!integer(r.last_verified_at_ms)||!integer(r.source_capture_age_ms)||
    r.recovery_point_at_ms<=0||r.recovery_point_at_ms>r.last_verified_at_ms||r.last_verified_at_ms>r.observed_at_ms||
    r.source_capture_age_ms!==r.observed_at_ms-r.recovery_point_at_ms) return false;
  return r.status===(r.source_capture_age_ms>r.rpo_seconds*1000?'STALE':'CURRENT');
}
export function backupStatusValid(value:unknown,asOf:number):boolean {
  if (!object(value)||!exact(value,'status report')) return false;
  if (['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(value.status))) return value.report===null;
  const r=value.report;
  if (!['AVAILABLE','STALE'].includes(String(value.status))||!object(r)||
    !exact(r,'schema environment scope source_commit observed_at_ms phase components '+safety)||
    r.schema!=='DEMO_BACKUP_STATUS_V1'||r.environment!=='BINANCE_USDM_DEMO'||r.scope!=='TECHNICAL_VALIDATION_ONLY'||
    !safety.split(' ').every(k=>r[k]===false)||typeof r.source_commit!=='string'||!/^[a-f0-9]{40}$/.test(r.source_commit)||
    !integer(r.observed_at_ms)||r.observed_at_ms<=0||r.observed_at_ms>asOf||
    (value.status==='STALE')!==(asOf-r.observed_at_ms>1800000)||!Array.isArray(r.components)||r.components.length!==4) return false;
  const at=r.observed_at_ms;
  let current=0;
  if (!r.components.every((row:unknown,index:number)=>{
    if (!object(row)||!exact(row,'namespace status summary capture_timer seal_timer')||row.namespace!==names[index]||
      !['ACTIVE','INACTIVE','NOT_INSTALLED','UNKNOWN'].includes(String(row.capture_timer))||
      !['ACTIVE','INACTIVE','NOT_INSTALLED','UNKNOWN'].includes(String(row.seal_timer))) return false;
    if (row.status!=='OBSERVED') return ['NO_SPOOL','NO_BACKUP_DIRECTORY','UNREADABLE','BACKUP_VOLUME_UNAVAILABLE'].includes(String(row.status))&&row.summary===null;
    if (!summaryValid(row.summary,names[index],at)) return false;
    if (object(row.summary)&&row.summary.status==='CURRENT') current+=1;
    return true;
  })) return false;
  return r.phase===(current===4?'ALL_RECOVERY_POINTS_CURRENT':current?'PARTIAL_RECOVERY_POINTS':'NO_CURRENT_RECOVERY_POINTS');
}
