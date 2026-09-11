import {RESEARCH_CONSOLE_API_BASE} from './research-console-api';
import type {BinanceDemoPayload} from '../types/binance-demo';
import {executionSnapshotValid, orderRecoveryValid} from './demo-execution-contract';

const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v);
const time = (v: unknown) => integer(v) && v > 0;
const nullableTime = (v: unknown) => v === null || time(v);
const decimal = (v: unknown) => typeof v === 'string' && /^-?\d{1,30}(\.\d{1,24})?$/.test(v);
const reason = (v: unknown) => typeof v === 'string' && /^[A-Z_]{1,80}$/.test(v);
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
function privateChecksValid(v: unknown, asOf: number) {
  if (!object(v)) return false;
  if (['NO_SNAPSHOT', 'DATA_BLOCKED', 'CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report === null;
  const r = v.report;
  const names = ['credentials', 'public', 'account', 'BTCUSDT_orders', 'BTCUSDT_trades',
    'BTCUSDC_orders', 'BTCUSDC_trades', 'listen_key', 'websocket'];
  if (!['AVAILABLE', 'STALE'].includes(String(v.status)) || !object(r) ||
      r.schema_version !== 'binance-demo-private-check.v1' || r.environment !== 'BINANCE_USDM_DEMO' ||
      !hash(r.source_sha256) || !time(r.observed_at_ms) || !time(r.completed_at_ms) ||
      Number(r.completed_at_ms) < Number(r.observed_at_ms) || Number(r.completed_at_ms) > asOf ||
      Number(r.completed_at_ms) - Number(r.observed_at_ms) > 130000 ||
      (v.status === 'STALE') !== (asOf - Number(r.observed_at_ms) > 120000) ||
      r.authoritative !== false || r.submission_status !== 'BLOCKED' ||
      r.submission_reason !== 'LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE' || r.ledger_status !== 'UNKNOWN' ||
      !['NOT_REQUESTED', 'CHECKS_PASSED', 'FAILED'].includes(String(r.connectivity_status)) ||
      !['UNKNOWN', 'EVENTS_OBSERVED'].includes(String(r.event_delivery_status)) ||
      !['websocket_connections', 'websocket_pongs', 'websocket_events'].every(k => integer(r[k]) && Number(r[k]) >= 0) ||
      Number(r.websocket_connections) > 2 || Number(r.websocket_pongs) > 2 || Number(r.websocket_events) > 200 ||
      Number(r.websocket_pongs) > Number(r.websocket_connections) ||
      (Number(r.websocket_events) > 0) !== (r.event_delivery_status === 'EVENTS_OBSERVED') ||
      !Array.isArray(r.steps) || r.steps.length > 10 || !r.steps.every(s => object(s) &&
        [...names, 'worker'].includes(String(s.name)) && ['PASSED', 'FAILED'].includes(String(s.status)) && reason(s.reason) &&
        (s.status === 'PASSED') === (s.reason === 'CHECK_SUCCEEDED') &&
        (s.rows === null || integer(s.rows) && s.rows >= 0 && s.rows <= 1000))) return false;
  if (new Set(r.steps.map(s => s.name)).size !== r.steps.length) return false;
  if (r.connectivity_status === 'CHECKS_PASSED') return r.websocket_connections === 2 && r.websocket_pongs === 2 &&
    r.steps.length === names.length && r.steps.every((s, i) => s.name === names[i] && s.status === 'PASSED');
  if (r.connectivity_status === 'NOT_REQUESTED') return r.steps.length === 0 && r.websocket_connections === 0 &&
    r.websocket_pongs === 0 && r.websocket_events === 0;
  return r.steps.some(s => s.status === 'FAILED');
}
function publicValid(v: unknown) {
  if (v === null) return true;
  if (!object(v) || !['READY', 'FAILED'].includes(String(v.status)) || !reason(v.reason) ||
      !nullableTime(v.server_time_ms) || !(v.round_trip_ms === null || integer(v.round_trip_ms) && v.round_trip_ms >= 0) ||
      !(v.clock_offset_ms === null || integer(v.clock_offset_ms)) || !Array.isArray(v.symbols)) return false;
  if (v.status === 'FAILED') return v.symbols.length === 0;
  return time(v.server_time_ms) && integer(v.round_trip_ms) && v.round_trip_ms <= 5000 &&
    integer(v.clock_offset_ms) && Math.abs(v.clock_offset_ms) <= 2000 && v.symbols.length === 2 &&
    v.symbols.every((s, i) => object(s) && s.symbol === ['BTCUSDT', 'BTCUSDC'][i] &&
      s.quote_asset === ['USDT', 'USDC'][i] && s.margin_asset === s.quote_asset &&
      s.contract_type === 'PERPETUAL' && s.status === 'TRADING' &&
      ['tick_size', 'step_size', 'min_qty', 'min_notional'].every(k => decimal(s[k]) && Number(s[k]) > 0));
}
function accountValid(v: unknown) {
  if (v === null) return true;
  if (!object(v) || !['READY', 'NOT_CHECKED', 'FAILED', 'SKIPPED'].includes(String(v.status)) ||
      !reason(v.reason) || !nullableTime(v.observed_at_ms) || !Array.isArray(v.assets) || !Array.isArray(v.positions)) return false;
  if (v.status !== 'READY') return v.observed_at_ms === null && v.assets.length === 0 && v.positions.length === 0;
  return time(v.observed_at_ms) && v.reason === 'ACCOUNT_READ_SUCCEEDED' && v.assets.length <= 500 && v.positions.length <= 5000 &&
    v.assets.every(a => object(a) && typeof a.asset === 'string' && /^[A-Z0-9]{1,16}$/.test(a.asset) &&
      [a.wallet_balance, a.available_balance, a.unrealized_profit].every(decimal)) &&
    v.positions.every(p => object(p) && typeof p.symbol === 'string' && /^[A-Z0-9_]{1,32}$/.test(p.symbol) &&
      ['BOTH', 'LONG', 'SHORT'].includes(String(p.position_side)) && [p.quantity, p.unrealized_profit].every(decimal));
}
export function parseDemoPayload(v: unknown): BinanceDemoPayload {
  if (!object(v) || v.read_only !== true || v.authoritative !== false || v.submission_enabled !== false ||
      v.environment !== 'BINANCE_USDM_DEMO' || v.rest_base !== 'https://demo-fapi.binance.com' ||
      v.data_provenance !== 'DEMO_CONNECTIVITY_DIAGNOSTIC' || typeof v.source !== 'string' || v.api_version !== '0.1.0' ||
      !['REAL', 'STALE', 'NO_SNAPSHOT', 'DATA_BLOCKED', 'CORRUPT_OR_UNREADABLE'].includes(String(v.snapshot_status)) ||
      !(v.snapshot_id === null || hash(v.snapshot_id)) || !Array.isArray(v.snapshot_ids) || !v.snapshot_ids.every(hash) ||
      !object(v.data_quality) || v.data_quality.status !== v.snapshot_status || v.data_quality.provenance !== v.data_provenance ||
      !reason(v.data_quality.reason) || !time(v.as_of_ms) || !nullableTime(v.observed_at_ms) ||
      !(v.age_ms === null || integer(v.age_ms) && v.age_ms >= 0) || v.max_age_ms !== 120000 ||
      !publicValid(v.public) || !accountValid(v.account) || !executionSnapshotValid(v.execution, v.as_of_ms as number) || !orderRecoveryValid(v.order_recovery) ||
      !privateChecksValid(v.private_checks, v.as_of_ms as number) || v.execution_reason !== 'LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE' ||
      v.order_stream_status !== 'NOT_CHECKED' || v.ledger_reconciliation_status !== 'NOT_CHECKED') {
    throw new Error('模拟盘数据契约不匹配');
  }
  const result = v as unknown as BinanceDemoPayload;
  if ((result.public === null) !== (result.account === null) ||
      (result.public !== null && (result.observed_at_ms === null || result.age_ms !== result.as_of_ms - result.observed_at_ms)) ||
      (result.snapshot_status === 'REAL' && (result.public?.status !== 'READY' || result.age_ms === null || result.age_ms > result.max_age_ms)) ||
      (result.account?.status === 'READY' && (result.public?.status !== 'READY' || result.account.observed_at_ms! > result.as_of_ms))) {
    throw new Error('模拟盘数据契约不匹配');
  }
  return result;
}
export async function fetchBinanceDemo(signal: AbortSignal) {
  if (!RESEARCH_CONSOLE_API_BASE) throw new Error('研究数据 API 尚未配置');
  const response = await fetch(new URL('/api/v1/console/binance-demo', RESEARCH_CONSOLE_API_BASE), {method: 'GET', signal});
  if (!response.ok) throw new Error(`模拟盘快照读取失败 (${response.status})`);
  return parseDemoPayload(await response.json());
}
