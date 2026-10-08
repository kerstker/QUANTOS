import type {DemoClockReport} from '../types/demo-clock';

const integer=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v);
export function clockReportValid(value:unknown,asOf:number):value is DemoClockReport {
  if (!value||typeof value!=='object'||Array.isArray(value)) return false;
  const r=value as Record<string,unknown>;
  return r.schema==='BINANCE_USDM_CLOCK_BRACKET_V1'&&
    r.policy==='ENDPOINT_ENVELOPE_WITH_DECLARED_500MS_INTERIOR_DRIFT_V1'&&
    typeof r.input_sha256==='string'&&/^[a-f0-9]{64}$/.test(r.input_sha256)&&
    integer(r.start_ms)&&r.start_ms>=0&&integer(r.end_ms)&&r.start_ms<r.end_ms&&r.end_ms-r.start_ms<=150000&&
    integer(r.available_at_ms)&&r.available_at_ms>=r.end_ms&&r.available_at_ms<=asOf&&r.available_at_ms-r.start_ms<=160000&&
    integer(r.offset_lower_ms)&&integer(r.offset_upper_ms)&&-5001<=r.offset_lower_ms&&
    r.offset_lower_ms<=r.offset_upper_ms&&r.offset_upper_ms<=5001&&
    r.local_domain==='CAPTURE_PROCESS_EPOCH_MS'&&r.venue_domain==='BINANCE_USDM_SERVER_EPOCH_MS'&&
    r.quantization_ms===1&&r.declared_interior_drift_ms===500&&r.source_verified===false&&
    r.interior_drift_verified===false&&r.exact_latency_ms===null&&r.causality_status==='COMPATIBILITY_ONLY_NOT_CAUSAL_CERTIFICATION';
}
