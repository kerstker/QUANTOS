import {afterEach,expect,it} from 'vitest';
import {cleanup,render,screen,within} from '@testing-library/react';
import {DemoOmsStatus} from './DemoOmsStatus';
import {omsStatusValid} from '@/lib/api/demo-oms-status-contract';
import type {DemoOmsStatusReport} from '@/lib/types/demo-oms-status';
afterEach(cleanup);
const now=1800000000000;
function report():DemoOmsStatusReport {
  return {schema:'DEMO_OMS_STATUS_V1',environment:'BINANCE_USDM_DEMO',scope:'TECHNICAL_VALIDATION_ONLY',
    phase:'OMS_TERMINAL_PROJECTED',observed_at_ms:now,order_count:1,matched_orders:1,projected_orders:1,issues:[],
    orders:[{client_order_id:'QDtest000001',venue_order_id:'9007199254740993',terminal_status:'FILLED',
      filled_quantity:'0.00200000000000000001',query_native_status:'FILLED',private_event_count:2,
      last_received_at_ms:now-5,query_record_sha256:'f'.repeat(64)}],
    reservation_sha256:'a'.repeat(64),proposal_sha256:'b'.repeat(64),plan_sha256:'c'.repeat(64),
    import_sha256:'d'.repeat(64),manifest_hash:'e'.repeat(64),journal_tail:'f'.repeat(64),
    source_verified:false,authoritative:false,submission_enabled:false,economic_authority:false,cash_reconciled:false};
}
it('shows unavailable observations separately from verified order recovery',()=>{
  const v=render(<DemoOmsStatus refreshFailed={false}/>);
  expect(screen.getByText('等待订单恢复记录')).toBeInTheDocument();
  v.rerender(<DemoOmsStatus snapshot={{status:'CORRUPT_OR_UNREADABLE',report:null}} refreshFailed={false}/>);
  expect(screen.getByText('订单恢复记录无法验证')).toBeInTheDocument();
  expect(screen.queryByText('订单状态恢复已完成')).not.toBeInTheDocument();
});
it.each([
  ['INPUTS_UNRESOLVED','输入未齐'],['AWAITING_PROJECTION','输入已配对，等待订单状态投影'],
  ['PROJECTION_INTERRUPTED','订单状态投影中断，等待恢复'],['OMS_TERMINAL_PROJECTED','订单状态恢复已完成'],
] as const)('renders %s without conflating private matching and durable completion',(phase,title)=>{
  const r=report();r.phase=phase;
  if(phase!=='OMS_TERMINAL_PROJECTED')r.projected_orders=null;
  if(phase==='INPUTS_UNRESOLVED'){r.issues=['ORIGINAL_ID_QUERY_MISSING'];r.orders=[];r.matched_orders=0;}
  if(phase==='PROJECTION_INTERRUPTED')r.issues=['OMS_PROJECTION_INTERRUPTED'];
  expect(omsStatusValid({status:'AVAILABLE',report:r},now)).toBe(true);
  render(<DemoOmsStatus snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText(title)).toBeInTheDocument();
  expect(screen.getByText(/现金核对请查看唯一账本/)).toBeInTheDocument();
  if(phase!=='OMS_TERMINAL_PROJECTED')expect(within(screen.getByText('已恢复订单').parentElement!).getByText('未知')).toBeInTheDocument();
  if(r.orders.length){expect(screen.getByText('9007199254740993')).toBeInTheDocument();expect(screen.getByText('0.00200000000000000001')).toBeInTheDocument();}
});
it('keeps failed refresh historical and rejects forged cash or incomplete completion',()=>{
  const r=report();render(<DemoOmsStatus snapshot={{status:'AVAILABLE',report:r}} refreshFailed/>);
  expect(screen.getByText(/刷新失败，以下保留上次成功读取/)).toBeInTheDocument();
  for(const bad of [{cash_reconciled:true},{projected_orders:0},{matched_orders:0},{orders:[]},
    {observed_at_ms:now+1},{source_verified:true},{phase:'AWAITING_PROJECTION'},
    {orders:[{...r.orders[0],venue_order_id:Number('9007199254740993')}]},
    {orders:[{...r.orders[0],filled_quantity:'0'}]}]){
    expect(omsStatusValid({status:'AVAILABLE',report:{...r,...bad}},now)).toBe(false);
  }
});
