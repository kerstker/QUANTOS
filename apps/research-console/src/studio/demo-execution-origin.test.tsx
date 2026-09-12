import {afterEach, expect, it} from 'vitest';
import {cleanup, render, screen} from '@testing-library/react';
import {DemoExecutionOrigin} from './DemoExecutionOrigin';
import {executionOriginValid} from '@/lib/api/demo-execution-origin-contract';
import type {DemoExecutionOriginReport} from '@/lib/types/demo-execution-origin';

afterEach(cleanup);
const now=1800000000000;
function report(): DemoExecutionOriginReport {
  return {schema:'DEMO_EXECUTION_ORIGIN_V1',environment:'BINANCE_USDM_DEMO',status:'OBSERVED_FLAT_QUOTE',
    symbol:'BTCUSDC',quote_asset:'USDC',wallet:'5000',other_quote_positions:14,quote_positions:0,
    ordinary_orders:0,algo_orders:0,account_ref_sha256:'a'.repeat(64),observed_at_ms:now,completed_at_ms:now+20000,
    source_sha256:'b'.repeat(64),read_count:16,issues:[],failures:[],settlement_rounding:'UNVERIFIED',source_verified:false,
    atomic_snapshot_verified:false,submission_enabled:false,strategy_pnl:null};
}
it('shows the quote scope and preserves external positions without declaring execution ready',()=>{
  const r=report();
  r.wallet='5000.00000000';
  expect(executionOriginValid({status:'AVAILABLE',report:r},now+20000)).toBe(true);
  const view=render(<DemoExecutionOrigin snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('观察到目标币种空仓')).toBeInTheDocument();
  expect(screen.getByText('5000')).toBeInTheDocument();
  expect(screen.getByText(/其他计价币种的已有持仓：14/)).toBeInTheDocument();
  expect(screen.getByText(/结算舍入规则尚未核验/)).toBeInTheDocument();
  view.rerender(<DemoExecutionOrigin snapshot={{status:'AVAILABLE',report:r}} refreshFailed/>);
  expect(screen.queryByText('观察到目标币种空仓')).not.toBeInTheDocument();
  expect(screen.getByText(/刷新失败，以下为历史观察/)).toBeInTheDocument();
});
it('shows conditional orders and unknown inventory without substituting zeros',()=>{
  const r=report();r.status='UNRESOLVED';r.issues=['PREEXISTING_ALGO_ORDERS','CAPTURE_INCOMPLETE'];
  r.algo_orders=1;r.quote_positions=null;r.other_quote_positions=null;r.read_count=7;
  render(<DemoExecutionOrigin snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText(/账户存在条件挂单/)).toBeInTheDocument();
  expect(screen.getByText(/其他计价币种的已有持仓：未知/)).toBeInTheDocument();
});
it('rejects stale success, future inputs, unsupported authority and inconsistent success',()=>{
  const bad: Array<(r:DemoExecutionOriginReport)=>void>=[r=>{r.completed_at_ms=now+30001;},
    r=>{r.algo_orders=1;},r=>{r.read_count=15;},r=>{r.account_ref_sha256=null;},
    r=>{Object.assign(r,{submission_enabled:true});},r=>{Object.assign(r,{strategy_pnl:'0'});},
    r=>{r.quote_asset='USDT';},r=>{r.other_quote_positions=-1;},r=>{r.issues=['ORIGIN_STALE'];}];
  for (const mutate of bad) {const r=report();mutate(r);expect(executionOriginValid({status:'AVAILABLE',report:r},now+20000)).toBe(false);}
  expect(executionOriginValid({status:'AVAILABLE',report:report()},now+30001)).toBe(false);
  const stale=report();stale.status='UNRESOLVED';stale.issues=['ORIGIN_STALE'];
  expect(executionOriginValid({status:'AVAILABLE',report:stale},now+30001)).toBe(true);
});
it('explains absent and corrupt input without claiming a completed check',()=>{
  render(<DemoExecutionOrigin snapshot={{status:'CORRUPT_OR_UNREADABLE',report:null}} refreshFailed={false}/>);
  expect(screen.getByText('起点记录无法验证')).toBeInTheDocument();
  expect(screen.queryByText('观察到目标币种空仓')).not.toBeInTheDocument();
});
