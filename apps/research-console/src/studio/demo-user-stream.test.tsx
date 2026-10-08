import {afterEach, expect, it} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {DemoUserStream} from './DemoUserStream';
import {userStreamValid} from '@/lib/api/demo-user-stream-contract';
import type {DemoUserStreamReport, DemoUserStreamRecord} from '@/lib/types/demo-user-stream';

afterEach(cleanup);
const at = 1789160000000;
function record(sequence: number, kind: string): DemoUserStreamRecord {
  return {sequence,kind,at_ms:at,record_hash:'c'.repeat(64),event_kind:null,event_ms:null,transaction_ms:null,
    reason:null,symbol:null,order_id:null,order_status:null,last_quantity:null,commission:null,commission_asset:null,account_reason:null};
}
function report(): DemoUserStreamReport {
  return {schema:'DEMO_USER_STREAM_V1',environment:'BINANCE_USDM_DEMO',account_ref_sha256:'a'.repeat(64),source_sha256:'b'.repeat(64),
    observed_at_ms:at,state:'CONNECTED',records:3,head_sha256:'c'.repeat(64),sessions:1,connections:1,heartbeats:1,
    messages:0,unique_messages:0,gaps:0,event_counts:{},last_gap:null,
    recent:[record(1,'SESSION_START'),record(2,'CONNECTED'),record(3,'HEARTBEAT')],integrity_scope:'RECENT_100_RECORDS_AND_MANIFEST',
    coverage_verified:false,cash_reconciliation:'UNKNOWN',submission_enabled:false,authoritative:false};
}
it('shows a connected but empty stream without claiming order delivery; failed refresh hides current status', () => {
  const r = report();
  expect(userStreamValid({status:'AVAILABLE',report:r},at)).toBe(true);
  const view = render(<DemoUserStream snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('持续连接中')).toBeInTheDocument();
  expect(screen.getByText(/尚未收到任何账户或订单事件/)).toBeInTheDocument();
  view.rerender(<DemoUserStream snapshot={{status:'AVAILABLE',report:r}} refreshFailed/>);
  expect(screen.queryByText('持续连接中')).not.toBeInTheDocument();
  expect(screen.getByText('当前状态待更新')).toBeInTheDocument();
});
it('filters transport events while retaining exact large order identifiers and commission', () => {
  const r = report();r.records=4;r.messages=1;r.unique_messages=1;r.event_counts={ORDER_TRADE_UPDATE:1};
  r.recent.push({...record(4,'MESSAGE'),event_kind:'ORDER_TRADE_UPDATE',event_ms:at+20,transaction_ms:at+10,
    symbol:'BTCUSDC',order_id:'9007199254740993',order_status:'FILLED',last_quantity:'0.001',commission:'0.00000003',commission_asset:'USDC'});
  expect(userStreamValid({status:'AVAILABLE',report:r},at)).toBe(true);
  render(<DemoUserStream snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  fireEvent.change(screen.getByLabelText('事件筛选'),{target:{value:'MESSAGE'}});
  expect(screen.getByText(/9007199254740993/)).toBeInTheDocument();
  expect(screen.getByText(/0.00000003 USDC/)).toBeInTheDocument();
  expect(screen.queryByText('会话开始')).not.toBeInTheDocument();
});
it('rejects falsely complete history, inconsistent totals and unsafe order IDs', () => {
  for (const update of [{coverage_verified:true},{unique_messages:1},{event_counts:{ORDER_TRADE_UPDATE:1}},{state:'STALE'}]) {
    expect(userStreamValid({status:'AVAILABLE',report:{...report(),...update}},at)).toBe(false);
  }
  const r = report();r.recent[0].order_id=Number('9007199254740993') as unknown as string;
  expect(userStreamValid({status:'AVAILABLE',report:r},at)).toBe(false);
});
