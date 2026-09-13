export type DemoMarketSymbol='BTCUSDC'|'BTCUSDT';
export interface DemoMarketInputsReport {
  schema:'DEMO_MARKET_INPUTS_VIEW_V1';environment:'BINANCE_USDM_DEMO';symbol:DemoMarketSymbol;
  capture_status:'CAPTURED'|'FAILED';observed_at_ms:number;completed_at_ms:number;
  source_sha256:string;input_sha256:string;source_verified:false;submission_enabled:false;economic_authority:false;
  failure:{kind:string;reason:'REQUEST_FAILED'|'RATE_LIMITED'|'RESPONSE_INVALID'|'INPUTS_INVALID'|'CAPTURE_BUDGET_EXHAUSTED'}|null;
  metrics:{daily_volume_base:string;instrument_vol:string;volatility_policy:'SQRT_SUM_LOG_RETURNS_SQUARED_60X1M_CEIL18_V1';
    last_close_ms:number;ticker_close_ms:number;return_intervals:60;
    bars:Array<{close_ms:number;open:string;high:string;low:string;close:string;volume_base:string}>}|null;
}
export interface DemoMarketInputsSnapshot {
  status:'AVAILABLE'|'STALE'|'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE';report:DemoMarketInputsReport|null;
}
export type DemoMarketInputsSnapshots=Record<DemoMarketSymbol,DemoMarketInputsSnapshot>;
