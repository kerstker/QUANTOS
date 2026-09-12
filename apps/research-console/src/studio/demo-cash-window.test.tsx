import {afterEach, expect, it} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {DemoCashWindow} from './DemoCashWindow';
import {cashWindowValid} from '@/lib/api/demo-cash-window-contract';
import type {DemoCashWindowReport} from '@/lib/types/demo-cash-window';
import type {DemoCashAttempt} from '@/lib/types/demo-cash-window';

afterEach(cleanup);
const at=1789170000000;
function report(): DemoCashWindowReport {
  return {schema:'DEMO_CASH_WINDOW_V1',environment:'BINANCE_USDM_DEMO',status:'MATCHED_REPORTED_WINDOW',capture_status:'CAPTURED',
    observed_at_ms:at,completed_at_ms:at+100000,observe_seconds:60,account_ref_sha256:'a'.repeat(64),source_sha256:'b'.repeat(64),
    bounds:{query_start_ms:at-5000,query_end_ms:at+65000,inner_start_ms:at+5000,inner_end_ms:at+55000},
    read_count:6,income_count:1,duplicate_rows:0,endpoint_ambiguous_rows:0,sweeps_match:true,issues:[],failures:[],
    assets:[{asset:'USDT',opening_wallet:'1000',closing_wallet:'999.99999997',wallet_change:'-0.00000003',reported_flow:'-0.00000003',
      residual:'0',transfers:'0',commissions:'-0.00000003',funding:'0',realized_pnl:'0',breakdown:[{type:'COMMISSION',amount:'-0.00000003'}]}],
    recent_flows:[{asset:'USDT',type:'COMMISSION',amount:'-0.00000003',time_ms:at+30000,transaction_id:'9007199254740993',symbol:'BTCUSDT'}],
    ledger_reconciliation:'NOT_EVALUATED',strategy_pnl:null,history_complete:false,source_verified:false,submission_enabled:false,authoritative:false};
}
it('shows exact movement and scope without turning an account comparison into strategy profit',()=>{
  const r=report();expect(cashWindowValid({status:'AVAILABLE',report:r},at+100000)).toBe(true);
  render(<DemoCashWindow snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('窗口报告数值相符')).toBeInTheDocument();
  expect(screen.getByText('999.99999997')).toBeInTheDocument();
  fireEvent.click(screen.getByText('查看窗口流水明细'));
  expect(screen.getByText('9007199254740993')).toBeInTheDocument();
  expect(screen.getByText(/不代表策略盈利/)).toBeInTheDocument();
});
it('preserves unknown and failed states and shows a tiny mismatch exactly',()=>{
  const r=report();r.status='DIFFERENCE_FOUND';r.assets[0].residual='-0.00000001';
  const view=render(<DemoCashWindow snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('发现未解释差额')).toBeInTheDocument();
  expect(screen.getByText('-0.00000001')).toBeInTheDocument();
  r.status='UNRESOLVED';r.issues=['ENDPOINT_EVENT_AMBIGUITY'];r.assets[0].residual=null;
  view.rerender(<DemoCashWindow snapshot={{status:'AVAILABLE',report:r}} refreshFailed/>);
  expect(screen.getByText('核对条件未齐')).toBeInTheDocument();
  expect(screen.getByText(/采样边界附近存在流水/)).toBeInTheDocument();
  expect(screen.getByText(/刷新失败/)).toBeInTheDocument();
});
it('rejects false matches, inflated authority, unsafe IDs and future evidence',()=>{
  for(const patch of [{history_complete:true},{source_verified:true},{strategy_pnl:'1'},{completed_at_ms:at+100001},
    {status:'MATCHED_REPORTED_WINDOW',issues:['CAPTURE_INCOMPLETE']}]) {
    expect(cashWindowValid({status:'AVAILABLE',report:{...report(),...patch}},at+100000)).toBe(false);
  }
  const r=report();r.assets[0].opening_wallet=null;
  expect(cashWindowValid({status:'AVAILABLE',report:r},at+100000)).toBe(false);
  r.assets[0].opening_wallet='1000';r.recent_flows[0].transaction_id=Number('9007199254740993') as unknown as string;
  expect(cashWindowValid({status:'AVAILABLE',report:r},at+100000)).toBe(false);
});

function recovered(): DemoCashWindowReport {
  const r=report();r.schema='DEMO_CASH_WINDOW_V2';r.completed_at_ms=at+110000;
  const kinds: DemoCashAttempt['kind'][]=['clock_before','wallet_before','wallet_after','clock_after','income','income'];
  const times=[0,200,60300,60500,81000,101100];
  r.attempts=kinds.map((kind,i)=>({kind,requested_at_ms:at+times[i],received_at_ms:at+times[i]+100,
    sweep:i<4 ? 0 : i-3,page:i<4 ? 0 : 1,attempt:1,outcome:'SUCCESS',reason:null,
    account_ref_sha256:kind.startsWith('clock') ? null : r.account_ref_sha256,response_sha256:'c'.repeat(64),
    server_time_ms:kind.startsWith('clock') ? at+times[i]+50 : null}));
  const failed: DemoCashAttempt={...r.attempts[5],requested_at_ms:at+100000,received_at_ms:at+100100,
    outcome:'FAILED',reason:'REQUEST_FAILED',response_sha256:null};
  r.attempts[5].attempt=2;r.attempts.splice(5,0,failed);
  return r;
}
it('keeps recovered failures visible without classifying matching cash as profit',()=>{
  const r=recovered();expect(cashWindowValid({status:'AVAILABLE',report:r},at+110000)).toBe(true);
  render(<DemoCashWindow snapshot={{status:'AVAILABLE',report:r}} refreshFailed={false}/>);
  expect(screen.getByText('窗口报告数值相符')).toBeInTheDocument();
  fireEvent.click(screen.getByText('查看读取与重试记录（7 次尝试，1 次失败）'));
  expect(screen.getByText(/请求暂时失败/)).toBeInTheDocument();
  expect(screen.getByText(/第 2 轮 \/ 第 1 页 · 尝试 2 ·读取成功/)).toBeInTheDocument();
});
it('rejects missing traces, false retries, wrong identities and terminal failure concealment',()=>{
  const mutations: Array<(r:DemoCashWindowReport)=>void>=[
    r=>{delete r.attempts;},
    r=>{r.attempts![5].reason='AUTHENTICATION_REJECTED';},
    r=>{r.attempts![5].account_ref_sha256='d'.repeat(64);},
    r=>{r.attempts![6].attempt=4;},
    r=>{r.attempts![5].received_at_ms=at+101000;},
    r=>{r.attempts!.pop();},
    r=>{r.schema='DEMO_CASH_WINDOW_V1';},
  ];
  for (const mutate of mutations) {
    const r=recovered();mutate(r);expect(cashWindowValid({status:'AVAILABLE',report:r},at+110000)).toBe(false);
  }
});
