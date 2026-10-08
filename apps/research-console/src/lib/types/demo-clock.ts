export interface DemoClockReport {
  schema:'BINANCE_USDM_CLOCK_BRACKET_V1';
  policy:'ENDPOINT_ENVELOPE_WITH_DECLARED_500MS_INTERIOR_DRIFT_V1';
  input_sha256:string; start_ms:number; end_ms:number; available_at_ms:number;
  offset_lower_ms:number; offset_upper_ms:number;
  local_domain:'CAPTURE_PROCESS_EPOCH_MS'; venue_domain:'BINANCE_USDM_SERVER_EPOCH_MS';
  quantization_ms:1; declared_interior_drift_ms:500;
  source_verified:false; interior_drift_verified:false; exact_latency_ms:null;
  causality_status:'COMPATIBILITY_ONLY_NOT_CAUSAL_CERTIFICATION';
}
