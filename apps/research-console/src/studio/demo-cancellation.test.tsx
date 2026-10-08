import {afterEach,expect,it} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import {DemoCancellation} from './DemoCancellation';
import {cancelStatusValid} from '@/lib/api/demo-cancel-status-contract';
import type {DemoCancelStatusReport} from '@/lib/types/demo-cancel-status';
afterEach(cleanup);
const now=1800000000000;
function report():DemoCancelStatusReport {
  return {schema:'DEMO_CANCEL_STATUS_V1',environment:'BINANCE_USDM_DEMO',scope:'TECHNICAL_VALIDATION_ONLY',
    phase:'ORIGINAL_ID_QUERY_RECOVERY_REQUIRED',observed_at_ms:now,order_count:1,claimed_orders:1,response_orders:1,unresolved_responses:0,
    grant_sha256:'a'.repeat(64),reservation_sha256:'b'.repeat(64),snapshot_sha256:'c'.repeat(64),
    source_verified:false,new_order_submission_enabled:false,economic_authority:false,cash_reconciled:false,
    orders:[{symbol:'BTCUSDC',client_order_id:'QDtest000001',venue_order_id:'9007199254740993',status:'RESPONSE_OBSERVED',
      claim_sha256:'d'.repeat(64),result_sha256:'e'.repeat(64),response_sha256:'f'.repeat(64),
      venue_status:'CANCELED',executed_quantity:'0.000000000000000001',last_observed_at_ms:now}]};
}
it('keeps cancelled responses separate from actual OMS or cash completion',()=>{
  const r=report();expect(cancelStatusValid({status:'AVAILABLE',report:r},now)).toBe(true);
  render(<DemoCancellation snapshot={{status:'AVAILABLE',report:r}} refreshFailed/>);
  expect(screen.getByText('撤单后等待原编号查询与私有回执')).toBeInTheDocument();
  expect(screen.getByText('9007199254740993')).toBeInTheDocument();
  expect(screen.getByText('0.000000000000000001')).toBeInTheDocument();
  expect(screen.getByText(/刷新失败/)).toBeInTheDocument();
  expect(screen.queryByText('撤单已完成')).not.toBeInTheDocument();
  for(const change of [{phase:'COMPLETED'},{unresolved_responses:1},{claimed_orders:0},{response_orders:0},{cash_reconciled:true},
    {orders:[{...r.orders[0],venue_order_id:Number('9007199254740993')}]},{observed_at_ms:now+1},
    {orders:[{...r.orders[0],venue_status:'FILLED',executed_quantity:'0'}]},
    {orders:[{...r.orders[0],venue_status:'NEW'}]}]){
    expect(cancelStatusValid({status:'AVAILABLE',report:{...r,...change}},now)).toBe(false);
  }
});
it('shows durable claims without a response as unknown and unclaimed commands separately',()=>{
  const r=report();r.orders[0]={...r.orders[0],status:'CLAIMED_RESPONSE_UNKNOWN',result_sha256:null,response_sha256:null,venue_status:null,executed_quantity:null};
  r.response_orders=0;r.unresolved_responses=1;
  expect(cancelStatusValid({status:'AVAILABLE',report:r},now)).toBe(true);
  const v=render(<DemoCancellation snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('已保留发送标记，结果未知')).toBeInTheDocument();
  r.phase='RESERVED_AWAITING_ATTEMPTS';r.claimed_orders=0;r.unresolved_responses=0;
  r.orders[0]={...r.orders[0],status:'NOT_CLAIMED',claim_sha256:null};
  expect(cancelStatusValid({status:'AVAILABLE',report:r},now)).toBe(true);
  v.rerender(<DemoCancellation snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('撤单批准已保留，尚无发送标记')).toBeInTheDocument();
});
it('does not turn missing or corrupt records into zero successful cancellations',()=>{
  const v=render(<DemoCancellation refreshFailed={false}/>);
  expect(screen.getByText('暂无撤单执行记录')).toBeInTheDocument();
  v.rerender(<DemoCancellation snapshot={{status:'CORRUPT_OR_UNREADABLE',report:null}} refreshFailed={false}/>);
  expect(screen.getByText('撤单记录无法验证')).toBeInTheDocument();
});

it('renders gold with an explicit protocol and rejects cross-market relabeling',()=>{
  const r=report();r.schema='DEMO_GOLD_CANCEL_STATUS_V1';r.orders[0].symbol='XAUUSDT';
  expect(cancelStatusValid({status:'AVAILABLE',report:r},now)).toBe(true);
  render(<DemoCancellation snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('XAUUSDT')).toBeInTheDocument();
  expect(screen.queryByText('撤单已完成')).not.toBeInTheDocument();
  expect(cancelStatusValid({status:'AVAILABLE',report:{...r,schema:'DEMO_CANCEL_STATUS_V1'}},now)).toBe(false);
  r.orders[0].symbol='BTCUSDT';
  expect(cancelStatusValid({status:'AVAILABLE',report:r},now)).toBe(false);
});
